"""Unit tests for vault_mcp.rest_client with mocked httpx.Client."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from vault_mcp.rest_client import ObsidianRESTClient


def _mock_response(status: int = 200, json_data=None, text: str = "", headers=None):
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
        client._client.request.return_value = _mock_response(404, text="Not Found")
        result = client.get("/vault/missing.md")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"

    def test_get_401(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "bad-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(401, text="Unauthorized")
        result = client.get("/")
        assert result["ok"] is False
        assert result["error"] == "rest_unauthorized"

    def test_get_500(self):
        client = ObsidianRESTClient(key_path=None)
        client._api_key = "test-key"
        client._reachable = True
        client._last_probed = time.time()
        client._client = MagicMock()
        client._client.request.return_value = _mock_response(500, text="Internal Error")
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
