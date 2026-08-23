#!/usr/bin/env python3
"""Local smoke-test CLI for vault-mcp tools.

Usage:
    vault-mcp-cli--tool find_notes_by_frontmatter --json '{"filters": {"note_type": "handoff"}}'
    vault-mcp-cli--tool read_note --json '{"stem_or_path": "AGENTS"}'
    vault-mcp-cli--tool reindex
    vault-mcp-cli--tool recent_edits --json '{"since": "2026-05-01"}'
    vault-mcp-cli--tool find_by_filename --json '{"pattern": "2026-05-*"}'
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from vault_mcp import server as _srv
from vault_mcp import verbs_query as _query

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# Sourced from vault_mcp.verbs_query since vault-mcp#5294 moved the read-side
# verbs out of server.py. Importing verbs_query here is also what registers
# them if the CLI is the entry point rather than the server.
TOOLS: dict[str, Callable[..., Any]] = {
    "find_notes_by_frontmatter": _query.find_notes_by_frontmatter,
    "find_by_filename": _query.find_by_filename,
    "recent_edits": _query.recent_edits,
    "read_note": _query.read_note,
    "reindex": _query.reindex,
    "backlinks_to": _query.backlinks_to,
    "outbound_links": _query.outbound_links,
    "find_orphans": _query.find_orphans,
    "tag_glossary_check": _query.tag_glossary_check,
    "vault_stats": _query.vault_stats,
}

# REST tools (Phases 6-7) — only present when REST is enabled.
#
# Sourced from vault_mcp.verbs_rest since vault-mcp#5294 moved them out of
# server.py. The `if not REST_DISABLE` guard is unchanged and still necessary:
# with the lane disabled those names are never defined, in that module or any
# other. Type-checkers caught this move — pytest did not, because no test
# imports the CLI.
if not _srv.REST_DISABLE:
    from vault_mcp import verbs_rest as _rest

    TOOLS.update(
        {
            "rest_health": _rest.rest_health,
            "active_note": _rest.active_note,
            "periodic_note": _rest.periodic_note,
            "unsaved_buffer": _rest.unsaved_buffer,
            "obsidian_search": _rest.obsidian_search,
            "execute_command": _rest.execute_command,
            "jsonlogic_search": _rest.jsonlogic_search,
            "vault_tags": _rest.vault_tags,
            "list_directory": _rest.list_directory,
            "open_in_obsidian": _rest.open_in_obsidian,
            "document_map": _rest.document_map,
        }
    )


def main() -> None:
    """Run a single vault-mcp tool from the CLI for smoke-testing."""
    parser = argparse.ArgumentParser(
        description="vault-mcp CLI smoke-test runner"
    )
    parser.add_argument("--tool", required=True, choices=list(TOOLS.keys()))
    parser.add_argument("--json", default="{}", help="JSON kwargs for the tool")
    args = parser.parse_args()

    kwargs = json.loads(args.json)
    result = TOOLS[args.tool](**kwargs)

    if args.tool == "read_note" and "body" in result:
        body = result["body"]
        if len(body) > 500:
            result["body"] = body[:500] + f"... [{len(body)} chars total]"

    json.dump(result, sys.stdout, indent=2, default=str, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
