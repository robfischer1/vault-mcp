"""The query, graph and governance verb surface — 16 read-side verbs.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE: server.py
imports it at its foot for the side effect of these `@mcp.tool()` calls.

Everything here reads the vault INDEX rather than writing, which is why the
group travels together: the CLI passthroughs, the frontmatter/filename/recency
lookups, the link-graph walks and the tag-glossary checks all answer off
`_get_index()` and none of them touches the Convention Gate.
"""

from __future__ import annotations

from typing import Any

from vault_mcp.index import VaultIndex

# _AUDIT_IGNORES_REL and _TAGS_GLOSSARY_REL are NOT imported: they are defined
# further down this file. They lived inside the moved range, so they travelled
# with the verbs that use them — which is correct, since nothing outside this
# module reads either one.
from vault_mcp.server import (
    VAULT_PATH,
    _get_cli_client,
    _get_index,
    mcp,
)


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
