# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

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

# VERIFY: `dict[str, Any]` at the JSON boundary, and only there.
#
# An MCP tool return IS a JSON object, so the value type is open by the
# protocol's own contract — pinning it to a TypedDict per verb would encode a
# wire shape the client is free to ignore, and would still be `Any` one level
# down where Obsidian's REST payloads and YAML frontmatter arrive untyped.
# Measured 2026-08-22: of 276 `Any` in this package, 127 are `-> dict[str, Any]`
# verb returns and 34 are `list[dict[str, Any]]` rows of the same. This is a
# stated decision at the boundary, not an unexamined default.
#
# What is NOT excused by it: a BARE `: Any` or `-> Any` on anything that is not
# that boundary. Those were audited to zero in this package on the same date —
# the survivors are three sites in the Bases formula evaluator, each carrying
# its own VERIFY where it sits.

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Self

import httpx

from vault_mcp.gate import ObsidianIOError

log = logging.getLogger(__name__)

DEFAULT_REST_URL = "http://127.0.0.1:27123"

_BACKOFF_STEPS = [30, 300, 1800]

#: The ``Markdown-Patch-Version`` this client speaks on PATCH. The Local REST API
#: (5.x) refuses ``Target-Type``/``Target`` headers on a PATCH unless the version
#: is explicit (``40084``: they are ambiguous between the deprecated 1.x format and
#: raw-content mode). ``2`` is the non-deprecated one; ``1`` sunsets in 6.0.
PATCH_VERSION = "2"

#: How many ``name (n)`` candidates a trash move probes before giving up.
_TRASH_NAME_TRIES = 100


def _encode_target(value: str, target_type: str = "heading") -> str:
    """Encode a PATCH ``Target`` header value for ``Markdown-Patch-Version: 2``.

    Raw-content mode (the API's ``PATCH`` documentation) types the encoding by
    ``Target-Type``: a **heading** Target is the heading *path* as JSON, then
    percent-encoded (``["A","B"]`` -> ``%5B%22A%22%2C%22B%22%5D``); **block** and
    **frontmatter** Targets are the plain id/key, percent-encoded. Everything is
    encoded (``safe=""``), so the header is latin-1 clean and a literal ``%`` or
    em-dash survives the server's ``decodeURIComponent``.

    Callers still hand over the 1.x spelling of a heading path -- ``::``-joined,
    optionally with leading ``#`` markup -- which is split into the JSON path.
    """
    if target_type == "heading":
        parts = [
            re.sub(r"^#+\s*", "", part.strip()) for part in value.split("::")
        ]
        value = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return urllib.parse.quote(value, safe="")


class ObsidianRESTClient:
    """Single-class HTTP client for the Obsidian Local REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_REST_URL,
        key_path: str | Path | None = None,
        api_key: str | None = None,
    ):
        """Build a REST client for `base_url`.

        The API key is taken from `api_key` if provided (e.g. injected from a
        secrets manager), otherwise read from `key_path` if given.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key: str | None = None
        self._key_error: str | None = None

        if api_key:
            self._api_key = api_key.strip()
        elif key_path is not None:
            kp = Path(key_path).expanduser()
            try:
                self._api_key = kp.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
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

    def close(self) -> None:
        """Close the underlying HTTP connection pool.

        The client owns an httpx.Client and previously had no way to release
        it. In the long-lived MCP server that is one socket for the process
        lifetime and benign; anywhere a client is short-lived — a test, a CLI
        invocation, a script — it leaks. Surfaced by running the REST tests
        against the live API for the first time: under
        `filterwarnings = ["error"]` the ResourceWarning from the finalizer is
        an error, so the leak stops being invisible.
        """
        self._client.close()

    def __enter__(self) -> Self:
        """Enter a context that closes the pool on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the pool."""
        self.close()

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
        except (httpx.HTTPError, OSError) as exc:
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
            return {
                "ok": False,
                "error": "rest_no_key",
                "detail": self._key_error,
            }
        if self._api_key is None:
            return {
                "ok": False,
                "error": "rest_no_key",
                "detail": "no API key configured",
            }

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
            return {
                "ok": False,
                "error": "rest_unreachable",
                "detail": str(exc),
            }

        self._mark_reachable(self._version)

        if resp.status_code == 204:
            return {"ok": True, "data": None}
        if resp.status_code == 401:
            return {
                "ok": False,
                "error": "rest_unauthorized",
                "detail": "invalid API key",
            }
        if resp.status_code == 404:
            return {
                "ok": False,
                "error": "rest_not_found",
                "detail": resp.text[:200],
            }
        if 400 <= resp.status_code < 500:
            return {
                "ok": False,
                "error": "rest_invalid_request",
                "detail": resp.text[:500],
            }
        if resp.status_code >= 500:
            return {
                "ok": False,
                "error": "rest_obsidian_error",
                "detail": resp.text[:500],
            }

        if not resp.content:
            return {"ok": True, "data": None}
        content_type = resp.headers.get("content-type")
        if content_type and "json" in content_type:
            return {"ok": True, "data": resp.json()}
        return {"ok": True, "data": resp.text}

    def get(
        self,
        path: str,
        *,
        accept: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Issue a GET to `path` and return the response envelope."""
        return self._request(
            "GET", path, accept=accept, extra_headers=extra_headers
        )

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
        """Issue a POST to `path` with params/body/content and return the response envelope."""
        return self._request(
            "POST",
            path,
            params=params,
            json_body=json_body,
            content=content,
            content_type=content_type,
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
            "PUT",
            path,
            content=content,
            content_type=content_type,
            accept=accept,
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
        """PATCH a note section. Headers select target + operation.

        Header targeting is sent as ``Markdown-Patch-Version: 2`` with the
        ``Target`` encoded per ``Target-Type`` (JSON heading path, plain
        block/frontmatter key), percent-encoded so non-ASCII survives latin-1
        header encoding. See ``_encode_target``.
        """
        if extra_headers and "Target" in extra_headers:
            extra_headers = {
                **extra_headers,
                "Target": _encode_target(
                    extra_headers["Target"],
                    extra_headers.get("Target-Type", "heading"),
                ),
            }
        if extra_headers and "Target-Type" in extra_headers:
            extra_headers = {
                "Markdown-Patch-Version": PATCH_VERSION,
                **extra_headers,
            }
        return self._request(
            "PATCH",
            path,
            content=content,
            content_type=content_type,
            accept=accept,
            extra_headers=extra_headers,
        )

    def delete(
        self, path: str, *, accept: str = "application/json"
    ) -> dict[str, Any]:
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
        """Wrap an ObsidianRESTClient for note read/write via REST."""
        self._client = client

    def create_note(self, path: str, content: str) -> None:
        """Create a note at `path` with `content` via REST PUT."""
        self._put(path, content)

    def write_note(self, path: str, content: str) -> None:
        """Overwrite the note at `path` with `content` via REST PUT."""
        self._put(path, content)

    def read_note(self, path: str) -> str:
        """Read and return the note body at `path` via REST."""
        res = self._client.get(f"/vault/{path}", accept="text/markdown")
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST read {path}: {res.get('error')}: {res.get('detail')}"
            )
        data = res.get("data")
        if not isinstance(data, str):
            raise ObsidianIOError(
                f"REST read {path}: unexpected result {data!r}"
            )
        return data

    def _put(self, path: str, content: str) -> None:
        res = self._client.put(f"/vault/{path}", content=content)
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST write {path}: {res.get('error')}: {res.get('detail')}"
            )

    def _put_free_trash(self, path: str, content: str) -> None:
        """PUT `content` under ``.trash/`` at the first name Obsidian will accept.

        ``.trash/`` is a dot-folder Obsidian does not index, so a file left there
        by an earlier delete of a same-named note is invisible to the vault but
        still on disk, and ``PUT`` then fails ``File already exists``. Measured
        2026-10-07: ``.trash/Brain Soup/2026-10-07-note.0.md`` held an unrelated
        252-byte note, which wedged every later delete of a note with that name.
        Fall through ``name (1).md``, ``name (2).md`` ... instead of failing.
        """
        stem, dot, ext = path.rpartition(".")
        if not dot or "/" in ext:
            stem, dot, ext = path, "", ""
        candidates = [path] + [
            f"{stem} ({n}){dot}{ext}" for n in range(1, _TRASH_NAME_TRIES)
        ]
        for name in candidates:
            res = self._client.put(f"/vault/.trash/{name}", content=content)
            if res.get("ok"):
                return
            if "already exists" not in str(res.get("detail")):
                raise ObsidianIOError(
                    f"REST write .trash/{name}: {res.get('error')}: "
                    f"{res.get('detail')}"
                )
        raise ObsidianIOError(
            f"REST write .trash/{path}: no free trash name in "
            f"{_TRASH_NAME_TRIES} tries"
        )

    def delete_note(self, path: str) -> None:
        """Move a note to the vault-local ``.trash/`` (read -> copy -> remove origin).

        The content is re-PUT under ``.trash/{path}`` and only then is the
        original removed, so a failed delete never loses the note and the file
        stays recoverable from inside Obsidian.
        """
        content = self.read_note(path)  # raises ObsidianIOError if absent
        self._put_free_trash(path, content)
        res = self._client.delete(f"/vault/{path}")
        if not res.get("ok"):
            raise ObsidianIOError(
                f"REST delete {path}: {res.get('error')}: {res.get('detail')}"
            )

    def list_notes(
        self, directory: str = "", *, recursive: bool = True
    ) -> list[str]:
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
                    out.extend(
                        self.list_notes(full.rstrip("/"), recursive=True)
                    )
            elif entry.endswith(".md"):
                out.append(full)
        return out
