"""Bulk-carve driver tests (C6) — proven via fixtures / dry-run, never a live sweep.

Every test injects a fake file lister + a fake ``dissolve_one``; no real vault
file is written or deleted. Covers the HADES_URL-unset pre-flight refusal, the
dry-run selection, the live per-file write->verify->delete (faked), the fail-safe
on a per-file error, governance exclusion, the acted-on limit, and the
Calliope-backed dissolution-wave mapper.
"""

from __future__ import annotations

from typing import Any

from vault_mcp.carve import (
    bulk_carve,
    carve_preflight,
    documents_to_waves,
)

# A fixture "vault" listing — dissolvable + governance + unlisted, mixed.
FIXTURE_FILES = [
    "Brain Soup/Idea One.md",
    "Brain Soup/Idea Two.md",
    "Records/A Record.md",
    "Entities/Books/Book.md",
    "System/Pantheon/WBS/Plan.md",  # governance — stays
    "Garden/Canon/Voice.md",  # unlisted — stays (fail-safe)
    "attachments/pic.png",  # non-md — stays
]


def _lister(files: list[str]):
    def _list() -> list[str]:
        return files

    return _list


class _RecordingDissolver:
    """Records the files it was asked to dissolve; configurable failure."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.dissolved: list[str] = []
        self.fail_on = fail_on or set()

    def __call__(self, path: str) -> dict[str, Any]:
        self.dissolved.append(path)
        if path in self.fail_on:
            return {"ok": False, "error": "boom", "stage": "write"}
        return {
            "ok": True,
            "written": [{"table": "documents", "id": 1}],
            "deleted": True,
        }


# -- pre-flight ---------------------------------------------------------------


def test_preflight_refuses_when_hades_url_unset() -> None:
    pf = carve_preflight(hades_url="")
    assert pf.ok is False
    assert pf.error == "hades_url_unset"
    assert "retired phdb" in (pf.detail or "")


def test_preflight_passes_with_hades_url_and_smoke() -> None:
    pf = carve_preflight(
        hades_url="http://nas01:8101", calliope_check=lambda: True
    )
    assert pf.ok is True


def test_preflight_refuses_when_calliope_smoke_fails() -> None:
    pf = carve_preflight(
        hades_url="http://nas01:8101", calliope_check=lambda: False
    )
    assert pf.ok is False
    assert pf.error == "calliope_unreachable"


def test_unset_hades_url_short_circuits_the_whole_sweep() -> None:
    dissolver = _RecordingDissolver()
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=dissolver,
        preflight=carve_preflight(hades_url=""),
        dry_run=False,
    )
    d = report.to_dict()
    assert d["ok"] is False
    assert d["preflight"]["error"] == "hades_url_unset"
    assert d["scanned"] == 0
    assert dissolver.dissolved == []  # nothing touched


# -- dry-run selection --------------------------------------------------------


def test_dry_run_selects_scope_policied_files_and_touches_nothing() -> None:
    dissolver = _RecordingDissolver()
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=dissolver,
        preflight=carve_preflight(hades_url="http://nas01:8101"),
        dry_run=True,
    )
    d = report.to_dict()
    assert d["dry_run"] is True
    assert d["scanned"] == len(FIXTURE_FILES)
    # 4 dissolvable (2 Brain Soup + 1 Records + 1 Entities); 3 stay.
    assert d["selected"] == 4
    assert d["skipped"] == 3
    assert d["dissolved"] == 0
    assert dissolver.dissolved == []  # dry-run never calls dissolve_one

    would = {f["path"] for f in d["files"] if f["action"] == "would-dissolve"}
    assert would == {
        "Brain Soup/Idea One.md",
        "Brain Soup/Idea Two.md",
        "Records/A Record.md",
        "Entities/Books/Book.md",
    }
    # Governance / unlisted / non-md are all skipped.
    reasons = {
        f["path"]: f["reason"] for f in d["files"] if f["action"] == "skipped"
    }
    assert reasons["System/Pantheon/WBS/Plan.md"] == "protected-pillar"
    assert reasons["Garden/Canon/Voice.md"] == "unlisted-pillar-default-stay"
    assert reasons["attachments/pic.png"] == "not-markdown"


def test_open_decisions_ride_on_the_report() -> None:
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=_RecordingDissolver(),
        preflight=carve_preflight(hades_url="http://nas01:8101"),
    )
    d = report.to_dict()
    assert any("SCOPE CUT UNCONFIRMED" in x for x in d["open_decisions"])
    assert any("OBSIDIAN DISPOSITION" in x for x in d["open_decisions"])


# -- live sweep (faked dissolve) ----------------------------------------------


def test_live_sweep_dissolves_each_selected_file_once() -> None:
    dissolver = _RecordingDissolver()
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=dissolver,
        preflight=carve_preflight(hades_url="http://nas01:8101"),
        dry_run=False,
    )
    d = report.to_dict()
    assert d["dissolved"] == 4
    assert d["failed"] == 0
    assert sorted(dissolver.dissolved) == [
        "Brain Soup/Idea One.md",
        "Brain Soup/Idea Two.md",
        "Entities/Books/Book.md",
        "Records/A Record.md",
    ]
    # Governance was never handed to the dissolver.
    assert "System/Pantheon/WBS/Plan.md" not in dissolver.dissolved


def test_fail_safe_records_failure_and_keeps_going() -> None:
    # A per-file failure must not halt the sweep, and (content-never-lost) the
    # dissolver's own write->verify->delete leaves the file in place.
    dissolver = _RecordingDissolver(fail_on={"Brain Soup/Idea One.md"})
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=dissolver,
        preflight=carve_preflight(hades_url="http://nas01:8101"),
        dry_run=False,
    )
    d = report.to_dict()
    assert d["failed"] == 1
    assert d["dissolved"] == 3  # the other three still went through
    failed = [f for f in d["files"] if f["action"] == "failed"]
    assert failed[0]["path"] == "Brain Soup/Idea One.md"
    assert failed[0]["reason"] == "boom"


def test_a_raising_dissolver_is_caught_per_file() -> None:
    def _boom(_path: str) -> dict[str, Any]:
        raise RuntimeError("network died")

    report = bulk_carve(
        list_files=_lister(["Brain Soup/One.md", "Records/Two.md"]),
        dissolve_one=_boom,
        preflight=carve_preflight(hades_url="http://nas01:8101"),
        dry_run=False,
    )
    d = report.to_dict()
    assert d["failed"] == 2
    assert d["dissolved"] == 0
    assert all(f["reason"] == "dissolve-raised" for f in d["files"])


def test_limit_caps_acted_on_files() -> None:
    dissolver = _RecordingDissolver()
    report = bulk_carve(
        list_files=_lister(FIXTURE_FILES),
        dissolve_one=dissolver,
        preflight=carve_preflight(hades_url="http://nas01:8101"),
        dry_run=False,
        limit=2,
    )
    d = report.to_dict()
    assert d["dissolved"] == 2
    assert len(dissolver.dissolved) == 2
    # The rest of the dissolvable files are skipped-by-limit, not dropped.
    limited = [f for f in d["files"] if f["reason"] == "limit-reached"]
    assert len(limited) == 2


# -- the dissolution-wave mapper (list_dissolution_waves repoint) -------------


def test_documents_to_waves_projects_the_calliope_record() -> None:
    docs = [
        {
            "id": 7,
            "source_path": "Brain Soup/Idea.md",
            "schema_type": "DigitalDocument",
            "created_at": "2026-07-17T00:00:00Z",
        },
        {
            "id": 8,
            "source_path": "Entities/Books/Book.md",
            "schema_type": "Book",
            "created_at": "2026-07-16T00:00:00Z",
        },
    ]
    waves = documents_to_waves(docs)
    assert len(waves) == 2
    assert waves[0]["source_path"] == "Brain Soup/Idea.md"
    assert waves[0]["target_tables"] == ["documents"]
    assert waves[0]["target_schemas"] == ["DigitalDocument"]
    assert waves[0]["dissolved_at"] == "2026-07-17T00:00:00Z"
    assert waves[1]["target_schemas"] == ["Book"]
