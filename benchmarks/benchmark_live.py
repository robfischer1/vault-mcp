"""Performance benchmark for vault-mcp against a live vault.

Usage:
    uv run python tests/benchmark_live.py <vault-path>

Exercises all VaultIndex operations and reports timing statistics.
Not a pytest test — run manually for one-shot perf validation.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.index import VaultIndex  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def _time(fn, *, iterations: int = 5) -> dict:
    """Run fn() N times, return timing stats in ms."""
    times = []
    result = None
    for _ in range(iterations):
        t0 = time.perf_counter()
        result = fn()
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)
    return {
        "min_ms": round(min(times), 1),
        "mean_ms": round(statistics.mean(times), 1),
        "max_ms": round(max(times), 1),
        "median_ms": round(statistics.median(times), 1),
        "iterations": iterations,
        "result": result,
    }


def _count(result):
    """Extract a count from various result types."""
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return result.get("total_indexed", result.get("indexed", "n/a"))
    return "n/a"


def run_benchmark(vault_path: Path) -> None:
    print("vault-mcp Performance Benchmark")
    print(f"{'=' * 60}")
    print(f"Vault: {vault_path}")
    print()

    idx = VaultIndex(vault_path, ttl_seconds=9999)

    # --- Phase 0: Cold reindex ---
    print("Phase 0: Cold reindex (full vault scan + link graph)")
    stats = _time(idx.reindex, iterations=3)
    reindex_result = stats["result"]
    print(f"  Indexed: {reindex_result['indexed']} content files")
    print(f"  Names:   {reindex_result['names']} unique stems")
    print(
        f"  Timing:  {stats['min_ms']:.0f} / {stats['median_ms']:.0f} / {stats['max_ms']:.0f} ms (min/median/max)"
    )
    print()

    # --- Phase 1: find_notes_by_frontmatter ---
    print("Phase 1: find_notes_by_frontmatter")

    queries = [
        ("@type = Person", {"@type": "Person"}, None),
        ("@type = CreativeWork", {"@type": "CreativeWork"}, None),
        ("note_type = task", {"note_type": "task"}, None),
        ("note_type = plan", {"note_type": "plan"}, None),
        ("scoped to Entities/", {"@type": "Person"}, "Entities/"),
        ("scoped to System/", {"note_type": "task"}, "System/"),
        ("author_type = external", {"author_type": "external"}, None),
        ("status = complete", {"status": "complete"}, None),
    ]
    for label, filters, scope in queries:
        stats = _time(
            lambda f=filters, s=scope: idx.find_notes_by_frontmatter(f, s)
        )
        count = _count(stats["result"])
        print(
            f"  {label:35s} → {count:>4} results  {stats['median_ms']:7.1f} ms median"
        )
    print()

    # --- Phase 2: find_by_filename ---
    print("Phase 2: find_by_filename")

    filename_queries = [
        ("exact stem: AGENTS", "AGENTS"),
        ("glob *-handoff*", "*-handoff*"),
        ("glob 2026-05-*", "2026-05-*"),
        ("glob *Plan*", "*Plan*"),
    ]
    for label, pattern in filename_queries:
        stats = _time(lambda p=pattern: idx.find_by_filename(p))
        count = _count(stats["result"])
        print(
            f"  {label:35s} → {count:>4} results  {stats['median_ms']:7.1f} ms median"
        )
    print()

    # --- Phase 3: Graph queries ---
    print("Phase 3: Graph queries (backlinks / outbound)")

    well_connected = ["Plans", "Tasks", "AGENTS", "MEMORY", "SCHEMA"]
    for stem in well_connected:
        stats_bl = _time(lambda s=stem: idx.backlinks_to(s))
        stats_ol = _time(lambda s=stem: idx.outbound_links(s))
        bl_count = _count(stats_bl["result"])
        ol_count = _count(stats_ol["result"])
        print(
            f"  {stem:20s}  backlinks: {bl_count:>3} ({stats_bl['median_ms']:5.1f} ms)  outbound: {ol_count:>3} ({stats_ol['median_ms']:5.1f} ms)"
        )
    print()

    # --- Phase 4: find_orphans ---
    print("Phase 4: find_orphans")

    stats = _time(idx.find_orphans, iterations=3)
    orphan_count = _count(stats["result"])
    print(f"  Orphans found: {orphan_count}")
    print(
        f"  Timing: {stats['min_ms']:.0f} / {stats['median_ms']:.0f} / {stats['max_ms']:.0f} ms (min/median/max)"
    )

    ignores_path = (
        vault_path
        / "System"
        / "Tools"
        / "Skills"
        / "vault-propagation"
        / "audit-ignores.md"
    )
    if ignores_path.exists():
        prefixes = VaultIndex.parse_audit_ignores(ignores_path)
        stats_scoped = _time(
            lambda: idx.find_orphans(exempt_prefixes=prefixes), iterations=3
        )
        scoped_count = _count(stats_scoped["result"])
        print(
            f"  With audit-ignores:  {scoped_count} orphans  {stats_scoped['median_ms']:.0f} ms median"
        )
    print()

    # --- Phase 5: recent_edits ---
    print("Phase 5: recent_edits")

    date_queries = [
        ("since 2026-05-17 (today)", "2026-05-17"),
        ("since 2026-05-10 (week)", "2026-05-10"),
        ("since 2026-05-01 (month)", "2026-05-01"),
        ("since 2026-01-01 (YTD)", "2026-01-01"),
    ]
    for label, since in date_queries:
        stats = _time(lambda s=since: idx.recent_edits(s, limit=200))
        count = _count(stats["result"])
        print(
            f"  {label:35s} → {count:>4} results  {stats['median_ms']:7.1f} ms median"
        )
    print()

    # --- Phase 6: tag_glossary_check ---
    print("Phase 6: tag_glossary_check (full vault body-tag scan)")

    glossary_path = vault_path / "System" / "Tags Glossary.md"
    if glossary_path.exists():
        stats = _time(
            lambda: idx.tag_glossary_check(glossary_path), iterations=3
        )
        violations = stats["result"]
        print(f"  Files with violations: {len(violations)}")
        print(
            f"  Timing: {stats['min_ms']:.0f} / {stats['median_ms']:.0f} / {stats['max_ms']:.0f} ms (min/median/max)"
        )
    else:
        print(f"  SKIPPED — {glossary_path} not found")
    print()

    # --- Phase 7: vault_stats ---
    print("Phase 7: vault_stats")

    stats = _time(idx.vault_stats, iterations=3)
    vs = stats["result"]
    print(
        f"  Total indexed: {vs['total_indexed']}, Total names: {vs['total_names']}"
    )
    print(f"  Types: {len(vs['types'])}, Top tags: {len(vs['top_tags'])}")
    print(
        f"  Timing: {stats['min_ms']:.0f} / {stats['median_ms']:.0f} / {stats['max_ms']:.0f} ms (min/median/max)"
    )
    print()

    # --- Phase 7b: all_tags (FS-based) ---
    print("Phase 7b: all_tags (FS-based, no Obsidian needed)")

    stats_fm_only = _time(lambda: idx.all_tags(include_body=False))
    fm_count = len(stats_fm_only["result"])
    print(
        f"  Frontmatter-only: {fm_count} unique tags  {stats_fm_only['median_ms']:.1f} ms median"
    )

    stats_with_body = _time(
        lambda: idx.all_tags(include_body=True), iterations=3
    )
    all_count = len(stats_with_body["result"])
    print(
        f"  With body tags:   {all_count} unique tags  {stats_with_body['median_ms']:.0f} ms median"
    )
    print()

    # --- Phase 8: read_note (single file) ---
    print("Phase 8: read_note (single file with link resolution)")

    read_targets = ["AGENTS", "COWORK", "TODO"]
    for stem in read_targets:
        stats = _time(lambda s=stem: idx.read_note(s))
        r = stats["result"]
        link_count = (
            len(r.get("outbound_links", [])) if isinstance(r, dict) else 0
        )
        print(
            f"  {stem:20s}  {link_count:>3} outbound links  {stats['median_ms']:7.1f} ms median"
        )
    print()

    # --- Summary ---
    print(f"{'=' * 60}")
    print("Summary")
    print(
        f"  Vault size: {reindex_result['indexed']} indexed / {reindex_result['names']} names"
    )
    print(f"  Reindex (cold): {reindex_result['elapsed_ms']} ms")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python tests/benchmark_live.py <vault-path>")
        sys.exit(1)
    vault = Path(sys.argv[1])
    if not vault.is_dir():
        print(f"Error: {vault} is not a directory")
        sys.exit(1)
    run_benchmark(vault)
