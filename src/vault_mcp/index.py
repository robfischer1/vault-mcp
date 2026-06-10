"""TTL-cached vault index.

Wraps parsers.build_content_index with a time-based cache. The index
rebuilds automatically after TTL_SECONDS (default 300 = 5 min) or on
explicit reindex() call.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import logging
import re
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .parsers import (
    SKIP_CONTENT_CHECKS,
    SKIP_DIRS,
    WIKILINK_RE,
    build_content_index,
    extract_wikilink_targets,
    parse_frontmatter,
    strip_frontmatter,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_EMBED_RE = re.compile(r'!\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]')
BASE_EMBED_RE = re.compile(r'!\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|[^\]]+)?\]\]')


class VaultIndex:
    """In-memory vault index with TTL-based refresh and per-file invalidation."""

    @staticmethod
    def _normalize_link_targets(raw: list[str]) -> list[str]:
        """Strip path prefixes and .md suffixes to match by_name stems."""
        normalized = []
        for raw_target in raw:
            stem = raw_target.rsplit('/', 1)[-1].removesuffix('.md')
            normalized.append(stem)
        return list(set(normalized))

    def __init__(self, vault_path: Path, ttl_seconds: int = 300):
        """Initialize an empty index over a vault path with the given TTL (seconds)."""
        self.vault = vault_path.resolve()
        self.ttl = ttl_seconds
        self._content: list[tuple[Path, dict[str, Any], str]] = []
        self._by_name: dict[str, list[Path]] = {}
        self._mtime: dict[Path, float] = {}
        self._outbound: dict[str, list[str]] = {}
        self._inbound: dict[str, list[str]] = {}
        self._built_at: float = 0.0
        self._lock = threading.Lock()
        self._watcher_active = False
        self.last_indexed_at: str | None = None
        self.on_invalidate: list[Callable[[Path], None]] = []

    def _ensure_fresh(self) -> None:
        if self._watcher_active:
            if self._built_at == 0.0:
                self.reindex()
            return
        if time.time() - self._built_at > self.ttl:
            self.reindex()

    def _build_link_graph(self) -> None:
        """Compute outbound/inbound link maps from indexed content."""
        outbound: dict[str, list[str]] = {}
        inbound: dict[str, list[str]] = {}

        for path, fm, _rel in self._content:
            stem = path.stem
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("link graph: skip unreadable %s: %s", path, exc)
                continue

            body = strip_frontmatter(text)
            body_links = [m.group(1).strip() for m in WIKILINK_RE.finditer(body)]

            up_val = fm.get('up', '')
            up_links = extract_wikilink_targets(up_val)

            raw_targets = list(set(body_links + up_links))
            all_targets = self._normalize_link_targets(raw_targets)
            outbound[stem] = all_targets

            for target in all_targets:
                inbound.setdefault(target, []).append(stem)

        self._outbound = outbound
        self._inbound = inbound

    def reindex(self) -> dict[str, Any]:
        """Force full rebuild. Returns stats."""
        t0 = time.time()
        with self._lock:
            self._content, self._by_name, self._mtime = build_content_index(self.vault)
            self._build_link_graph()
            self._built_at = time.time()
            self.last_indexed_at = (
                datetime.fromtimestamp(self._built_at, tz=UTC).astimezone().isoformat(timespec="seconds")
            )
        elapsed_ms = int((self._built_at - t0) * 1000)
        log.info("reindex: %d content, %d names in %dms", len(self._content), len(self._by_name), elapsed_ms)
        return {"indexed": len(self._content), "names": len(self._by_name), "elapsed_ms": elapsed_ms}

    def invalidate_file(self, path: Path) -> None:
        """Re-parse a single file and update all index structures."""
        if self._built_at == 0.0:
            return

        path = path.resolve()
        vault_resolved = self.vault.resolve()
        try:
            rel_dir = path.parent.relative_to(vault_resolved)
        except ValueError:
            # Try again with resolved parent
            try:
                rel_dir = path.parent.resolve().relative_to(vault_resolved)
            except ValueError:
                return

        parts = rel_dir.parts
        top = parts[0] if parts else ''
        if top.startswith('.') or top in SKIP_DIRS:
            return

        stem = path.stem

        with self._lock:
            self._content = [(p, fm, r) for p, fm, r in self._content if p.resolve() != path]
            for s, paths in list(self._by_name.items()):
                self._by_name[s] = [p for p in paths if p.resolve() != path]
                if not self._by_name[s]:
                    del self._by_name[s]

            old_outbound = self._outbound.pop(stem, [])
            for target in old_outbound:
                if target in self._inbound:
                    self._inbound[target] = [s for s in self._inbound[target] if s != stem]
                    if not self._inbound[target]:
                        del self._inbound[target]

            self._mtime.pop(path, None)

            if not path.exists() or path.suffix not in ('.md', '.base'):
                return

            stat = path.stat() if path.exists() else None
            if stat is not None:
                self._mtime[path] = stat.st_mtime

            if not top.startswith('.'):
                self._by_name.setdefault(stem, []).append(path)

            if path.suffix != '.md':
                return

            if top in SKIP_CONTENT_CHECKS:
                return

            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("invalidate: skip unreadable %s: %s", path, exc)
                return

            fm = parse_frontmatter(text)
            if not fm:
                return

            rel = path.relative_to(self.vault)
            rel_str = str(rel).replace('\\', '/')
            self._content.append((path, fm, rel_str))

            body = strip_frontmatter(text)
            body_links = [m.group(1).strip() for m in WIKILINK_RE.finditer(body)]
            up_val = fm.get('up', '')
            up_links = extract_wikilink_targets(up_val)

            raw_targets = list(set(body_links + up_links))
            all_targets = self._normalize_link_targets(raw_targets)
            self._outbound[stem] = all_targets

            for target in all_targets:
                self._inbound.setdefault(target, []).append(stem)

        for callback in self.on_invalidate:
            try:
                callback(path)
            except Exception:
                log.exception("Error in on_invalidate callback for %s", path)

    def enable_watcher(self) -> None:
        """Mark that a filesystem watcher is active — disables TTL refresh."""
        self._watcher_active = True

    @property
    def content(self) -> list[tuple[Path, dict[str, Any], str]]:
        """Return the indexed (path, frontmatter, rel) tuples, refreshing if stale."""
        self._ensure_fresh()
        return self._content

    @property
    def by_name(self) -> dict[str, list[Path]]:
        """Return the stem→paths name index, refreshing if stale."""
        self._ensure_fresh()
        return self._by_name

    def find_notes_by_frontmatter(
        self, filters: dict[str, Any], scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Equality-match on frontmatter fields, with optional scope prefix."""
        results = []
        for _path, fm, rel in self.content:
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
        for stem, paths in self.by_name.items():
            if not fnmatch.fnmatch(stem, pattern):
                continue
            for p in paths:
                rel = str(p.relative_to(self.vault)).replace('\\', '/')
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

        for stem, paths in self.by_name.items():
            for p in paths:
                rel = str(p.relative_to(self.vault)).replace('\\', '/')
                if scope and not rel.startswith(scope):
                    continue
                mtime = self._mtime.get(p)
                if mtime is None:
                    continue
                if mtime >= since_ts:
                    candidates.append({
                        "path": rel,
                        "modified": datetime.fromtimestamp(mtime, tz=UTC).astimezone().isoformat(timespec="seconds"),
                        "stem": stem,
                    })

        candidates.sort(key=lambda r: r["modified"], reverse=True)
        return candidates[:limit]

    def read_note(self, stem_or_path: str) -> dict[str, Any]:
        """Read a note by wikilink stem or vault-relative path.

        Returns {frontmatter, body, path} with resolved wikilinks per Q7.
        """
        target_path: Path | None = None

        if '/' in stem_or_path or stem_or_path.endswith('.md'):
            candidate = self.vault / stem_or_path
            if candidate.exists():
                target_path = candidate

        if target_path is None:
            stem = stem_or_path.removesuffix('.md')
            matches = self.by_name.get(stem, [])
            if len(matches) == 1:
                target_path = matches[0]
            elif len(matches) > 1:
                return {
                    "error": "ambiguous",
                    "stem": stem,
                    "candidates": [
                        str(p.relative_to(self.vault)).replace('\\', '/') for p in matches
                    ],
                }
            else:
                return {"error": "not_found", "query": stem_or_path}

        try:
            text = target_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as e:
            return {"error": "read_failed", "path": str(target_path), "detail": str(e)}

        fm = parse_frontmatter(text)
        body = strip_frontmatter(text).lstrip('\n\r')
        rel = str(target_path.relative_to(self.vault)).replace("\\", "/")

        outbound = extract_wikilink_targets(body)
        resolved_links: list[dict[str, Any]] = []
        for stem in outbound:
            link_matches = self.by_name.get(stem, [])
            if len(link_matches) == 1:
                resolved_links.append({
                    "stem": stem,
                    "path": str(link_matches[0].relative_to(self.vault)).replace('\\', '/'),
                })
            elif len(link_matches) > 1:
                resolved_links.append({
                    "stem": stem,
                    "resolution": "ambiguous",
                    "candidates": [
                        str(p.relative_to(self.vault)).replace('\\', '/') for p in link_matches
                    ],
                })
            else:
                resolved_links.append({"stem": stem, "resolution": "unresolved"})

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
            matches = self.by_name.get(stem, [])

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
                        "path": str(embed_target_path.relative_to(self.vault)).replace(
                            "\\", "/"
                        ),
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
                            "path": str(embed_target_path.relative_to(self.vault)).replace(
                                "\\", "/"
                            ),
                            "error": {
                                "type": "view_not_found",
                                "message": f"View '{view_name}' not found",
                            },
                        }
                    )
                    continue

            res = execute_base(base, self, view_name=view_name)

            resolved_embeds.append(
                {
                    "token": token,
                    "path": str(embed_target_path.relative_to(self.vault)).replace(
                        "\\", "/"
                    ),
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

    # ------------------------------------------------------------------
    # Phase 2 — Graph tools
    # ------------------------------------------------------------------

    def backlinks_to(self, stem: str) -> list[dict[str, Any]]:
        """Files that link to `stem` via body wikilinks or `up:` frontmatter."""
        self._ensure_fresh()
        sources = self._inbound.get(stem, [])
        results = []
        for src_stem in sources:
            paths = self._by_name.get(src_stem, [])
            for p in paths:
                results.append({
                    "stem": src_stem,
                    "path": str(p.relative_to(self.vault)).replace('\\', '/'),
                })
        return results

    def outbound_links(
        self, stem: str, include_image_embeds: bool = False
    ) -> list[dict[str, Any]]:
        """Wikilinks from `stem`'s body (excluding image embeds by default)."""
        self._ensure_fresh()
        targets = self._outbound.get(stem, [])

        if not include_image_embeds:
            paths = self._by_name.get(stem, [])
            image_stems: set[str] = set()
            for p in paths:
                try:
                    text = p.read_text(encoding='utf-8')
                except (OSError, UnicodeDecodeError) as exc:
                    log.debug("image-embed scan: skip unreadable %s: %s", p, exc)
                    continue
                body = strip_frontmatter(text)
                for m in IMAGE_EMBED_RE.finditer(body):
                    image_stems.add(m.group(1).strip())
            targets = [t for t in targets if t not in image_stems]

        results: list[dict[str, Any]] = []
        for target in targets:
            matches = self._by_name.get(target, [])
            if len(matches) == 1:
                results.append({
                    "stem": target,
                    "path": str(matches[0].relative_to(self.vault)).replace('\\', '/'),
                })
            elif len(matches) > 1:
                results.append({
                    "stem": target,
                    "resolution": "ambiguous",
                    "candidates": [
                        str(p.relative_to(self.vault)).replace('\\', '/')
                        for p in matches
                    ],
                })
            else:
                results.append({"stem": target, "resolution": "unresolved"})
        return results

    def find_orphans(
        self, scope: str | None = None,
        exempt_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Files with no inbound links and no `up:` frontmatter.

        Honors folder-prefix exemptions from audit-ignores.md.
        """
        self._ensure_fresh()
        results = []
        for path, fm, rel in self._content:
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
            has_up = bool(fm.get('up', ''))
            has_inbound = stem in self._inbound

            if not has_up and not has_inbound:
                reasons = []
                if not has_up:
                    reasons.append("no up: frontmatter")
                if not has_inbound:
                    reasons.append("no inbound links")
                results.append({
                    "path": rel,
                    "stem": stem,
                    "reason": "; ".join(reasons),
                })
        return results

    def find_dangling_links(
        self, scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find wikilinks and ``up:`` values that point at non-existent notes."""
        self._ensure_fresh()
        dangles: list[dict[str, Any]] = []
        seen: set[str] = set()

        for stem, targets in self._outbound.items():
            if scope:
                paths = self._by_name.get(stem, [])
                if paths and not any(
                    str(p.relative_to(self.vault)).replace("\\", "/").startswith(scope)
                    for p in paths
                ):
                    continue
            for target in targets:
                if target not in self._by_name:
                    key = f"{stem}->{target}"
                    if key not in seen:
                        seen.add(key)
                        src_paths = self._by_name.get(stem, [])
                        src_path = (
                            str(src_paths[0].relative_to(self.vault)).replace("\\", "/")
                            if src_paths else stem
                        )
                        dangles.append({
                            "source": src_path,
                            "target": target,
                            "link_type": "wikilink",
                        })

        for _path, fm, rel in self._content:
            if scope and not rel.startswith(scope):
                continue
            up_val = fm.get("up")
            if not up_val:
                continue
            up_refs = [up_val] if isinstance(up_val, str) else list(up_val)
            for ref in up_refs:
                ref_stem = ref.strip().strip("[]").split("|")[0].strip()
                if ref_stem and ref_stem not in self._by_name:
                    key = f"{rel}->up:{ref_stem}"
                    if key not in seen:
                        seen.add(key)
                        dangles.append({
                            "source": rel,
                            "target": ref_stem,
                            "link_type": "up",
                        })

        return dangles

    @staticmethod
    def parse_audit_ignores(ignores_path: Path) -> list[str]:
        """Extract folder-prefix exemptions from audit-ignores.md."""
        if not ignores_path.exists():
            return []
        text = ignores_path.read_text(encoding='utf-8')
        prefixes = []
        in_folder_table = False
        for line in text.split('\n'):
            if 'Folder-level exemptions' in line:
                in_folder_table = True
                continue
            if in_folder_table and line.startswith('| `'):
                m = re.match(r'\|\s*`([^`]+)`', line)
                if m:
                    prefix = m.group(1).rstrip('/')
                    prefixes.append(prefix + '/')
            elif in_folder_table and line.startswith('#'):
                break
        return prefixes

    # ------------------------------------------------------------------
    # Phase 3 — Governance tools
    # ------------------------------------------------------------------

    BODY_TAG_RE = re.compile(
        r'(?<![\\])#([a-zA-Z][a-zA-Z0-9_/-]*(?:/[a-zA-Z0-9_☀-➿\U0001F300-\U0001FAFF-]+)*)'
    )

    @staticmethod
    def parse_tags_glossary(glossary_path: Path) -> set[str]:
        """Extract the closed tag vocabulary from Tags Glossary.md."""
        if not glossary_path.exists():
            return set()
        text = glossary_path.read_text(encoding='utf-8')
        tags: set[str] = set()
        in_code = False
        for line in text.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
                continue
            if in_code:
                stripped = line.strip()
                if stripped.startswith('#'):
                    tag = stripped.split(' ')[0].split(' -')[0].strip()
                    if tag:
                        tags.add(tag)
        return tags

    def tag_glossary_check(self, glossary_path: Path) -> list[dict[str, Any]]:
        r"""Find body #tags not in the Tags Glossary.

        Excludes \\#tag escapes and #activity/processed per policy.
        """
        self._ensure_fresh()
        valid_tags = self.parse_tags_glossary(glossary_path)
        violations: dict[str, list[str]] = {}

        for path, _fm, rel in self._content:
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("tag check: skip unreadable %s: %s", path, exc)
                continue

            body = strip_frontmatter(text)
            for m in self.BODY_TAG_RE.finditer(body):
                tag = '#' + m.group(1)
                if tag in valid_tags:
                    continue
                if tag == '#activity/processed':
                    continue
                violations.setdefault(rel, []).append(tag)

        results = []
        for file_path, tags in sorted(violations.items()):
            results.append({
                "path": file_path,
                "invalid_tags": sorted(set(tags)),
            })
        return results

    def vault_stats(self) -> dict[str, Any]:
        """Aggregate stats: counts by @type, top tags, edit volume by week."""
        self._ensure_fresh()
        type_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        week_counts: dict[str, int] = {}

        for path, fm, _rel in self._content:
            at_type = fm.get('@type', fm.get('type', 'unknown'))
            if isinstance(at_type, str):
                type_counts[at_type] = type_counts.get(at_type, 0) + 1

            fm_tags = fm.get('tags', [])
            if isinstance(fm_tags, list):
                for t in fm_tags:
                    if isinstance(t, str) and t:
                        tag_counts[t] = tag_counts.get(t, 0) + 1

            mtime = self._mtime.get(path)
            if mtime is not None:
                week = datetime.fromtimestamp(mtime, tz=UTC).astimezone().strftime('%Y-W%W')
                week_counts[week] = week_counts.get(week, 0) + 1

        top_types = sorted(type_counts.items(), key=lambda x: -x[1])
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:20]
        recent_weeks = sorted(week_counts.items(), reverse=True)[:8]

        return {
            "total_indexed": len(self._content),
            "total_names": len(self._by_name),
            "types": [{"type": t, "count": c} for t, c in top_types],
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "edit_volume_by_week": [{"week": w, "count": c} for w, c in recent_weeks],
        }

    def all_tags(self, include_body: bool = True) -> list[dict[str, Any]]:
        """Collect all tags from frontmatter and optionally body text, with counts.

        Returns sorted list of {tag, count, sources} where sources indicates
        whether the tag appears in frontmatter, body, or both.
        """
        self._ensure_fresh()
        fm_counts: dict[str, int] = {}
        body_counts: dict[str, int] = {}

        for path, fm, _rel in self._content:
            fm_tags = fm.get('tags', [])
            if isinstance(fm_tags, list):
                for t in fm_tags:
                    if isinstance(t, str) and t:
                        fm_counts[t] = fm_counts.get(t, 0) + 1

            if include_body:
                try:
                    text = path.read_text(encoding='utf-8')
                except (OSError, UnicodeDecodeError) as exc:
                    log.debug("all_tags: skip unreadable %s: %s", path, exc)
                    continue
                body = strip_frontmatter(text)
                for m in self.BODY_TAG_RE.finditer(body):
                    tag = '#' + m.group(1)
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
            results.append({
                "tag": tag,
                "count": fc + bc,
                "frontmatter_count": fc,
                "body_count": bc,
                "sources": sources,
            })
        results.sort(key=lambda x: -(x["count"] if isinstance(x["count"], int) else 0))
        return results
