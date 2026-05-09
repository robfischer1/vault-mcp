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

from server import (  # noqa: E402
    backlinks_to,
    find_by_filename,
    find_notes_by_frontmatter,
    find_orphans,
    outbound_links,
    read_note,
    recent_edits,
    reindex,
    tag_glossary_check,
    vault_stats,
)

TOOLS = {
    "find_notes_by_frontmatter": find_notes_by_frontmatter,
    "find_by_filename": find_by_filename,
    "recent_edits": recent_edits,
    "read_note": read_note,
    "reindex": reindex,
    "backlinks_to": backlinks_to,
    "outbound_links": outbound_links,
    "find_orphans": find_orphans,
    "tag_glossary_check": tag_glossary_check,
    "vault_stats": vault_stats,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="vault-mcp CLI smoke-test runner")
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
