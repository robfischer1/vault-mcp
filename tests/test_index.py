"""Unit tests for vault_mcp.index against the mini-vault fixture."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.index import VaultIndex

MINI_VAULT = ROOT / "tests" / "fixtures" / "mini-vault"


def _idx() -> VaultIndex:
    idx = VaultIndex(MINI_VAULT, ttl_seconds=9999)
    idx.reindex()
    return idx


class TestReindex:
    def test_reindex_returns_stats(self):
        idx = VaultIndex(MINI_VAULT, ttl_seconds=9999)
        stats = idx.reindex()
        assert stats["indexed"] > 0
        assert stats["names"] > 0
        assert stats["elapsed_ms"] >= 0

    def test_content_excludes_archives(self):
        idx = _idx()
        rel_paths = [rel for _, _, rel in idx.content]
        assert not any("Archives/" in r for r in rel_paths)

    def test_by_name_includes_archives(self):
        idx = _idx()
        assert "old-note" in idx.by_name

    def test_by_name_includes_no_frontmatter(self):
        idx = _idx()
        assert "no-frontmatter" in idx.by_name

    def test_content_excludes_no_frontmatter(self):
        idx = _idx()
        stems = [p.stem for p, _, _ in idx.content]
        assert "no-frontmatter" not in stems


class TestFindNotesByFrontmatter:
    def test_find_by_note_type(self):
        idx = _idx()
        results = idx.find_notes_by_frontmatter({"note_type": "daily"})
        assert len(results) == 2
        assert all("Journal" in r["path"] for r in results)

    def test_find_with_scope(self):
        idx = _idx()
        results = idx.find_notes_by_frontmatter(
            {"note_type": "daily"}, scope="Timelines/Journal/"
        )
        assert len(results) == 2

    def test_find_no_match(self):
        idx = _idx()
        results = idx.find_notes_by_frontmatter({"note_type": "nonexistent"})
        assert len(results) == 0

    def test_find_by_status(self):
        idx = _idx()
        results = idx.find_notes_by_frontmatter({"status": "Active"})
        paths = [r["path"] for r in results]
        assert any("AGENTS" in p for p in paths)
        assert any("Stoicism" in p for p in paths)


class TestFindByFilename:
    def test_glob_pattern(self):
        idx = _idx()
        results = idx.find_by_filename("2026-05-*")
        assert len(results) == 2

    def test_exact_stem(self):
        idx = _idx()
        results = idx.find_by_filename("AGENTS")
        assert len(results) == 1
        assert "System/Governance/AGENTS.md" in results[0]["path"]

    def test_scope_filter(self):
        idx = _idx()
        results = idx.find_by_filename("*", scope="Garden/")
        stems = [r["stem"] for r in results]
        assert "Stoicism" in stems
        assert "no-frontmatter" in stems


class TestRecentEdits:
    def test_returns_results(self):
        idx = _idx()
        results = idx.recent_edits(since="2020-01-01")
        assert len(results) > 0
        assert "path" in results[0]
        assert "modified" in results[0]

    def test_limit(self):
        idx = _idx()
        results = idx.recent_edits(since="2020-01-01", limit=2)
        assert len(results) <= 2

    def test_bad_date(self):
        idx = _idx()
        results = idx.recent_edits(since="not-a-date")
        assert results[0].get("error")


class TestReadNote:
    def test_read_by_stem(self):
        idx = _idx()
        result = idx.read_note("Stoicism")
        assert result["path"] == "Garden/Stoicism.md"
        assert result["frontmatter"]["@type"] == "CreativeWork"
        assert "philosophy" in result["body"]

    def test_read_by_path(self):
        idx = _idx()
        result = idx.read_note("System/Governance/AGENTS.md")
        assert result["frontmatter"]["identifier"] == "agents"

    def test_read_not_found(self):
        idx = _idx()
        result = idx.read_note("DoesNotExist")
        assert result["error"] == "not_found"

    def test_outbound_links_resolved(self):
        idx = _idx()
        result = idx.read_note("Stoicism")
        links = result["outbound_links"]
        stems = [lnk["stem"] for lnk in links]
        assert "AGENTS" in stems
        resolved = [lnk for lnk in links if lnk["stem"] == "AGENTS"]
        assert "path" in resolved[0]

    def test_unresolved_link(self):
        idx = _idx()
        result = idx.read_note("Stoicism")
        links = result["outbound_links"]
        unresolved = [lnk for lnk in links if lnk["stem"] == "Nonexistent Note"]
        assert len(unresolved) == 1
        assert unresolved[0]["resolution"] == "unresolved"


BASES_VAULT = ROOT / "tests" / "fixtures" / "bases-vault"


def _idx_bases() -> VaultIndex:
    idx = VaultIndex(BASES_VAULT, ttl_seconds=9999)
    idx.reindex()
    return idx


class TestReadNoteEmbeds:
    def test_read_note_standalone_embed(self):
        idx = _idx_bases()
        result = idx.read_note("Projects.md")
        assert "resolved_embeds" in result
        embeds = result["resolved_embeds"]
        assert len(embeds) == 2

        # ![[ActiveProjects.base#CardView]]
        card_view = next(e for e in embeds if "#CardView" in e["token"])
        assert card_view["path"] == "ActiveProjects.base"
        assert "results" in card_view
        assert card_view["results"]["view_name"] == "CardView"
        # In our fixture, only Alpha has note_type: plan and status: active
        assert card_view["results"]["total"] >= 1

        # ![[ActiveProjects.base]]
        default_view = next(e for e in embeds if "#" not in e["token"])
        assert default_view["path"] == "ActiveProjects.base"
        assert "results" in default_view
        assert default_view["results"]["total"] >= 1

    def test_read_note_inline_embed(self):
        idx = _idx_bases()
        result = idx.read_note("Report.md")
        assert "resolved_embeds" in result
        embeds = result["resolved_embeds"]
        assert len(embeds) == 1

        inv = embeds[0]
        assert inv["path"] == "Database.md"
        assert inv["results"]["view_name"] == "Inventory"
        assert inv["results"]["total"] >= 3  # Alpha, Beta, Gamma

    def test_read_note_missing_embed_targets(self):
        idx = _idx_bases()
        result = idx.read_note("MissingTarget.md")
        assert "resolved_embeds" in result
        embeds = result["resolved_embeds"]
        assert len(embeds) == 2

        missing_file = next(e for e in embeds if "Missing.base" in e["token"])
        assert missing_file["error"]["type"] == "not_found"

        missing_view = next(e for e in embeds if "#MissingView" in e["token"])
        assert missing_view["error"]["type"] == "view_not_found"



# ------------------------------------------------------------------
# Phase 2 — Graph tools
# ------------------------------------------------------------------

class TestBacklinksTo:
    def test_agents_has_backlinks(self):
        idx = _idx()
        results = idx.backlinks_to("AGENTS")
        stems = [r["stem"] for r in results]
        assert "Stoicism" in stems
        assert "2026-05-01" in stems

    def test_no_backlinks(self):
        idx = _idx()
        results = idx.backlinks_to("DoesNotExist")
        assert len(results) == 0

    def test_up_link_counts(self):
        """Stoicism has up: [[Garden]] — Garden should show in backlinks_to('Garden')."""
        idx = _idx()
        results = idx.backlinks_to("Garden")
        stems = [r["stem"] for r in results]
        assert "Stoicism" in stems


class TestOutboundLinks:
    def test_stoicism_outbound(self):
        idx = _idx()
        results = idx.outbound_links("Stoicism")
        stems = [r["stem"] for r in results]
        assert "AGENTS" in stems
        assert "Nonexistent Note" in stems

    def test_no_outbound(self):
        idx = _idx()
        results = idx.outbound_links("old-note")
        assert len(results) == 0


class TestFindOrphans:
    def test_finds_orphans(self):
        idx = _idx()
        results = idx.find_orphans()
        paths = [r["path"] for r in results]
        # 2026-05-02 has no up: and nothing links to it
        assert any("2026-05-02" in p for p in paths)

    def test_non_orphan_excluded(self):
        idx = _idx()
        results = idx.find_orphans()
        stems = [r["stem"] for r in results]
        # AGENTS has backlinks from Stoicism and 2026-05-01
        assert "AGENTS" not in stems

    def test_scope_filter(self):
        idx = _idx()
        results = idx.find_orphans(scope="System/")
        paths = [r["path"] for r in results]
        assert not any("Garden/" in p for p in paths)

    def test_exempt_prefixes(self):
        idx = _idx()
        all_orphans = idx.find_orphans()
        filtered = idx.find_orphans(exempt_prefixes=["Timelines/Journal/"])
        assert len(filtered) <= len(all_orphans)
        assert not any("Timelines/Journal/" in r["path"] for r in filtered)


class TestParseAuditIgnores:
    def test_parses_fixture(self):
        from vault_mcp.index import VaultIndex
        ignores_path = ROOT / "tests" / "fixtures" / "audit-ignores-fixture.md"
        if not ignores_path.exists():
            ignores_path.write_text(
                "### Folder-level exemptions (missing-up only)\n\n"
                "| Path prefix | Reason | Date added |\n"
                "| :--- | :--- | :--- |\n"
                "| `Entities/People/` | test | 2026-01-01 |\n"
                "| `Archives/` | test | 2026-01-01 |\n"
            )
        prefixes = VaultIndex.parse_audit_ignores(ignores_path)
        assert "Entities/People/" in prefixes
        assert "Archives/" in prefixes


# ------------------------------------------------------------------
# Phase 3 — Governance tools
# ------------------------------------------------------------------

class TestParseTagsGlossary:
    def test_parses_mini_vault_glossary(self):
        from vault_mcp.index import VaultIndex
        glossary = MINI_VAULT / "System" / "Tags Glossary.md"
        tags = VaultIndex.parse_tags_glossary(glossary)
        assert "#todo" in tags
        assert "#starred" in tags
        assert "#philosophy" in tags


class TestTagGlossaryCheck:
    def test_finds_invalid_tags(self):
        idx = _idx()
        glossary = MINI_VAULT / "System" / "Tags Glossary.md"
        results = idx.tag_glossary_check(glossary)
        all_invalid = []
        for r in results:
            all_invalid.extend(r["invalid_tags"])
        assert "#bogus-tag" in all_invalid

    def test_valid_tag_not_flagged(self):
        idx = _idx()
        glossary = MINI_VAULT / "System" / "Tags Glossary.md"
        results = idx.tag_glossary_check(glossary)
        all_invalid = []
        for r in results:
            all_invalid.extend(r["invalid_tags"])
        assert "#philosophy" not in all_invalid

    def test_escaped_tag_not_flagged(self):
        idx = _idx()
        glossary = MINI_VAULT / "System" / "Tags Glossary.md"
        results = idx.tag_glossary_check(glossary)
        all_invalid = []
        for r in results:
            all_invalid.extend(r["invalid_tags"])
        assert "#escaped" not in all_invalid


class TestVaultStats:
    def test_returns_structure(self):
        idx = _idx()
        stats = idx.vault_stats()
        assert "total_indexed" in stats
        assert "total_names" in stats
        assert "types" in stats
        assert stats["total_indexed"] > 0

    def test_types_populated(self):
        idx = _idx()
        stats = idx.vault_stats()
        type_names = [t["type"] for t in stats["types"]]
        assert "CreativeWork" in type_names
        assert "DigitalDocument" in type_names


# ------------------------------------------------------------------
# Phase 4 — Incremental invalidation
# ------------------------------------------------------------------

class TestInvalidateFile:
    def test_invalidate_removes_from_content(self):
        idx = _idx()
        p = MINI_VAULT / "Garden" / "Stoicism.md"
        before = len([1 for pp, _, _ in idx._content if pp.resolve() == p.resolve()])
        assert before == 1
        idx.invalidate_file(p)
        after = len([1 for pp, _, _ in idx._content if pp.resolve() == p.resolve()])
        # Re-parsed, so still 1
        assert after == 1

    def test_invalidate_deleted_file(self, tmp_path):
        idx = _idx()
        test_file = MINI_VAULT / "Garden" / "_temp_test.md"
        test_file.write_text("---\nname: temp\n---\nBody [[AGENTS]].", encoding='utf-8')
        idx.invalidate_file(test_file)
        assert "AGENTS" in idx._outbound.get("_temp_test", [])
        test_file.unlink()
        idx.invalidate_file(test_file)
        assert "_temp_test" not in idx._outbound

    def test_invalidate_updates_links(self):
        idx = _idx()
        p = MINI_VAULT / "Garden" / "Stoicism.md"
        original_outbound = idx._outbound.get("Stoicism", [])
        assert "AGENTS" in original_outbound
        idx.invalidate_file(p)
        refreshed_outbound = idx._outbound.get("Stoicism", [])
        assert "AGENTS" in refreshed_outbound

    def test_invalidate_outside_vault_ignored(self):
        idx = _idx()
        before_content = len(idx._content)
        idx.invalidate_file(Path("/tmp/not-in-vault.md"))
        assert len(idx._content) == before_content

    def test_last_indexed_at_set(self):
        idx = _idx()
        assert idx.last_indexed_at is not None
