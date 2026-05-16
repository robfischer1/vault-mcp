# Implementation Plan: Bases Tier 2 Expressions and Cards View

**Branch**: `002-bases-tier-2` | **Date**: 2026-05-16 | **Spec**: [specs/002-bases-tier2-cards/spec.md](spec.md)

## Summary
Extend the existing Bases support in `vault-mcp` to support Tier 2 formula evaluation (`if`, `map`, `join`, `replace`, etc.) and the execution of `cards` view types. The approach involves implementing a restricted expression evaluator with safety limits (timeouts and nesting depth) and extracting card layout metadata from the Base configuration.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastMCP, PyYAML (Standard library `ast`, `re`, `concurrent.futures`)

**Storage**: Read-only access to local vault markdown files.

**Testing**: `pytest` with single-file fixtures in `tests/fixtures/bases/`.

**Target Platform**: Any host running Obsidian-compatible vaults.

**Project Type**: MCP Server (FastMCP)

**Performance Goals**: Formula evaluation should not significantly impact query latency; 100ms timeout for regex.

**Constraints**: <10 levels of nesting for `if()`; No full `eval()` usage.

**Scale/Scope**: Focus on the specific vocabulary used in "Core Concepts" vault and common Tier 2 patterns.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Sibling, Not Subordinate**: PASS. The implementation is confined to `src/vault_mcp/bases.py` and uses generic fixtures.
2. **REST API Augments**: PASS. Bases evaluation is purely local and does not depend on the Local REST API.
3. **Test-First, Single-File Fixtures**: PASS. New tests will follow the established `test_bases.py` pattern with dedicated fixtures.
4. **Publish-Ready Hygiene**: PASS. MIT-licensed code; no personal vault data in fixtures.
5. **Strict Quality Tooling**: PASS. All changes subject to `ruff`, `mypy --strict`, and `pytest`.

## Project Structure

### Documentation (this feature)

```text
specs/002-bases-tier2-cards/
├── plan.md              # This file
├── research.md          # Research findings and decisions
├── data-model.md        # Updated entity and result definitions
├── quickstart.md        # Summary and usage examples
├── contracts/           
│   └── mcp-execute-base.md # Interface contract for the MCP tool
└── tasks.md             # Implementation tasks (Phase 2)
```

### Source Code (repository root)

```text
src/
└── vault_mcp/
    └── bases.py         # Main logic for parsing and evaluation

tests/
├── fixtures/
│   └── bases/           # Test fixtures (markdown files with base blocks)
└── test_bases.py        # Unit and integration tests for Bases
```

**Structure Decision**: Single project structure. All changes are contained within the existing `bases.py` module and its corresponding tests.
