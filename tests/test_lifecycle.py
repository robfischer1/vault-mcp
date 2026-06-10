"""Unit tests for vault_mcp.lifecycle — the materialize verb.

Covers Lifecycle Verbs / Materialize (#135): payload contract validation,
the compute-mode write path that bypasses materialize-only write-mode
rejection (#136), and deterministic byte-identical template rendering (#137).
The Obsidian writer is faked (Constitution III: never a live instance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gate import ConventionGate, WriteModeError
from vault_mcp.lifecycle import (
    MaterializePayloadError,
    Materializer,
)
from vault_mcp.provenance import Actor, Provenance, WriteMode
from vault_mcp.schema import load_schema

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"


class FakeVault:
    """In-memory vault IO; captures writes instead of touching Obsidian."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def create_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content


def _materializer(
    templates: dict[str, str] | None = None,
) -> tuple[Materializer, ConventionGate, FakeVault]:
    vault = FakeVault()
    gate = ConventionGate(load_schema(VALID), vault)
    return Materializer(gate, templates), gate, vault


def _payload(**over: object) -> dict[str, object]:
    # Dossier is the fixture's materialize-only @type; it has no schema route,
    # so the caller supplies the directory explicitly.
    base: dict[str, object] = {
        "title": "Acme Dossier",
        "note_type": "Dossier",
        "directory": "Knowledge/Dossiers",
        "body": "A materialized dossier body.",
    }
    base.update(over)
    return base


class TestPayloadContract:
    def test_valid_payload_writes(self):
        mat, _, vault = _materializer()
        result = mat.materialize(_payload(), created="2026-05-30")
        assert result.path == "Knowledge/Dossiers/Acme Dossier.md"
        assert "Knowledge/Dossiers/Acme Dossier.md" in vault.store

    def test_missing_required_key_rejected(self):
        mat, _, _ = _materializer()
        bad = _payload()
        del bad["note_type"]
        with pytest.raises(MaterializePayloadError) as exc:
            mat.materialize(bad)
        assert "note_type" in str(exc.value)

    def test_non_string_required_field_rejected(self):
        mat, _, _ = _materializer()
        with pytest.raises(MaterializePayloadError):
            mat.materialize(_payload(title=123))

    def test_non_mapping_payload_rejected(self):
        mat, _, _ = _materializer()
        with pytest.raises(MaterializePayloadError):
            mat.materialize(["not", "a", "mapping"])


class TestWriteModeBypass:
    def test_materialize_writes_materialize_only_type(self):
        # #136: materialize is the sanctioned path for a materialize-only @type.
        mat, _, vault = _materializer()
        result = mat.materialize(_payload(), created="2026-05-30")
        assert result.frontmatter["note_type"] == "Dossier"
        assert "Knowledge/Dossiers/Acme Dossier.md" in vault.store

    def test_agent_create_of_materialize_only_rejected(self):
        # The bypass is real: the ordinary agent-create path is still refused.
        _, gate, _ = _materializer()
        with pytest.raises(WriteModeError):
            gate.create_note(
                title="Acme Dossier",
                note_type="Dossier",
                directory="Knowledge/Dossiers",
                mode=WriteMode.CREATE,
                actor=Actor.AGENT,
                created="2026-05-30",
            )

    def test_stamped_ai_computed(self):
        mat, _, _ = _materializer()
        result = mat.materialize(_payload(), created="2026-05-30")
        assert result.provenance is Provenance.AI_COMPUTED
        assert result.frontmatter["author_level"] == "ai-computed"
        assert result.frontmatter["author_type"] == "ai"


class TestRendering:
    def test_byte_identical_for_same_payload(self):
        # #137: same payload + same created -> byte-identical note.
        mat1, _, v1 = _materializer()
        mat2, _, v2 = _materializer()
        mat1.materialize(_payload(), created="2026-05-30")
        mat2.materialize(_payload(), created="2026-05-30")
        path = "Knowledge/Dossiers/Acme Dossier.md"
        assert v1.store[path] == v2.store[path]

    def test_default_stub_is_passthrough_body(self):
        mat, _, vault = _materializer()
        mat.materialize(_payload(body="Verbatim body line."), created="2026-05-30")
        content = vault.store["Knowledge/Dossiers/Acme Dossier.md"]
        assert "Verbatim body line." in content

    def test_custom_template_overrides_stub(self):
        mat, _, vault = _materializer({"note.stub": "# $title\n\n$body"})
        mat.materialize(_payload(), created="2026-05-30")
        content = vault.store["Knowledge/Dossiers/Acme Dossier.md"]
        assert "# Acme Dossier" in content
        assert "A materialized dossier body." in content

    def test_unknown_template_rejected(self):
        mat, _, _ = _materializer()
        with pytest.raises(MaterializePayloadError) as exc:
            mat.materialize(_payload(template="nope"))
        assert "nope" in str(exc.value)

    def test_frontmatter_passed_through(self):
        mat, _, _ = _materializer()
        result = mat.materialize(
            _payload(frontmatter={"summary": "one-line dossier summary"}),
            created="2026-05-30",
        )
        assert result.frontmatter["summary"] == "one-line dossier summary"
