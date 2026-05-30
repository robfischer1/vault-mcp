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
    from vault_mcp.cli_client import ObsidianCLI
    from vault_mcp.rest_client import ObsidianRESTClient

_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from vault_mcp.bases import (  # noqa: E402, I001
    _serialize_base,
    execute_base as _execute_base_impl,
    parse_file as _parse_file_impl,
    validate_base as _validate_base_impl,
    write_base_to_file as _write_base_to_file_impl,
)
from vault_mcp.index import VaultIndex  # noqa: E402, I001
from vault_mcp.rest_client import DEFAULT_REST_URL  # noqa: E402, I001

# phdb sibling-repo import for predicate table (Phase 9 — triple tools)
_phdb_src = Path(__file__).resolve().parent.parent / "personal-history-db" / "src"
if str(_phdb_src) not in sys.path:
    sys.path.insert(0, str(_phdb_src))


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

# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

_active_sessions: set[Any] = set()


@dataclass
class Subscription:
    handle: str
    path: str
    view: str | None
    base_index: int
    last_result_hash: str | None = None


class SubscriptionManager:
    """Manages Bases live update subscriptions and pushes notifications."""

    def __init__(self, mcp_server: FastMCP):
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
        with self.lock:
            if handle in self.subscriptions:
                del self.subscriptions[handle]
                return True
        return False

    def _hash_result(self, result: Any) -> str:
        """Create a stable hash of a QueryResult."""
        data = asdict(result) if hasattr(result, "__dataclass_fields__") else result

        hash_data = {
            "notes": [
                {"path": n["path"], "formulas": n["formulas"]} for n in data.get("notes", [])
            ],
            "summaries": data.get("summaries", {}),
            "total": data.get("total", 0),
        }
        dump = json.dumps(hash_data, sort_keys=True)
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    async def notify_all(self, _changed_path: Path) -> None:
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
            "results": asdict(result) if hasattr(result, "__dataclass_fields__") else result,
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
                await session.send_notification(cast(Any, notification))
            except Exception:
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
        _rest_client = ObsidianRESTClient(base_url=REST_URL, key_path=REST_KEY_PATH or None)
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
    results = _get_index().outbound_links(stem, include_image_embeds=include_image_embeds)
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
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Subscribe to live updates for a base query.

    Pushes a notification whenever the result set for this base changes.
    Notifications use method 'notifications/bases/update'.

    Args:
        path: Vault-relative file path containing the base.
        view: Named view to restrict to.
        base_index: 0-based index of which base (default 0).

    Returns:
        {"handle": str, "initial_results": dict}
    """
    idx = _get_index()
    file_path = idx.vault / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}

    pf = _parse_file_impl(file_path)
    if pf.errors and not pf.bases:
        return {"error": "parse_error", "path": path, "detail": pf.errors[0]["message"]}

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
            mgr.subscriptions[handle].last_result_hash = mgr._hash_result(result)

    return {
        "handle": handle,
        "initial_results": asdict(result) if hasattr(result, "__dataclass_fields__") else result,
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
        return {"error": "parse_error", "path": path, "detail": pf.errors[0]["message"]}

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
            return {"error": "rest_invalid_request", "detail": f"level must be one of {valid}"}
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
        "workspace:open-in-new-leaf",
        "app:reveal-active-file",
        "editor:focus",
        "app:open-settings",
    }

    @mcp.tool()
    def execute_command(command_id: str) -> dict[str, Any]:
        """[REST-backed] Execute an Obsidian command by ID (allowlisted only).

        Only non-mutating navigation/focus commands are permitted. Current
        allowlist: workspace:open-in-new-leaf, app:reveal-active-file,
        editor:focus, app:open-settings.

        Args:
            command_id: Obsidian command ID. Example: "editor:focus"

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
    def jsonlogic_search(query: dict) -> dict[str, Any]:
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

PHDB_DB_PATH = os.environ.get("PHDB_DB_PATH", "")
_phdb_conn = None


def _get_phdb_conn():
    global _phdb_conn
    if _phdb_conn is None:
        if not PHDB_DB_PATH:
            return None
        db_file = Path(PHDB_DB_PATH)
        if not db_file.exists():
            return None
        from phdb.db import connect_persistent
        _phdb_conn = connect_persistent(db_file)
    return _phdb_conn


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.triples import query_triples as _qt
    results = _qt(
        conn,
        subject=subject,
        predicate=predicate,
        object_=object,
        provenance=provenance,
        since=since,
        until=until,
        include_null_objects=include_null_objects,
        include_inverse=include_inverse,
        limit=limit,
    )
    return {"count": len(results), "triples": results}


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.triples import add_triple as _at
    try:
        return _at(
            conn,
            subject=subject,
            predicate=predicate,
            object_=object,
            observed_at=observed_at,
            provenance=provenance,
            source_ref=source_ref,
            subject_kind=subject_kind,
            object_kind=object_kind,
            qualifiers=qualifiers,
        )
    except ValueError as e:
        return {"error": "invalid_predicate", "detail": str(e)}


@mcp.tool()
def triple_stats() -> dict[str, Any]:
    """Get summary statistics for the predicate table.

    Returns node count, triple count, null-object count, qualifier count,
    predicate usage breakdown, and node-kind distribution.

    Returns:
        {"nodes": int, "triples": int, ...} or {"error": str}.
    """
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.triples import triple_stats as _ts
    return _ts(conn)


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.triples import node_neighborhood as _nn
    return _nn(conn, node, limit=limit)


@mcp.tool()
def list_predicates() -> dict[str, Any]:
    """List all predicates in the controlled vocabulary.

    Returns:
        {"count": int, "predicates": [...]} or {"error": str}.
    """
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.triples import list_predicates as _lp
    preds = _lp(conn)
    return {"count": len(preds), "predicates": preds}


# ---------------------------------------------------------------------------
# Phase 6 of Git for Ideas — file_revisions tools (migration 0039)
# ---------------------------------------------------------------------------


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.file_revisions import get_revision, materialize
    row = get_revision(conn, rev_id)
    if row is None:
        return {"error": "not_found", "detail": f"rev_id={rev_id} has no file_revisions row"}
    try:
        body = materialize(conn, rev_id, repo_root=repo_root)
    except FileNotFoundError as e:
        return {"error": "not_found", "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — surface git errors verbatim
        return {"error": "materialize_failed", "detail": str(e)}
    return {
        "rev_id": rev_id,
        "repo": row["repo"],
        "commit_sha": row["commit_sha"],
        "file_path": row["file_path"],
        "git_blob_sha": row["git_blob_sha"],
        "change_type": row["change_type"],
        "authorship": row["authorship"],
        "body": body,
    }


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.file_revisions import list_for_path
    rows = list_for_path(
        conn, file_path, repo=repo,
        limit=None if limit == 0 else limit,
    )
    return {"count": len(rows), "revisions": rows}


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.file_revisions import triple_deltas
    deltas = triple_deltas(conn, rev_id)
    return {"count": len(deltas), "deltas": deltas}


# ---------------------------------------------------------------------------
# Phase 6 of Dissolution Tracking — registry lookup tools (migration 0041)
# ---------------------------------------------------------------------------


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.dissolutions import lookup_vault_path
    return lookup_vault_path(conn, vault_path, repo=repo)


@mcp.tool()
def list_dissolution_waves(repo: str = "vault") -> dict[str, Any]:
    """Wave-level browse over the dissolution registry.

    Args:
        repo: Repo name (default "vault").

    Returns:
        {"count": int, "waves": [...]} where each wave dict includes
        plan_slug, migration_id, target_schemas/tables, dissolved_at,
        and linked_files count.
    """
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.dissolutions import list_waves
    waves = list_waves(conn, repo=repo)
    return {"count": len(waves), "waves": waves}


@mcp.tool()
def dissolution_for_revision(rev_id: int) -> dict[str, Any]:
    """Given a file_revisions row id, return its dissolution (if any).

    Args:
        rev_id: Primary key of the file_revisions row.

    Returns:
        Dissolution dict on hit, {"dissolution": None} when the
        revision is not linked to any dissolution, or
        {"error": str} on lookup failure.
    """
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    row = conn.execute(
        "SELECT d.id FROM dissolutions d"
        " JOIN file_revision_dissolutions frd ON frd.dissolution_pk = d.id"
        " WHERE frd.file_revision_pk = ?",
        (rev_id,),
    ).fetchone()
    if row is None:
        return {"dissolution": None}
    from phdb.dissolutions import get
    return {"dissolution": get(conn, int(row[0]))}


# ---------------------------------------------------------------------------
# Phase 8 of Git for Ideas — vault_notes tools (migration 0042)
# ---------------------------------------------------------------------------


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.vault_notes import lookup
    result = lookup(conn, query)
    if result is None:
        return {"result": None}
    return result


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.vault_notes import search
    results = search(conn, query, limit=limit)
    return {"count": len(results), "results": results}


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.vault_notes import read_note
    body = read_note(conn, name_or_path)
    if body is None:
        return {"result": None}
    return {"name_or_path": name_or_path, "body": body}


@mcp.tool()
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
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb_unavailable", "detail": "PHDB_DB_PATH not set or DB not found"}
    from phdb.vault_notes import list_notes
    results = list_notes(conn, status=status, at_type=at_type, limit=limit)
    return {"count": len(results), "notes": results}


def main() -> None:
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
        default="0.0.0.0",
        help="Bind host for HTTP transports (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8100,
        help="Bind port for HTTP transports (default: 8100)",
    )
    args = parser.parse_args()

    watch_status = "watch=on" if WATCH_ENABLED else "watch=off"
    rest_status = "rest=off" if REST_DISABLE else f"rest={REST_URL}"
    phdb_status = f"phdb={PHDB_DB_PATH}" if PHDB_DB_PATH else "phdb=off"
    print(
        f"vault-mcp: vault={VAULT_PATH}, ttl={TTL_SECONDS}s, "
        f"{watch_status}, {rest_status}, {phdb_status}, "
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
        print(f"vault-mcp: listening on {args.host}:{args.port}", file=sys.stderr)

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
