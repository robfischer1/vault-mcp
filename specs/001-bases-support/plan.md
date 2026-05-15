# Implementation Plan: Obsidian Bases Support

**Branch**: `001-bases-support` | **Date**: 2026-05-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-bases-support/spec.md`

## Summary

Add Obsidian Bases support to vault-mcp: parse inline `base` code blocks from markdown, execute their filter/formula logic against the vault index, write/update bases with byte-perfect surrounding markdown preservation, and validate bases before writing. Exposed as 4 new MCP tools. Tier 1 formula expressions only (property access, link counting, hasLink); Tier 2 (html/map/join/replace) deferred.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastMCP (mcp[cli] ≥1.0), PyYAML (new dependency — needed for parsing/emitting base YAML; the existing hand-rolled frontmatter parser cannot handle arbitrary nested structures)

**Storage**: N/A — reads from vault index + filesystem; writes to markdown files

**Testing**: pytest with single-file fixtures (one .md fixture per test case, per constitution §III)

**Target Platform**: Windows (primary), cross-platform compatible

**Project Type**: Library (MCP server plugin extension)

**Performance Goals**: Base execution ≤2x wall-clock time of `find_notes_by_frontmatter` on the same vault (SC-006)

**Constraints**: `mypy --strict`, `ruff` clean, no `# type: ignore` without justification. No vault-specific paths in `src/vault_mcp/`. Module count discipline — current shape is 4 modules + `server.py`.

**Scale/Scope**: ~250 markdown files contain inline base code blocks in the primary consumer vault. Typical base has 1-3 views, 0-5 formulas, 1-3 filter clauses.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Sibling, Not Subordinate | ✅ PASS | No vault-specific paths. Base parser works against any Obsidian-compatible directory. All fixtures use synthetic content. |
| II. REST API Augments, Never Foundations | ✅ PASS | Bases feature is parser-layer (like Phases 1-5). Works without Obsidian running. No REST dependency. |
| III. Test-First, Single-File Fixtures | ✅ PASS | Each test case gets one .md fixture with an inline base block. No multi-file setups for parser logic. |
| IV. Publish-Ready Hygiene | ✅ PASS | Fixtures use synthetic folder/note names. No PII. No personal vault content. |
| V. Strict Quality Tooling | ✅ PASS | All new code under mypy --strict, ruff, pytest. PyYAML has stubs (`types-PyYAML`). |
| Module count discipline | ⚠️ JUSTIFIED | Adding one module: `bases.py`. Bases are a cohesive domain (parser + evaluator + writer + validator) that doesn't belong in `parsers.py` (frontmatter/wikilinks) or `index.py` (TTL cache layer). A single ~500 LOC module is the minimum viable addition. See Complexity Tracking. |

## Project Structure

### Documentation (this feature)

```text
specs/001-bases-support/
├── plan.md              # This file
├── research.md          # Phase 0: dependency + expression evaluator research
├── data-model.md        # Phase 1: dataclasses, filter tree, formula tiers
├── quickstart.md        # Phase 1: getting started with bases tools
├── contracts/
│   └── mcp-tools.md     # Phase 1: MCP tool schemas and response shapes
└── tasks.md             # Phase 2 output (via /speckit-tasks)
```

### Source Code (repository root)

```text
src/vault_mcp/
├── __init__.py
├── bases.py             # NEW — parser, evaluator, writer, validator (~500 LOC)
├── index.py             # EXISTING — no changes (bases.py consumes VaultIndex)
├── parsers.py           # EXISTING — no changes (bases.py uses parse_frontmatter)
├── rest_client.py       # EXISTING — no changes
└── watcher.py           # EXISTING — no changes

server.py                # MODIFIED — 4 new @mcp.tool() registrations

tests/
├── test_index.py        # EXISTING
├── test_bases.py        # NEW — unit tests for bases module
├── test_rest_client.py  # EXISTING
├── test_rest_smoke.py   # EXISTING
└── fixtures/
    ├── mini-vault/      # EXISTING
    └── bases/           # NEW — single-file .md fixtures for base tests
        ├── simple-table.md
        ├── multi-view.md
        ├── nested-filters.md
        ├── has-link-filter.md
        ├── link-count-formula.md
        ├── tier2-formula.md
        ├── cards-view.md
        ├── invalid-yaml.md
        ├── multiple-bases.md
        ├── no-views.md
        ├── write-target.md
        └── validation-errors.md
```

**Structure Decision**: Single new module `bases.py` in the existing flat package. No subpackage — the bases domain is cohesive enough for one file and doesn't justify a directory split. Server.py gains 4 tool functions following the existing registration pattern.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| 5th module (`bases.py`) | Bases are a complete domain: YAML parsing, filter tree evaluation, expression evaluation, markdown writer, validator. None of these belong in the existing 4 modules — `parsers.py` is frontmatter/wikilinks, `index.py` is TTL cache, `rest_client.py` is HTTP, `watcher.py` is filesystem events. | Stuffing into `parsers.py` would make it >600 LOC and conflate two unrelated parsing domains (frontmatter vs. Bases YAML). Stuffing into `index.py` would add filter/formula logic to the cache layer. Both violate single-responsibility. |
