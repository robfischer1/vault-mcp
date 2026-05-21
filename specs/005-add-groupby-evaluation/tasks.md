# Tasks: Bases Tier 5 — Grouping

**Feature**: 005-add-groupby-evaluation | **Plan**: [specs/005-add-groupby-evaluation/plan.md](specs/005-add-groupby-evaluation/plan.md)

**Input**: Design documents from `specs/005-add-groupby-evaluation`

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/vault_mcp/`, `tests/` at repository root

## Execution Wave DAG

Tasks are grouped into execution waves. Tasks within the same wave can be executed in parallel. A wave can only begin when all its dependencies (previous waves) are complete.

- **Wave 1 (Setup)**: T001, T002, T003
- **Wave 2 (Fixtures & US1 Models)**: T004, T005, T006 (Depends on Wave 1)
- **Wave 3 (US1 Implementation)**: T007, T008, T009 (Depends on Wave 2)
- **Wave 4 (US2 Tests)**: T010 (Depends on Wave 3)
- **Wave 5 (US2 Implementation)**: T011, T012 (Depends on Wave 4)
- **Wave 6 (US3 & Edge Cases Tests)**: T013, T014 (Depends on Wave 5)
- **Wave 7 (US3 Implementation & Polish)**: T015, T016, T017, T018 (Depends on Wave 6)

## Phase 1: Setup & Foundational

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

- [x] T001 Define `GroupByConfig` dataclass in `src/vault_mcp/bases.py`
- [x] T002 Define `GroupResult` dataclass in `src/vault_mcp/bases.py`
- [x] T003 Update `QueryResult` and `ViewConfig` in `src/vault_mcp/bases.py` to support groups.

## Phase 2: User Story 1 - Grouping by Raw Property (Priority: P1)

**Goal**: As a user, I want to see my notes grouped by a simple property like "status".

- [x] T004 [P] [US1] Create test fixture `tests/fixtures/bases/groupby-raw.md` with `groupBy: { property: status, direction: ASC }` (depends on T003)
- [x] T005 [P] [US1] Create test fixture `tests/fixtures/bases/groupby-desc.md` with `groupBy: { property: status, direction: DESC }` (depends on T003)
- [x] T006 [US1] Add test `test_groupby_raw_property` in `tests/test_bases.py` referencing the new fixtures (depends on T004, T005)
- [x] T007 [US1] Update `parse_base_yaml` in `src/vault_mcp/bases.py` to parse `groupBy` into `ViewConfig` (depends on T003)
- [x] T008 [US1] Implement `_partition_results` in `src/vault_mcp/bases.py` to group notes by a raw property and sort the groups (depends on T007)
- [x] T009 [US1] Integrate `_partition_results` into `execute_base` in `src/vault_mcp/bases.py` to populate `QueryResult.groups` (depends on T008)

## Phase 3: User Story 2 - Grouping by Computed Formula (Priority: P2)

**Goal**: As a user, I want to group my notes by a derived value, such as a computed formula.

- [x] T010 [P] [US2] Create test fixture `tests/fixtures/bases/groupby-formula.md` defining a formula and grouping by it (depends on T009)
- [x] T011 [US2] Add test `test_groupby_formula` in `tests/test_bases.py` (depends on T010)
- [x] T012 [US2] Update `_partition_results` in `src/vault_mcp/bases.py` to handle grouping keys that are evaluated formulas, placing evaluation errors in an "Error" group (depends on T011)

## Phase 4: User Story 3 - Preserving Group Member Order (Priority: P3)

**Goal**: As a user, I want the notes within each group to be sorted according to the view's `sort` criteria.

- [x] T013 [P] [US3] Create test fixture `tests/fixtures/bases/groupby-sorted.md` combining `groupBy` and `sort` (depends on T012)
- [x] T014 [US3] Add test `test_groupby_preserves_sort` in `tests/test_bases.py` (depends on T013)
- [x] T015 [US3] Verify or update `execute_base` in `src/vault_mcp/bases.py` to ensure notes are sorted *before* partitioning, thus maintaining internal group order (depends on T014)

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Edge cases, formatting, and quality checks.

- [x] T016 Update `_serialize_base` in `src/vault_mcp/bases.py` to include `groupBy` in the serialized output (depends on T007)
- [x] T017 Run `uv run ruff check src/ tests/` and `uv run mypy src/` for static analysis checks (depends on T015)
- [x] T018 Run `uv run pytest` to ensure all tests pass (depends on T015)
