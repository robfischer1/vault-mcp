"""Tests for the automatic re-dissolve trigger (F4).

No threads, no clock, no network: the loop's decision logic is a pure function
over an injected sweep, which is the whole reason it is shaped that way.
"""

from __future__ import annotations

import logging

from vault_mcp.plan_freshness import (
    PlanDriftRecord,
    PlanDriftState,
    PlanSweepReport,
    StoredCopy,
    is_probably_stale,
    sweep_plans,
)
from vault_mcp.plan_reconcile import (
    DEFAULT_INTERVAL_SECONDS,
    reconcile_enabled,
    reconcile_interval,
    reconcile_once,
    start_plan_reconcile,
)


def _report(*records: PlanDriftRecord) -> PlanSweepReport:
    return PlanSweepReport(dry_run=False, directory="d", records=list(records))


# ── the cheap gate ──────────────────────────────────────────────────────────


def test_gate_says_stale_when_disk_is_newer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    assert is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime="2000-01-01T00:00:00Z"
    )


def test_gate_says_fresh_when_stored_clock_is_newer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    assert not is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime="2999-01-01T00:00:00Z"
    )


def test_gate_is_conservative_when_the_clock_is_unknown(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An unknown clock must fall through to the hash, never assume current."""
    (tmp_path / "p.md").write_text("x", encoding="utf-8")
    assert is_probably_stale(
        "p.md", vault_root=str(tmp_path), stored_mtime=None
    )


def test_gate_is_conservative_when_the_file_is_unstattable(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
        build_payload=lambda p, r: {"source_path": p},
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

    writes: list[dict] = []
    sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: body,
        read_stored=lambda p: StoredCopy(
            p, raw_hash=body_hash(body), body_bytes=len(body)
        ),
        build_payload=lambda p, r: {"source_path": p},
        write_stored=lambda pl: writes.append(pl) or {"ok": True},  # type: ignore[func-returns-value]
        refresh=True,
        cheap_gate=lambda _p: True,  # gate says "check it"
    )
    assert writes == [], "the hash said current, so nothing may be written"


# ── one reconcile pass ──────────────────────────────────────────────────────


def test_pass_with_no_change_writes_nothing_and_stays_quiet(caplog) -> None:  # type: ignore[no-untyped-def]
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


def test_pass_that_refreshes_names_each_plan(caplog) -> None:  # type: ignore[no-untyped-def]
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


def test_pass_reports_per_plan_errors(caplog) -> None:  # type: ignore[no-untyped-def]
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
    thread = start_plan_reconcile(
        lambda: called.append(1) or _report(),  # type: ignore[func-returns-value]
        enabled=False,
    )
    assert thread is None
    assert called == []


def test_enabled_loop_starts_a_daemon_thread_and_waits_first() -> None:
    """It must not sweep during startup — the first tick is after one interval."""
    called: list[int] = []
    thread = start_plan_reconcile(
        lambda: called.append(1) or _report(),  # type: ignore[func-returns-value]
        enabled=True,
        interval=3600,
    )
    assert thread is not None
    assert thread.daemon is True
    assert called == [], "no sweep may run at startup"
