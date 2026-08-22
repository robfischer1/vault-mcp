"""The index's GOVERNANCE collaborator — tag glossary, vault stats and tag rollups.

Extracted from VaultIndex under vault-mcp#5294 (index.py was 845 LOC over a 600
block). DELEGATION, not inheritance: VaultIndex holds one of these and forwards,
so a reader looking for `read_note` finds IndexQueries by name.

Reads index state — `_content`, `_by_name`, `_mtime`, `_inbound`, `_outbound`,
`vault` — through the back-reference, keeping it single-sourced on the index.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from vault_mcp.parsers import strip_frontmatter

# Own logger: ruff's BLE001 only permits a log-and-degrade blind except where it
# can see `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from vault_mcp.index import VaultIndex


class IndexGovernance:
    """The index's GOVERNANCE collaborator — tag glossary, vault stats and tag rollups."""

    def __init__(self, index: VaultIndex) -> None:
        """Bind to the index whose state this collaborator reads."""
        self._index = index

    @staticmethod
    @staticmethod
    def parse_audit_ignores(ignores_path: Path) -> list[str]:
        """Extract folder-prefix exemptions from audit-ignores.md."""
        if not ignores_path.exists():
            return []
        text = ignores_path.read_text(encoding="utf-8")
        prefixes = []
        in_folder_table = False
        for line in text.split("\n"):
            if "Folder-level exemptions" in line:
                in_folder_table = True
                continue
            if in_folder_table and line.startswith("| `"):
                m = re.match(r"\|\s*`([^`]+)`", line)
                if m:
                    prefix = m.group(1).rstrip("/")
                    prefixes.append(prefix + "/")
            elif in_folder_table and line.startswith("#"):
                break
        return prefixes

    @staticmethod
    @staticmethod
    def parse_tags_glossary(glossary_path: Path) -> set[str]:
        """Extract the closed tag vocabulary from Tags Glossary.md."""
        if not glossary_path.exists():
            return set()
        text = glossary_path.read_text(encoding="utf-8")
        tags: set[str] = set()
        in_code = False
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                stripped = line.strip()
                if stripped.startswith("#"):
                    tag = stripped.split(" ")[0].split(" -")[0].strip()
                    if tag:
                        tags.add(tag)
        return tags

    def tag_glossary_check(self, glossary_path: Path) -> list[dict[str, Any]]:
        r"""Find body #tags not in the Tags Glossary.

        Excludes \\#tag escapes and #activity/processed per policy.
        """
        self._index._ensure_fresh()
        valid_tags = self.parse_tags_glossary(glossary_path)
        violations: dict[str, list[str]] = {}

        for path, _fm, rel in self._index._content:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("tag check: skip unreadable %s: %s", path, exc)
                continue

            body = strip_frontmatter(text)
            for m in self._index.BODY_TAG_RE.finditer(body):
                tag = "#" + m.group(1)
                if tag in valid_tags:
                    continue
                if tag == "#activity/processed":
                    continue
                violations.setdefault(rel, []).append(tag)

        results = []
        for file_path, tags in sorted(violations.items()):
            results.append(
                {
                    "path": file_path,
                    "invalid_tags": sorted(set(tags)),
                }
            )
        return results

    def vault_stats(self) -> dict[str, Any]:
        """Aggregate stats: counts by @type, top tags, edit volume by week."""
        self._index._ensure_fresh()
        type_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        week_counts: dict[str, int] = {}

        for path, fm, _rel in self._index._content:
            at_type = fm.get("@type", fm.get("type", "unknown"))
            if isinstance(at_type, str):
                type_counts[at_type] = type_counts.get(at_type, 0) + 1

            fm_tags = fm.get("tags", [])
            if isinstance(fm_tags, list):
                for t in fm_tags:
                    if isinstance(t, str) and t:
                        tag_counts[t] = tag_counts.get(t, 0) + 1

            mtime = self._index._mtime.get(path)
            if mtime is not None:
                week = (
                    datetime.fromtimestamp(mtime, tz=UTC)
                    .astimezone()
                    .strftime("%Y-W%W")
                )
                week_counts[week] = week_counts.get(week, 0) + 1

        top_types = sorted(type_counts.items(), key=lambda x: -x[1])
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:20]
        recent_weeks = sorted(week_counts.items(), reverse=True)[:8]

        return {
            "total_indexed": len(self._index._content),
            "total_names": len(self._index._by_name),
            "types": [{"type": t, "count": c} for t, c in top_types],
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "edit_volume_by_week": [
                {"week": w, "count": c} for w, c in recent_weeks
            ],
        }

    def all_tags(self, include_body: bool = True) -> list[dict[str, Any]]:
        """Collect all tags from frontmatter and optionally body text, with counts.

        Returns sorted list of {tag, count, sources} where sources indicates
        whether the tag appears in frontmatter, body, or both.
        """
        self._index._ensure_fresh()
        fm_counts: dict[str, int] = {}
        body_counts: dict[str, int] = {}

        for path, fm, _rel in self._index._content:
            fm_tags = fm.get("tags", [])
            if isinstance(fm_tags, list):
                for t in fm_tags:
                    if isinstance(t, str) and t:
                        fm_counts[t] = fm_counts.get(t, 0) + 1

            if include_body:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    log.debug("all_tags: skip unreadable %s: %s", path, exc)
                    continue
                body = strip_frontmatter(text)
                for m in self._index.BODY_TAG_RE.finditer(body):
                    tag = "#" + m.group(1)
                    body_counts[tag] = body_counts.get(tag, 0) + 1

        all_keys = set(fm_counts) | set(body_counts)
        results = []
        for tag in all_keys:
            fc = fm_counts.get(tag, 0)
            bc = body_counts.get(tag, 0)
            sources = []
            if fc:
                sources.append("frontmatter")
            if bc:
                sources.append("body")
            results.append(
                {
                    "tag": tag,
                    "count": fc + bc,
                    "frontmatter_count": fc,
                    "body_count": bc,
                    "sources": sources,
                }
            )
        results.sort(
            key=lambda x: -(x["count"] if isinstance(x["count"], int) else 0)
        )
        return results
