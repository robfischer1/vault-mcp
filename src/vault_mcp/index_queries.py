"""The index's QUERY collaborator — frontmatter, filename, recency and note reads.

Extracted from VaultIndex under vault-mcp#5294 (index.py was 845 LOC over a 600
block). DELEGATION, not inheritance: VaultIndex holds one of these and forwards,
so a reader looking for `read_note` finds IndexQueries by name.

Reads index state — `_content`, `_by_name`, `_mtime`, `_inbound`, `_outbound`,
`vault` — through the back-reference, keeping it single-sourced on the index.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from vault_mcp.index import BASE_EMBED_RE
from vault_mcp.parsers import (
    extract_wikilink_targets,
    parse_frontmatter,
    strip_frontmatter,
)

# Own logger: ruff's BLE001 only permits a log-and-degrade blind except where it
# can see `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from vault_mcp.index import VaultIndex


class IndexQueries:
    """The index's QUERY collaborator — frontmatter, filename, recency and note reads."""

    def __init__(self, index: VaultIndex) -> None:
        """Bind to the index whose state this collaborator reads."""
        self._index = index

    def find_notes_by_frontmatter(
        self, filters: dict[str, Any], scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Equality-match on frontmatter fields, with optional scope prefix."""
        results = []
        for _path, fm, rel in self._index.content:
            if scope and not rel.startswith(scope):
                continue
            match = True
            for key, expected in filters.items():
                val = fm.get(key)
                if isinstance(val, list):
                    if expected not in val:
                        match = False
                        break
                elif val != expected:
                    match = False
                    break
            if match:
                results.append({"path": rel, "frontmatter": fm})
        return results

    def find_by_filename(
        self, pattern: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Fnmatch over the by_name index keys."""
        results = []
        for stem, paths in self._index.by_name.items():
            if not fnmatch.fnmatch(stem, pattern):
                continue
            for p in paths:
                rel = str(p.relative_to(self._index.vault)).replace("\\", "/")
                if scope and not rel.startswith(scope):
                    continue
                results.append({"stem": stem, "path": rel})
        return results

    def recent_edits(
        self, since: str, scope: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Files modified since a given ISO-8601 date, sorted by mtime desc."""
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            return [{"error": f"Invalid date format: {since}"}]

        since_ts = since_dt.timestamp()
        candidates = []

        for stem, paths in self._index.by_name.items():
            for p in paths:
                rel = str(p.relative_to(self._index.vault)).replace("\\", "/")
                if scope and not rel.startswith(scope):
                    continue
                mtime = self._index._mtime.get(p)
                if mtime is None:
                    continue
                if mtime >= since_ts:
                    candidates.append(
                        {
                            "path": rel,
                            "modified": datetime.fromtimestamp(mtime, tz=UTC)
                            .astimezone()
                            .isoformat(timespec="seconds"),
                            "stem": stem,
                        }
                    )

        candidates.sort(key=lambda r: r["modified"], reverse=True)
        return candidates[:limit]

    def read_note(self, stem_or_path: str) -> dict[str, Any]:
        """Read a note by wikilink stem or vault-relative path.

        Returns {frontmatter, body, path} with resolved wikilinks per Q7.
        """
        target_path: Path | None = None

        if "/" in stem_or_path or stem_or_path.endswith(".md"):
            candidate = self._index.vault / stem_or_path
            if candidate.exists():
                target_path = candidate

        if target_path is None:
            stem = stem_or_path.removesuffix(".md")
            matches = self._index.by_name.get(stem, [])
            if len(matches) == 1:
                target_path = matches[0]
            elif len(matches) > 1:
                return {
                    "error": "ambiguous",
                    "stem": stem,
                    "candidates": [
                        str(p.relative_to(self._index.vault)).replace("\\", "/")
                        for p in matches
                    ],
                }
            else:
                return {"error": "not_found", "query": stem_or_path}

        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {
                "error": "read_failed",
                "path": str(target_path),
                "detail": str(e),
            }

        fm = parse_frontmatter(text)
        body = strip_frontmatter(text).lstrip("\n\r")
        rel = str(target_path.relative_to(self._index.vault)).replace("\\", "/")

        outbound = extract_wikilink_targets(body)
        resolved_links: list[dict[str, Any]] = []
        for stem in outbound:
            link_matches = self._index.by_name.get(stem, [])
            if len(link_matches) == 1:
                resolved_links.append(
                    {
                        "stem": stem,
                        "path": str(
                            link_matches[0].relative_to(self._index.vault)
                        ).replace("\\", "/"),
                    }
                )
            elif len(link_matches) > 1:
                resolved_links.append(
                    {
                        "stem": stem,
                        "resolution": "ambiguous",
                        "candidates": [
                            str(p.relative_to(self._index.vault)).replace(
                                "\\", "/"
                            )
                            for p in link_matches
                        ],
                    }
                )
            else:
                resolved_links.append(
                    {"stem": stem, "resolution": "unresolved"}
                )

        # Resolve base embeds (T008, T009, T010, T014, T015)
        resolved_embeds: list[dict[str, Any]] = []
        for m in BASE_EMBED_RE.finditer(body):
            token = m.group(0)
            target_stem = m.group(1).strip()
            view_name = m.group(2).strip() if m.group(2) else None

            # Only resolve if it's a .base file or has a view name
            if not (target_stem.endswith(".base") or view_name):
                continue

            stem = target_stem.removesuffix(".md").removesuffix(".base")
            matches = self._index.by_name.get(stem, [])

            # Filter matches to prefer .base if it ends in .base
            if target_stem.endswith(".base"):
                matches = [p for p in matches if p.suffix == ".base"]

            embed_target_path = None
            if len(matches) == 1:
                embed_target_path = matches[0]

            if not embed_target_path:
                resolved_embeds.append(
                    {
                        "token": token,
                        "error": {
                            "type": "not_found",
                            "message": f"File not found: {target_stem}",
                        },
                    }
                )
                continue

            from .bases import execute_base, parse_file

            pf = parse_file(embed_target_path)
            if pf.errors:
                resolved_embeds.append(
                    {
                        "token": token,
                        "path": str(
                            embed_target_path.relative_to(self._index.vault)
                        ).replace("\\", "/"),
                        "error": {
                            "type": "parse_error",
                            "message": pf.errors[0]["message"],
                        },
                    }
                )
                continue

            if not pf.bases:
                continue

            # Execute first base
            base = pf.bases[0]
            if view_name:
                view_names = [v.name for v in base.views]
                if view_name not in view_names:
                    resolved_embeds.append(
                        {
                            "token": token,
                            "path": str(
                                embed_target_path.relative_to(self._index.vault)
                            ).replace("\\", "/"),
                            "error": {
                                "type": "view_not_found",
                                "message": f"View '{view_name}' not found",
                            },
                        }
                    )
                    continue

            res = execute_base(base, self._index, view_name=view_name)

            resolved_embeds.append(
                {
                    "token": token,
                    "path": str(
                        embed_target_path.relative_to(self._index.vault)
                    ).replace("\\", "/"),
                    "results": dataclasses.asdict(res),
                }
            )

        return {
            "path": rel,
            "frontmatter": fm,
            "body": body,
            "outbound_links": resolved_links,
            "resolved_embeds": resolved_embeds,
        }
