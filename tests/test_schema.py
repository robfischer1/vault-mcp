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

from vault_mcp.schema import (  # noqa: E402
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
        monkeypatch.setenv("VAULT_MCP_SCHEMA", str(FIXTURES / "does-not-exist.yml"))
        # explicit valid path must win over the (broken) env var
        schema = load_schema(VALID)
        assert "Knowledge" in schema.pillars

    def test_missing_env_names_the_variable(self, monkeypatch: pytest.MonkeyPatch):
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
        assert schema.resolve_directory(note_type="note", pillar="Knowledge") == "Knowledge/Notes"

    def test_resolve_by_pillar_only(self):
        schema = load_schema(VALID)
        assert schema.resolve_directory(pillar="Records") == "Records"

    def test_no_match_raises(self):
        schema = load_schema(VALID)
        with pytest.raises(RouteError):
            schema.resolve_directory(note_type="unknown", pillar="Knowledge")
