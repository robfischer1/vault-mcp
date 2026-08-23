"""The index's LINK-GRAPH collaborator — backlinks, outbound links, orphans, dangling refs.

Extracted from VaultIndex under vault-mcp#5294 (index.py was 845 LOC over a 600
block). DELEGATION, not inheritance: VaultIndex holds one of these and forwards,
so a reader looking for `read_note` finds IndexQueries by name.

Reads index state — `_content`, `_by_name`, `_mtime`, `_inbound`, `_outbound`,
`vault` — through the back-reference, keeping it single-sourced on the index.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from vault_mcp.index import IMAGE_EMBED_RE
from vault_mcp.parsers import strip_frontmatter

# Own logger: ruff's BLE001 only permits a log-and-degrade blind except where it
# can see `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vault_mcp.index import VaultIndex


class IndexLinkGraph:
    """The index's LINK-GRAPH collaborator — backlinks, outbound links, orphans, dangling refs."""

    def __init__(self, index: VaultIndex) -> None:
        """Bind to the index whose state this collaborator reads."""
        self._index = index

    def backlinks_to(self, stem: str) -> list[dict[str, Any]]:
        """Files that link to `stem` via body wikilinks or `up:` frontmatter."""
        self._index._ensure_fresh()
        sources = self._index._inbound.get(stem, [])
        results = []
        for src_stem in sources:
            paths = self._index._by_name.get(src_stem, [])
            for p in paths:
                results.append(
                    {
                        "stem": src_stem,
                        "path": str(p.relative_to(self._index.vault)).replace(
                            "\\", "/"
                        ),
                    }
                )
        return results

    def outbound_links(
        self, stem: str, include_image_embeds: bool = False
    ) -> list[dict[str, Any]]:
        """Wikilinks from `stem`'s body (excluding image embeds by default)."""
        self._index._ensure_fresh()
        targets = self._index._outbound.get(stem, [])

        if not include_image_embeds:
            paths = self._index._by_name.get(stem, [])
            image_stems: set[str] = set()
            for p in paths:
                try:
                    text = p.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    log.debug(
                        "image-embed scan: skip unreadable %s: %s", p, exc
                    )
                    continue
                body = strip_frontmatter(text)
                for m in IMAGE_EMBED_RE.finditer(body):
                    image_stems.add(m.group(1).strip())
            targets = [t for t in targets if t not in image_stems]

        results: list[dict[str, Any]] = []
        for target in targets:
            matches = self._index._by_name.get(target, [])
            if len(matches) == 1:
                results.append(
                    {
                        "stem": target,
                        "path": str(
                            matches[0].relative_to(self._index.vault)
                        ).replace("\\", "/"),
                    }
                )
            elif len(matches) > 1:
                results.append(
                    {
                        "stem": target,
                        "resolution": "ambiguous",
                        "candidates": [
                            str(p.relative_to(self._index.vault)).replace(
                                "\\", "/"
                            )
                            for p in matches
                        ],
                    }
                )
            else:
                results.append({"stem": target, "resolution": "unresolved"})
        return results

    def find_orphans(
        self,
        scope: str | None = None,
        exempt_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Files with no inbound links and no `up:` frontmatter.

        Honors folder-prefix exemptions from audit-ignores.md.
        """
        self._index._ensure_fresh()
        results = []
        for path, fm, rel in self._index._content:
            if scope and not rel.startswith(scope):
                continue

            if exempt_prefixes:
                skip = False
                for prefix in exempt_prefixes:
                    if rel.startswith(prefix):
                        skip = True
                        break
                if skip:
                    continue

            stem = path.stem
            has_up = bool(fm.get("up", ""))
            has_inbound = stem in self._index._inbound

            if not has_up and not has_inbound:
                reasons = []
                if not has_up:
                    reasons.append("no up: frontmatter")
                if not has_inbound:
                    reasons.append("no inbound links")
                results.append(
                    {
                        "path": rel,
                        "stem": stem,
                        "reason": "; ".join(reasons),
                    }
                )
        return results

    def find_dangling_links(
        self,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find wikilinks and ``up:`` values that point at non-existent notes."""
        self._index._ensure_fresh()
        dangles: list[dict[str, Any]] = []
        seen: set[str] = set()

        for stem, targets in self._index._outbound.items():
            if scope:
                paths = self._index._by_name.get(stem, [])
                if paths and not any(
                    str(p.relative_to(self._index.vault))
                    .replace("\\", "/")
                    .startswith(scope)
                    for p in paths
                ):
                    continue
            for target in targets:
                if target not in self._index._by_name:
                    key = f"{stem}->{target}"
                    if key not in seen:
                        seen.add(key)
                        src_paths = self._index._by_name.get(stem, [])
                        src_path = (
                            str(
                                src_paths[0].relative_to(self._index.vault)
                            ).replace("\\", "/")
                            if src_paths
                            else stem
                        )
                        dangles.append(
                            {
                                "source": src_path,
                                "target": target,
                                "link_type": "wikilink",
                            }
                        )

        for _path, fm, rel in self._index._content:
            if scope and not rel.startswith(scope):
                continue
            up_val = fm.get("up")
            if not up_val:
                continue
            up_refs = [up_val] if isinstance(up_val, str) else list(up_val)
            for ref in up_refs:
                ref_stem = ref.strip().strip("[]").split("|")[0].strip()
                if ref_stem and ref_stem not in self._index._by_name:
                    key = f"{rel}->up:{ref_stem}"
                    if key not in seen:
                        seen.add(key)
                        dangles.append(
                            {
                                "source": rel,
                                "target": ref_stem,
                                "link_type": "up",
                            }
                        )

        return dangles
