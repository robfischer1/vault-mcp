# Implementation Plan: Bases Tier 5 — Grouping

**Branch**: `005-add-groupby-evaluation` | **Date**: 2026-05-21 | **Spec**: [specs/005-add-groupby-evaluation/spec.md](spec.md)
**Input**: Feature specification from `/specs/005-add-groupby-evaluation/spec.md`

## Table of Contents

- [Summary](#summary)
- [Technical Context](#technical-context)
- [Constitution Check](#constitution-check)
- [Project Structure](#project-structure)
- [Phases](#phases)
- [Governance & Security](#governance--security)

## Summary
Add `groupBy` evaluation to the Bases query engine. This allows partitioning query results into groups based on frontmatter fields, file metadata, or computed formulas. The implementation will be a single-pass partitioning step integrated into `execute_base`, preserving internal note sort order and supporting group-level sorting (ASC/DESC).

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastMCP, yaml
**Storage**: N/A (In-memory evaluation)
**Testing**: pytest
**Target Platform**: win32 / Linux (via uv)
**Project Type**: Library / MCP Server
**Performance Goals**: <10% overhead on base execution.
**Constraints**: Regex timeouts, evaluation depth limits.

## Constitution Check

- [x] Sibling, Not Subordinate: No vault-specific paths.
- [x] REST API Augments: Grouping works on local index without REST API.
- [x] Test-First, Single-File Fixtures: Will create specific grouping fixtures.
- [x] Publish-Ready Hygiene: Generic test data.
- [x] Strict Quality Tooling: ruff, mypy, pytest.

## Project Structure

### Documentation (this feature)

```text
specs/005-add-groupby-evaluation/
├── plan.md              # This file
├── research.md          # Implementation decisions
├── data-model.md        # Updated QueryResult entities
├── quickstart.md        # Usage examples
└── contracts/
    └── execute_base_result.md # JSON schema update
```

### Source Code

```text
src/vault_mcp/
└── bases.py             # core logic update

tests/
├── test_bases.py        # new grouping tests
└── fixtures/
    └── bases/           # grouping fixtures
```

## Phases

### Phase 1: Setup & Infrastructure
- [ ] T001 Define `GroupByConfig` and `GroupResult` dataclasses in `src/vault_mcp/bases.py`.
- [ ] T002 Update `QueryResult` to include `groups: list[GroupResult]`.
- [ ] T003 Update YAML parser in `bases.py` to correctly populate `ViewConfig.groupBy`.

### Phase 2: Implementation (Core Logic)
- [ ] T004 Implement `_partition_results` helper in `bases.py` that takes a list of notes and a `GroupByConfig`.
- [ ] T005 Integrate `_partition_results` into `execute_base`.
- [ ] T006 Handle error cases: missing properties, formula errors, non-string keys.
- [ ] T007 Implement group-level sorting based on `direction`.

### Phase 3: Verification & Security
- [ ] T008 Create grouping fixtures in `tests/fixtures/bases/`.
- [ ] T009 Add `TestGrouping` suite to `tests/test_bases.py`.
- [ ] T010 Run `uv run ruff check` and `uv run mypy` (Security Checkpoint: Static Analysis).
- [ ] T011 Run `uv run pytest` (Security Checkpoint: Behavioral Verification).

## Governance & Security

### Cross-Platform
- The implementation is in pure Python, ensuring parity across Windows and Linux.
- Tests will be verified on Windows (native) and documented for Bash/Linux.

### Security
- **Memory Safety**: Python 3.11 is memory-safe. No `ctypes` or unsafe extensions used.
- **Input Validation**: `groupBy.property` is validated against available formulas and fields.
- **Denial of Service**: Grouping logic respects existing evaluation timeouts and depth limits.
