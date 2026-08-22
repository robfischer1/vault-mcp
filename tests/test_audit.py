"""Unit tests for the Audit epic — drift scan + heal through the Convention Gate.

The healing path: scan a directory for schema drift, report it, and (with
resolve=True) deterministically correct what is safely fixable while reporting
the rest for a human. Writes only when the corrected note lints clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gate import ConventionGate
from vault_mcp.schema import load_schema

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"


class FakeVault:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def create_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def delete_note(self, path: str) -> None:
        self.store.pop(path, None)

    def list_notes(
        self, directory: str = "", *, recursive: bool = True
    ) -> list[str]:
        return [
            p
            for p in self.store
            if not directory
            or p == f"{directory}.md"
            or p.startswith(directory + "/")
        ]


def _gate() -> tuple[ConventionGate, FakeVault]:
    vault = FakeVault()
    return ConventionGate(load_schema(VALID), vault), vault


def _note(fm_lines: list[str], body: str = "body") -> str:
    return "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n"


_BASE = [
    "title: t",
    "created: 2026-05-30",
    "author_type: ai",
    "author_level: ai-assisted",
]


class TestScan:
    def test_clean_note_not_reported(self):
        gate, _ = _gate()
        gate.create_note(
            title="Clean",
            note_type="note",
            pillar="Knowledge",
            body="x",
            created="2026-05-30",
        )
        report = gate.audit("Knowledge", resolve=False)
        assert report["drifted"] == 0
        assert report["corrected"] is None  # report-only

    def test_deprecated_key_and_dead_key_reported(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/d.md"] = _note(
            [*_BASE, "id: legacy-1", "sub_type: junk"]
        )
        report = gate.audit("Knowledge", resolve=False)
        assert report["drifted"] == 1
        cats = {d["category"] for d in report["notes"][0]["drift"]}
        assert {"deprecated_key", "dead_key"} <= cats

    def test_unknown_tag_and_bad_status_reported(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/u.md"] = _note(
            [*_BASE, "status: bogus", "tags: [topic/nope]"]
        )
        report = gate.audit("Knowledge", resolve=False)
        cats = {d["category"] for d in report["notes"][0]["drift"]}
        assert "tag" in cats
        assert "field" in cats


class TestHeal:
    def test_heals_deprecated_key_status_and_dead_key(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/h.md"] = _note(
            [*_BASE, "status: done", "id: legacy-7", "sub_type: junk"]
        )
        report = gate.audit("Knowledge", resolve=True)
        entry = report["notes"][0]
        assert entry["corrected"] is True
        stored = vault.store["Knowledge/Notes/h.md"]
        front = stored.split("---")[1]
        assert (
            "identifier: legacy-7" in front
        )  # id -> identifier (value preserved)
        assert "\nid:" not in front  # old key gone
        assert "status: Completed" in front  # done -> Completed
        assert "sub_type" not in front  # dead key dropped

    def test_unknown_tag_blocks_write_and_is_reported(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/x.md"] = _note(
            [*_BASE, "status: done", "tags: [topic/nope]"]
        )
        report = gate.audit("Knowledge", resolve=True)
        entry = report["notes"][0]
        assert entry["corrected"] is False  # not fully clean -> not written
        assert any("topic/nope" in r for r in entry["remaining"])
        assert (
            "status: done" in vault.store["Knowledge/Notes/x.md"]
        )  # left untouched

    def test_routing_relocation_moves_misfiled_note(self):
        gate, vault = _gate()
        # a 'note' (note_type+pillar route to Knowledge/Notes) sitting in Projects
        vault.store["Projects/m.md"] = _note(
            [*_BASE, "note_type: note", "pillar: Knowledge"]
        )
        report = gate.audit("Projects", resolve=True)
        entry = report["notes"][0]
        assert entry["corrected"] is True
        assert "Projects/m.md" not in vault.store  # moved out of the wrong dir
        assert (
            "Knowledge/Notes/m.md" in vault.store
        )  # into the schema-resolved dir
        assert any("moved" in c for c in entry["corrections"])

    def test_all_dirs_scope(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/a.md"] = _note([*_BASE, "id: keep"])
        report = gate.audit(all_dirs=True, resolve=False)
        assert report["scope"] == "<all>"
        assert report["drifted"] >= 1
