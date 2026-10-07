# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for vault_mcp.rest_client with mocked httpx.Client."""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
import pytest

from vault_mcp.gate import ObsidianIOError
from vault_mcp.rest_client import ObsidianRESTClient, RestNoteIO


def _mock_response(
    status: int = 200, json_data=None, text: str = "", headers=None
):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text
    resp.headers = headers or {"content-type": "application/json"}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


class TestProbe:
    def test_probe_success(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._client = MagicMock()
        client._client.get.return_value = _mock_response(
            200, {"status": "OK", "versions": {"self": "3.6.2"}}
        )
        result = client.probe()
        assert result["reachable"] is True
        assert result["version"] == "3.6.2"
        assert result["last_error"] is None

    def test_probe_unreachable(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._client = MagicMock()
        client._client.get.side_effect = httpx.ConnectError("refused")
        result = client.probe()
        assert result["reachable"] is False
        assert "refused" in result["last_error"]

    def test_probe_no_key(self, tmp_path):
        bad_path = tmp_path / "nonexistent.txt"
        client = ObsidianRESTClient(key_path=bad_path)
        result = client.probe()
        assert result["reachable"] is False
        assert "key unreadable" in result["last_error"]


class TestRequest:
    def test_get_success(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            200, {"path": "foo.md", "content": "bar"}
        )
        result = client.get("/vault/foo.md")
        assert result["ok"] is True
        assert result["data"]["path"] == "foo.md"

    def test_get_404(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            404, text="Not Found"
        )
        result = client.get("/vault/missing.md")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"

    def test_get_401(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "bad-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            401, text="Unauthorized"
        )
        result = client.get("/")
        assert result["ok"] is False
        assert result["error"] == "rest_unauthorized"

    def test_get_500(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            500, text="Internal Error"
        )
        result = client.get("/")
        assert result["ok"] is False
        assert result["error"] == "rest_obsidian_error"

    def test_no_key_returns_error(self):
        client = ObsidianRESTClient(key_path=None)
        result = client.get("/anything")
        assert result["ok"] is False
        assert result["error"] == "rest_no_key"

    def test_post_204(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(204)
        result = client.post("/commands/editor:focus/")
        assert result["ok"] is True
        assert result["data"] is None

    def test_connection_error_marks_unreachable(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.side_effect = httpx.ConnectError("refused")
        result = client.get("/")
        assert result["ok"] is False
        assert result["error"] == "rest_unreachable"
        assert client._reachable is False


class TestBackoff:
    def test_backoff_increases(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._client = MagicMock()
        client._client.get.side_effect = httpx.ConnectError("refused")

        client.probe()
        assert client._probe_ttl == 30

        client.probe()
        assert client._probe_ttl == 300

        client.probe()
        assert client._probe_ttl == 1800

    def test_backoff_resets_on_success(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._client = MagicMock()

        client._client.get.side_effect = httpx.ConnectError("refused")
        client.probe()
        client.probe()
        assert client._fail_count == 2

        client._client.get.side_effect = None
        client._client.get.return_value = _mock_response(
            200, {"versions": {"self": "3.6.2"}}
        )
        client.probe()
        assert client._fail_count == 0
        assert client._probe_ttl == 30.0


class TestContentTypes:
    """Phase 7: raw content body + content-type support."""

    def test_post_with_jsonlogic_content_type(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            200, [{"filename": "bar.md", "result": True}]
        )
        result = client.post(
            "/search/",
            json_body={"glob": ["*.md", {"var": "path"}]},
            content_type="application/vnd.olrapi.jsonlogic+json",
        )
        assert result["ok"] is True
        assert result["data"][0]["filename"] == "bar.md"

    def test_empty_body_returns_none(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        resp = _mock_response(200)
        resp.content = b""
        client._client.request.return_value = resp
        result = client.post("/open/foo.md")
        assert result["ok"] is True
        assert result["data"] is None

    def test_document_map_accept_header(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(
            200,
            {"headings": ["H1"], "blocks": [], "frontmatterFields": ["title"]},
        )
        result = client.get(
            "/active/",
            accept="application/vnd.olrapi.document-map+json",
        )
        assert result["ok"] is True
        assert result["data"]["headings"] == ["H1"]
        call_kwargs = client._client.request.call_args
        assert (
            call_kwargs[1]["headers"]["Accept"]
            == "application/vnd.olrapi.document-map+json"
        )


class TestPatchTargetEncoding:
    """PATCH header targeting: explicit v2 version + type-dependent Target encoding.

    The Local REST API (5.x) answers 40084 to ``Target-Type``/``Target`` headers
    without ``Markdown-Patch-Version`` (issue vault-mcp#15410). Semantics under
    ``2`` per the API's raw-content-mode docs: a heading Target is the JSON path
    array, percent-encoded; block/frontmatter Targets are the plain key.
    """

    def _client(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(200, {"ok": True})
        return client

    def _sent(self, client):
        return cast("MagicMock", client._client.request).call_args[1]["headers"]

    def test_header_targeting_sends_explicit_patch_version(self):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="x",
            extra_headers={
                "Target-Type": "heading",
                "Operation": "append",
                "Target": "Plain",
            },
        )
        assert self._sent(client)["Markdown-Patch-Version"] == "2"

    def test_heading_target_is_percent_encoded_json_path(self):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="x",
            extra_headers={
                "Target-Type": "heading",
                "Operation": "replace",
                "Target": "## Top::Nested Heading",
            },
        )
        assert (
            self._sent(client)["Target"]
            == "%5B%22Top%22%2C%22Nested%20Heading%22%5D"
        )

    def test_em_dash_target_is_utf8_percent_encoded_and_latin1_safe(self):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="x",
            extra_headers={
                "Target-Type": "heading",
                "Operation": "replace",
                "Target": "Checkpoint — RFC::As-built (2026-06-04)",
            },
        )
        sent = self._sent(client)["Target"]
        assert "—" not in sent
        assert "%E2%80%94" in sent
        sent.encode("latin-1")
        decoded = json.loads(urllib.parse.unquote(sent))
        assert decoded == ["Checkpoint — RFC", "As-built (2026-06-04)"]

    def test_literal_percent_round_trips(self):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="x",
            extra_headers={
                "Target-Type": "heading",
                "Operation": "replace",
                "Target": "50% Done",
            },
        )
        sent = self._sent(client)["Target"]
        assert json.loads(urllib.parse.unquote(sent)) == ["50% Done"]

    def test_frontmatter_target_is_plain_key_not_json(self):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="done",
            extra_headers={
                "Target-Type": "frontmatter",
                "Operation": "replace",
                "Target": "status",
            },
        )
        assert self._sent(client)["Target"] == "status"

    def test_patch_with_no_extra_headers_at_all(self):
        client = self._client()
        assert client.patch("/vault/Note.md", content="x")["ok"] is True
        assert "Markdown-Patch-Version" not in self._sent(client)

    @pytest.mark.parametrize(
        ("target_type", "expected"),
        [
            ("block", "blk-1"),
            ("frontmatter", "blk-1"),
            ("zzz", "blk-1"),
            ("heading", "%5B%22blk-1%22%5D"),
        ],
    )
    def test_only_heading_targets_are_json(self, target_type, expected):
        client = self._client()
        client.patch(
            "/vault/Note.md",
            content="x",
            extra_headers={
                "Target-Type": target_type,
                "Operation": "replace",
                "Target": "blk-1",
            },
        )
        assert self._sent(client)["Target"] == expected

    def test_patch_without_targeting_headers_sends_no_version(self):
        client = self._client()
        client.patch("/vault/Note.md", content="x", extra_headers={})
        assert "Markdown-Patch-Version" not in self._sent(client)


class TestTrashNameCollision:
    """delete must pick a free ``.trash/`` name (issue vault-mcp#15410, fault 3).

    ``.trash/`` is unindexed, so a leftover same-named file makes the REST PUT
    fail ``File already exists`` even though the vault never saw it.
    """

    def _io(self, taken: set[str]):
        client = MagicMock()
        puts: list[str] = []

        def put(path, **_):
            if path in taken:
                return {
                    "ok": False,
                    "error": "rest_invalid_request",
                    "detail": "Error\nFile already exists.",
                }
            puts.append(path)
            return {"ok": True, "data": None}

        client.put.side_effect = put
        client.get.return_value = {"ok": True, "data": "body"}
        client.delete.return_value = {"ok": True, "data": None}
        return RestNoteIO(client), client, puts

    def test_free_name_used_as_is(self):
        io, client, puts = self._io(set())
        io.delete_note("Brain Soup/n.md")
        assert puts == ["/vault/.trash/Brain Soup/n.md"]
        client.delete.assert_called_once_with("/vault/Brain Soup/n.md")

    def test_occupied_trash_name_falls_through_to_suffix(self):
        io, client, puts = self._io(
            {
                "/vault/.trash/Brain Soup/n.md",
                "/vault/.trash/Brain Soup/n (1).md",
            }
        )
        io.delete_note("Brain Soup/n.md")
        assert puts == ["/vault/.trash/Brain Soup/n (2).md"]
        client.delete.assert_called_once_with("/vault/Brain Soup/n.md")

    def test_name_without_extension(self):
        io, _, puts = self._io({"/vault/.trash/x"})
        io.delete_note("x")
        assert puts == ["/vault/.trash/x (1)"]

    def test_dot_only_in_a_directory_is_not_an_extension(self):
        io, _, puts = self._io({"/vault/.trash/dir.d/x"})
        io.delete_note("dir.d/x")
        assert puts == ["/vault/.trash/dir.d/x (1)"]

    def test_gives_up_after_exactly_one_hundred_names(self):
        client = MagicMock()
        client.get.return_value = {"ok": True, "data": "body"}
        client.put.return_value = {
            "ok": False,
            "error": "rest_invalid_request",
            "detail": "File already exists.",
        }
        with pytest.raises(ObsidianIOError, match="no free trash name"):
            RestNoteIO(client).delete_note("a.md")
        assert client.put.call_count == 100
        names = [c.args[0] for c in client.put.call_args_list]
        assert names[0] == "/vault/.trash/a.md"
        assert names[99] == "/vault/.trash/a (99).md"
        client.delete.assert_not_called()

    def test_other_put_errors_still_raise_and_keep_the_original(self):
        io, client, _ = self._io(set())
        client.put.side_effect = None
        client.put.return_value = {
            "ok": False,
            "error": "rest_unreachable",
            "detail": "down",
        }
        with pytest.raises(ObsidianIOError):
            io.delete_note("a.md")
        client.delete.assert_not_called()


class TestKeyLoading:
    def test_loads_key_from_file(self, tmp_path):
        key_file = tmp_path / "key.txt"
        key_file.write_text("my-secret-key\n")
        client = ObsidianRESTClient(key_path=key_file)
        assert client._api_key == "my-secret-key"

    def test_missing_key_file_stores_error(self, tmp_path):
        client = ObsidianRESTClient(key_path=tmp_path / "nonexistent.txt")
        assert client._api_key is None
        assert client._key_error is not None

    def test_loads_key_from_api_key_arg(self):
        client = ObsidianRESTClient(api_key="injected-key\n")
        assert client._api_key == "injected-key"

    def test_api_key_takes_precedence_over_path(self, tmp_path):
        key_file = tmp_path / "key.txt"
        key_file.write_text("file-key\n")
        client = ObsidianRESTClient(key_path=key_file, api_key="injected-key")
        assert client._api_key == "injected-key"


class TestPatchNoteWithin:
    """patch_note forwards ``within`` as the API's ``Within`` header, 0 included."""

    def _call(self, monkeypatch, **extra):
        import asyncio

        from vault_mcp import server

        client = MagicMock()
        client.patch.return_value = {"ok": True, "data": None}
        monkeypatch.setattr(server, "_get_rest_client", lambda: client)
        asyncio.run(
            server.mcp.call_tool(
                "patch_note",
                {"path": "a.md", "content": "x", "target": "T", **extra},
            )
        )
        return client.patch.call_args.kwargs["extra_headers"]

    def test_within_zero_is_sent(self, monkeypatch):
        assert self._call(monkeypatch, within=0)["Within"] == "0"

    def test_within_negative_is_sent(self, monkeypatch):
        assert self._call(monkeypatch, within=-1)["Within"] == "-1"

    def test_within_absent_sends_no_header(self, monkeypatch):
        assert "Within" not in self._call(monkeypatch)
