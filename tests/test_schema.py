"""Unit tests for vault_mcp.schema against single-file schema fixtures.

Covers Schema Engine (#45): config loading + validation, tag-glossary
lookup, and pillar routing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.schema import (
    RouteError,
    SchemaConfigError,
    SchemaValidationError,
    load_schema,
)

FIXTURES = ROOT / "tests" / "fixtures" / "schema"
VALID = FIXTURES / "valid.schema.yml"


class TestLoad:
    def test_load_from_explicit_path(self):
        schema = load_schema(VALID)
        assert "Knowledge" in schema.pillars
        assert "status/active" in schema.tags
        assert len(schema.routes) == 3
        assert "title" in schema.required_frontmatter

    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VAULT_MCP_SCHEMA", str(VALID))
        schema = load_schema()
        assert "Projects" in schema.pillars

    def test_explicit_path_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "VAULT_MCP_SCHEMA", str(FIXTURES / "does-not-exist.yml")
        )
        # explicit valid path must win over the (broken) env var
        schema = load_schema(VALID)
        assert "Knowledge" in schema.pillars

    def test_missing_env_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("VAULT_MCP_SCHEMA", raising=False)
        with pytest.raises(SchemaConfigError) as exc:
            load_schema()
        assert "VAULT_MCP_SCHEMA" in str(exc.value)

    def test_missing_file_names_the_path(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VAULT_MCP_SCHEMA", raising=False)
        missing = FIXTURES / "nope.schema.yml"
        with pytest.raises(SchemaConfigError) as exc:
            load_schema(missing)
        assert "nope.schema.yml" in str(exc.value)


class TestValidate:
    def test_unknown_pillar_rejected(self):
        with pytest.raises(SchemaValidationError) as exc:
            load_schema(FIXTURES / "unknown_pillar.schema.yml")
        assert "Nonexistent" in str(exc.value)

    def test_duplicate_tag_rejected(self):
        with pytest.raises(SchemaValidationError) as exc:
            load_schema(FIXTURES / "duplicate_tags.schema.yml")
        assert "status/active" in str(exc.value)


class TestTagGlossary:
    def test_valid_tag(self):
        schema = load_schema(VALID)
        assert schema.is_valid_tag("topic/ai") is True

    def test_invalid_tag(self):
        schema = load_schema(VALID)
        assert schema.is_valid_tag("topic/unknown") is False

    def test_nearest_tags_suggests_close_matches(self):
        schema = load_schema(VALID)
        near = schema.nearest_tags("status/activ")
        assert "status/active" in near


class TestPillarRouting:
    def test_resolve_by_type_and_pillar(self):
        schema = load_schema(VALID)
        assert (
            schema.resolve_directory(note_type="note", pillar="Knowledge")
            == "Knowledge/Notes"
        )

    def test_resolve_by_pillar_only(self):
        schema = load_schema(VALID)
        assert schema.resolve_directory(pillar="Records") == "Records"

    def test_no_match_raises(self):
        schema = load_schema(VALID)
        with pytest.raises(RouteError):
            schema.resolve_directory(note_type="unknown", pillar="Knowledge")


class TestDiscriminatorRouting:
    ROUTING = FIXTURES / "routing.schema.yml"

    def test_discriminator_match_wins_over_bare(self):
        schema = load_schema(self.ROUTING)
        got = schema.resolve_directory(
            note_type="Widget", pillar="Shop", attrs={"category": "premium"}
        )
        assert got == "Shop/Premium"

    def test_second_discriminator_value_set(self):
        schema = load_schema(self.ROUTING)
        got = schema.resolve_directory(
            note_type="Widget", pillar="Shop", attrs={"category": "basic"}
        )
        assert got == "Shop/Basic"

    def test_falls_back_to_bare_when_no_discriminator_value(self):
        schema = load_schema(self.ROUTING)
        got = schema.resolve_directory(
            note_type="Widget", pillar="Shop", attrs={}
        )
        assert got == "Shop"

    def test_unmatched_discriminator_value_falls_back_to_bare(self):
        schema = load_schema(self.ROUTING)
        got = schema.resolve_directory(
            note_type="Widget", pillar="Shop", attrs={"category": "mystery"}
        )
        assert got == "Shop"

    def test_explicit_subtype_routes(self):
        schema = load_schema(self.ROUTING)
        assert (
            schema.resolve_directory("Tome", "Library") == "Library/Index/Tome"
        )
        assert (
            schema.resolve_directory("Scroll", "Library")
            == "Library/Index/Scroll"
        )

    def test_equally_specific_overlap_is_ambiguous(self):
        schema = load_schema(self.ROUTING)
        with pytest.raises(RouteError) as exc:
            schema.resolve_directory(
                note_type="Relic",
                pillar="Library",
                attrs={"era": "ancient", "dynasty": "han"},
            )
        assert "ambiguous" in str(exc.value)

    def test_same_directory_from_multiple_routes_not_ambiguous(self):
        schema = load_schema(self.ROUTING)
        got = schema.resolve_directory(note_type="Artifact", pillar="Shop")
        assert got == "Shop/Display"


class TestTypeConfig:
    def test_type_config_parsed(self):
        schema = load_schema(VALID)
        tc = schema.type_config("Note")
        assert tc is not None
        assert tc.required_fields == ("title",)
        assert "description" in tc.freeform_fields

    def test_unknown_type_returns_none(self):
        schema = load_schema(VALID)
        assert schema.type_config("Nonexistent") is None

    def test_value_constraints_lookup(self):
        schema = load_schema(VALID)
        tc = schema.type_config("Gadget")
        assert tc is not None
        assert tc.allowed_values("condition") == ("New", "Used", "Broken")
        assert tc.allowed_values("vendor") is None

    def test_write_mode_default_is_agent(self):
        schema = load_schema(VALID)
        tc = schema.type_config("Note")
        assert tc is not None
        assert tc.write_mode == "agent"

    def test_write_mode_variants(self):
        schema = load_schema(VALID)
        dossier = schema.type_config("Dossier")
        signal = schema.type_config("signal")
        assert dossier is not None and dossier.write_mode == "materialize-only"
        assert signal is not None and signal.write_mode == "pure-DB"

    def test_invalid_write_mode_rejected(self):
        with pytest.raises(SchemaValidationError) as exc:
            load_schema(FIXTURES / "bad_write_mode.schema.yml")
        assert "telepathy" in str(exc.value)

    def test_format_lookup(self):
        schema = load_schema(VALID)
        tc = schema.type_config("Spot")
        assert tc is not None
        assert tc.format_for("coords") == "geo"
        assert tc.format_for("title") is None


class TestPillarDefaults:
    def test_pillar_default_resolved(self):
        schema = load_schema(VALID)
        pd = schema.pillar_default("Knowledge")
        assert pd is not None
        assert pd.nn_color == "#8caaee"
        assert pd.nn_icon == "book"

    def test_unknown_pillar_returns_none(self):
        schema = load_schema(VALID)
        assert schema.pillar_default("Nowhere") is None

    def test_none_pillar_returns_none(self):
        schema = load_schema(VALID)
        assert schema.pillar_default(None) is None


class TestStatusVocabulary:
    def test_blank_repaired_to_default(self):
        schema = load_schema(VALID)
        assert schema.normalize_status("") == "Pending"
        assert schema.normalize_status(None) == "Pending"

    def test_explicit_repair(self):
        schema = load_schema(VALID)
        assert schema.normalize_status("Archive") == "Archived"
        assert schema.normalize_status("done") == "Completed"

    def test_singleton_list_unwrapped(self):
        schema = load_schema(VALID)
        assert schema.normalize_status(["Active"]) == "Active"

    def test_valid_and_invalid(self):
        schema = load_schema(VALID)
        assert schema.is_valid_status("Active") is True
        assert schema.is_valid_status("Bogus") is False


class TestValueFormats:
    def test_geo_valid(self):
        schema = load_schema(VALID)
        assert schema.is_valid_format("geo", "40.7128,-74.0060") is True
        assert schema.is_valid_format("geo", "0, 0") is True

    def test_geo_invalid(self):
        schema = load_schema(VALID)
        assert schema.is_valid_format("geo", "not coords") is False
        assert schema.is_valid_format("geo", "40.7") is False

    def test_unknown_format_passes(self):
        schema = load_schema(VALID)
        assert schema.is_valid_format("isbn", "anything") is True
