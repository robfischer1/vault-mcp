#!/usr/bin/env python3
"""Local smoke-test CLI for vault-mcp tools.

Usage:
    python cli.py --tool find_notes_by_frontmatter --json '{"filters": {"note_type": "handoff"}}'
    python cli.py --tool read_note --json '{"stem_or_path": "AGENTS"}'
    python cli.py --tool reindex
    python cli.py --tool recent_edits --json '{"since": "2026-05-01"}'
    python cli.py --tool find_by_filename --json '{"pattern": "2026-05-*"}'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import server as _srv  # noqa: E402

TOOLS: dict[str, object] = {
    "find_notes_by_frontmatter": _srv.find_notes_by_frontmatter,
    "find_by_filename": _srv.find_by_filename,
    "recent_edits": _srv.recent_edits,
    "read_note": _srv.read_note,
    "reindex": _srv.reindex,
    "backlinks_to": _srv.backlinks_to,
    "outbound_links": _srv.outbound_links,
    "find_orphans": _srv.find_orphans,
    "tag_glossary_check": _srv.tag_glossary_check,
    "vault_stats": _srv.vault_stats,
}

# REST tools (Phases 6-7) — only present when REST is enabled
if not _srv.REST_DISABLE:
    TOOLS.update(
        {
            "rest_health": _srv.rest_health,
            "active_note": _srv.active_note,
            "periodic_note": _srv.periodic_note,
            "unsaved_buffer": _srv.unsaved_buffer,
            "obsidian_search": _srv.obsidian_search,
            "execute_command": _srv.execute_command,
            "dataview_query": _srv.dataview_query,
            "jsonlogic_search": _srv.jsonlogic_search,
            "vault_tags": _srv.vault_tags,
            "list_directory": _srv.list_directory,
            "open_in_obsidian": _srv.open_in_obsidian,
            "document_map": _srv.document_map,
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
