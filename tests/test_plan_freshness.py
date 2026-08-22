"""Tests for the plan-freshness sweep (F1).

Every test runs over injected callables and a tmp_path vault — no network, no
live store. The invariant under test throughout is direction: the sweep reads
the vault and writes the store, and never the reverse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from vault_mcp.plan_freshness import (
    DEFAULT_PLAN_DIRECTORY,
    PlanDriftState,
    StoredCopy,
    body_hash,
    compare_plan,
    sweep_plans,
)

FRONTMATTER = "---\ntitle: A plan\nnote_type: Master-plan\n---\n"
BODY = "# A plan\n\nSome prose.\n"
RAW = FRONTMATTER + BODY


def _stripped(raw: str) -> str:
    from vault_mcp.parsers import strip_frontmatter

    return strip_frontmatter(raw)


class _Writer:
    """A recording write_document double."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result if result is not None else {"ok": True, "id": 1}

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return self.result


def _payload(source_path: str, raw: str) -> dict[str, Any]:
    return {"source_path": source_path, "body_text": _stripped(raw)}


def _sweep(
    files: dict[str, str],
    stored: dict[str, StoredCopy | None],
    *,
    refresh: bool = False,
    include_missing: bool = False,
    writer: _Writer | None = None,
    limit: int | None = None,
) -> tuple[Any, _Writer]:
    w = writer or _Writer()

    def read_stored(p: str) -> StoredCopy | None:
        return stored.get(p)

    report = sweep_plans(
        list_files=lambda _d: list(files),
        read_vault=files.__getitem__,
        read_stored=read_stored,
        build_payload=_payload,
        write_stored=w,
        refresh=refresh,
        include_missing=include_missing,
        limit=limit,
    )
    return report, w


# ── User Story 1: the drift report ──────────────────────────────────────────


def test_drift_report_flags_stale_plan() -> None:
    """T004 — a stored hash that differs is `drifted`, with the delta. FR-001/003."""
    stale_body = "# A plan\n"
    report, _ = _sweep(
        {"p.md": RAW},
        {
            "p.md": StoredCopy(
                source_path="p.md",
                raw_hash=body_hash(stale_body),
                body_bytes=len(stale_body.encode()),
            )
        },
    )
    (rec,) = report.records
    assert rec.state is PlanDriftState.DRIFTED
    assert rec.delta_bytes == len(_stripped(RAW).encode()) - len(
        stale_body.encode()
    )
    assert rec.delta_bytes > 0


def test_matching_plan_reports_current() -> None:
    """T005 — equal hashes are `current`. Equality is by hash, not timestamp. FR-002."""
    body = _stripped(RAW)
    report, _ = _sweep(
        {"p.md": RAW},
        {
            "p.md": StoredCopy(
                source_path="p.md",
                raw_hash=body_hash(body),
                body_bytes=len(body.encode()),
            )
        },
    )
    (rec,) = report.records
    assert rec.state is PlanDriftState.CURRENT
    assert rec.delta_bytes == 0
    assert report.counts["drifted"] == 0


def test_plan_absent_from_store_reports_missing() -> None:
    """T006 — a vault file with no stored copy is `missing`, not `drifted`. FR-008."""
    report, _ = _sweep({"p.md": RAW}, {})
    (rec,) = report.records
    assert rec.state is PlanDriftState.MISSING
    assert rec.stored_hash is None


def test_stored_copy_without_vault_file_reports_orphaned() -> None:
    """T007 — a stored copy whose file has vanished is `orphaned`, untouched. FR-008."""
    rec = compare_plan(
        "gone.md",
        vault_text=None,
        stored=StoredCopy(source_path="gone.md", raw_hash="abc", body_bytes=10),
    )
    assert rec.state is PlanDriftState.ORPHANED
    assert rec.refreshed is False


def test_report_only_writes_nothing() -> None:
    """T008 — the default mode performs zero writes. FR-004, G-1."""
    report, writer = _sweep({"p.md": RAW}, {})
    assert writer.calls == []
    assert report.dry_run is True
    assert report.refreshed == 0


def test_full_file_size_reported_alongside_stripped_body() -> None:
    """The two sizes are distinct and both surfaced, so neither is mistaken."""
    report, _ = _sweep({"p.md": RAW}, {})
    (rec,) = report.records
    assert rec.vault_bytes == len(_stripped(RAW).encode())
    assert rec.vault_file_bytes == len(RAW.encode())
    assert rec.vault_file_bytes > rec.vault_bytes


# ── User Story 2: the refresh ───────────────────────────────────────────────


def test_refresh_writes_new_version_for_drifted_plan() -> None:
    """T011 — a drifted plan is written, and reports current afterwards. FR-005."""
    report, writer = _sweep(
        {"p.md": RAW},
        {"p.md": StoredCopy("p.md", raw_hash=body_hash("old"), body_bytes=3)},
        refresh=True,
    )
    (rec,) = report.records
    assert len(writer.calls) == 1
    assert writer.calls[0]["body_text"] == _stripped(RAW)
    assert rec.refreshed is True
    assert rec.state is PlanDriftState.CURRENT
    assert rec.stored_hash == rec.vault_hash
    assert report.refreshed == 1


def test_refresh_does_not_populate_missing_plans_by_default() -> None:
    """A refresh repairs drift; it does not populate the store.

    Measured 2026-08-11: 173 of 175 WBS plans have no stored copy (carve_policy
    keeps System/ in the vault). A refresh that also populated would push ~10MB
    of prose into the store as a side effect of a freshness repair.
    """
    report, writer = _sweep({"p.md": RAW}, {}, refresh=True)
    assert writer.calls == []
    assert report.records[0].state is PlanDriftState.MISSING
    assert report.records[0].refreshed is False


def test_include_missing_populates_explicitly() -> None:
    """Populating is available, but only as a deliberate opt-in."""
    report, writer = _sweep(
        {"p.md": RAW}, {}, refresh=True, include_missing=True
    )
    assert len(writer.calls) == 1
    assert report.records[0].refreshed is True


def test_refresh_skips_current_plan() -> None:
    """T012 — a current plan is never written, even with refresh=True. FR-006, G-3."""
    body = _stripped(RAW)
    report, writer = _sweep(
        {"p.md": RAW},
        {
            "p.md": StoredCopy(
                "p.md", raw_hash=body_hash(body), body_bytes=len(body)
            )
        },
        refresh=True,
    )
    assert writer.calls == []
    assert report.records[0].refreshed is False
    assert report.refreshed == 0


def test_refresh_never_touches_vault_file(tmp_path: Path) -> None:
    """T013 — the vault file survives a refresh unmodified. G-2."""
    f = tmp_path / "p.md"
    f.write_text(RAW, encoding="utf-8")
    before_bytes = f.read_bytes()
    before_mtime = f.stat().st_mtime_ns

    sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: f.read_text(encoding="utf-8"),
        read_stored=lambda _p: StoredCopy(
            "p.md", raw_hash="stale", body_bytes=1
        ),
        build_payload=_payload,
        write_stored=_Writer(),
        refresh=True,
    )

    assert f.exists()
    assert f.read_bytes() == before_bytes
    assert f.stat().st_mtime_ns == before_mtime


def test_write_failure_is_per_plan_and_sweep_continues() -> None:
    """T014 — one plan's write failure does not abort the others. FR-007, G-5."""

    def failing(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["source_path"] == "bad.md":
            return {"ok": False, "error": "boom"}
        return {"ok": True, "id": 2}

    def stale(p: str) -> StoredCopy:
        return StoredCopy(p, raw_hash=body_hash("old"), body_bytes=3)

    report = sweep_plans(
        list_files=lambda _d: ["bad.md", "good.md"],
        read_vault=lambda _p: RAW,
        read_stored=stale,
        build_payload=_payload,
        write_stored=failing,
        refresh=True,
    )
    by_path = {r.source_path: r for r in report.records}
    assert by_path["bad.md"].state is PlanDriftState.ERROR
    assert "boom" in (by_path["bad.md"].error or "")
    assert by_path["good.md"].state is PlanDriftState.CURRENT
    assert report.scanned == 2


def test_vault_read_failure_is_per_plan() -> None:
    """An unreadable file becomes an error record; the sweep continues."""

    def read(p: str) -> str:
        if p == "bad.md":
            raise OSError("unreadable")
        return RAW

    report = sweep_plans(
        list_files=lambda _d: ["bad.md", "good.md"],
        read_vault=read,
        read_stored=lambda _p: None,
        build_payload=_payload,
        write_stored=_Writer(),
    )
    by_path = {r.source_path: r for r in report.records}
    assert by_path["bad.md"].state is PlanDriftState.ERROR
    assert by_path["good.md"].state is PlanDriftState.MISSING


def test_store_read_failure_is_per_plan() -> None:
    """A store read that raises becomes an error record, not a crash."""

    # `read_stored(source_path)` — called positionally; this one raises before
    # it could read the path. The other three doubles in this file DO read it.
    def read_stored(_p: str) -> StoredCopy | None:
        raise RuntimeError("store down")

    report = sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: RAW,
        read_stored=read_stored,
        build_payload=_payload,
        write_stored=_Writer(),
    )
    assert report.records[0].state is PlanDriftState.ERROR
    assert "store down" in (report.records[0].error or "")


def test_refresh_is_idempotent() -> None:
    """T015 — a second refresh run writes nothing. G-4."""
    files = {"p.md": RAW}
    stored: dict[str, StoredCopy | None] = {
        "p.md": StoredCopy("p.md", raw_hash=body_hash("old"), body_bytes=3)
    }
    writer = _Writer()

    def write(payload: dict[str, Any]) -> dict[str, Any]:
        # Model the store: the write lands and becomes the newest copy.
        body = payload["body_text"]
        stored["p.md"] = StoredCopy(
            "p.md", raw_hash=body_hash(body), body_bytes=len(body.encode())
        )
        return writer(payload)

    def read_stored(p: str) -> StoredCopy | None:
        return stored.get(p)

    for _ in range(2):
        sweep_plans(
            list_files=lambda _d: list(files),
            read_vault=files.__getitem__,
            read_stored=read_stored,
            build_payload=_payload,
            write_stored=write,
            refresh=True,
        )

    assert len(writer.calls) == 1, "second run must be a no-op"


# ── User Story 3: the sweep ─────────────────────────────────────────────────


def test_sweep_covers_every_plan_once() -> None:
    """T018 — each plan appears exactly once. SC-001."""
    files = {f"p{i}.md": RAW for i in range(5)}
    report, _ = _sweep(files, {})
    assert report.scanned == 5
    assert len({r.source_path for r in report.records}) == 5


def test_limit_caps_acted_on_plans() -> None:
    """T019 — limit bounds the run."""
    files = {f"p{i}.md": RAW for i in range(5)}
    report, _ = _sweep(files, {}, limit=2)
    assert report.scanned == 2


def test_counts_are_derived_from_records() -> None:
    """counts/refreshed are computed, never tracked separately."""
    body = _stripped(RAW)
    report, _ = _sweep(
        {"a.md": RAW, "b.md": RAW},
        {
            "a.md": StoredCopy(
                "a.md", raw_hash=body_hash(body), body_bytes=len(body)
            )
        },
    )
    assert report.counts["current"] == 1
    assert report.counts["missing"] == 1
    assert sum(report.counts.values()) == report.scanned


def test_preflight_failure_short_circuits_the_sweep() -> None:
    """A failed pre-flight returns an empty, not-ok report without reading."""
    report = sweep_plans(
        list_files=lambda _d: pytest.fail("must not enumerate"),
        read_vault=lambda _p: pytest.fail("must not read"),
        read_stored=lambda _p: None,
        build_payload=_payload,
        write_stored=_Writer(),
        preflight={"ok": False, "error": "hades_url_unset"},
    )
    assert report.to_dict()["ok"] is False
    assert report.records == []


def test_report_dict_shape() -> None:
    """The wire shape carries the documented keys."""
    report, _ = _sweep({"p.md": RAW}, {})
    d = report.to_dict()
    assert d["ok"] is True
    assert d["dry_run"] is True
    assert d["directory"] == DEFAULT_PLAN_DIRECTORY
    assert d["scanned"] == 1
    assert d["records"][0]["state"] == "missing"
    assert set(d["counts"]) == {
        "current",
        "drifted",
        "missing",
        "orphaned",
        "error",
    }


# ── F2: the source-modification clock ───────────────────────────────────────


def test_file_mtime_iso_is_utc_second_precision(tmp_path: Path) -> None:
    """FR-004 — timezone-explicit, fixed precision, lexicographically ordered."""
    from vault_mcp.plan_freshness import file_mtime_iso

    f = tmp_path / "n.md"
    f.write_text("x", encoding="utf-8")
    got = file_mtime_iso(f)
    assert got is not None
    assert got.endswith("Z")
    assert len(got) == len("2026-08-11T21:22:29Z")


def test_file_mtime_iso_returns_none_instead_of_raising() -> None:
    """FR-007 — a stat failure must never fail a dissolve."""
    from vault_mcp.plan_freshness import file_mtime_iso

    assert file_mtime_iso("/nonexistent/nope.md") is None
    assert file_mtime_iso("") is None


def test_file_mtime_beats_frontmatter_updated() -> None:
    """FR-002 / SC-004 — the mechanical clock wins over the declared one."""
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    fm = {"updated": "2026-07-24", "title": "A plan"}
    pls = note_to_payloads(
        fm, BODY, "p.md", source_mtime="2026-08-11T21:22:29Z"
    )
    doc = next(p["payload"] for p in pls if p["endpoint"] == DOC_ENDPOINT)
    assert doc["mtime"] == "2026-08-11T21:22:29Z"


def test_frontmatter_updated_is_the_fallback() -> None:
    """FR-003 rung 2 — no file time, but a declared one."""
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    pls = note_to_payloads({"updated": "2026-07-24"}, BODY, "p.md")
    doc = next(p["payload"] for p in pls if p["endpoint"] == DOC_ENDPOINT)
    assert doc["mtime"] == "2026-07-24"


def test_no_clock_at_all_omits_the_key() -> None:
    """FR-003 rung 3 — absent means unset, never a fabricated value."""
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    pls = note_to_payloads({}, BODY, "p.md")
    doc = next(p["payload"] for p in pls if p["endpoint"] == DOC_ENDPOINT)
    assert "mtime" not in doc


def test_omitting_source_mtime_is_backwards_compatible() -> None:
    """The new parameter is additive — existing callers behave as before."""
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    fm = {"updated": "2026-07-24", "created": "2026-07-04"}
    before = next(
        p["payload"]
        for p in note_to_payloads(fm, BODY, "p.md")
        if p["endpoint"] == DOC_ENDPOINT
    )
    assert before["mtime"] == "2026-07-24"
    assert before["ctime"] == "2026-07-04"


def test_backfill_reconciles_current_plan_without_new_version() -> None:
    """F2 backfill — a current plan is re-sent for provenance only."""
    body = _stripped(RAW)
    stored = {
        "p.md": StoredCopy(
            "p.md", raw_hash=body_hash(body), body_bytes=len(body)
        )
    }
    w = _Writer()

    def read_stored(p: str) -> StoredCopy | None:
        return stored.get(p)

    report = sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: RAW,
        read_stored=read_stored,
        build_payload=_payload,
        write_stored=w,
        backfill=True,
    )
    rec = report.records[0]
    assert len(w.calls) == 1
    assert rec.state is PlanDriftState.CURRENT
    assert rec.backfilled is True
    assert rec.refreshed is False, "a backfill is not a body refresh"
    assert report.backfilled == 1
    assert report.refreshed == 0


def test_refresh_still_never_writes_a_current_plan_when_backfill_is_off() -> (
    None
):
    """G-3 stays literally true — backfill is a separate mode, not a widening."""
    body = _stripped(RAW)
    report, writer = _sweep(
        {"p.md": RAW},
        {
            "p.md": StoredCopy(
                "p.md", raw_hash=body_hash(body), body_bytes=len(body)
            )
        },
        refresh=True,
    )
    assert writer.calls == []
    assert report.records[0].backfilled is False


def test_backfill_failure_is_reported_per_plan() -> None:
    """A failed backfill becomes an error record, not a crash."""
    body = _stripped(RAW)
    report = sweep_plans(
        list_files=lambda _d: ["p.md"],
        read_vault=lambda _p: RAW,
        read_stored=lambda _p: StoredCopy(
            "p.md", raw_hash=body_hash(body), body_bytes=len(body)
        ),
        build_payload=_payload,
        write_stored=lambda _p: {"ok": False, "error": "nope"},
        backfill=True,
    )
    assert report.records[0].state is PlanDriftState.ERROR
    assert "nope" in (report.records[0].error or "")
