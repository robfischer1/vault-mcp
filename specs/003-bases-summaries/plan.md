# Implementation Plan: Bases Summaries

**Branch**: `003-bases-summaries` | **Date**: 2026-05-16 | **Spec**: [specs/003-bases-summaries/spec.md](specs/003-bases-summaries/spec.md)

## Summary

This feature adds support for summary-formula evaluation in Bases. Bases and their Views can declare a `summaries:` section containing aggregations like `count`, `sum`, `average`, `min`, `max`, and `range`. Evaluation happens in the same pass as the main query for maximum performance.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: FastMCP, PyYAML

**Storage**: Obsidian Vault (Markdown + YAML)

**Testing**: pytest

**Target Platform**: Desktop (Obsidian-compatible)

**Project Type**: MCP Server (FastMCP)

**Performance Goals**: <5% overhead for summary evaluation (SC-002), O(N) single-pass aggregation (FR-004).

**Constraints**: Strict type checking (mypy --strict), Lint-clean (ruff).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check |
|-----------|-------|
| Sibling, Not Subordinate | Passed. Summaries are generic aggregations over note metadata. No vault-specific paths. |
| REST API Augments | Passed. Summary evaluation is part of the local parser/evaluator, not dependent on REST API. |
| Test-First | Passed. New fixtures and tests will be added before implementation. |
| Publish-Ready Hygiene | Passed. All test data will be synthetic. |
| Strict Quality Tooling | Passed. mypy and ruff will be run. |

## Project Structure

### Documentation (this feature)

```text
specs/003-bases-summaries/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── mcp-execute-base.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
src/
└── vault_mcp/
    ├── bases.py        # ADD Summary dataclass, update Base, ViewConfig, execute_base
    └── index.py        # No changes required

server.py               # Update execute_base tool to return summaries

tests/
├── fixtures/
│   └── bases/
│       ├── summaries-base.md   # NEW fixture
│       └── summaries-view.md   # NEW fixture
└── test_bases.py        # NEW test cases for summaries
```

**Structure Decision**: Single project. Changes are localized to the `bases.py` module and the server entry point.

## Complexity Tracking

*No violations identified.*
