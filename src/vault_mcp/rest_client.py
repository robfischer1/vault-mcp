"""HTTP client for the Obsidian Local REST API.

Wraps httpx.Client with reachability caching, backoff on repeated failures,
and a closed error-code vocabulary. All public methods return a uniform
envelope: {"ok": True, "data": ...} or {"ok": False, "error": "<code>", "detail": "<msg>"}.

Error codes (closed vocabulary):
    rest_unreachable     — network/timeout
    rest_no_key          — key file missing or unreadable
    rest_unauthorized    — 401
    rest_not_found       — 404
    rest_obsidian_error  — 5xx from API
    rest_invalid_request — 4xx other than 401/404
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from vault_mcp.cli_client import ObsidianIOError

log = logging.getLogger(__name__)

DEFAULT_REST_URL = "http://127.0.0.1:27123"

_BACKOFF_STEPS = [30, 300, 1800]


class ObsidianRESTClient:
    """Single-class HTTP client for the Obsidian Local REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_REST_URL,
        key_path: str | Path | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key: str | None = None
        self._key_error: str | None = None

        if key_path is not None:
            kp = Path(key_path).expanduser()
            try:
                self._api_key = kp.read_text(encoding="utf-8").strip()
            except Exception as exc:
                self._key_error = str(exc)
                log.warning("REST API key unreadable at %s: %s", kp, exc)

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=10.0,
            follow_redirects=True,
        )

        self._reachable: bool | None = None
        self._last_probed: float = 0.0
        self._last_error: str | None = None
        self._probe_ttl: float = 30.0
        self._fail_count: int = 0

        self._version: str | None = None

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        h: dict[str, str] = {"Accept": accept}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _mark_unreachable(self, detail: str) -> None:
        self._reachable = False
        self._last_probed = time.time()
        self._last_error = detail
        self._fail_count += 1
        idx = min(self._fail_count - 1, len(_BACKOFF_STEPS) - 1)
        self._probe_ttl = _BACKOFF_STEPS[idx]

    def _mark_reachable(self, version: str | None = None) -> None:
        self._reachable = True
        self._last_probed = time.time()
        self._last_error = None
        self._fail_count = 0
        self._probe_ttl = 30.0
        if version:
            self._version = version

    def _should_probe(self) -> bool:
        if self._reachable is None:
            return True
        return time.time() - self._last_probed > self._probe_ttl

    def probe(self) -> dict[str, Any]:
        """Probe GET / to check reachability. Returns health dict."""
        if self._key_error:
            return {
                "reachable": False,
                "version": None,
                "last_probed": time.time(),
                "last_error": f"key unreadable: {self._key_error}",
            }
        try:
            resp = self._client.get("/", headers=self._headers())
            if resp.status_code == 200:
                data = resp.json()
                ver = data.get("versions", {}).get("self")
                self._mark_reachable(ver)
                return {
                    "reachable": True,
                    "version": ver,
                    "last_probed": self._last_probed,
                    "last_error": None,
                }
            detail = f"HTTP {resp.status_code}"
            self._mark_unreachable(detail)
            return {
                "reachable": False,
                "version": None,
                "last_probed": self._last_probed,
                "last_error": detail,
            }
        except Exception as exc:
            detail = str(exc)
            self._mark_unreachable(detail)
            return {
                "reachable": False,
                "version": None,
                "last_probed": self._last_probed,
                "last_error": detail,
            }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        content: str | None = None,
        content_type: str | None = None,
        accept: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Shared request handler with uniform error envelope."""
        if self._key_error:
            return {"ok": False, "error": "rest_no_key", "detail": self._key_error}
        if self._api_key is None:
            return {"ok": False, "error": "rest_no_key", "detail": "no API key configured"}

        if self._should_probe():
            self.probe()

        if self._reachable is False and not self._should_probe():
            return {
                "ok": False,
                "error": "rest_unreachable",
                "detail": self._last_error or "API unreachable (cached)",
            }

        headers = self._headers(accept)
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)

        try:
            kwargs: dict[str, Any] = {"params": params, "headers": headers}
            if json_body is not None:
                kwargs["json"] = json_body
            elif content is not None:
                kwargs["content"] = content.encode("utf-8")
            resp = self._client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            self._mark_unreachable(str(exc))
            return {"ok": False, "error": "rest_unreachable", "detail": str(exc)}

        self._mark_reachable(self._version)

        if resp.status_code == 204:
            return {"ok": True, "data": None}
        if resp.status_code == 401:
            return {"ok": False, "error": "rest_unauthorized", "detail": "invalid API key"}
        if resp.status_code == 404:
            return {"ok": False, "error": "rest_not_found", "detail": resp.text[:200]}
        if 400 <= resp.status_code < 500:
            return {"ok": False, "error": "rest_invalid_request", "detail": resp.text[:500]}
        if resp.status_code >= 500:
            return {"ok": False, "error": "rest_obsidian_error", "detail": resp.text[:500]}

        if not resp.content:
            return {"ok": True, "data": None}
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return {"ok": True, "data": resp.json()}
        return {"ok": True, "data": resp.text}

    def get(
        self,
        path: str,
        *,
        accept: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", path, accept=accept, extra_headers=extra_headers)

    def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        content: str | None = None,
        content_type: str | None = None,
        accept: str = "application/json",
    ) -> dict[str, Any]:
        return self._request(
            "POST", path,
            params=params, json_body=json_body,
            content=content, content_type=content_type,
            accept=accept,
        )

    def put(
        self,
        path: str,
        *,
        content: str,
        content_type: str = "text/markdown",
        accept: str = "application/json",
    ) -> dict[str, Any]:
        """PUT a note body. ``PUT /vault/{path}`` creates or overwrites the file."""
        return self._request(
            "PUT", path, content=content, content_type=content_type, accept=accept,
        )

    def patch(
        self,
        path: str,
        *,
        content: str,
        content_type: str = "text/markdown",
        accept: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """PATCH a note section. Headers select target + operation."""
        return self._request(
            "PATCH", path,
            content=content, content_type=content_type,
            accept=accept, extra_headers=extra_headers,
        )

    def delete(self, path: str, *, accept: str = "application/json") -> dict[str, Any]:
        """DELETE a note. ``DELETE /vault/{path}`` removes the file."""
        return self._request("DELETE", path, accept=accept)


class RestNoteIO:
    """Convention Gate ``NoteIO`` over the Obsidian Local REST API.

    The session-0 alternative to the CLI-backed ``ObsidianNoteIO``: the CLI talks
    to Obsidian over same-session IPC and cannot reach a desktop (session-1)
    instance, but the REST API is HTTP on loopback and crosses the session
    boundary. ``PUT /vault/{path}`` creates/overwrites and ``GET /vault/{path}``
    reads — writes still go through Obsidian, so its indexing fires. Raises
    ``ObsidianIOError`` (the shared NoteIO error the Gate's envelope maps) on any
    non-ok REST response.
    """

    def __init__(self, client: ObsidianRESTClient) -> None:
        self._client = client

    def create_note(self, path: str, content: str) -> None:
        self._put(path, content)

    def write_note(self, path: str, content: str) -> None:
        self._put(path, content)

    def read_note(self, path: str) -> str:
        res = self._client.get(f"/vault/{path}", accept="text/markdown")
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST read {path}: {res.get('error')}: {res.get('detail')}"
            )
        data = res.get("data")
        if not isinstance(data, str):
            raise ObsidianIOError(f"REST read {path}: unexpected result {data!r}")
        return data

    def _put(self, path: str, content: str) -> None:
        res = self._client.put(f"/vault/{path}", content=content)
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST write {path}: {res.get('error')}: {res.get('detail')}"
            )

    def delete_note(self, path: str) -> None:
        """Move a note to the vault-local ``.trash/`` (read -> copy -> remove origin).

        The content is re-PUT under ``.trash/{path}`` and only then is the
        original removed, so a failed delete never loses the note and the file
        stays recoverable from inside Obsidian.
        """
        content = self.read_note(path)  # raises ObsidianIOError if absent
        self._put(f".trash/{path}", content)
        res = self._client.delete(f"/vault/{path}")
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST delete {path}: {res.get('error')}: {res.get('detail')}"
            )

    def list_notes(self, directory: str = "", *, recursive: bool = True) -> list[str]:
        """List ``.md`` note paths under ``directory`` (recursive by default).

        Uses the REST directory listing (``GET /vault/{dir}/`` -> ``{files: [...]}``,
        subfolders carry a trailing ``/``). Skips Obsidian's ``.trash/``. Returns
        an empty list when the directory is unreachable rather than raising — a
        scan over a missing subtree is empty, not an error.
        """
        prefix = directory.strip("/")
        listing = f"/vault/{prefix}/" if prefix else "/vault/"
        res = self._client.get(listing, accept="application/json")
        if not res.get("ok"):
            return []
        data = res.get("data")
        files = data.get("files", []) if isinstance(data, dict) else []
        out: list[str] = []
        for entry in files:
            if entry.startswith(".trash"):
                continue
            full = f"{prefix}/{entry}" if prefix else entry
            if entry.endswith("/"):
                if recursive:
                    out.extend(self.list_notes(full.rstrip("/"), recursive=True))
            elif entry.endswith(".md"):
                out.append(full)
        return out
