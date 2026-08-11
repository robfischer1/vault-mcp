#!/usr/bin/env python3
"""MCP server for vault-mcp.

Read-only query tools over an Obsidian vault's frontmatter, filenames,
and link graph. Boilerplate follows personal-history-db/server.py.

Config resolution (highest priority first):
    VAULT_MCP_PATH          explicit path to vault root
    OBSIDIAN_VAULT_PATH     shared env var (also used by other tools)
    (walk up)               find nearest .obsidian/ parent

    VAULT_MCP_TTL_SECONDS   index TTL in seconds (default: 300)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mcp.server.fastmcp import Context, FastMCP

if TYPE_CHECKING:
    from collections.abc import Iterator

    from vault_mcp.cli_client import ObsidianCLI
    from vault_mcp.compute import ComputeReceiver
    from vault_mcp.gate import ConventionGate
    from vault_mcp.gitops import GitCommitter
    from vault_mcp.lifecycle import Materializer
    from vault_mcp.plan_freshness import StoredCopy
    from vault_mcp.rest_client import ObsidianRESTClient

from vault_mcp.bases import (
    _serialize_base,
)
from vault_mcp.bases import (
    execute_base as _execute_base_impl,
)
from vault_mcp.bases import (
    parse_file as _parse_file_impl,
)
from vault_mcp.bases import (
    validate_base as _validate_base_impl,
)
from vault_mcp.bases import (
    write_base_to_file as _write_base_to_file_impl,
)
from vault_mcp.index import VaultIndex
from vault_mcp.rest_client import DEFAULT_REST_URL

# The phdb sibling-repo sys.path import is GONE (2026-08-04). It resolved
# ../personal-history-db/src off the Forge workspace so the triple and
# vault_notes verbs could import phdb directly — the cross-tree import that
# SRSC isolation #1317 asked to remove. Both verb families are now retired and
# the monolith is dissolved, so there is nothing left to import: #1317 is
# closed by deletion rather than by the config-addressed call it proposed.


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _resolve_vault_path() -> Path:
    if p := os.environ.get("VAULT_MCP_PATH"):
        return Path(p).resolve()
    if p := os.environ.get("OBSIDIAN_VAULT_PATH"):
        return Path(p).resolve()
    # Walk up from this file looking for .obsidian/
    cursor = Path(__file__).resolve().parent
    while cursor != cursor.parent:
        if (cursor / ".obsidian").is_dir():
            return cursor
        cursor = cursor.parent
    raise FileNotFoundError(
        "Could not locate an Obsidian vault. "
        "Set VAULT_MCP_PATH or OBSIDIAN_VAULT_PATH env var."
    )


VAULT_PATH = _resolve_vault_path()
TTL_SECONDS = int(os.environ.get("VAULT_MCP_TTL_SECONDS", "300"))
WATCH_ENABLED = os.environ.get("VAULT_MCP_WATCH", "1") != "0"

# REST API config (Phase 6)
REST_DISABLE = os.environ.get("VAULT_MCP_REST_DISABLE", "0") == "1"
REST_URL = os.environ.get("VAULT_MCP_REST_URL", DEFAULT_REST_URL)
REST_KEY_PATH = os.environ.get(
    "VAULT_MCP_REST_KEY_PATH",
    "",
)
# Direct key injection (e.g. from a secrets manager); takes precedence over the path.
REST_KEY = os.environ.get("VAULT_MCP_REST_KEY", "")

# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

_active_sessions: set[Any] = set()


@dataclass
class Subscription:
    """A live-update subscription: a handle bound to a base path/view plus its last result hash."""

    handle: str
    path: str
    view: str | None
    base_index: int
    last_result_hash: str | None = None


class SubscriptionManager:
    """Manages Bases live update subscriptions and pushes notifications."""

    def __init__(self, mcp_server: FastMCP):
        """Initialize the subscription manager bound to the FastMCP server."""
        self.mcp = mcp_server
        self.subscriptions: dict[str, Subscription] = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger(__name__)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Path] | None = None
        self._worker_task: asyncio.Task[None] | None = None

    def _ensure_worker(self) -> None:
        """Ensure the background worker is running."""
        if self._worker_task is not None:
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not in an event loop yet
            return

        self._queue = asyncio.Queue()
        self._worker_task = self._loop.create_task(self._worker())
        self.log.info("Subscription worker started")

    async def _worker(self) -> None:
        while True:
            if self._queue is None:
                break
            path = await self._queue.get()
            try:
                await self.notify_all(path)
            except Exception:
                self.log.exception("Error in subscription worker for %s", path)
            finally:
                self._queue.task_done()

    def on_file_invalidated(self, path: Path) -> None:
        """Sync callback for VaultIndex."""
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    def add(self, path: str, view: str | None, base_index: int) -> str:
        """Register a subscription for a base path/view and return its handle."""
        self._ensure_worker()
        handle = f"sub_{uuid.uuid4().hex[:8]}"
        with self.lock:
            self.subscriptions[handle] = Subscription(
                handle=handle,
                path=path,
                view=view,
                base_index=base_index,
            )
        return handle

    def remove(self, handle: str) -> bool:
        """Remove the subscription with `handle`; return True if it existed."""
        with self.lock:
            if handle in self.subscriptions:
                del self.subscriptions[handle]
                return True
        return False

    def _hash_result(self, result: Any) -> str:
        """Create a stable hash of a QueryResult."""
        data = (
            asdict(result)
            if hasattr(result, "__dataclass_fields__")
            else result
        )

        hash_data = {
            "notes": [
                {"path": n["path"], "formulas": n["formulas"]}
                for n in data.get("notes", [])
            ],
            "summaries": data.get("summaries", {}),
            "total": data.get("total", 0),
        }
        dump = json.dumps(hash_data, sort_keys=True)
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    async def notify_all(self, _changed_path: Path) -> None:
        """Re-evaluate every subscription and push notifications for changed result sets."""
        subs_to_check = []
        with self.lock:
            subs_to_check = list(self.subscriptions.values())

        if not subs_to_check:
            return

        idx = _get_index()

        for sub in subs_to_check:
            file_path = idx.vault / sub.path
            if not file_path.exists():
                continue

            try:
                pf = _parse_file_impl(file_path)
                if sub.base_index >= len(pf.bases):
                    continue
                base = pf.bases[sub.base_index]

                if sub.view:
                    matched = [v for v in base.views if v.name == sub.view]
                    if not matched:
                        continue

                result = _execute_base_impl(base, idx, view_name=sub.view)
                current_hash = self._hash_result(result)

                if current_hash == sub.last_result_hash:
                    continue

                sub.last_result_hash = current_hash
                await self._push_notification(sub, result)
            except Exception:
                self.log.exception("Error updating subscription %s", sub.handle)

    async def _push_notification(self, sub: Subscription, result: Any) -> None:
        payload = {
            "handle": sub.handle,
            "path": sub.path,
            "view": sub.view,
            "results": asdict(result)
            if hasattr(result, "__dataclass_fields__")
            else result,
        }

        from mcp.types import JSONRPCNotification

        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/bases/update",
            params=payload,
        )

        disconnected = []
        for session in _active_sessions:
            try:
                await session.send_notification(cast("Any", notification))
            except Exception:  # noqa: BLE001 — any send failure means the session is gone
                disconnected.append(session)

        for session in disconnected:
            _active_sessions.discard(session)


_sub_manager: SubscriptionManager | None = None


def _get_sub_manager() -> SubscriptionManager:
    global _sub_manager
    if _sub_manager is None:
        _sub_manager = SubscriptionManager(mcp)
        _get_index().on_invalidate.append(_sub_manager.on_file_invalidated)
    return _sub_manager


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_index: VaultIndex | None = None
_observer = None


def _get_index() -> VaultIndex:
    global _index, _observer
    if _index is None:
        _index = VaultIndex(VAULT_PATH, ttl_seconds=TTL_SECONDS)
        if WATCH_ENABLED:
            from vault_mcp.watcher import start_watcher

            _index.enable_watcher()
            _observer = start_watcher(_index)
    return _index


# REST client (Phase 6)
_rest_client: ObsidianRESTClient | None = None


def _get_rest_client() -> ObsidianRESTClient:
    global _rest_client
    if _rest_client is None:
        from vault_mcp.rest_client import ObsidianRESTClient

        _rest_client = ObsidianRESTClient(
            base_url=REST_URL,
            key_path=REST_KEY_PATH or None,
            api_key=REST_KEY or None,
        )
        _rest_client.probe()
    return _rest_client


# CLI client (Phase 006)
_cli_client: ObsidianCLI | None = None


def _get_cli_client() -> ObsidianCLI:
    global _cli_client
    if _cli_client is None:
        from vault_mcp.cli_client import ObsidianCLI

        _cli_client = ObsidianCLI()
        _cli_client.probe()
    return _cli_client


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------
mcp = FastMCP("vault-mcp")


@mcp.tool()
def obsidian_cli_status() -> dict[str, Any]:
    """Check Obsidian CLI availability and version.

    Returns:
        {"available": bool, "version": str|None, "error": str|None, "detail": str|None}

    """
    return _get_cli_client().probe()


@mcp.tool()
def obsidian_cli_reload_plugin(id: str) -> dict[str, Any]:
    """[CLI-backed] Reload an Obsidian community plugin by ID.

    Essential for plugin development workflows.

    Args:
        id: The plugin ID (e.g., "obsidian-local-rest-api").

    Returns:
        {"ok": bool, "data": Any} on success.

    """
    return _get_cli_client().run("plugin:reload", id=id)


@mcp.tool()
def obsidian_cli_eval(code: str) -> dict[str, Any]:
    """[CLI-backed] Execute arbitrary JavaScript in the Obsidian app console.

    Extremely powerful; provides full access to the Obsidian API. Use with
    caution.

    Args:
        code: JavaScript snippet to execute.

    Returns:
        {"ok": bool, "data": Any} on success.

    """
    return _get_cli_client().run("eval", code=code)


@mcp.tool()
def obsidian_cli_command(
    command: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """[CLI-backed] Execute an Obsidian CLI command (allowlisted only).

    The allowlist is enforced in ObsidianCLI.run() itself (defense in depth).

    Args:
        command: The CLI command name.
        params: Optional dict of key=value parameters.

    Returns:
        {"ok": bool, "data": Any} on success.

    """
    return _get_cli_client().run(command, **(params or {}))


@mcp.tool()
def find_notes_by_frontmatter(
    filters: dict[str, str],
    scope: str | None = None,
) -> dict[str, Any]:
    """Find notes whose frontmatter fields match all given key-value pairs.

    Args:
        filters: Dict of field=value equality checks. For list-valued fields,
                 matches if the value appears anywhere in the list.
                 Example: {"note_type": "handoff", "status": "Active"}
        scope: Optional vault-relative path prefix to restrict results.
               Example: "System/Handoffs/"

    Returns:
        {"count": int, "results": [{path, frontmatter}]}

    """
    results = _get_index().find_notes_by_frontmatter(filters, scope=scope)
    return {"count": len(results), "results": results}


@mcp.tool()
def find_by_filename(
    pattern: str,
    scope: str | None = None,
) -> dict[str, Any]:
    """Find notes by filename glob pattern.

    Args:
        pattern: Glob pattern matched against file stems (without .md).
                 Example: "2026-05-*-handoff*"
        scope: Optional vault-relative path prefix.

    Returns:
        {"count": int, "results": [{stem, path}]}

    """
    results = _get_index().find_by_filename(pattern, scope=scope)
    return {"count": len(results), "results": results}


@mcp.tool()
def recent_edits(
    since: str,
    scope: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List recently modified notes, sorted newest-first.

    Args:
        since: ISO-8601 date or datetime string. Example: "2026-05-01"
        scope: Optional vault-relative path prefix.
        limit: Max results (default 50).

    Returns:
        {"count": int, "results": [{path, modified, stem}]}

    """
    results = _get_index().recent_edits(since, scope=scope, limit=limit)
    return {"count": len(results), "results": results}


@mcp.tool()
def read_note(
    stem_or_path: str,
) -> dict[str, Any]:
    """Read a note's frontmatter and body by wikilink stem or vault-relative path.

    Accepts either:
    - A wikilink stem: "AGENTS" (resolved via the filename index)
    - A vault-relative path: "System/Governance/AGENTS.md"

    Returns:
        {path, frontmatter, body, outbound_links} on success.
        {error, ...} on ambiguous stem, not found, or read failure.

    Outbound links include resolution: each wikilink stem is resolved to its
    full path when unambiguous, or flagged as "ambiguous"/"unresolved".

    """
    return _get_index().read_note(stem_or_path)


@mcp.tool()
def reindex() -> dict[str, Any]:
    """Force a full vault index rebuild.

    Clears the cached index and rebuilds from disk. Use after making
    many changes or when stale results are suspected.

    Returns:
        {"indexed": int, "names": int, "elapsed_ms": int}

    """
    return _get_index().reindex()


# ---------------------------------------------------------------------------
# Phase 2 — Graph tools
# ---------------------------------------------------------------------------

_AUDIT_IGNORES_REL = "System/Tools/Skills/vault-propagation/audit-ignores.md"


@mcp.tool()
def backlinks_to(stem: str) -> dict[str, Any]:
    """Find all notes that link to a given note (by wikilink or `up:` frontmatter).

    Args:
        stem: The file stem to find backlinks for. Example: "AGENTS"

    Returns:
        {"stem": str, "count": int, "results": [{stem, path}]}

    """
    results = _get_index().backlinks_to(stem)
    return {"stem": stem, "count": len(results), "results": results}


@mcp.tool()
def outbound_links(
    stem: str,
    include_image_embeds: bool = False,
) -> dict[str, Any]:
    """List all wikilinks from a note's body.

    Args:
        stem: The file stem to inspect. Example: "COWORK"
        include_image_embeds: If True, include ![[image]] embeds. Default False.

    Returns:
        {"stem": str, "count": int, "results": [{stem, path|resolution}]}

    """
    results = _get_index().outbound_links(
        stem, include_image_embeds=include_image_embeds
    )
    return {"stem": stem, "count": len(results), "results": results}


@mcp.tool()
def find_orphans(scope: str | None = None) -> dict[str, Any]:
    """Find notes with no inbound links and no `up:` frontmatter.

    Honors folder-prefix exemptions from vault-propagation/audit-ignores.md.

    Args:
        scope: Optional vault-relative path prefix to restrict search.

    Returns:
        {"count": int, "results": [{path, stem, reason}]}

    """
    idx = _get_index()
    ignores_path = VAULT_PATH / _AUDIT_IGNORES_REL
    exempt = VaultIndex.parse_audit_ignores(ignores_path)
    results = idx.find_orphans(scope=scope, exempt_prefixes=exempt)
    return {"count": len(results), "results": results}


@mcp.tool()
def find_dangling_links(scope: str | None = None) -> dict[str, Any]:
    """Find wikilinks and ``up:`` values that point at non-existent notes.

    Scans the vault for broken links — useful for finding stale references
    after moves, renames, or dissolutions.

    Args:
        scope: Optional vault-relative path prefix to restrict search.
            Example: "Knowledge" scans only Knowledge/ notes.

    Returns:
        {"count": int, "results": [{source, target, link_type}]}
        link_type is "wikilink" or "up".

    """
    idx = _get_index()
    results = idx.find_dangling_links(scope=scope)
    return {"count": len(results), "results": results}


# ---------------------------------------------------------------------------
# Phase 3 — Governance tools
# ---------------------------------------------------------------------------

_TAGS_GLOSSARY_REL = "System/Tags Glossary.md"


@mcp.tool()
def tag_glossary_check() -> dict[str, Any]:
    """Find body #tags not in the Tags Glossary.

    Scans all indexed files for inline #tags in body text and reports any
    that are not in the closed vocabulary at System/Tags Glossary.md.
    Excludes backslash-escaped tags and #activity/processed.

    Returns:
        {"files_with_violations": int,
         "results": [{path, invalid_tags: [str]}]}

    """
    idx = _get_index()
    glossary_path = VAULT_PATH / _TAGS_GLOSSARY_REL
    results = idx.tag_glossary_check(glossary_path)
    return {"files_with_violations": len(results), "results": results}


@mcp.tool()
def all_tags(include_body: bool = True) -> dict[str, Any]:
    """Get all tags in the vault with counts (parser-backed, no Obsidian needed).

    Collects tags from frontmatter `tags:` arrays and optionally inline #tags
    in body text. Unlike the REST-backed vault_tags(), this works without
    Obsidian running — it reads directly from the indexed files.

    Args:
        include_body: If True (default), also scan body text for inline #tags.
                      Set False for frontmatter-only (faster).

    Returns:
        {"count": int, "tags": [{tag, count, frontmatter_count, body_count, sources}]}

    """
    results = _get_index().all_tags(include_body=include_body)
    return {"count": len(results), "tags": results}


@mcp.tool()
def vault_stats() -> dict[str, Any]:
    """Aggregate vault statistics.

    Returns counts by @type, top frontmatter tags, recent edit volume
    by ISO week, and last_indexed_at timestamp.

    Returns:
        {"total_indexed": int, "total_names": int,
         "last_indexed_at": str | None,
         "watcher_active": bool,
         "types": [{type, count}],
         "top_tags": [{tag, count}],
         "edit_volume_by_week": [{week, count}]}

    """
    idx = _get_index()
    stats = idx.vault_stats()
    stats["last_indexed_at"] = idx.last_indexed_at
    stats["watcher_active"] = idx._watcher_active
    return stats


# ---------------------------------------------------------------------------
# Phase 8 — Bases tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def subscribe_base(
    path: str,
    view: str | None = None,
    base_index: int = 0,
    ctx: Context[Any, Any] | None = None,
) -> dict[str, Any]:
    """Subscribe to live updates for a base query.

    Pushes a notification whenever the result set for this base changes.
    Notifications use method 'notifications/bases/update'.

    Args:
        path: Vault-relative file path containing the base.
        view: Named view to restrict to.
        base_index: 0-based index of which base (default 0).
        ctx: FastMCP context used to push live-update notifications (injected).

    Returns:
        {"handle": str, "initial_results": dict}

    """
    idx = _get_index()
    file_path = idx.vault / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}

    pf = _parse_file_impl(file_path)
    if pf.errors and not pf.bases:
        return {
            "error": "parse_error",
            "path": path,
            "detail": pf.errors[0]["message"],
        }

    if base_index < 0 or base_index >= len(pf.bases):
        return {
            "error": "invalid_base_index",
            "path": path,
            "index": base_index,
            "available": len(pf.bases),
        }

    if ctx:
        _active_sessions.add(ctx.session)

    mgr = _get_sub_manager()
    handle = mgr.add(path, view, base_index)

    base = pf.bases[base_index]
    result = _execute_base_impl(base, idx, view_name=view)

    with mgr.lock:
        if handle in mgr.subscriptions:
            mgr.subscriptions[handle].last_result_hash = mgr._hash_result(
                result
            )

    return {
        "handle": handle,
        "initial_results": asdict(result)
        if hasattr(result, "__dataclass_fields__")
        else result,
    }


@mcp.tool()
async def unsubscribe_base(handle: str) -> dict[str, Any]:
    """Cancel a base live update subscription.

    Args:
        handle: The subscription handle returned by subscribe_base.

    Returns:
        {"ok": bool}

    """
    mgr = _get_sub_manager()
    success = mgr.remove(handle)
    return {"ok": success}


@mcp.tool()
def parse_base(path: str) -> dict[str, Any]:
    """Parse a markdown file for Obsidian Bases code blocks.

    Returns the structured representation of each base: filter tree,
    formula definitions, and view configurations.

    Args:
        path: Vault-relative file path. Example: "Outputs/Plans/Plans.md"

    Returns:
        {"path": str, "count": int, "bases": [...], "errors": [...]}

    """
    file_path = VAULT_PATH / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}
    pf = _parse_file_impl(file_path)
    return {
        "path": path,
        "count": len(pf.bases),
        "bases": [_serialize_base(b) for b in pf.bases],
        "errors": pf.errors,
    }


@mcp.tool()
def execute_base(
    path: str,
    view: str | None = None,
    base_index: int = 0,
) -> dict[str, Any]:
    """Execute a base's filters and formulas against the vault index.

    Returns matching notes with computed formula columns, optionally
    restricted to a named view.

    Args:
        path: Vault-relative file path containing the base.
        view: Named view to restrict to. If null, base-level filters only.
        base_index: 0-based index of which base to execute (default 0).

    Returns:
        {"total": int, "view": str|null, "notes": [...], "warnings": [...]}

    """
    file_path = VAULT_PATH / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}

    pf = _parse_file_impl(file_path)
    if pf.errors and not pf.bases:
        return {
            "error": "parse_error",
            "path": path,
            "detail": pf.errors[0]["message"],
        }

    if base_index < 0 or base_index >= len(pf.bases):
        return {
            "error": "invalid_base_index",
            "path": path,
            "index": base_index,
            "available": len(pf.bases),
        }

    base = pf.bases[base_index]

    if view is not None:
        view_names = [v.name for v in base.views]
        matched = [v for v in base.views if v.name == view]
        if not matched:
            return {
                "error": "view_not_found",
                "path": path,
                "view": view,
                "available": view_names,
            }
        if matched[0].type != "table":
            return {
                "error": "unsupported_view_type",
                "view": view,
                "type": matched[0].type,
                "detail": "Only table views are executable in Phase 1",
            }

    idx = _get_index()
    result = _execute_base_impl(base, idx, view_name=view)
    return {
        "total": result.total,
        "view": result.view_name,
        "notes": result.notes,
        "warnings": result.warnings,
        "summaries": result.summaries,
    }


@mcp.tool()
def write_base(
    path: str,
    base: dict[str, Any],
    base_index: int | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Write or update an inline base code block in a markdown file.

    Args:
        path: Vault-relative file path.
        base: Base configuration dict with keys: filters, formulas, views.
        base_index: 0-based index of which base to replace. If null and file
                    has no bases, appends. If null and one base, replaces it.
        validate: Run validation before writing (default true).

    Returns:
        {"written": true, "path": str, "action": str, "base_index": int} on success.

    """
    file_path = VAULT_PATH / path
    if not file_path.exists() and not file_path.parent.exists():
        return {"error": "not_found", "path": path}

    if validate:
        vr = _validate_base_impl(base)
        if not vr.valid:
            return {
                "written": False,
                "path": path,
                "validation": {
                    "valid": False,
                    "errors": vr.errors,
                    "warnings": vr.warnings,
                },
            }

    result = _write_base_to_file_impl(file_path, base, base_index=base_index)
    result["path"] = path
    return result


@mcp.tool()
def validate_base_tool(base: dict[str, Any]) -> dict[str, Any]:
    """Validate a base configuration without writing it.

    Checks YAML validity, formula references, special characters,
    and sort property references.

    Args:
        base: Base configuration dict (same shape as write_base.base).

    Returns:
        {"valid": bool, "errors": [...], "warnings": [...]}

    """
    vr = _validate_base_impl(base)
    return {
        "valid": vr.valid,
        "errors": vr.errors,
        "warnings": vr.warnings,
    }


# ---------------------------------------------------------------------------
# Phase 6 — REST-backed tools
# ---------------------------------------------------------------------------

if not REST_DISABLE:

    @mcp.tool()
    def rest_health() -> dict[str, Any]:
        """[REST-backed] Check Obsidian Local REST API reachability.

        Probes the API at http://127.0.0.1:27123 and returns connection
        status, plugin version, and last error (if any). Cheap probe;
        no vault data returned.

        Returns:
            {"reachable": bool, "version": str|None,
             "last_probed": float, "last_error": str|None}

        """
        return _get_rest_client().probe()

    @mcp.tool()
    def active_note() -> dict[str, Any]:
        """[REST-backed] Get the currently active note in Obsidian's editor.

        Returns the note open in the active pane, including its parsed
        frontmatter, content, file stats, and vault-relative path.
        Reflects the live editor buffer (may include unsaved changes).

        Code-only — returns {"error": "rest_unreachable"} from environments
        where Obsidian is not running.

        Returns:
            {"path": str, "frontmatter": dict, "content": str, "stat": dict,
             "as_of": "rest"} on success.

        """
        client = _get_rest_client()
        result = client.get(
            "/active/", accept="application/vnd.olrapi.note+json"
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        data["as_of"] = "rest"
        return data

    @mcp.tool()
    def periodic_note(
        level: str,
        date: str | None = None,
    ) -> dict[str, Any]:
        """[REST-backed] Get a periodic note (daily, weekly, monthly, quarterly, yearly).

        Args:
            level: One of "daily", "weekly", "monthly", "quarterly", "yearly".
            date: Optional date string (e.g. "2026-05-09"). Defaults to today.

        Returns:
            {"path": str, "frontmatter": dict, "content": str, "stat": dict,
             "as_of": "rest"} on success.

        """
        valid = {"daily", "weekly", "monthly", "quarterly", "yearly"}
        if level not in valid:
            return {
                "error": "rest_invalid_request",
                "detail": f"level must be one of {valid}",
            }
        path = f"/periodic/{level}/"
        if date:
            path = f"/periodic/{level}/{date}"
        client = _get_rest_client()
        result = client.get(
            path,
            accept="application/vnd.olrapi.note+json",
            extra_headers={"Target-Type": "note"},
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        data["as_of"] = "rest"
        return data

    @mcp.tool()
    def unsaved_buffer(path: str) -> dict[str, Any]:
        """[REST-backed] Read a note's current editor buffer from Obsidian.

        Distinct from read_note: reflects unsaved edits, requires Obsidian
        running, Code-only. Use read_note for disk-state reads that work
        everywhere.

        Args:
            path: Vault-relative path, e.g. "System/Governance/AGENTS.md"

        Returns:
            {"path": str, "frontmatter": dict, "content": str, "stat": dict,
             "as_of": "rest"} on success.

        """
        client = _get_rest_client()
        result = client.get(
            f"/vault/{path}",
            accept="application/vnd.olrapi.note+json",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        data["as_of"] = "rest"
        return data

    @mcp.tool()
    def patch_note(
        path: str,
        content: str,
        target_type: str = "heading",
        target: str | None = None,
        operation: str = "replace",
        create_if_missing: bool = False,
    ) -> dict[str, Any]:
        """[REST-backed] Patch a section of a note without rewriting the whole file.

        Uses Obsidian's PATCH /vault/{path} with targeting headers to modify
        a specific heading, block, or frontmatter field. Use document_map()
        first to discover valid targets.

        Args:
            path: Vault-relative file path.
            content: The markdown content to write.
            target_type: "heading", "block", or "frontmatter". Default "heading".
            target: Target identifier — heading path ("## Section"), block ID,
                or frontmatter field name. If None, targets the whole note.
            operation: "replace", "append", or "prepend". Default "replace".
            create_if_missing: If True, create the target heading/block if it
                doesn't exist.

        Returns:
            {"ok": True, "patched": path} on success.

        """
        client = _get_rest_client()
        headers: dict[str, str] = {
            "Target-Type": target_type,
            "Operation": operation,
        }
        if target:
            headers["Target"] = target
        if create_if_missing:
            headers["Create-Target-If-Missing"] = "true"
        result = client.patch(
            f"/vault/{path}",
            content=content,
            extra_headers=headers,
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"ok": True, "patched": path}

    @mcp.tool()
    def append_note(path: str, content: str) -> dict[str, Any]:
        """[REST-backed] Append content to the end of a note.

        Convenience wrapper around POST /vault/{path}. Appends the content
        after the note's existing body.

        Args:
            path: Vault-relative file path.
            content: Markdown content to append.

        Returns:
            {"ok": True, "appended": path} on success.

        """
        client = _get_rest_client()
        result = client.post(
            f"/vault/{path}",
            content=content,
            content_type="text/markdown",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"ok": True, "appended": path}

    @mcp.tool()
    def set_field(path: str, key: str, value: str) -> dict[str, Any]:
        """[REST-backed] Set a single frontmatter field on a note.

        Convenience wrapper around PATCH with Target-Type: frontmatter.
        Replaces the field value; creates the field if it doesn't exist.

        Args:
            path: Vault-relative file path.
            key: Frontmatter field name (e.g. "status", "created").
            value: New value as a string. YAML scalars are auto-parsed by
                Obsidian (e.g. "true" -> boolean, "42" -> number).

        Returns:
            {"ok": True, "field": key, "path": path} on success.

        """
        client = _get_rest_client()
        headers: dict[str, str] = {
            "Target-Type": "frontmatter",
            "Target": key,
            "Operation": "replace",
            "Create-Target-If-Missing": "true",
        }
        result = client.patch(
            f"/vault/{path}",
            content=value,
            extra_headers=headers,
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"ok": True, "field": key, "path": path}

    @mcp.tool()
    def periodic_append(
        level: str,
        content: str,
        date: str | None = None,
    ) -> dict[str, Any]:
        """[REST-backed] Append content to a periodic note (daily, weekly, etc.).

        Creates the note if it doesn't exist (Obsidian's periodic note
        behavior). Useful for Journal/ daily capture and atom emission.

        Args:
            level: One of "daily", "weekly", "monthly", "quarterly", "yearly".
            content: Markdown content to append.
            date: Optional date string. Defaults to today.

        Returns:
            {"ok": True, "appended": level, "date": date} on success.

        """
        valid = {"daily", "weekly", "monthly", "quarterly", "yearly"}
        if level not in valid:
            return {
                "error": "rest_invalid_request",
                "detail": f"level must be one of {valid}",
            }
        path = f"/periodic/{level}/"
        if date:
            path = f"/periodic/{level}/{date}"
        client = _get_rest_client()
        result = client.post(
            path, content=content, content_type="text/markdown"
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"ok": True, "appended": level, "date": date}

    @mcp.tool()
    def obsidian_search(query: str) -> dict[str, Any]:
        """[REST-backed] Search the vault using Obsidian's built-in search engine.

        Passes the query verbatim to Obsidian — supports all Obsidian search
        operators (tag:, path:, file:, section:, etc.). Returns results ranked
        by Obsidian's relevance scoring.

        Args:
            query: Obsidian search query string. Example: "tag:#brainsoup"

        Returns:
            {"count": int, "results": [{filename, score, matches}]}

        """
        client = _get_rest_client()
        result = client.post(
            "/search/simple/",
            params={"query": query},
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        if isinstance(data, list):
            return {"count": len(data), "results": data}
        return {"count": 0, "results": [], "raw": data}

    REST_COMMAND_ALLOWLIST = {
        # Navigation / focus
        "workspace:open-in-new-leaf",
        "app:reveal-active-file",
        "editor:focus",
        "app:open-settings",
        # Linter / formatting
        "obsidian-linter:lint-file",
        "obsidian-linter:lint-all-files",
        # Templater
        "templater-obsidian:replace-in-file-templater",
        # Update frontmatter
        "update-time-on-edit:update-current",
    }

    @mcp.tool()
    def execute_command(command_id: str) -> dict[str, Any]:
        """[REST-backed] Execute an Obsidian command by ID (allowlisted only).

        Crosses the session boundary (HTTP on loopback), so this works
        headless where the CLI cannot. Allowed commands include navigation,
        linter, templater, and frontmatter-update triggers.

        Args:
            command_id: Obsidian command ID. Example: "obsidian-linter:lint-file"

        Returns:
            {"executed": str} on success, {"error": ...} on rejection.

        """
        if command_id not in REST_COMMAND_ALLOWLIST:
            return {
                "error": "rest_invalid_request",
                "detail": f"command not in allowlist: {command_id}. "
                f"Allowed: {sorted(REST_COMMAND_ALLOWLIST)}",
            }
        client = _get_rest_client()
        result = client.post(f"/commands/{command_id}/")
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"executed": command_id}

    # -------------------------------------------------------------------
    # Phase 7 — Advanced query tools
    # -------------------------------------------------------------------

    @mcp.tool()
    def dataview_query(dql: str) -> dict[str, Any]:
        """[REST-backed] Run a Dataview DQL TABLE query against the vault.

        Passes the query to the Dataview plugin via the REST API. Only TABLE
        queries are supported (LIST and TASK are rejected by the API). Full
        DQL syntax: WHERE, SORT, GROUP BY, FLATTEN, functions.

        Requires the Dataview plugin to be active in Obsidian.

        Args:
            dql: Dataview TABLE query string.
                 Example: 'TABLE status FROM "System/Handoffs" WHERE note_type = "handoff" SORT file.mtime DESC LIMIT 5'

        Returns:
            {"count": int, "results": [{filename, result: {field: value}}]} on success.

        """
        client = _get_rest_client()
        result = client.post(
            "/search/",
            content=dql,
            content_type="application/vnd.olrapi.dataview.dql+txt",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        if isinstance(data, list):
            return {"count": len(data), "results": data}
        return {"count": 0, "results": [], "raw": data}

    @mcp.tool()
    def jsonlogic_search(query: dict[str, Any]) -> dict[str, Any]:
        """[REST-backed] Search vault files using a JsonLogic query.

        Evaluates the query against every file in the vault. Files are
        represented as NoteJson objects with path, frontmatter, tags, stat,
        and content fields.

        Extended operators beyond standard JsonLogic:
        - glob: {"glob": ["pattern", {"var": "path"}]}
        - regexp: {"regexp": ["pattern", {"var": "path"}]}

        Returns only non-falsy results.

        Args:
            query: JsonLogic query object.
                   Example: {"glob": ["Entities/People/*", {"var": "path"}]}

        Returns:
            {"count": int, "results": [...]} on success.

        """
        client = _get_rest_client()
        result = client.post(
            "/search/",
            json_body=query,
            content_type="application/vnd.olrapi.jsonlogic+json",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        if isinstance(data, list):
            return {"count": len(data), "results": data}
        return {"count": 0, "results": [], "raw": data}

    @mcp.tool()
    def vault_tags() -> dict[str, Any]:
        """[REST-backed] Get all tags in the vault with counts from Obsidian's live index.

        Returns tags from both inline #tag and frontmatter syntax. Hierarchical
        tags (e.g. interest/cooking) also contribute counts to parent prefixes
        (e.g. interest), mirroring Obsidian's sidebar.

        Complements the FS-based tag_glossary_check() which only reports
        violations — this tool gives the full picture.

        Returns:
            {"count": int, "tags": [{"name": str, "count": int}]}

        """
        client = _get_rest_client()
        result = client.get("/tags/")
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        data = result["data"]
        tags = data.get("tags", []) if isinstance(data, dict) else []
        return {"count": len(tags), "tags": tags}

    @mcp.tool()
    def list_directory(path: str = "") -> dict[str, Any]:
        """[REST-backed] List files in a vault directory via Obsidian's file index.

        Returns filenames and subdirectory names. Directories end with '/'.

        Args:
            path: Vault-relative directory path. Empty string for vault root.
                  Example: "System/Governance/"

        Returns:
            {"files": [str]} — filenames and subdirectory names.

        """
        client = _get_rest_client()
        endpoint = f"/vault/{path}" if path else "/vault/"
        if not endpoint.endswith("/"):
            endpoint += "/"
        result = client.get(endpoint)
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return result["data"]

    @mcp.tool()
    def open_in_obsidian(path: str, new_leaf: bool = False) -> dict[str, Any]:
        """[REST-backed] Open a file in Obsidian's editor UI.

        WARNING: If the file does not exist, Obsidian will create an empty
        file at the specified path. Verify the path exists before calling
        if file creation is not intended.

        Not a data mutation — same class as execute_command() (UI action).
        Code-only.

        Args:
            path: Vault-relative file path. Example: "System/Governance/AGENTS.md"
            new_leaf: If True, open in a new split pane. Default False.

        Returns:
            {"opened": str} on success.

        """
        client = _get_rest_client()
        params = {"newLeaf": "true"} if new_leaf else None
        result = client.post(f"/open/{path}", params=params)
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return {"opened": path}

    @mcp.tool()
    def document_map(path: str | None = None) -> dict[str, Any]:
        """[REST-backed] Get the structural map of a note (headings, blocks, frontmatter fields).

        Returns PATCH-targetable sections: heading paths, block reference IDs,
        and frontmatter field names. Useful for understanding document shape.

        Args:
            path: Vault-relative file path. If None, returns the map for the
                  currently active note. Example: "System/Governance/AGENTS.md"

        Returns:
            {"headings": [str], "blocks": [str], "frontmatterFields": [str]}

        """
        client = _get_rest_client()
        endpoint = f"/vault/{path}" if path else "/active/"
        result = client.get(
            endpoint,
            accept="application/vnd.olrapi.document-map+json",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return result["data"]


# ---------------------------------------------------------------------------
# Phase 9 — Predicate table (triple store) tools
# ---------------------------------------------------------------------------

# PHDB_DB_PATH is retired (2026-08-04) along with the direct-DB read seam it
# fed. It opened a phdb SQLite snapshot frozen 2026-06-10 whose vault_notes
# index had not been written since 2026-05-27; every verb that read it is now
# de-tooled, so the env var is no longer consulted and can be dropped from the
# service definition.
# phdb's plain-HTTP base URL — the dissolve verb POSTs typed writes here (VDV F3
# transport decision: writes go over HTTP, not the legacy direct-DB seam). Only
# /write/plan still falls through to it, and only when HADES_URL is unset; the
# monolith is gone, so that path fails fast rather than half-writing.
PHDB_HTTP_URL = os.environ.get("PHDB_HTTP_URL", "http://localhost:8101").rstrip(
    "/"
)
# The Hades gateway — strangled phdb concerns route here instead (entity
# writes -> harmonia_write_entity_typed). Unset HADES_URL = legacy phdb
# routing for everything (safe: post-deregister phdb answers 404 and the
# dissolve halts rather than half-writing).
HADES_URL = os.environ.get("HADES_URL", "").rstrip("/")
HADES_TOKEN = os.environ.get("HADES_TOKEN", "")

# ---------------------------------------------------------------------------
# RETIRED 2026-08-04 — the phdb-backed note verbs, and the phdb dependency.
#
# note_lookup / note_search / note_read / note_list are de-tooled, along with
# the two already-untooled dissolution_* helpers that shared their connection.
# With them go `_get_phdb_conn`, the module-level connection cache, PHDB_DB_PATH
# and the sys.path sibling import at the top of this file. vault-mcp no longer
# imports phdb at all.
#
# Why. They read a phdb SQLite whose vault_notes index was last written
# 2026-05-27 — two months stale, returning System/Trash entries and zero hits
# for anything created since. Worse, they shadowed live verbs under
# near-identical names: `note_read` served the frozen snapshot while `read_note`
# served the live index, so "read a note" was a coin-flip between current and
# stale, failing silently with plausible old content rather than erroring.
#
# Not rebuilt. `read_note` and `find_notes_by_frontmatter` already cover reads
# and structured lookup off the live index, and `write_note` / `move_note` go
# through the Gate. Full-text body search was the only capability without a live
# equivalent; per Rob 2026-08-04 it is deliberately NOT being rebuilt.
# ---------------------------------------------------------------------------
_RETIRED_NOTE_VERB: dict[str, Any] = {
    "error": "retired",
    "detail": (
        "The phdb-backed note verbs were retired 2026-08-04 with the PHDB "
        "dissolution; they served a vault index frozen 2026-05-27. Use "
        "read_note or find_notes_by_frontmatter (live index), or write_note / "
        "move_note (the Gate). Full-text body search was not rebuilt."
    ),
}


def _phdb_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a typed-write/declare payload to phdb's HTTP route; structured result.

    Never raises across the verb boundary — an unreachable phdb or a non-200
    becomes {"ok": False, "error": ...} so dissolve halts before deleting.

    Strangled concerns route to their sovereign star over Hades instead:
    entity writes (``/write/entity``) call ``harmonia_write_entity_typed``
    when ``HADES_URL`` is configured — same payload, same result contract.
    Atom emits (``/emit``) call Terpsichore's ``fleet_emit`` (C1), the last
    route that kept the retired monolith's :8101 surface load-bearing.
    """
    if endpoint == "/write/entity" and HADES_URL:
        from vault_mcp.hades_client import write_entity_typed

        return write_entity_typed(
            payload, url=f"{HADES_URL}/", token=HADES_TOKEN
        )

    if endpoint == "/write/document" and HADES_URL:
        from vault_mcp.hades_client import write_document

        return write_document(payload, url=f"{HADES_URL}/", token=HADES_TOKEN)

    if endpoint == "/emit" and HADES_URL:
        from vault_mcp.hades_client import emit_session_event

        return emit_session_event(
            payload, url=f"{HADES_URL}/", token=HADES_TOKEN
        )

    import httpx

    try:
        resp = httpx.post(
            f"{PHDB_HTTP_URL}{endpoint}", json=payload, timeout=30.0
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"phdb unreachable: {e}"}
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text)
        except (ValueError, KeyError, AttributeError):
            detail = resp.text
        return {"ok": False, "error": f"phdb HTTP {resp.status_code}: {detail}"}
    return resp.json()


# ---------------------------------------------------------------------------
# RETIRED 2026-08-03 — the typed-graph (triple) + file-revision verbs.
#
# The ten functions below are de-tooled: the ``@mcp.tool()`` decorator is gone
# and every phdb import and invocation with it, so each is an inert stub that
# returns ``_RETIRED_TRIPLE_VERB``. Signatures are kept so re-enabling is a
# one-line change; the bodies were deleted rather than left commented because
# this package lints under ``select = ["ALL"]`` and ERA001 is exempted only for
# tests/ and benchmarks/. Rollback is the commit that introduced this block:
# ``git show <sha>^:src/vault_mcp/server.py``.
#
# Why. They read a phdb SQLite snapshot frozen 2026-06-10, of which only 11,839
# of 13,841,482 triples (0.086 %) came from note frontmatter — the rest is
# personal corpus (7.4 M inThread, 2.4 M hasMetadata, 1.85 M hasGeoTrace,
# 934 K hasHeartRateSample). Nothing had written to it in eight weeks: vault-mcp
# never had a frontmatter->triple extractor, only the manual ``add_triple``. The
# ten verbs saw 19 calls between them across their whole lifetime, and five were
# never called at all. The ontology is now Chaos's — ``sentTo``, ``inThread``,
# ``hasChunk``, ``occurredAt``, ``locatedAt``, ``hasAttachment`` and
# ``taggedWith`` all resolve as live Chaos predicates over the Hades gateway.
#
# The note verbs that shared this connection — ``note_lookup`` / ``note_search``
# / ``note_read`` / ``note_list`` — were retired the next day, 2026-08-04, and
# took ``_get_phdb_conn`` with them. See the block below.
# ---------------------------------------------------------------------------
_RETIRED_TRIPLE_VERB: dict[str, Any] = {
    "error": "retired",
    "detail": (
        "The typed-graph and file-revision verbs were retired 2026-08-03 with "
        "the PHDB dissolution. The graph substrate is Chaos — query it over the "
        "Hades gateway (the graph_* verbs)."
    ),
}


def query_triples(
    subject: str | None = None,
    predicate: str | None = None,
    object: str | None = None,
    provenance: str | None = None,
    since: str | None = None,
    until: str | None = None,
    include_null_objects: bool | None = None,
    include_inverse: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Query the predicate table (RDF-triple store) for typed edges.

    Searches subject → predicate → object triples with optional filters.
    String arguments are resolved to node/predicate IDs automatically.

    Args:
        subject: Node label or ID to filter by subject.
        predicate: Predicate name (e.g. "childOf", "mentions") or ID.
        object: Node label or ID to filter by object.
        provenance: Filter by provenance ("extraction", "explicit", "ai-emitted").
        since: ISO-8601 datetime — only triples observed at or after this time.
        until: ISO-8601 datetime — only triples observed at or before this time.
        include_null_objects: True = only open triples, False = only complete, None = all.
        include_inverse: If True, also return triples via the inverse predicate.
        limit: Max results (default 100).

    Returns:
        {"count": int, "triples": [...]} or {"error": str} if DB unavailable.

    """
    del (
        subject,
        predicate,
        object,
        provenance,
        since,
        until,
        include_null_objects,
        include_inverse,
        limit,
    )
    return _RETIRED_TRIPLE_VERB


def add_triple(
    subject: str,
    predicate: str,
    object: str | None = None,
    observed_at: str | None = None,
    provenance: str = "explicit",
    source_ref: str | None = None,
    subject_kind: str = "concept",
    object_kind: str = "concept",
    qualifiers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Add a typed triple to the predicate table.

    Creates nodes on demand; idempotent (INSERT OR IGNORE). Uses the
    controlled predicate vocabulary seeded by migration 0012.

    Args:
        subject: Subject node label (resolved or created automatically).
        predicate: Predicate name (e.g. "childOf", "mentions", "wantsTo").
        object: Object node label. Omit for an open/incomplete triple.
        observed_at: ISO-8601 datetime when the relationship held.
        provenance: One of "extraction", "explicit", "ai-emitted".
        source_ref: File path or session ID for audit trail.
        subject_kind: Node kind for subject ("file", "concept", "person", etc.).
        object_kind: Node kind for object.
        qualifiers: Optional list of {"key": str, "value": str} qualifier pairs.

    Returns:
        {"triple_id": int, "created": bool, ...} or {"error": str}.

    """
    del (
        subject,
        predicate,
        object,
        observed_at,
        provenance,
        source_ref,
        subject_kind,
        object_kind,
        qualifiers,
    )
    return _RETIRED_TRIPLE_VERB


def delete_triple(
    triple_id: int,
    prune_orphan_nodes: bool = False,
) -> dict[str, Any]:
    """Delete a triple by ID (idempotent). Qualifiers cascade automatically.

    Use ``query_triples`` to find the triple_id first, then pass it here.

    Args:
        triple_id: The ID of the triple to delete.
        prune_orphan_nodes: If True, also delete subject/object nodes that
            have no remaining triples after this deletion.

    Returns:
        {"deleted": bool, "triple_id": int, "nodes_pruned": int} or {"error": str}.

    """
    del triple_id, prune_orphan_nodes
    return _RETIRED_TRIPLE_VERB


def delete_node(
    node_id: int,
    cascade: bool = False,
) -> dict[str, Any]:
    """Delete a node by ID. Refuses if the node has triples unless cascade=True.

    Args:
        node_id: The ID of the node to delete.
        cascade: If True, also delete all triples referencing this node.

    Returns:
        {"deleted": bool, "node_id": int, ...} or {"error": str}.

    """
    del node_id, cascade
    return _RETIRED_TRIPLE_VERB


def triple_stats() -> dict[str, Any]:
    """Get summary statistics for the predicate table.

    Returns node count, triple count, null-object count, qualifier count,
    predicate usage breakdown, and node-kind distribution.

    Returns:
        {"nodes": int, "triples": int, ...} or {"error": str}.

    """
    return _RETIRED_TRIPLE_VERB


def node_neighborhood(
    node: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Get the 1-hop neighborhood of a node in the triple graph.

    Returns the node's metadata plus all outgoing and incoming triples.

    Args:
        node: Node label to look up.
        limit: Max triples per direction (default 50).

    Returns:
        {"node": {...}, "outgoing": [...], "incoming": [...]} or {"error": str}.

    """
    del node, limit
    return _RETIRED_TRIPLE_VERB


def list_predicates() -> dict[str, Any]:
    """List all predicates in the controlled vocabulary.

    Returns:
        {"count": int, "predicates": [...]} or {"error": str}.

    """
    return _RETIRED_TRIPLE_VERB


# ---------------------------------------------------------------------------
# Phase 6 of Git for Ideas — file_revisions tools (migration 0039)
# ---------------------------------------------------------------------------


def materialize_revision(
    rev_id: int,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Materialize the body of a file_revisions row via `git cat-file -p`.

    Bodies are not stored in phdb — the revision row references a git
    blob SHA, and this tool shells out to git to retrieve it. Constant
    time per call (git's content-addressable storage).

    Args:
        rev_id: Primary key of the file_revisions row.
        repo_root: Override the repo checkout path. Defaults to the
            value of commit_authorship_repos.repo_path for the
            revision's repo (typically the vault repo).

    Returns:
        {"rev_id": int, "file_path": str, "body": str, ...} on success,
        {"error": str, "detail": str} on miss.

    """
    del rev_id, repo_root
    return _RETIRED_TRIPLE_VERB


def list_file_revisions(
    file_path: str,
    repo: str = "vault",
    limit: int = 50,
) -> dict[str, Any]:
    """List chronological revision history for one vault path.

    Args:
        file_path: Vault-relative POSIX path (forward slashes).
        repo: Repo name (default "vault").
        limit: Max rows; pass 0 for the full history.

    Returns:
        {"count": int, "revisions": [...]} or {"error": str}.

    """
    del file_path, repo, limit
    return _RETIRED_TRIPLE_VERB


def revision_triple_deltas(rev_id: int) -> dict[str, Any]:
    """Return predicate-graph edges added/removed by one revision.

    Each delta row records the op ('add' | 'remove'), the three node
    IDs, and human-readable labels where resolvable. Use to trace how
    a single commit reshaped the typed-graph for one note.

    Args:
        rev_id: Primary key of the file_revisions row.

    Returns:
        {"count": int, "deltas": [...]} or {"error": str}.

    """
    del rev_id
    return _RETIRED_TRIPLE_VERB


# ---------------------------------------------------------------------------
# Phase 6 of Dissolution Tracking — registry lookup tools (migration 0041)
# ---------------------------------------------------------------------------


# RETIRED 2026-07-04 (C5, the dissolution-bridge retirement): de-tooled — the
# bridge's registries are ARCHIVED on Calliope (archive_dissolutions /
# archive_file_revision_dissolutions / archive_materialization_events, count
# parity 441/935/31); historical queries go to the archive tables. Rollback =
# re-register: restore the @mcp.tool() decorator.
def dissolution_lookup(vault_path: str, repo: str = "vault") -> dict[str, Any]:
    """Full lifecycle for a vault path — dissolution + materialization events.

    Returns dissolution waves and materialization events linked to the
    given path, ordered chronologically. Use to answer "what happened
    to this file?" — dissolved to which DB tables, when, and whether
    it's been materialized back as a stub since.

    Args:
        vault_path: Vault-relative POSIX path (forward slashes).
        repo: Repo name (default "vault").

    Returns:
        {"file_path": ..., "repo": ..., "dissolutions": [...],
         "materializations": [...], "lifecycle": [...]} or {"error": ...}.

    """
    del vault_path, repo
    return _RETIRED_NOTE_VERB


# RETIRED 2026-07-04 (C5): de-tooled (see dissolution_lookup). REPOINTED
# 2026-07-17 (C6): the retired `phdb.dissolutions` import is gone — the
# go-forward record of what dissolved when is the Calliope documents table
# (source_path + created_at + dedup), so this reads Calliope's `read_documents`
# over Hades and projects the rows into the wave view. Kept de-tooled (the MCP
# surface stays retired per C5); the repoint is so the VDV audit code no longer
# imports a dead module.
def list_dissolution_waves(
    repo: str = "vault", limit: int = 200
) -> dict[str, Any]:
    """Wave-level browse over the dissolution record — now Calliope-backed.

    Args:
        repo: Registry repo scope (retained for signature compatibility).
        limit: Max rows to read from Calliope (newest first).

    Returns:
        {"count": int, "waves": [...]} — one wave per stored Calliope document,
        or {"error": ...} when Calliope is not the wired target.

    """
    _ = repo
    if not HADES_URL:
        return {
            "error": "hades_url_unset",
            "detail": (
                "HADES_URL unset — the dissolution record lives in Calliope "
                "(the phdb.dissolutions registry is retired). Set HADES_URL."
            ),
        }
    from vault_mcp.carve import documents_to_waves
    from vault_mcp.hades_client import call_verb

    res = call_verb(
        "read_documents",
        {"limit": limit, "omit_body": True},
        url=f"{HADES_URL}/",
        token=HADES_TOKEN,
    )
    if res.get("ok") is False:
        return {
            "error": "calliope_read_failed",
            "detail": str(res.get("error")),
        }
    waves = documents_to_waves(res.get("documents") or [])
    return {"count": len(waves), "waves": waves}


# RETIRED 2026-07-04 (C5): de-tooled (see dissolution_lookup).
def dissolution_for_revision(rev_id: int) -> dict[str, Any]:
    """Given a file_revisions row id, return its dissolution (if any).

    Args:
        rev_id: Primary key of the file_revisions row.

    Returns:
        Dissolution dict on hit, {"dissolution": None} when the
        revision is not linked to any dissolution, or
        {"error": str} on lookup failure.

    """
    del rev_id
    return _RETIRED_NOTE_VERB


# ---------------------------------------------------------------------------
# Phase 8 of Git for Ideas — vault_notes tools (migration 0042)
# ---------------------------------------------------------------------------


def note_lookup(query: str) -> dict[str, Any]:
    """Exact-match lookup of a vault note by name or file_path.

    Returns the full note record including body text, frontmatter
    metadata, and lifecycle status. Use when you know the exact note
    name or path.

    Args:
        query: Note name (filename stem or frontmatter `name:`) or
            vault-relative POSIX path.

    Returns:
        Full note dict on hit, {"result": None} on miss, or {"error": ...}.

    """
    del query
    return _RETIRED_NOTE_VERB


def note_search(query: str, limit: int = 20) -> dict[str, Any]:
    """Full-text search over vault notes (name, description, body).

    Uses SQLite FTS5 with porter stemming. Returns ranked results
    with body snippets. Use for keyword/topic discovery across the
    vault's current state.

    Args:
        query: FTS5 query string (supports AND, OR, NOT, phrase quotes).
        limit: Max results (default 20).

    Returns:
        {"count": int, "results": [...]} with snippet per hit.

    """
    del query, limit
    return _RETIRED_NOTE_VERB


def note_read(name_or_path: str) -> dict[str, Any]:
    """Read the full body text of one vault note.

    Returns the complete markdown content including frontmatter.
    Use when you need to read a specific note's content without
    filesystem access.

    Args:
        name_or_path: Note name or vault-relative POSIX path.

    Returns:
        {"name_or_path": str, "body": str} on hit,
        {"result": None} on miss.

    """
    del name_or_path
    return _RETIRED_NOTE_VERB


def note_list(
    status: str | None = None,
    at_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse vault notes with optional filters.

    Returns a lightweight listing (no body text) sorted by most
    recently updated. Use for browsing by status or @type.

    Args:
        status: Filter by lifecycle status ('live', 'dissolved', 'deleted').
        at_type: Filter by Schema.org @type (e.g., 'Person', 'CreativeWork').
        limit: Max results (default 50).

    Returns:
        {"count": int, "notes": [...]}.

    """
    del status, at_type, limit
    return _RETIRED_NOTE_VERB


# ---------------------------------------------------------------------------
# vault-mcp v2 — Convention Gate write tools (schema-driven, provenance-stamped)
# ---------------------------------------------------------------------------


def _prune_empty_parents(child_path: Path, root: Path) -> list[str]:
    """Walk up from child_path's parent, removing empty dirs until root."""
    pruned: list[str] = []
    d = child_path.parent
    while d != root and d.is_relative_to(root):
        try:
            if not any(d.iterdir()):
                d.rmdir()
                pruned.append(str(d.relative_to(root)))
            else:
                break
        except OSError:
            break
        d = d.parent
    return pruned


_gate: ConventionGate | None = None
_compute_receiver: ComputeReceiver | None = None


def _get_gate() -> ConventionGate:
    """Lazily build the Convention Gate from VAULT_MCP_SCHEMA + the Obsidian REST API.

    Writes go through the REST API (HTTP on loopback) rather than the CLI: as a
    session-0 service vault-mcp cannot reach the desktop (session-1) Obsidian via
    the CLI's same-session IPC, but the REST API crosses that boundary.
    """
    global _gate
    if _gate is None:
        from vault_mcp.gate import ConventionGate
        from vault_mcp.rest_client import RestNoteIO
        from vault_mcp.schema import load_schema

        _gate = ConventionGate(load_schema(), RestNoteIO(_get_rest_client()))
    return _gate


# ---------------------------------------------------------------------------
# Single-writer git committer (VG initiative — vault-mcp owns vault git)
# ---------------------------------------------------------------------------
_committer: GitCommitter | None = None


def _get_committer() -> GitCommitter:
    """Lazily build the git committer from VAULT_MCP_GIT_* env, rooted at the vault.

    Commit + push default OFF (flip VAULT_MCP_GIT_COMMIT=1 / VAULT_MCP_GIT_PUSH=1
    once proven), so this code is inert until the flag is set.
    """
    global _committer
    if _committer is None:
        from vault_mcp.gitops import committer_from_env

        _committer = committer_from_env(VAULT_PATH)
    return _committer


def _commit_write(
    result: dict[str, Any],
    op: str,
    commit_message: str | None = None,
    *,
    is_delete: bool = False,
) -> dict[str, Any]:
    """Commit a successful Gate write and attach ``commit_sha`` (the checkpoint handshake).

    Fail-safe: a commit failure leaves the write intact, sha None.
    A caller-supplied ``commit_message`` is used verbatim; otherwise a
    ``vault: {op} {path}`` fallback is generated. The in-flight markers let the
    scheduled sweep defer rather than race this per-transaction commit.
    """
    if not result.get("ok"):
        return result
    path = result.get("path")
    if not isinstance(path, str) or not path:
        return result
    committer = _get_committer()
    message = commit_message or f"vault: {op} {path}"
    committer.begin_write()
    try:
        result["commit_sha"] = committer.commit_paths(
            [path], message, wait_for_create=not is_delete
        )
    finally:
        committer.end_write()
    return result


def _start_sweep_scheduler() -> None:
    """Start the whole-tree sweep daemon (captures human Obsidian edits).

    No-op unless VAULT_MCP_GIT_COMMIT=1. Interval via VAULT_MCP_GIT_SWEEP_SECONDS
    (default 3600s). Sweep commits are checkpoint-silent by design.
    """
    import time as _time
    from datetime import datetime

    log = logging.getLogger(__name__)
    committer = _get_committer()
    if not committer.enabled:
        print(
            "vault-mcp: git sweep disabled (VAULT_MCP_GIT_COMMIT != 1)",
            file=sys.stderr,
        )
        return
    interval = int(os.environ.get("VAULT_MCP_GIT_SWEEP_SECONDS", "3600"))

    def _loop() -> None:
        while True:
            _time.sleep(interval)
            try:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")  # noqa: DTZ005 — local stamp for a commit message
                res = committer.sweep_commit(f"vault: periodic sweep {stamp}")
                if res.get("committed"):
                    log.info(
                        "git sweep committed %s (pushed=%s)",
                        res.get("sha"),
                        res.get("pushed"),
                    )
            except Exception:
                log.exception("git sweep tick failed")

    threading.Thread(target=_loop, name="vault-git-sweep", daemon=True).start()
    push_state = "on" if committer.push_enabled else "off"
    print(
        f"vault-mcp: git sweep every {interval}s (push={push_state})",
        file=sys.stderr,
    )


def _load_templates() -> dict[str, str]:
    d = os.environ.get("VAULT_MCP_TEMPLATES")
    if not d:
        return {}
    base = Path(d)
    if not base.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in base.glob("*.md")}


def _get_compute_receiver() -> ComputeReceiver:
    global _compute_receiver
    if _compute_receiver is None:
        from vault_mcp.compute import ComputeReceiver

        _compute_receiver = ComputeReceiver(_get_gate(), _load_templates())
    return _compute_receiver


_materializer: Materializer | None = None


def _get_materializer() -> Materializer:
    global _materializer
    if _materializer is None:
        from vault_mcp.lifecycle import Materializer

        _materializer = Materializer(_get_gate(), _load_templates())
    return _materializer


def _gate_error_envelope(exc: Exception) -> dict[str, Any]:
    """Map Gate/schema/IO exceptions to a structured tool error."""
    from vault_mcp.cli_client import ObsidianIOError
    from vault_mcp.gate import GateError
    from vault_mcp.schema import RouteError, SchemaError

    if isinstance(exc, SchemaError) and not isinstance(exc, RouteError):
        return {"ok": False, "error": "schema_unavailable", "detail": str(exc)}
    if isinstance(exc, RouteError):
        return {"ok": False, "error": "no_route", "detail": str(exc)}
    if isinstance(exc, GateError):
        return {"ok": False, "error": "rejected", "detail": str(exc)}
    if isinstance(exc, ObsidianIOError):
        return {"ok": False, "error": "io_error", "detail": str(exc)}
    raise exc


@mcp.tool()
def write_note(
    title: str,
    note_type: str | None = None,
    pillar: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
    mode: str = "upsert",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Create-or-update a vault note through the Convention Gate — the write surface.

    Resolves the target path from title + routing, then creates a missing note or
    updates an existing one (the caller need not know which). The Gate generates
    correct frontmatter, validates against the closed tag glossary and per-type
    rules, enforces write-protection, and stamps provenance. On update, body/tags/
    fields left null are untouched; a pre-existing invalid value in a field this
    write does not touch surfaces as a warning, not a rejection.

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing (e.g., 'note').
        pillar: Schema pillar used for routing (e.g., 'Knowledge').
        body: Markdown body. Null on update leaves the existing body untouched.
        tags: Tags; each must be in the closed glossary. Null on update = untouched.
        fields: Extra frontmatter fields (required / constrained values, etc.).
        actor: 'agent' (default) or 'human' — drives the provenance stamp.
        mode: 'upsert' (default), 'create' (refuse if exists), 'update' (refuse if missing).
        commit_message: Commit message recording this write; auto-generated when omitted.

    Returns:
        {"ok": True, "path", "frontmatter", "created", "warnings", ...} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.write_note(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags,
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
            mode=mode,
        )
        return _commit_write(
            result.to_dict(),
            "create" if result.created else "update",
            commit_message,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def delete(
    path: str, actor: str = "agent", commit_message: str | None = None
) -> dict[str, Any]:
    """Move a vault note to Obsidian's .trash/ (reversible) through the Gate.

    Enforces the same write-protection as a write — an agent cannot trash a
    voice-only, compute-only, fully-immutable, or body-immutable note. The note
    remains recoverable from Obsidian's trash.

    Returns:
        {"ok": True, "path", "deleted": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.delete(
            path, actor=Actor.HUMAN if actor == "human" else Actor.AGENT
        )
        committed = _commit_write(
            result, "delete", commit_message, is_delete=True
        )
        if committed.get("ok"):
            abs_path = (VAULT_PATH / path).resolve()
            committed["dirs_pruned"] = _prune_empty_parents(
                abs_path, VAULT_PATH
            )
        return committed
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def move_note(
    src: str,
    dst: str,
    actor: str = "agent",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Move a vault note from src to dst, preserving content and frontmatter.

    Reads the source note, writes it to the destination path, then deletes the
    source. Checks write-protection on both directories. Does NOT update
    backlinks — use ``backlinks_to`` to enumerate inbound references and update
    them separately via ``write_note``.

    Args:
        src: Vault-relative source path (e.g. 'Software/README.md').
        dst: Vault-relative destination path (e.g. 'References/README.md').
        actor: 'agent' (default) or 'human'.
        commit_message: Optional git commit message.

    Returns:
        {"ok": True, "src", "dst", "moved": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.move_note(
            src,
            dst,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
        committed = _commit_write(result, "move", commit_message)
        if committed.get("ok"):
            abs_src = (VAULT_PATH / src).resolve()
            committed["dirs_pruned"] = _prune_empty_parents(abs_src, VAULT_PATH)
        return committed
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def lint(
    title: str = "",
    note_type: str | None = None,
    pillar: str | None = None,
    body: str = "",
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
) -> dict[str, Any]:
    """Dry-run a note payload through the Convention Gate's validator — no write.

    Runs the full validation pipeline and returns every finding at once
    (collect-all), so a caller can assemble -> lint -> fix -> write without
    touching disk. Each finding carries a code, the offending field/value, a
    message, and a severity ('error' blocks a write; 'warning' is advisory).

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing.
        pillar: Schema pillar used for routing.
        body: Markdown body.
        tags: Tags to validate against the closed glossary.
        fields: Extra frontmatter fields (e.g. required/constrained values).
        actor: 'agent' (default) or 'human'.

    Returns:
        {"ok": bool, "errors": [...], "warnings": [...]} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        return gate.lint_payload(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags or [],
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def list_types() -> dict[str, Any]:
    """List every schema @type with its write-mode and required-field summary.

    Returns:
        {"ok": True, "types": [{"name", "write_mode", "required", "body_empty", "atom_slug"}, ...]}

    """
    try:
        return {"ok": True, "types": _get_gate()._schema.list_types()}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def list_tags() -> dict[str, Any]:
    """List the closed tag glossary, grouped by prefix.

    Returns:
        {"ok": True, "tags": {"<prefix>": ["<prefix>/<leaf>", ...], ...}}

    """
    try:
        return {"ok": True, "tags": _get_gate()._schema.list_tags()}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def list_keys() -> dict[str, Any]:
    """List the global union of frontmatter property keys across all @types.

    Returns:
        {"ok": True, "keys": ["<key>", ...]}

    """
    try:
        return {"ok": True, "keys": _get_gate()._schema.list_keys()}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def query(note_type: str) -> dict[str, Any]:
    """Describe a note type's authoring contract — the spec sheet for a @type.

    Returns the fields a caller may set, their value constraints and formats,
    freeform fields, where the type routes (including discriminators), and body
    guidance (schema ``body_guidance`` plus a Templater template pointer when one
    exists). Use it to assemble a compliant note without reading governance prose.

    Args:
        note_type: The schema @type to describe (e.g., 'Person').

    Returns:
        {"ok": True, ...spec...} or {"ok": False, "error": "unknown_type"}.

    """
    try:
        spec = _get_gate()._schema.describe_type(note_type)
        if spec is None:
            return {
                "ok": False,
                "error": "unknown_type",
                "detail": f"no @type {note_type!r} in the schema",
            }
        templates = (
            _load_templates()
        )  # the one vault-read: locate a matching template
        spec["body_template"] = note_type if note_type in templates else None
        return {"ok": True, **spec}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def audit(
    directory: str = "", resolve: bool = False, all_dirs: bool = False
) -> dict[str, Any]:
    """Scan a vault directory for schema drift; optionally auto-correct it.

    resolve=False reports every drifted note (missing required fields, unknown
    tags, deprecated/dead keys, off-vocabulary values, routing misplacement,
    untyped notes) and writes nothing. resolve=True applies deterministic
    corrections (key renames, dead-key drops, status repairs, routing
    relocation), re-lints the corrected note, and rewrites it only when clean —
    uncorrectable faults are reported for manual handling, never guessed.
    Healed writes land through the Gate (Obsidian) and are captured by the
    git-sweep daemon.

    Args:
        directory: Pillar/directory to scan (e.g., 'Inbox'). Ignored if all_dirs.
        resolve: False = report only; True = heal what is deterministically fixable.
        all_dirs: Scan the whole vault instead of a single directory.

    Returns:
        {"ok": True, "scanned", "drifted", "corrected", "notes": [...]} or an error.

    """
    try:
        return _get_gate().audit(directory, resolve=resolve, all_dirs=all_dirs)
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def dissolve(
    path: str,
    plan_slug: str,
    rationale: str,
    declared_by: str = "code",
    repo: str = "vault",
) -> dict[str, Any]:
    """Dissolve a vault note into phdb and remove the original (VDV F3).

    Writes the note's full prose verbatim to phdb's documents table (and, for a
    ``note_type: Plan``, its metadata to plans) over HTTP, declares a dissolution
    wave, then deletes the vault file — ordering is write -> verify -> declare ->
    delete, so a failure never deletes the file and a re-run is idempotent (phdb
    dedups on content). The body is passed through verbatim (no fence-extraction).

    Args:
        path: Vault-relative path to the note (e.g. 'System/Plans/Foo.md').
        plan_slug: The dissolution wave's driving plan identifier.
        rationale: Why this content is dissolved (recorded on the wave).
        declared_by: 'code' / 'cowork' / 'backfill'.
        repo: Registry repo scope (default 'vault').

    Returns:
        {"ok": True, "written": [...], "dissolution_id", "deleted": True}, or a
        structured error naming the failing stage (the file is left in place).

    """
    from vault_mcp.lifecycle_verbs import dissolve_note

    abs_path = (VAULT_PATH / path).resolve()
    try:
        abs_path.relative_to(VAULT_PATH.resolve())
    except ValueError:
        return {"ok": False, "error": "outside_vault", "detail": path}
    if not abs_path.is_file():
        return {"ok": False, "error": "not_found", "detail": str(abs_path)}

    raw_text = abs_path.read_text(encoding="utf-8")
    res = dissolve_note(
        source_path=str(abs_path),
        raw_text=raw_text,
        file_path=abs_path.name,
        vault_rel_path=path,
        plan_slug=plan_slug,
        rationale=rationale,
        post=_phdb_post,
        delete_file=abs_path.unlink,
        declared_by=declared_by,
        repo=repo,
    )
    # Commit the deletion through the single writer (the file is already gone, so
    # skip the disk-landing poll); attach the sha for the checkpoint handshake.
    if res.get("ok"):
        committer = _get_committer()
        committer.begin_write()
        try:
            res["commit_sha"] = committer.commit_paths(
                [path], f"vault: dissolve {path}", wait_for_create=False
            )
        finally:
            committer.end_write()
        res["dirs_pruned"] = _prune_empty_parents(abs_path, VAULT_PATH)
    return res


def _vault_md_paths() -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` in the vault."""
    root = VAULT_PATH.resolve()
    for p in sorted(root.rglob("*.md")):
        yield p.relative_to(root).as_posix()


@mcp.tool()
def dissolve_sweep(
    dry_run: bool = True,
    limit: int | None = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Bulk-carve the vault into Calliope under the scope policy (C6).

    Takes the one-note ``dissolve`` (write -> verify -> delete, idempotent,
    fail-safe) to a whole-vault sweep gated by a scope policy — governance /
    System stays; the named prose/records/entities pillars dissolve.

    **DRY-RUN by default.** A dry run classifies every ``.md`` and reports what
    *would* dissolve, touching nothing. A LIVE sweep (``dry_run=False``) is
    DESTRUCTIVE — it deletes each dissolvable file after a verified Calliope
    write — and additionally requires ``confirm="DISSOLVE"``.

    Pre-flight refuses the sweep when ``HADES_URL`` is unset (the documented
    trap: the dissolve leg would fall back to the retired phdb
    ``/write/document`` and halt). The scope policy is CONFIG
    (``carve_policy.DEFAULT_SCOPE_POLICY``); the exact pillar cut and the
    Obsidian vestigial-vs-retired end-state are OPEN Rob-decisions, surfaced in
    the report's ``open_decisions``. ``limit`` caps acted-on files (bounded
    live batches).

    Returns the structured :class:`~vault_mcp.carve.CarveReport`.
    """
    from vault_mcp.carve import bulk_carve, carve_preflight
    from vault_mcp.carve_policy import DEFAULT_SCOPE_POLICY

    pf = carve_preflight(hades_url=HADES_URL)
    if not pf.ok:
        return bulk_carve(
            list_files=_vault_md_paths,
            dissolve_one=lambda _p: {"ok": False, "error": "preflight"},
            policy=DEFAULT_SCOPE_POLICY,
            dry_run=dry_run,
            preflight=pf,
            limit=limit,
        ).to_dict()

    if not dry_run and confirm != "DISSOLVE":
        return {
            "ok": False,
            "error": "confirm_required",
            "detail": (
                "A live sweep DELETES vault files after writing them to "
                "Calliope. Re-run with dry_run=False and confirm='DISSOLVE'."
            ),
        }

    def _dissolve_one(rel_path: str) -> dict[str, Any]:
        return dissolve(
            path=rel_path,
            plan_slug="vault-carve",
            rationale="C6 bulk vault carve into Calliope",
        )

    report = bulk_carve(
        list_files=_vault_md_paths,
        dissolve_one=_dissolve_one,
        policy=DEFAULT_SCOPE_POLICY,
        dry_run=dry_run,
        preflight=pf,
        limit=limit,
    )
    return report.to_dict()


def _plan_md_paths(directory: str) -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` under *directory*."""
    root = VAULT_PATH.resolve()
    base = (root / directory).resolve()
    if not base.is_dir():
        return
    if not base.is_relative_to(root):  # never escape the vault
        return
    for p in sorted(base.rglob("*.md")):
        yield p.relative_to(root).as_posix()


def _read_stored_copy(source_path: str) -> StoredCopy | None:
    """Newest stored copy for *source_path*, as the freshness engine's projection."""
    from vault_mcp.hades_client import read_document_by_source_path
    from vault_mcp.plan_freshness import StoredCopy

    res = read_document_by_source_path(
        source_path, url=f"{HADES_URL}/", token=HADES_TOKEN
    )
    if res.get("ok") is False:
        raise RuntimeError(str(res.get("error", "read_documents failed")))
    docs = res.get("documents") or []
    if not docs:
        return None
    newest = docs[0]  # Calliope answers newest-first
    body = newest.get("body_text") or ""
    return StoredCopy(
        source_path=source_path,
        raw_hash=str(newest.get("raw_hash") or ""),
        body_bytes=len(body.encode("utf-8")),
    )


def _build_plan_payload(source_path: str, raw_text: str) -> dict[str, Any]:
    """Build the ``write_document`` payload for one plan.

    This is the seam F2 (``mtime`` from the source file) and F3 (``schema_type``
    from the source's ``note_type``) extend — the comparison engine and the verb
    are untouched by either.
    """
    from vault_mcp.parsers import parse_frontmatter, strip_frontmatter
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    frontmatter = parse_frontmatter(raw_text)
    body = strip_frontmatter(raw_text)
    file_path = str(VAULT_PATH.resolve() / source_path)
    payloads = note_to_payloads(
        frontmatter, body, source_path, file_path=file_path
    )
    doc = next(
        (p["payload"] for p in payloads if p["endpoint"] == DOC_ENDPOINT), None
    )
    if doc is None:
        msg = f"{source_path} does not translate to a document payload"
        raise ValueError(msg)
    return doc


@mcp.tool()
def plan_freshness(
    refresh: bool = False,
    directory: str = "System/Pantheon/WBS",
    source_path: str | None = None,
    include_missing: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Report (and optionally repair) drift between vault plans and their stored copies.

    A master-plan lives in the vault AND as a copy in Calliope's document store,
    which ``athena orchestrate_plan`` reads by reference. The store versions
    insert-only, so a re-write *would* land a new version — but nothing ever
    issued one, and stored copies are frozen at first write. Measured
    2026-08-10: the Aglaia plan's copy was 22 days and 18,584 bytes behind disk,
    which is why its A21/A22 amendment never reached the board.

    **REPORT-ONLY by default.** ``refresh=True`` writes the vault's current bytes
    to the store as a new version for each drifted or missing plan. Unlike
    ``dissolve_sweep`` this is **not destructive and takes no confirm token**: it
    never creates, modifies, moves or deletes a vault file under any argument,
    and the store is insert-only, so a refresh appends and converges. Direction
    is an invariant — vault to store, never the reverse.

    A plan already current is never written. One plan's failure is reported
    against that plan and the sweep continues.

    ``refresh`` repairs **drift only**. The store is not a mirror of the vault —
    ``carve_policy`` keeps ``System/`` in the vault, so a plan reaches the store
    only when something explicitly puts it there. Measured 2026-08-11: 173 of
    175 WBS plans have no stored copy at all, so a refresh that also populated
    would push ~10MB of prose into the store as a side effect of a freshness
    repair. Populating is a separate, deliberate act: ``include_missing=True``.

    Args:
        refresh: Write drifted copies (default False — report only).
        directory: Vault-relative directory to sweep.
        source_path: Sweep exactly this one plan instead of the directory.
        include_missing: Also write plans with no stored copy (populate).
        limit: Cap the number of plans acted on.

    Returns:
        The sweep report: ``{ok, dry_run, directory, scanned, refreshed,
        counts, records[]}``.

    """
    from vault_mcp.carve import carve_preflight
    from vault_mcp.hades_client import write_document
    from vault_mcp.plan_freshness import sweep_plans

    pf = carve_preflight(hades_url=HADES_URL)

    def _list(d: str) -> Iterator[str]:
        if source_path is not None:
            yield source_path
        else:
            yield from _plan_md_paths(d)

    def _read_vault(p: str) -> str:
        return (VAULT_PATH.resolve() / p).read_text(encoding="utf-8")

    def _write(payload: dict[str, Any]) -> dict[str, Any]:
        return write_document(payload, url=f"{HADES_URL}/", token=HADES_TOKEN)

    report = sweep_plans(
        list_files=_list,
        read_vault=_read_vault,
        read_stored=_read_stored_copy,
        build_payload=_build_plan_payload,
        write_stored=_write,
        directory=directory,
        refresh=refresh,
        include_missing=include_missing,
        limit=limit,
        preflight=pf.to_dict(),
    )
    return report.to_dict()


def _read_dissolved_row(table: str, row_id: int) -> dict[str, Any]:
    """Read a dissolved row for materialize — from Calliope, not the dead phdb (C6).

    Reverse-symmetry: dissolve writes documents to Calliope (over Hades when
    ``HADES_URL`` is set), so un-dissolve READS from Calliope too — the retired
    phdb is never the go-forward source. When ``HADES_URL`` is set, a
    ``documents`` read routes to Calliope's ``read_documents`` and the row is
    normalized to the phdb documents-row shape the mapper expects. With
    ``HADES_URL`` unset (or for the legacy ``plans`` table, which has no Calliope
    home), it falls back to the phdb HTTP read. Returns
    ``{ok, row, paired_body}`` or ``{ok: False, error, detail}``.
    """
    if table == "documents" and HADES_URL:
        from vault_mcp.hades_client import read_document
        from vault_mcp.translator import calliope_document_to_row

        res = read_document(row_id, url=f"{HADES_URL}/", token=HADES_TOKEN)
        if res.get("ok") is False:
            return {
                "ok": False,
                "error": "calliope_read_failed",
                "detail": str(res.get("error")),
            }
        docs = res.get("documents") or []
        if not docs:
            return {
                "ok": False,
                "error": "row_not_found",
                "detail": f"documents#{row_id} (Calliope)",
            }
        return {
            "ok": True,
            "row": calliope_document_to_row(docs[0]),
            "paired_body": None,
        }

    import httpx

    try:
        resp = httpx.get(
            f"{PHDB_HTTP_URL}/read/{table}", params={"id": row_id}, timeout=30.0
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"phdb unreachable: {e}"}
    if resp.status_code == 404:
        return {
            "ok": False,
            "error": "row_not_found",
            "detail": f"{table}#{row_id}",
        }
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"phdb HTTP {resp.status_code}",
            "detail": resp.text,
        }
    data = resp.json()
    return {
        "ok": True,
        "row": data.get("row") or {},
        "paired_body": data.get("paired_body"),
    }


@mcp.tool()
def materialize(table: str, row_id: int) -> dict[str, Any]:
    """Materialize a dissolved row back into a Convention-Gate note (VDV F3).

    The inverse of ``dissolve``: reads a typed row from the go-forward prose
    store — **Calliope** (C6 reverse-symmetry), not the retired phdb — and writes
    it back through the shared ``Materializer`` with ``mode=COMPUTE``, the
    sanctioned path for materialize-only ``@type``s (e.g. ``Plan``) that ordinary
    agent-create rejects. The target directory is resolved from the note_type's
    schema route. Returns the Gate write result or a structured error.

    Shares the ``Materializer`` backend with ``compute_receiver`` (which renders a
    fresh compute payload); this verb builds the payload from a dissolved row.
    """
    from vault_mcp.translator import row_to_payload

    if table not in ("documents", "plans"):
        return {"ok": False, "error": "bad_table", "detail": table}

    # 1. Read the row from Calliope (documents) / phdb fallback (plans).
    read = _read_dissolved_row(table, row_id)
    if not read.get("ok"):
        return read
    row = read["row"]
    paired_body: str | None = read.get("paired_body")

    note_type = (
        "Plan"
        if table == "plans"
        else (row.get("schema_type") or "DigitalDocument")
    )

    try:
        # 2. Resolve the target directory from the note_type's schema route.
        directory = _get_gate()._schema.resolve_directory(note_type)
        # 3. Build the Materializer payload and write via mode=COMPUTE.
        payload = row_to_payload(
            row, table, directory=directory, paired_body=paired_body
        )
        result = _get_materializer().materialize(payload)
        return _commit_write(result.to_dict(), "materialize")
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes
        return _gate_error_envelope(exc)


@mcp.tool()
def compute_receive(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Render a structured compute payload into an ai-computed vault note.

    Accepts a payload (template, title, directory, data, frontmatter) from a
    periodic compute job, renders it through a named template (pure
    substitution, no LLM), and writes it via the Gate's compute-only path.

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.compute import ComputePayloadError

    try:
        result = _get_compute_receiver().receive(payload, created=created)
        return _commit_write(result.to_dict(), "compute")
    except ComputePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def compute_receiver(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Receive a compute result and write it as a durable note (Compute Receiver).

    Accepts a structured payload (title, note_type, directory, body, frontmatter,
    optional template/data) and writes it through the Gate with mode=COMPUTE —
    the sanctioned path for materialize-only @types, which the ordinary
    agent-create path rejects. Rendering is deterministic (no LLM): the same
    payload yields a byte-identical note.

    (Renamed from ``materialize`` — that verb now belongs to the VDV dissolve
    inverse, which rehydrates a dissolved phdb row back into a note.)

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.lifecycle import MaterializePayloadError

    try:
        result = _get_materializer().materialize(payload, created=created)
        return _commit_write(result.to_dict(), "compute")
    except MaterializePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - mapped to structured envelopes below
        return _gate_error_envelope(exc)


@mcp.tool()
def atom(
    atom_type: str,
    payload: dict[str, Any],
    ts: str | None = None,
) -> dict[str, Any]:
    """Emit an AI-observed atom (decision/reversal/tension/pushback) to phdb.

    Records the atom directly into phdb's session_events table, skipping the
    vault filesystem entirely (lifecycle verb). The payload contract is
    per-type. When phdb is not configured (PHDB_DB_PATH unset or the DB file is
    missing), returns a structured 'phdb_unavailable' error rather than failing.

    Args:
        atom_type: One of 'decision', 'reversal', 'tension', 'pushback'.
        payload: Type-specific fields — decision: {polarity, reversed_by?};
            reversal: {reverses, position_before, position_after, trigger?,
            captured_when?}; tension: {position_a, position_b, held_since?,
            resolution?, captured_when?}; pushback: {from, challenge, response,
            position_changed?, captured_when?}.
        ts: Optional ISO-8601 event time; defaults to the payload's
            captured_when when present.

    Returns:
        {"ok": True, "atom_type", "event_id", "born_token", "ts"} or a
        structured error. Exactly one identifier is populated: ``born_token``
        on the Terpsichore fleet plane (the C1 default), ``event_id`` on the
        legacy phdb route when ``HADES_URL`` is unset.

    """
    from vault_mcp.phdb_client import (
        AtomError,
        PhdbUnavailableError,
        emit_atom,
    )

    try:
        result = emit_atom(atom_type, payload, ts=ts, post=_phdb_post)
        return result.to_dict()
    except AtomError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except PhdbUnavailableError as exc:
        return {"ok": False, "error": "phdb_unavailable", "detail": str(exc)}


def main() -> None:
    """Parse CLI args and run the MCP server over the chosen transport."""
    import argparse

    parser = argparse.ArgumentParser(description="vault-mcp MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("VAULT_MCP_HOST", "0.0.0.0"),  # noqa: S104 — operator-chosen bind host for HTTP transport
        help="Bind host for HTTP transports (env VAULT_MCP_HOST; default 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8100")),
        help="Bind port for HTTP transports (env PORT; default 8100)",
    )
    args = parser.parse_args()

    watch_status = "watch=on" if WATCH_ENABLED else "watch=off"
    rest_status = "rest=off" if REST_DISABLE else f"rest={REST_URL}"
    print(
        f"vault-mcp: vault={VAULT_PATH}, ttl={TTL_SECONDS}s, "
        f"{watch_status}, {rest_status}, "
        f"transport={args.transport}",
        file=sys.stderr,
    )

    if args.transport != "stdio":
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        print(
            f"vault-mcp: listening on {args.host}:{args.port}", file=sys.stderr
        )

    _start_sweep_scheduler()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
