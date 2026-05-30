"""Unit tests for vault_mcp.gate — the Convention Gate write API.

Covers Convention Gate (#53): note creation (frontmatter generation, routing,
write, success contract), write validation & rejection, and write-protection
enforcement. The Obsidian writer is mocked (Constitution III: never a live
instance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gate import (  # noqa: E402
    ConventionGate,
    FieldError,
    ProtectionError,
    TagError,
)
from vault_mcp.provenance import Actor, Provenance, WriteMode  # noqa: E402
from vault_mcp.schema import load_schema  # noqa: E402

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"


class FakeVault:
    """In-memory vault IO; captures create calls instead of touching Obsidian."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.store: dict[str, str] = {}

    def create_note(self, path: str, content: str) -> None:
        self.calls.append((path, content))
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content


def _gate(diff_sink=None) -> tuple[ConventionGate, FakeVault]:
    vault = FakeVault()
    return ConventionGate(load_schema(VALID), vault, diff_sink=diff_sink), vault


class TestCreateNote:
    def test_generates_required_frontmatter(self):
        gate, writer = _gate()
        result = gate.create_note(
            title="My Idea",
            note_type="note",
            pillar="Knowledge",
            body="An idea.",
            tags=["topic/ai"],
            created="2026-05-30",
        )
        for required in ("title", "created", "provenance"):
            assert required in result.frontmatter
        assert result.frontmatter["title"] == "My Idea"
        assert len(writer.calls) == 1

    def test_routes_to_resolved_directory(self):
        gate, writer = _gate()
        result = gate.create_note(
            title="My Idea", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path == "Knowledge/Notes/My Idea.md"
        assert writer.calls[0][0] == "Knowledge/Notes/My Idea.md"

    def test_success_contract_returns_path_fm_provenance(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="My Idea", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path
        assert isinstance(result.frontmatter, dict)
        assert result.provenance == Provenance.AI_ASSISTED

    def test_agent_create_stamps_ai_assisted(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.AGENT,
            created="2026-05-30",
        )
        assert result.frontmatter["provenance"] == "ai-assisted"

    def test_human_create_stamps_human(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.HUMAN,
            created="2026-05-30",
        )
        assert result.frontmatter["provenance"] == "human"


class TestValidation:
    def test_reject_unknown_tag(self):
        gate, writer = _gate()
        with pytest.raises(TagError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                tags=["topic/nope"],
                created="2026-05-30",
            )
        assert "topic/nope" in str(exc.value)
        assert len(writer.calls) == 0  # no file written

    def test_unknown_tag_suggests_nearest(self):
        gate, _ = _gate()
        with pytest.raises(TagError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                tags=["status/activ"],
                created="2026-05-30",
            )
        assert "status/active" in str(exc.value)

    def test_reject_missing_required_title(self):
        gate, writer = _gate()
        with pytest.raises(FieldError):
            gate.create_note(title="", note_type="note", pillar="Knowledge", created="2026-05-30")
        assert len(writer.calls) == 0


class TestProtection:
    def test_create_into_body_immutable_rejected(self):
        gate, writer = _gate()
        with pytest.raises(ProtectionError) as exc:
            gate.create_note(title="R", pillar="Records", created="2026-05-30")
        assert "Records are imported primary-source data" in str(exc.value)
        assert len(writer.calls) == 0

    def test_fully_immutable_rejects_all(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Artifacts", Actor.AGENT, WriteMode.CREATE)

    def test_compute_only_rejects_session_allows_compute(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Atlas", Actor.AGENT, WriteMode.CREATE)
        # compute mode is permitted
        gate.check_protection("Atlas", Actor.AGENT, WriteMode.COMPUTE)

    def test_voice_only_rejects_agent_allows_human(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Outputs/Tao", Actor.AGENT, WriteMode.CREATE)
        gate.check_protection("Outputs/Tao", Actor.HUMAN, WriteMode.CREATE)

    def test_unprotected_directory_passes(self):
        gate, _ = _gate()
        gate.check_protection("Knowledge/Notes", Actor.AGENT, WriteMode.CREATE)


class TestUpdateNote:
    def _seed(self, gate, vault) -> str:
        result = gate.create_note(
            title="Idea",
            note_type="note",
            pillar="Knowledge",
            body="Original body.",
            tags=["topic/ai"],
            created="2026-05-30",
        )
        return result.path

    def test_changes_only_requested_fields(self):
        gate, vault = _gate()
        path = self._seed(gate, vault)
        result = gate.update_note(path, fields={"status": "reviewed"})
        assert result.frontmatter["status"] == "reviewed"
        assert result.frontmatter["title"] == "Idea"  # untouched field preserved
        assert "Original body." in vault.store[path]  # body preserved

    def test_metadata_update_on_records_allowed(self):
        gate, vault = _gate()
        vault.store["Records/r.md"] = "---\ntitle: r\nprovenance: human\n---\n\nbody\n"
        # metadata-only (no body) update is permitted on a body-immutable dir
        gate.update_note("Records/r.md", fields={"status": "filed"})
        assert "status: filed" in vault.store["Records/r.md"]

    def test_body_update_on_records_rejected(self):
        gate, vault = _gate()
        vault.store["Records/r.md"] = "---\ntitle: r\nprovenance: human\n---\n\nbody\n"
        with pytest.raises(ProtectionError):
            gate.update_note("Records/r.md", body="rewritten")

    def test_human_edit_transitions_ai_assisted_to_human_edited(self):
        gate, vault = _gate()
        path = self._seed(gate, vault)  # created by agent -> ai-assisted
        result = gate.update_note(path, fields={"status": "reviewed"}, actor=Actor.HUMAN)
        assert result.provenance is Provenance.HUMAN_EDITED


class TestObservability:
    def test_diff_emitted_on_create(self):
        records: list[dict] = []
        gate, _ = _gate(diff_sink=records.append)
        gate.create_note(title="X", note_type="note", pillar="Knowledge", created="2026-05-30")
        assert len(records) == 1
        assert records[0]["op"] == "create"
        assert records[0]["path"] == "Knowledge/Notes/X.md"
        assert records[0]["provenance"] == "ai-assisted"

    def test_emission_failure_does_not_block_write(self):
        def boom(_record: dict) -> None:
            raise RuntimeError("sink down")

        gate, vault = _gate(diff_sink=boom)
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        # write still succeeded despite the sink raising
        assert len(vault.calls) == 1
        assert result.path == "Knowledge/Notes/X.md"
