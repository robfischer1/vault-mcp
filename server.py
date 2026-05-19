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

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from vault_mcp.index import VaultIndex  # noqa: E402
from vault_mcp.bases import (  # noqa: E402
    parse_file as _parse_file_impl,
    execute_base as _execute_base_impl,
    validate_base as _validate_base_impl,
    write_base_to_file as _write_base_to_file_impl,
    _serialize_base,
)

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
REST_URL = os.environ.get("VAULT_MCP_REST_URL", "http://127.0.0.1:27123")
REST_KEY_PATH = os.environ.get(
    "VAULT_MCP_REST_KEY_PATH",
    str(Path.home() / "Obsidian" / ".local" / "rest-api-key.txt"),
)

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
_rest_client = None


def _get_rest_client():
    global _rest_client
    if _rest_client is None:
        from vault_mcp.rest_client import ObsidianRESTClient
        _rest_client = ObsidianRESTClient(base_url=REST_URL, key_path=REST_KEY_PATH)
        _rest_client.probe()
    return _rest_client


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------
mcp = FastMCP("vault-mcp")


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
    if not file_path.exists():
        if not file_path.parent.exists():
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


@mcp.tool()
def render_predicate_stub(
    predicate_name: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Render an ephemeral hub-note for a predicate in Atlas/Predicates/.

    The hub-note contains wikilinks to every subject and object connected by
    this predicate, making the typed edge visible in Obsidian's graph view.

    Set apply=True to write the file; default is dry-run preview.
    """
    conn = _get_phdb_conn()
    if conn is None:
        return {"error": "phdb not configured (PHDB_DB_PATH missing)"}

    from phdb.triples import query_triples as _qt

    triples = _qt(conn, predicate=predicate_name, limit=10000)
    if not triples:
        return {"error": f"No triples found for predicate '{predicate_name}'"}

    from datetime import datetime

    generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    pairs = [(t["subject_label"], t.get("object_label")) for t in triples]

    lines = [
        "---",
        '"@context": "https://schema.org"',
        '"@type": "CollectionPage"',
        f'name: "Predicate: {predicate_name}"',
        f'identifier: "predicate-stub-{predicate_name}"',
        f'date: {datetime.now().strftime("%Y-%m-%d")}',
        'author_type: "ai-generated"',
        "execution_type: recomputed",
        f'generated_at: "{generated_at}"',
        "ephemeral: true",
        "status: Active",
        "tags: []",
        'up: "[[Atlas]]"',
        "---",
        "",
        f"## {predicate_name}",
        "",
        "> [!INFO] Ephemeral hub-note",
        f"> Auto-generated from the predicate table. {len(pairs)} triples.",
        "> Regenerate with `render_predicate_stubs.py` or the MCP tool.",
        "",
        "| Subject | Object |",
        "| :--- | :--- |",
    ]
    for subj, obj in pairs:
        obj_link = f"[[{obj}]]" if obj else "_(open)_"
        lines.append(f"| [[{subj}]] | {obj_link} |")
    lines.append("")
    content = "\n".join(lines)

    if apply:
        stub_dir = VAULT_PATH / "Atlas" / "Predicates"
        stub_dir.mkdir(parents=True, exist_ok=True)
        stub_path = stub_dir / f"{predicate_name}.md"
        stub_path.write_text(content, encoding="utf-8", newline="\n")
        return {
            "status": "written",
            "path": str(stub_path.relative_to(VAULT_PATH)),
            "triples": len(pairs),
        }

    return {
        "status": "dry-run",
        "triples": len(pairs),
        "preview_lines": len(lines),
    }


def main() -> None:
    watch_status = "watch=on" if WATCH_ENABLED else "watch=off"
    rest_status = "rest=off" if REST_DISABLE else f"rest={REST_URL}"
    phdb_status = f"phdb={PHDB_DB_PATH}" if PHDB_DB_PATH else "phdb=off"
    print(
        f"vault-mcp: vault={VAULT_PATH}, ttl={TTL_SECONDS}s, "
        f"{watch_status}, {rest_status}, {phdb_status}",
        file=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
