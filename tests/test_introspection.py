"""Unit tests for the Schema Introspection epic — list_* + query (describe_type).

The schema-as-a-service read surface: a session can enumerate types, tags, and
property keys, and ask a single type for its authoring contract, without reading
governance prose.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.schema import load_schema

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"


def _schema():
    return load_schema(VALID)


class TestListTypes:
    def test_lists_every_type_with_write_mode(self):
        types = {t["name"]: t for t in _schema().list_types()}
        assert "Gadget" in types and "Dossier" in types and "signal" in types
        assert types["Gadget"]["write_mode"] == "agent"
        assert types["Dossier"]["write_mode"] == "materialize-only"
        assert types["signal"]["write_mode"] == "pure-DB"
        assert types["Gadget"]["required"] == ["title", "serial"]
        assert types["Stub"]["body_empty"] is True
        assert types["atom"]["atom_slug"] is True


class TestListTags:
    def test_groups_by_prefix(self):
        tags = _schema().list_tags()
        assert tags["status"] == ["status/active", "status/done"]
        assert tags["topic"] == ["topic/ai"]
        assert set(tags["_root"]) == {"starred", "todo"}  # prefix-less tags


class TestListKeys:
    def test_unions_type_fields_and_frontmatter_and_status(self):
        keys = set(_schema().list_keys())
        assert {"title", "created", "author_type", "author_level"} <= keys  # required_frontmatter
        assert {"serial", "vendor", "condition"} <= keys  # Gadget fields + constraint key
        assert "coords" in keys  # Spot format key
        assert "status" in keys  # vocabulary exists
        assert keys == set(keys)  # returned sorted


class TestDescribeType:
    def test_unknown_type_returns_none(self):
        assert _schema().describe_type("Nonexistent") is None

    def test_caller_settable_excludes_managed_fields(self):
        spec = _schema().describe_type("Gadget")
        assert spec is not None
        # required 'title' is the managed label field -> excluded from caller_settable
        assert "title" not in spec["caller_settable"]
        assert "serial" in spec["caller_settable"]
        assert "vendor" in spec["caller_settable"]

    def test_constraints_and_formats_surface(self):
        gadget = _schema().describe_type("Gadget")
        assert gadget is not None
        assert gadget["constraints"]["condition"] == ["New", "Used", "Broken"]
        spot = _schema().describe_type("Spot")
        assert spot is not None
        assert spot["formats"]["coords"] == "geo"

    def test_routing_includes_directory(self):
        spec = _schema().describe_type("Note")
        assert spec is not None
        dirs = [r["directory"] for r in spec["routing"]]
        assert "Knowledge/Notes" in dirs

    def test_body_guidance_returned(self):
        spec = _schema().describe_type("Note")
        assert spec is not None
        assert "wikilinks" in (spec["body_guidance"] or "")

    def test_status_values_surface(self):
        spec = _schema().describe_type("Note")
        assert spec is not None
        assert "Active" in spec["status_values"]
