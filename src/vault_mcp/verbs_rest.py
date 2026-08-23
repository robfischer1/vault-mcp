"""The REST-backed verb surface — 16 verbs over the Obsidian Local REST API.

Split out of server.py under vault-mcp#5294, which blocks a source file at 600
LOC; server.py was 5x over. This is a REGISTRATION MODULE: importing it is what
puts these verbs on the surface, which is why server.py imports it for its side
effect rather than for a name.

THE `if not server.REST_DISABLE` GUARD IS LOAD-BEARING AND MOVED WITH THE BLOCK. These
16 are the only conditionally-registered verbs in the package, so the surface is
54 verbs with the REST lane enabled and 38 without. A split that dropped the
guard would silently register them against a disabled lane; one that dropped the
import would silently unregister all 16. Both are caught by
tests/test_server.py::test_every_verb_is_registered, which pins the count.
"""

from __future__ import annotations

from typing import Any

# IMPORTED AS A MODULE, NOT AS NAMES — deliberately.
#
# `from vault_mcp.server import HADES_URL` binds the VALUE at import time, so a
# test doing `monkeypatch.setattr(server, "HADES_URL", ...)` would patch a name
# this module never reads again. That is not hypothetical: it broke three
# test_carve_materialize tests the moment _read_dissolved_row moved out of
# server.py, and it fails as a confusing runtime error ("phdb unreachable")
# rather than as an import error.
#
# Qualifying every server-owned name keeps the indirection the tests rely on and
# makes the coupling visible at each use site.
from vault_mcp import server

# `mcp` IS imported directly, unlike everything else above. It is a singleton
# built once at server import and never rebound, so there is nothing for a test
# to patch — and qualifying it as `server.mcp` costs real type safety: mypy
# cannot resolve an attribute on a module that is still mid-import, so every
# decorated verb became "Cannot determine type of mcp" plus "Untyped decorator
# makes function untyped". 99 errors, entirely from that one indirection.
from vault_mcp.server import mcp


def _object_or_error(
    data: object, *, stamp_as_of: bool = False
) -> dict[str, Any]:
    """Narrow a REST payload to a JSON OBJECT, or return an error envelope.

    Every caller below reads `result["data"]`, which the client types as `Any`
    because an HTTP body is whatever the server sent. Under server.py's mypy
    override that Any flowed straight into a `dict[str, Any]` return unchecked;
    moving these verbs out of that module is what exposed it.

    The check is not type-appeasement. Three call sites do
    `data["as_of"] = "rest"` immediately after, which raises TypeError on a list
    and silently mutates nothing useful on anything else — so a shape the API
    was never expected to return became a crash inside the verb rather than an
    error envelope out of it.
    """
    if not isinstance(data, dict):
        return {
            "error": "rest_unexpected_shape",
            "detail": (
                f"expected a JSON object from the REST API, got "
                f"{type(data).__name__}"
            ),
        }
    out: dict[str, Any] = data
    if stamp_as_of:
        out["as_of"] = "rest"
    return out


if not server.REST_DISABLE:

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
        return server._get_rest_client().probe()

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
        client = server._get_rest_client()
        result = client.get(
            "/active/", accept="application/vnd.olrapi.note+json"
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return _object_or_error(result["data"], stamp_as_of=True)

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
        client = server._get_rest_client()
        result = client.get(
            path,
            accept="application/vnd.olrapi.note+json",
            extra_headers={"Target-Type": "note"},
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return _object_or_error(result["data"], stamp_as_of=True)

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
        client = server._get_rest_client()
        result = client.get(
            f"/vault/{path}",
            accept="application/vnd.olrapi.note+json",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return _object_or_error(result["data"], stamp_as_of=True)

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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
        endpoint = f"/vault/{path}" if path else "/vault/"
        if not endpoint.endswith("/"):
            endpoint += "/"
        result = client.get(endpoint)
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return _object_or_error(result["data"])

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
        client = server._get_rest_client()
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
        client = server._get_rest_client()
        endpoint = f"/vault/{path}" if path else "/active/"
        result = client.get(
            endpoint,
            accept="application/vnd.olrapi.document-map+json",
        )
        if not result["ok"]:
            return {"error": result["error"], "detail": result.get("detail")}
        return _object_or_error(result["data"])
