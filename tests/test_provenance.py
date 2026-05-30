"""Unit tests for vault_mcp.provenance — the single-axis provenance spectrum.

Covers Provenance System (#68): closed-enum taxonomy, context-derived
stamping, and the no-silent-downgrade transition rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.provenance import (  # noqa: E402
    SPECTRUM,
    Actor,
    Provenance,
    ProvenanceError,
    WriteMode,
    parse,
    stamp,
    transition,
    validate_schema_levels,
)


class TestTaxonomy:
    def test_spectrum_has_seven_ordered_levels(self):
        assert len(SPECTRUM) == 7
        assert SPECTRUM[0] is Provenance.HUMAN
        assert SPECTRUM[-1] is Provenance.AI_COMPUTED

    def test_parse_valid_level(self):
        assert parse("ai-assisted") is Provenance.AI_ASSISTED

    def test_parse_rejects_unknown_value(self):
        with pytest.raises(ProvenanceError) as exc:
            parse("ai-magic")
        assert "ai-magic" in str(exc.value)


class TestStamp:
    def test_compute_mode_is_ai_computed(self):
        assert stamp(Actor.AGENT, WriteMode.COMPUTE) is Provenance.AI_COMPUTED

    def test_human_create_is_human(self):
        assert stamp(Actor.HUMAN, WriteMode.CREATE) is Provenance.HUMAN

    def test_agent_metadata_only_is_ai_metadata(self):
        assert stamp(Actor.AGENT, WriteMode.METADATA) is Provenance.AI_METADATA

    def test_agent_create_is_ai_assisted(self):
        assert stamp(Actor.AGENT, WriteMode.CREATE) is Provenance.AI_ASSISTED


class TestTransition:
    def test_human_edit_of_ai_assisted_becomes_human_edited(self):
        assert transition(Provenance.AI_ASSISTED, Actor.HUMAN) is Provenance.HUMAN_EDITED

    def test_agent_edit_never_downgrades_ai_computed_to_human(self):
        result = transition(Provenance.AI_COMPUTED, Actor.AGENT)
        assert result is Provenance.AI_COMPUTED

    def test_agent_edit_of_human_content_becomes_ai_assisted(self):
        assert transition(Provenance.HUMAN, Actor.AGENT) is Provenance.AI_ASSISTED

    def test_human_edit_of_human_content_stays_human(self):
        assert transition(Provenance.HUMAN, Actor.HUMAN) is Provenance.HUMAN


class TestSchemaLink:
    def test_canonical_levels_match_valid_fixture(self):
        # The schema fixture declares the same 7 levels as the canonical spectrum.
        from vault_mcp.schema import load_schema

        schema = load_schema(ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml")
        validate_schema_levels(schema.provenance_levels)  # must not raise

    def test_mismatched_levels_rejected(self):
        with pytest.raises(ProvenanceError):
            validate_schema_levels(("human", "robot"))
