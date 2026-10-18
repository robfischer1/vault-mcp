# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the automatic re-dissolve trigger (F4).

No threads, no clock, no network: the loop's decision logic is a pure function
over an injected sweep, which is the whole reason it is shaped that way.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from vault_mcp.plan_freshness import (
    PlanDriftRecord,
    PlanDriftState,
    PlanSweepReport,
    StoredCopy,
    is_auth_refusal,
    is_probably_stale,
    parse_instant,
    sweep_plans,
)
from vault_mcp.plan_reconcile import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_BACKOFF_SECONDS,
    backoff_delay,
    reconcile_enabled,
    reconcile_interval,
    reconcile_once,
    start_plan_reconcile,
)


def _report(*records: PlanDriftRecord) -> PlanSweepReport:
    return PlanSweepReport(dry_run=False, directory="d", records=list(records))


# ── the cheap gate ──────────────────────────────────────────────────────────


def test_gate_says_stale_when_disk_is_newer(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    assert is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime="2000-01-01T00:00:00Z"
    )


def test_gate_says_fresh_when_stored_clock_is_newer(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    assert not is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime="2999-01-01T00:00:00Z"
    )


def _set_mtime(path: Path, iso: str) -> None:
    ts = datetime.fromisoformat(iso).timestamp()
    os.utime(path, (ts, ts))


def test_gate_compares_instants_across_offsets(tmp_path: Path) -> None:
    """Disk is 2026-10-17T22:11:45Z; the same second spelled in Eastern is not newer."""
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    _set_mtime(f, "2026-10-17T22:11:45+00:00")
    root = str(tmp_path)
    # Lexically "2026-10-17T18..." < "2026-10-17T22..." would call these stale.
    for same in (
        "2026-10-17T18:11:45.000000-04:00",
        "2026-10-17T22:11:45+00:00",
        "2026-10-17T22:11:45Z",
        "2026-10-17T22:11:46Z",
    ):
        assert not is_probably_stale("p.md", vault_root=root, stored_mtime=same)
    # One second behind on disk's clock, in an offset that sorts after it.
    assert is_probably_stale(
        "p.md", vault_root=root, stored_mtime="2026-10-17T18:11:44-04:00"
    )
    # Eastern text that sorts lexically AFTER the Z text but is an hour older.
    assert is_probably_stale(
        "p.md", vault_root=root, stored_mtime="2026-10-17T21:11:45.5+00:00"
    )


def test_gate_reads_a_naive_stored_clock_as_utc(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    _set_mtime(f, "2026-10-17T22:11:45+00:00")
    root = str(tmp_path)
    assert not is_probably_stale(
        "p.md", vault_root=root, stored_mtime="2026-10-17T22:11:45"
    )
    assert is_probably_stale(
        "p.md", vault_root=root, stored_mtime="2026-10-17T22:11:44"
    )


def test_gate_is_conservative_when_the_clock_is_unparseable(
    tmp_path: Path,
) -> None:
    (tmp_path / "p.md").write_text("x", encoding="utf-8")
    assert is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime="not a time"
    )


def test_parse_instant() -> None:
    want = datetime(2026, 10, 17, 22, 11, 45, 188000, tzinfo=UTC)
    assert parse_instant("2026-10-17T22:11:45.188Z") == want
    assert parse_instant("2026-10-17T22:11:45.188000+00:00") == want
    assert parse_instant("2026-10-17T18:11:45.188000-04:00") == want
    assert parse_instant("2026-10-17T22:11:45.188") == want  # naive -> UTC
    assert parse_instant("") is None
    assert parse_instant(None) is None
    assert parse_instant("soon") is None


def test_gate_is_conservative_when_the_clock_is_unknown(tmp_path: Path) -> None:
    """An unknown clock must fall through to the hash, never assume current."""
    (tmp_path / "p.md").write_text("x", encoding="utf-8")
    assert is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime=None
    )


def test_gate_is_conservative_when_the_file_is_unstattable(
    tmp_path: Path,
) -> None:
    assert is_probably_stale(
        "missing.md",
        vault_root=str(tmp_path),
        stored_mtime="2026-01-01T00:00:00Z",
    )


def test_gated_records_are_marked_and_skip_the_store_read() -> None:
    """The gate must skip work without the report claiming a verified check."""
    reads: list[str] = []

    def read_stored(p: str) -> StoredCopy | None:
        reads.append(p)
        return None

    report = sweep_plans(
        list_files=lambda _d: ["a.md", "b.md"],
        read_vault=lambda _p: "body",
        read_stored=read_stored,
        build_payload=lambda p, _r: {"source_path": p},
        write_stored=lambda _p: {"ok": True},
        cheap_gate=lambda p: p == "a.md",  # only a.md may be stale
    )
    assert reads == ["a.md"], "b.md must not hit the store"
    by = {r.source_path: r for r in report.records}
    assert by["b.md"].gated is True
    assert by["b.md"].state is PlanDriftState.CURRENT
    assert by["a.md"].gated is False


def test_gate_never_suppresses_a_write_on_its_own() -> None:
    """A gate that says 'stale' still needs the hash to authorise a write."""
    body = "unchanged"
    from vault_mcp.plan_freshness import body_hash

    writes: list[dict[str, Any]] = []

    def _write_stored(pl: dict[str, Any]) -> dict[str, Any]:
        writes.append(pl)
        return {"ok": True}

    sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: body,
        read_stored=lambda p: StoredCopy(
            p, raw_hash=body_hash(body), body_bytes=len(body)
        ),
        build_payload=lambda p, _r: {"source_path": p},
        write_stored=_write_stored,
        refresh=True,
        cheap_gate=lambda _p: True,  # gate says "check it"
    )
    assert writes == [], "the hash said current, so nothing may be written"


# ── one reconcile pass ──────────────────────────────────────────────────────


def test_pass_with_no_change_writes_nothing_and_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    out = reconcile_once(
        lambda: _report(
            PlanDriftRecord("a.md", PlanDriftState.CURRENT),
            PlanDriftRecord("b.md", PlanDriftState.CURRENT, gated=True),
        )
    )
    assert out["ok"] is True
    assert out["refreshed"] == 0
    assert out["plans"] == []
    assert caplog.records == [], "a quiet system must stay quiet at INFO"


def test_pass_that_refreshes_names_each_plan(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    out = reconcile_once(
        lambda: _report(
            PlanDriftRecord(
                "a.md", PlanDriftState.CURRENT, refreshed=True, delta_bytes=42
            ),
            PlanDriftRecord("b.md", PlanDriftState.CURRENT),
        )
    )
    assert out["refreshed"] == 1
    assert out["plans"] == ["a.md"]
    assert "a.md" in caplog.text, "the trigger must be observable"


def test_pass_reports_per_plan_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    out = reconcile_once(
        lambda: _report(
            PlanDriftRecord("bad.md", PlanDriftState.ERROR, error="boom")
        )
    )
    assert out["errors"] == 1
    assert "boom" in caplog.text


def test_a_failing_pass_does_not_raise() -> None:
    """One bad pass must never kill the loop — that restores the old defect."""

    def explode() -> PlanSweepReport:
        raise RuntimeError("store down")

    out = reconcile_once(explode)
    assert out["ok"] is False
    assert "store down" in out["error"]
    assert out["refreshed"] == 0


# ── configuration ───────────────────────────────────────────────────────────


def test_reconcile_is_on_by_default() -> None:
    assert reconcile_enabled({}) is True


def test_reconcile_can_be_disabled() -> None:
    for off in ("0", "false", "no", "off", "OFF"):
        assert reconcile_enabled({"VAULT_MCP_PLAN_RECONCILE": off}) is False


def test_interval_defaults_and_validates() -> None:
    assert reconcile_interval({}) == DEFAULT_INTERVAL_SECONDS
    assert reconcile_interval({"VAULT_MCP_PLAN_RECONCILE_SECONDS": "60"}) == 60
    # garbage and non-positive values fall back rather than busy-loop
    assert (
        reconcile_interval({"VAULT_MCP_PLAN_RECONCILE_SECONDS": "nope"})
        == DEFAULT_INTERVAL_SECONDS
    )
    assert (
        reconcile_interval({"VAULT_MCP_PLAN_RECONCILE_SECONDS": "0"})
        == DEFAULT_INTERVAL_SECONDS
    )
    assert (
        reconcile_interval({"VAULT_MCP_PLAN_RECONCILE_SECONDS": "-5"})
        == DEFAULT_INTERVAL_SECONDS
    )


def test_disabled_loop_starts_no_thread() -> None:
    called: list[int] = []

    def _sweep() -> PlanSweepReport:
        called.append(1)
        return _report()

    thread = start_plan_reconcile(_sweep, enabled=False)
    assert thread is None
    assert called == []


def test_enabled_loop_starts_a_daemon_thread_and_waits_first() -> None:
    """It must not sweep during startup — the first tick is after one interval."""
    called: list[int] = []

    def _sweep() -> PlanSweepReport:
        called.append(1)
        return _report()

    thread = start_plan_reconcile(_sweep, enabled=True, interval=3600)
    assert thread is not None
    assert thread.daemon is True
    assert called == [], "no sweep may run at startup"


# ── auth refusal: abort the sweep, back off the loop ───────────────────────

_REFUSAL = "calliope_materialize_note: caller not permitted (unmapped subject)"


def _sweep_files(
    n: int, *, read_error: str, reads: list[str]
) -> PlanSweepReport:
    def read_stored(p: str) -> StoredCopy | None:
        reads.append(p)
        raise RuntimeError(read_error)

    return sweep_plans(
        list_files=lambda _d: [f"p{i}.md" for i in range(n)],
        read_vault=lambda _p: "body",
        read_stored=read_stored,
        build_payload=lambda p, _r: {"source_path": p},
        write_stored=lambda _p: {"ok": True},
    )


def test_is_auth_refusal_matches_the_refusal_family() -> None:
    for msg in (
        "unmapped_subject",
        "caller not permitted (unmapped subject)",
        "denied: no grant",
        "hades HTTP 401: nope",
        "hades HTTP 403: nope",
    ):
        assert is_auth_refusal(msg), msg
    benign: tuple[str | None, ...] = (
        "not_found: x",
        "hades HTTP 500: boom",
        "",
        None,
    )
    for other in benign:
        assert not is_auth_refusal(other), other


def test_sweep_aborts_on_first_auth_refusal() -> None:
    reads: list[str] = []
    report = _sweep_files(226, read_error=_REFUSAL, reads=reads)
    assert reads == ["p0.md"], "one refusal must stop the other 225 calls"
    assert report.aborted_auth is not None
    assert "unmapped" in report.aborted_auth
    assert report.to_dict()["aborted_auth"] == report.aborted_auth


def test_sweep_continues_past_an_ordinary_store_error() -> None:
    reads: list[str] = []
    report = _sweep_files(3, read_error="hades HTTP 500: boom", reads=reads)
    assert len(reads) == 3
    assert report.aborted_auth is None


def test_sweep_aborts_on_an_auth_refusal_from_the_write_leg() -> None:
    writes: list[str] = []

    def write_stored(pl: dict[str, Any]) -> dict[str, Any]:
        writes.append(pl["source_path"])
        return {"ok": False, "error": "hades HTTP 403: forbidden"}

    report = sweep_plans(
        list_files=lambda _d: ["a.md", "b.md"],
        read_vault=lambda _p: "new",
        read_stored=lambda p: StoredCopy(p, raw_hash="old", body_bytes=3),
        build_payload=lambda p, _r: {"source_path": p},
        write_stored=write_stored,
        refresh=True,
    )
    assert writes == ["a.md"]
    assert report.aborted_auth is not None


def test_reconcile_once_reports_auth_refusal_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reads: list[str] = []
    out = reconcile_once(
        lambda: _sweep_files(5, read_error=_REFUSAL, reads=reads)
    )
    assert out["ok"] is False
    assert out["auth_refused"] is True
    assert caplog.records == [], "the loop owns the once-only log line"


def test_backoff_doubles_and_caps() -> None:
    assert backoff_delay(900, 0) == 900
    assert backoff_delay(900, 1) == 1800
    assert backoff_delay(900, 2) == 3600
    assert backoff_delay(900, 3) == 7200
    assert backoff_delay(900, 4) == 14400
    assert backoff_delay(900, 5) == MAX_BACKOFF_SECONDS
    assert backoff_delay(900, 50) == MAX_BACKOFF_SECONDS


def test_loop_backs_off_logs_once_and_recovers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Drive the real loop with a fake clock: refuse x3, then succeed."""
    import threading
    import types
    from unittest import mock

    import vault_mcp.plan_reconcile as pr

    caplog.set_level(logging.INFO)
    waits: list[float] = []
    results = iter([True, True, True, False])
    done = threading.Event()

    def _wait(t: float) -> bool:
        waits.append(t)
        return done.is_set()

    stop = mock.Mock()
    stop.wait.side_effect = _wait
    monkeypatch.setattr(
        pr,
        "threading",
        types.SimpleNamespace(Event=lambda: stop, Thread=threading.Thread),
    )

    def sweep() -> PlanSweepReport:
        try:
            refused = next(results)
        except StopIteration:
            done.set()
            return _report()
        if refused:
            return PlanSweepReport(
                dry_run=False,
                directory="d",
                aborted_auth=_REFUSAL,
            )
        return _report()

    thread = start_plan_reconcile(sweep, enabled=True, interval=100)
    assert thread is not None
    thread.join(timeout=5)
    assert waits[:5] == [100, 200, 400, 800, 100]
    refusal_logs = [
        r for r in caplog.records if "refused this caller" in r.message
    ]
    assert len(refusal_logs) == 1, "a standing refusal logs once, not per tick"
    assert "accepted the caller again" in caplog.text
