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

from vault_mcp.provenance import (
    SPECTRUM,
    Actor,
    AuthorType,
    Provenance,
    ProvenanceError,
    WriteMode,
    author_type_for,
    parse,
    parse_author_type,
    stamp,
    transition,
    transition_author_type,
    validate_schema_levels,
)


class TestAuthorType:
    def test_categories(self):
        assert {a.value for a in AuthorType} == {"human", "ai", "external"}

    def test_derive_human_from_pristine(self):
        assert author_type_for(Provenance.HUMAN) is AuthorType.HUMAN

    def test_derive_ai_from_any_ai_touched_level(self):
        assert author_type_for(Provenance.AI_ASSISTED) is AuthorType.AI
        assert author_type_for(Provenance.HUMAN_EDITED) is AuthorType.AI

    def test_declared_external_wins(self):
        assert (
            author_type_for(Provenance.HUMAN, AuthorType.EXTERNAL)
            is AuthorType.EXTERNAL
        )

    def test_parse_author_type(self):
        assert parse_author_type("external") is AuthorType.EXTERNAL

    def test_parse_rejects_unknown(self):
        with pytest.raises(ProvenanceError):
            parse_author_type("robot")

    def test_no_downgrade_agent_promotes_human(self):
        assert (
            transition_author_type(AuthorType.HUMAN, Actor.AGENT)
            is AuthorType.AI
        )

    def test_no_downgrade_ai_stays_ai(self):
        assert (
            transition_author_type(AuthorType.AI, Actor.HUMAN) is AuthorType.AI
        )

    def test_external_is_sticky(self):
        assert (
            transition_author_type(AuthorType.EXTERNAL, Actor.AGENT)
            is AuthorType.EXTERNAL
        )

    def test_human_human_stays_human(self):
        assert (
            transition_author_type(AuthorType.HUMAN, Actor.HUMAN)
            is AuthorType.HUMAN
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
        assert (
            transition(Provenance.AI_ASSISTED, Actor.HUMAN)
            is Provenance.HUMAN_EDITED
        )

    def test_agent_edit_never_downgrades_ai_computed_to_human(self):
        result = transition(Provenance.AI_COMPUTED, Actor.AGENT)
        assert result is Provenance.AI_COMPUTED

    def test_agent_edit_of_human_content_becomes_ai_assisted(self):
        assert (
            transition(Provenance.HUMAN, Actor.AGENT) is Provenance.AI_ASSISTED
        )

    def test_human_edit_of_human_content_stays_human(self):
        assert transition(Provenance.HUMAN, Actor.HUMAN) is Provenance.HUMAN


class TestSchemaLink:
    def test_canonical_levels_match_valid_fixture(self):
        # The schema fixture declares the same 7 levels as the canonical spectrum.
        from vault_mcp.schema import load_schema

        schema = load_schema(
            ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"
        )
        validate_schema_levels(schema.provenance_levels)  # must not raise

    def test_mismatched_levels_rejected(self):
        with pytest.raises(ProvenanceError):
            validate_schema_levels(("human", "robot"))
