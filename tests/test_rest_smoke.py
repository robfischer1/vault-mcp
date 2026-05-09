"""Smoke tests for REST tools against a running Obsidian instance.

Auto-skips when the REST API is unreachable (Cowork sandbox, no Obsidian).
Run from Code session with Obsidian open:
    VAULT_MCP_PATH=... python -m pytest tests/test_rest_smoke.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.rest_client import ObsidianRESTClient

KEY_PATH = os.environ.get(
    "VAULT_MCP_REST_KEY_PATH",
    str(Path.home() / "Obsidian" / ".local" / "rest-api-key.txt"),
)
REST_URL = os.environ.get("VAULT_MCP_REST_URL", "http://127.0.0.1:27123")


def _make_client() -> ObsidianRESTClient:
    return ObsidianRESTClient(base_url=REST_URL, key_path=KEY_PATH)


@pytest.fixture(scope="module")
def client() -> ObsidianRESTClient:
    c = _make_client()
    health = c.probe()
    if not health["reachable"]:
        pytest.skip("Obsidian REST API not reachable — skipping smoke tests")
    return c


class TestHealthSmoke:
    def test_reachable(self, client):
        result = client.probe()
        assert result["reachable"] is True
        assert result["version"] is not None

    def test_version_string(self, client):
        result = client.probe()
        assert "." in result["version"]


class TestActiveNote:
    def test_returns_path(self, client):
        result = client.get("/active/", accept="application/vnd.olrapi.note+json")
        assert result["ok"] is True
        assert "path" in result["data"]

    def test_has_frontmatter(self, client):
        result = client.get("/active/", accept="application/vnd.olrapi.note+json")
        assert "frontmatter" in result["data"]


class TestPeriodicNote:
    def test_daily_today(self, client):
        result = client.get(
            "/periodic/daily/",
            accept="application/vnd.olrapi.note+json",
            extra_headers={"Target-Type": "note"},
        )
        assert result["ok"] is True
        assert "path" in result["data"]


class TestVaultRead:
    def test_read_known_file(self, client):
        result = client.get(
            "/vault/CLAUDE.md",
            accept="application/vnd.olrapi.note+json",
        )
        assert result["ok"] is True
        assert "content" in result["data"]

    def test_read_missing_file(self, client):
        result = client.get("/vault/DOES_NOT_EXIST_12345.md")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"


class TestSearch:
    def test_simple_search(self, client):
        result = client.post("/search/simple/", params={"query": "CLAUDE"})
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_empty_query(self, client):
        result = client.post("/search/simple/", params={"query": ""})
        # Plugin may return 400 or empty — either is acceptable
        assert "ok" in result


class TestCommands:
    def test_list_commands(self, client):
        result = client.get("/commands/")
        assert result["ok"] is True
        assert "commands" in result["data"]

    def test_execute_focus(self, client):
        result = client.post("/commands/editor:focus/")
        assert result["ok"] is True


# -------------------------------------------------------------------
# Phase 7 — Advanced query tools
# -------------------------------------------------------------------


class TestDataviewDQL:
    def test_table_query(self, client):
        result = client.post(
            "/search/",
            content='TABLE file.name FROM "System/Governance" LIMIT 3',
            content_type="application/vnd.olrapi.dataview.dql+txt",
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_query_rejected(self, client):
        result = client.post(
            "/search/",
            content='LIST FROM "System/Governance" LIMIT 3',
            content_type="application/vnd.olrapi.dataview.dql+txt",
        )
        assert result["ok"] is False
        assert result["error"] == "rest_invalid_request"

    def test_invalid_dql(self, client):
        result = client.post(
            "/search/",
            content="NOT A VALID QUERY %%%",
            content_type="application/vnd.olrapi.dataview.dql+txt",
        )
        assert result["ok"] is False


class TestJsonLogic:
    def test_glob_query(self, client):
        result = client.post(
            "/search/",
            json_body={"glob": ["System/Governance/*", {"var": "path"}]},
            content_type="application/vnd.olrapi.jsonlogic+json",
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_frontmatter_equality(self, client):
        result = client.post(
            "/search/",
            json_body={"==": [{"var": "frontmatter.note_type"}, "folder-note"]},
            content_type="application/vnd.olrapi.jsonlogic+json",
        )
        assert result["ok"] is True
        assert isinstance(result["data"], list)
        assert len(result["data"]) > 0


class TestTags:
    def test_returns_tags(self, client):
        result = client.get("/tags/")
        assert result["ok"] is True
        data = result["data"]
        assert "tags" in data
        assert isinstance(data["tags"], list)
        assert len(data["tags"]) > 0

    def test_tag_has_name_and_count(self, client):
        result = client.get("/tags/")
        tag = result["data"]["tags"][0]
        assert "name" in tag
        assert "count" in tag


class TestListDirectory:
    def test_vault_root(self, client):
        result = client.get("/vault/")
        assert result["ok"] is True
        assert "files" in result["data"]
        files = result["data"]["files"]
        assert len(files) > 0
        assert any(f.endswith("/") for f in files)

    def test_subdirectory(self, client):
        result = client.get("/vault/System/Governance/")
        assert result["ok"] is True
        files = result["data"]["files"]
        assert any("AGENTS" in f for f in files)

    def test_missing_directory(self, client):
        result = client.get("/vault/NONEXISTENT_DIR_12345/")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"


class TestOpenInObsidian:
    def test_open_existing_file(self, client):
        result = client.post("/open/CLAUDE.md")
        assert result["ok"] is True

    def test_open_returns_empty_body(self, client):
        result = client.post("/open/CLAUDE.md")
        assert result["ok"] is True
        assert result["data"] is None


class TestDocumentMap:
    def test_active_note_map(self, client):
        result = client.get(
            "/active/",
            accept="application/vnd.olrapi.document-map+json",
        )
        assert result["ok"] is True
        data = result["data"]
        assert "headings" in data
        assert "blocks" in data
        assert "frontmatterFields" in data

    def test_specific_file_map(self, client):
        result = client.get(
            "/vault/CLAUDE.md",
            accept="application/vnd.olrapi.document-map+json",
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data["headings"], list)
        assert len(data["headings"]) > 0
