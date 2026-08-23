"""TTL-cached vault index.

Wraps parsers.build_content_index with a time-based cache. The index
rebuilds automatically after TTL_SECONDS (default 300 = 5 min) or on
explicit reindex() call.
"""

# VERIFY: `dict[str, Any]` at the JSON boundary, and only there.
#
# An MCP tool return IS a JSON object, so the value type is open by the
# protocol's own contract — pinning it to a TypedDict per verb would encode a
# wire shape the client is free to ignore, and would still be `Any` one level
# down where Obsidian's REST payloads and YAML frontmatter arrive untyped.
# Measured 2026-08-22: of 276 `Any` in this package, 127 are `-> dict[str, Any]`
# verb returns and 34 are `list[dict[str, Any]]` rows of the same. This is a
# stated decision at the boundary, not an unexamined default.
#
# What is NOT excused by it: a BARE `: Any` or `-> Any` on anything that is not
# that boundary. Those were audited to zero in this package on the same date —
# the survivors are three sites in the Bases formula evaluator, each carrying
# its own VERIFY where it sits.

from __future__ import annotations

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

IMAGE_EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
BASE_EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|[^\]]+)?\]\]")


class VaultIndex:
    """In-memory vault index with TTL-based refresh and per-file invalidation."""

    @staticmethod
    def _normalize_link_targets(raw: list[str]) -> list[str]:
        """Strip path prefixes and .md suffixes to match by_name stems."""
        normalized = []
        for raw_target in raw:
            stem = raw_target.rsplit("/", 1)[-1].removesuffix(".md")
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

        # DELEGATION (vault-mcp#5294): the query, link-graph and governance
        # behaviours are named collaborators rather than 790 lines of methods
        # here. Each takes `self` and reads index state through it, so the
        # caches stay single-sourced on the index.
        #
        # Imported here, not at module scope: each collaborator imports
        # VaultIndex for typing.
        from vault_mcp.index_governance import IndexGovernance
        from vault_mcp.index_links import IndexLinkGraph
        from vault_mcp.index_queries import IndexQueries

        self._queries = IndexQueries(self)
        self._links = IndexLinkGraph(self)
        self._governance = IndexGovernance(self)

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
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("link graph: skip unreadable %s: %s", path, exc)
                continue

            body = strip_frontmatter(text)
            body_links = [
                m.group(1).strip() for m in WIKILINK_RE.finditer(body)
            ]

            up_val = fm.get("up", "")
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
            self._content, self._by_name, self._mtime = build_content_index(
                self.vault
            )
            self._build_link_graph()
            self._built_at = time.time()
            self.last_indexed_at = (
                datetime.fromtimestamp(self._built_at, tz=UTC)
                .astimezone()
                .isoformat(timespec="seconds")
            )
        elapsed_ms = int((self._built_at - t0) * 1000)
        log.info(
            "reindex: %d content, %d names in %dms",
            len(self._content),
            len(self._by_name),
            elapsed_ms,
        )
        return {
            "indexed": len(self._content),
            "names": len(self._by_name),
            "elapsed_ms": elapsed_ms,
        }

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
        top = parts[0] if parts else ""
        if top.startswith(".") or top in SKIP_DIRS:
            return

        stem = path.stem

        with self._lock:
            self._content = [
                (p, fm, r) for p, fm, r in self._content if p.resolve() != path
            ]
            for s, paths in list(self._by_name.items()):
                self._by_name[s] = [p for p in paths if p.resolve() != path]
                if not self._by_name[s]:
                    del self._by_name[s]

            old_outbound = self._outbound.pop(stem, [])
            for target in old_outbound:
                if target in self._inbound:
                    self._inbound[target] = [
                        s for s in self._inbound[target] if s != stem
                    ]
                    if not self._inbound[target]:
                        del self._inbound[target]

            self._mtime.pop(path, None)

            if not path.exists() or path.suffix not in (".md", ".base"):
                return

            stat = path.stat() if path.exists() else None
            if stat is not None:
                self._mtime[path] = stat.st_mtime

            if not top.startswith("."):
                self._by_name.setdefault(stem, []).append(path)

            if path.suffix != ".md":
                return

            if top in SKIP_CONTENT_CHECKS:
                return

            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.debug("invalidate: skip unreadable %s: %s", path, exc)
                return

            fm = parse_frontmatter(text)
            if not fm:
                return

            rel = path.relative_to(self.vault)
            rel_str = str(rel).replace("\\", "/")
            self._content.append((path, fm, rel_str))

            body = strip_frontmatter(text)
            body_links = [
                m.group(1).strip() for m in WIKILINK_RE.finditer(body)
            ]
            up_val = fm.get("up", "")
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

    # ------------------------------------------------------------------
    # Phase 2 — Graph tools
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 3 — Governance tools
    # ------------------------------------------------------------------

    BODY_TAG_RE = re.compile(
        r"(?<![\\])#([a-zA-Z][a-zA-Z0-9_/-]*(?:/[a-zA-Z0-9_☀-➿\U0001F300-\U0001FAFF-]+)*)"
    )

    # --- delegated surface --------------------------------------------------
    # Thin forwards, so VaultIndex's public API is unchanged: every verb and
    # every test still says `idx.read_note(...)`. The collaborators are
    # reachable directly (`idx._queries`) when a caller wants to be explicit.

    def find_notes_by_frontmatter(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Delegate to the queries collaborator."""
        return self._queries.find_notes_by_frontmatter(*args, **kwargs)

    def find_by_filename(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Delegate to the queries collaborator."""
        return self._queries.find_by_filename(*args, **kwargs)

    def recent_edits(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegate to the queries collaborator."""
        return self._queries.recent_edits(*args, **kwargs)

    def read_note(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate to the queries collaborator."""
        return self._queries.read_note(*args, **kwargs)

    def backlinks_to(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegate to the links collaborator."""
        return self._links.backlinks_to(*args, **kwargs)

    def outbound_links(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegate to the links collaborator."""
        return self._links.outbound_links(*args, **kwargs)

    def find_orphans(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegate to the links collaborator."""
        return self._links.find_orphans(*args, **kwargs)

    def find_dangling_links(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Delegate to the links collaborator."""
        return self._links.find_dangling_links(*args, **kwargs)

    @staticmethod
    def parse_audit_ignores(*args: Any, **kwargs: Any) -> list[str]:
        """Delegate to IndexGovernance.

        STATIC, because callers invoke this unbound —
        `VaultIndex.parse_audit_ignores(path)` in verbs_query and in the
        tests. A self-taking forward binds the path argument as `self`.
        """
        from vault_mcp.index_governance import IndexGovernance

        return IndexGovernance.parse_audit_ignores(*args, **kwargs)

    @staticmethod
    def parse_tags_glossary(*args: Any, **kwargs: Any) -> set[str]:
        """Delegate to IndexGovernance.

        STATIC, because callers invoke this unbound —
        `VaultIndex.parse_tags_glossary(path)` in verbs_query and in the
        tests. A self-taking forward binds the path argument as `self`.
        """
        from vault_mcp.index_governance import IndexGovernance

        return IndexGovernance.parse_tags_glossary(*args, **kwargs)

    def tag_glossary_check(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Delegate to the governance collaborator."""
        return self._governance.tag_glossary_check(*args, **kwargs)

    def vault_stats(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate to the governance collaborator."""
        return self._governance.vault_stats(*args, **kwargs)

    def all_tags(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegate to the governance collaborator."""
        return self._governance.all_tags(*args, **kwargs)
