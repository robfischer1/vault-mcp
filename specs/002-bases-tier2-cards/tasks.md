# Tasks: Bases Tier 2 Expressions and Cards View

**Input**: Design documents from `specs/002-bases-tier2-cards/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-execute-base.md

**Tests**: Tests are requested via User Scenarios in spec.md. We follow a test-first approach.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 [P] Create test fixtures for Tier 2 expressions in `tests/fixtures/bases/tier2-formulas.md`
- [ ] T002 [P] Create test fixtures for cards view in `tests/fixtures/bases/cards-view.md`
- [ ] T003 [P] Add Tier 2 expression patterns to `_TIER2_PATTERNS` in `src/vault_mcp/bases.py` (if missing)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Implement a restricted formula evaluator (using `ast` or custom subset) in `src/vault_mcp/bases.py`
- [ ] T005 Implement the regex timeout logic (100ms) in the evaluator within `src/vault_mcp/bases.py`
- [ ] T006 Implement nesting depth tracking (max 10) in the evaluator within `src/vault_mcp/bases.py`

**Checkpoint**: Foundation ready - Tier 2 expression evaluation can now be integrated into user stories.

---

## Phase 3: User Story 1 - Executing Complex Bases (Priority: P1) 🎯 MVP

**Goal**: Support Tier 2 formulas (`if`, `map`, `join`, `replace`, etc.) in Base execution.

**Independent Test**: Run `pytest tests/test_bases.py` after implementing US1 tests; it should confirm Tier 2 formulas evaluate correctly using the `tier2-formulas.md` fixture.

### Tests for User Story 1

- [ ] T007 [P] [US1] Add unit tests for `if()` evaluation in `tests/test_bases.py`
- [ ] T008 [P] [US1] Add unit tests for list-shaping (`map`, `join`) in `tests/test_bases.py`
- [ ] T009 [P] [US1] Add unit tests for string concatenation and `.toString()` in `tests/test_bases.py`
- [ ] T010 [P] [US1] Add unit tests for `.replace()` with regex in `tests/test_bases.py`

### Implementation for User Story 1

- [ ] T011 [US1] Integrate the restricted evaluator into `evaluate_formula` in `src/vault_mcp/bases.py`
- [ ] T012 [US1] Remove the "Tier 2 expression not supported" error in `evaluate_formula` in `src/vault_mcp/bases.py`
- [ ] T013 [US1] Ensure `evaluate_formula` handles `html()` as a pass-through in `src/vault_mcp/bases.py`

**Checkpoint**: User Story 1 is functional. Tier 2 expressions now compute values instead of warnings in table views.

---

## Phase 4: User Story 2 - Cards View Execution (Priority: P2)

**Goal**: Execute Bases with `type: cards` and return card layout metadata.

**Independent Test**: Query a cards-type base via `execute_base`. The response should contain `view_properties` with card metadata.

### Tests for User Story 2

- [ ] T014 [P] [US2] Add integration test for cards view execution in `tests/test_bases.py`
- [ ] T015 [P] [US2] Add unit test for card metadata extraction in `tests/test_bases.py`

### Implementation for User Story 2

- [ ] T016 [US2] Update `QueryResult` dataclass to include `view_properties` in `src/vault_mcp/bases.py`
- [ ] T017 [US2] Update `execute_base` to allow `type: cards` and extract properties from `ViewConfig.extra` in `src/vault_mcp/bases.py`
- [ ] T018 [US2] Update `_serialize_base` to include `view_properties` in the serialized output in `src/vault_mcp/bases.py`

**Checkpoint**: User Story 2 is functional. Cards views can now be executed and return structured layout data.

---

## Phase 5: User Story 3 - Graceful Handling of Unsupported Expressions (Priority: P3)

**Goal**: Ensure genuine Tier 3+ or malformed expressions still return clear warnings.

**Independent Test**: Execute a base with an unknown function (e.g., `futureFunc()`). It should return `null` with a "Unsupported expression" warning.

### Tests for User Story 3

- [ ] T019 [P] [US3] Add test case for unknown function calls in `tests/test_bases.py`
- [ ] T020 [P] [US3] Add test case for exceeding nesting depth in `tests/test_bases.py`
- [ ] T021 [P] [US3] Add test case for regex timeout in `tests/test_bases.py`

### Implementation for User Story 3

- [ ] T022 [US3] Refine the warning path in `evaluate_formula` to capture evaluator errors in `src/vault_mcp/bases.py`
- [ ] T023 [US3] Ensure malformed Tier 2 expressions are caught and reported as warnings in `src/vault_mcp/bases.py`

**Checkpoint**: All user stories complete. The system is robust and handles complex formulas safely.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and hygiene

- [ ] T024 [P] Run `mypy --strict src/vault_mcp/bases.py` and resolve any type issues
- [ ] T025 [P] Run `ruff check src/vault_mcp/bases.py` and ensure lint cleanliness
- [ ] T026 [P] Update `docs/architecture.md` (if applicable) to reflect Tier 2 support
- [ ] T027 Run all tests (`pytest`) and ensure 100% pass rate including regressions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Can start immediately.
- **Foundational (Phase 2)**: Depends on T003. Blocks US1 and US2.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2. Can run in parallel with US1 if needed, but US2 often uses US1 formulas.
- **User Story 3 (Phase 5)**: Depends on US1/US2 completion to verify the "leftover" warning path.
- **Polish (Phase 6)**: Depends on all stories being complete.

### Parallel Opportunities

- T001, T002, T003 can run in parallel.
- All test tasks (T007-T010, T014-T015, T019-T021) can be prepared in parallel once fixtures exist.
- Phase 3 and Phase 4 implementation can be worked on in parallel by different developers.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Setup (Phase 1)
2. Complete Foundational (Phase 2) - This is the core technical challenge.
3. Complete User Story 1 (Phase 3)
4. **STOP and VALIDATE**: Run Tier 2 formula tests. This delivers the most value by unblocking existing Bases.

### Incremental Delivery

1. Tier 2 Formulas (US1) -> Unblocks "Core Concepts" evaluation.
2. Cards View (US2) -> Unblocks "Core Concepts" rendering data.
3. Safety & Warnings (US3) -> Hardens the system for production.

---

## Notes

- [P] tasks = different files or independent logic.
- [Story] label ensures traceability back to spec.md.
- The restricted evaluator in T004 is the most critical task; it must be designed with the 10-level depth and 100ms timeout in mind from the start.
