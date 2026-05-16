# Tasks: Bases Summaries

**Feature**: 003-bases-summaries | **Plan**: [specs/003-bases-summaries/plan.md](specs/003-bases-summaries/plan.md)

## Implementation Strategy

We follow the **Test-First** principle. For each user story, we will first create the necessary fixtures and test cases in `tests/test_bases.py` and `tests/fixtures/bases/`, then implement the logic in `src/vault_mcp/bases.py`. Summaries are aggregated in a single pass during `execute_base` to minimize overhead.

## Phase 1: Setup & Foundational

- [X] T001 [P] Create test fixture `tests/fixtures/bases/summaries-base.md` with base-level and view-level summaries
- [X] T002 [P] Create test fixture `tests/fixtures/bases/summaries-numeric.md` with numeric data for sum/avg/min/max/range
- [X] T003 [P] Add `Summary` dataclass to `src/vault_mcp/bases.py`
- [X] T004 Update `Base` and `ViewConfig` dataclasses in `src/vault_mcp/bases.py` to include `summaries: list[Summary]`
- [X] T005 Update `QueryResult` dataclass in `src/vault_mcp/bases.py` to include `summaries: dict[str, Any]`
- [X] T006 Update `parse_base_yaml` in `src/vault_mcp/bases.py` to parse the `summaries:` section from YAML

## Phase 2: User Story 1 - Basic Aggregation (Priority: P1)

**Goal**: Support `count` summary at base and view levels.
**Test**: `tests/test_bases.py` -> `TestSummaries.test_count_summary`

- [X] T007 [P] [US1] Create test `TestSummaries.test_count_summary` in `tests/test_bases.py`
- [X] T008 [US1] Implement `count` aggregation logic in `execute_base` within `src/vault_mcp/bases.py`
- [X] T009 [US1] Verify `execute_base` returns the correct count in the `summaries` result dictionary

## Phase 3: User Story 2 - Numeric Summaries (Priority: P2)

**Goal**: Support `sum` and `average` summaries.
**Test**: `tests/test_bases.py` -> `TestSummaries.test_numeric_summaries`

- [X] T010 [P] [US2] Create test `TestSummaries.test_numeric_summaries` in `tests/test_bases.py`
- [X] T011 [US2] Implement property value extraction logic for summaries in `execute_base`
- [X] T012 [US2] Implement `sum` and `average` accumulation in `execute_base` note loop
- [X] T013 [US2] Add logic to handle non-numeric values and missing properties gracefully (exclude from calculation)

## Phase 4: User Story 3 - Extremes and Range (Priority: P3)

**Goal**: Support `min`, `max`, and `range` summaries.
**Test**: `tests/test_bases.py` -> `TestSummaries.test_boundary_summaries`

- [X] T014 [P] [US3] Create test `TestSummaries.test_boundary_summaries` in `tests/test_bases.py`
- [X] T015 [US3] Implement `min` and `max` tracking in `execute_base` loop
- [X] T016 [US3] Implement `range` calculation (max - min) after the note loop
- [X] T017 [US3] Verify correct results for empty result sets (should return 0 or null)

## Phase 5: Polish & Integration

- [X] T018 [P] Update `_serialize_base` in `src/vault_mcp/bases.py` to include summaries in the serialized output
- [X] T019 Update `execute_base` tool in `server.py` to return the `summaries` dictionary to MCP clients
- [X] T020 [P] Run full test suite `uv run pytest` to ensure no regressions in Tier 1 or Tier 2
- [X] T021 [P] Run `uv run ruff check src/ tests/` and `uv run mypy src/` for quality compliance

## Dependencies

- Phase 1 is foundational for all subsequent phases.
- User stories [US1], [US2], [US3] are largely independent but implementation-wise it's best to follow the order to build up the accumulator logic.
- Phase 5 integrates the functionality into the MCP server.

## Parallel Execution Examples

- **Setup & Foundational**: T001, T002, T003 can be done in parallel.
- **Testing**: T007, T010, T014 can be drafted in parallel before implementation.
- **Serial**: T004 -> T005 -> T006 must be done sequentially in `bases.py` to avoid collisions.
