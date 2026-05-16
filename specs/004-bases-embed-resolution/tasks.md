# Tasks: Bases Embed Resolution

**Input**: Design documents from `/specs/004-bases-embed-resolution/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/vault_mcp/`, `tests/` at repository root

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create `specs/004-bases-embed-resolution/tasks.md` tracking document (Completed)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T002 Update `src/vault_mcp/bases.py` constants to expose regex patterns for embeds (if needed for index.py) or define them centrally in `index.py`.

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Resolve Standalone Base Embed (Priority: P1) 🎯 MVP

**Goal**: An agent reads a folder note that embeds a specific view from a `.base` file, getting the actual query results inlined.

**Independent Test**: Create a `.base` file with a view, create a `.md` file embedding it, and verify the `read_note` output contains the resolved base data.

### Tests for User Story 1 (OPTIONAL) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T003 [P] [US1] Create standalone base fixture `tests/fixtures/bases-vault/ActiveProjects.base`
- [ ] T004 [P] [US1] Create markdown note fixture `tests/fixtures/bases-vault/Projects.md` embedding `ActiveProjects.base#CardView` and `ActiveProjects.base`
- [ ] T005 [US1] Add test function `test_read_note_standalone_embed` to `tests/test_index.py` (depends on T003, T004)

### Implementation for User Story 1

- [ ] T006 [P] [US1] Update `src/vault_mcp/bases.py` `parse_file` to handle `.base` files directly (reading entire file as YAML if no fenced code blocks).
- [ ] T007 [P] [US1] Define `IMAGE_EMBED_RE` and a new `BASE_EMBED_RE` in `src/vault_mcp/index.py` (or modify existing to capture `.base` targets).
- [ ] T008 [US1] Update `VaultIndex.read_note` in `src/vault_mcp/index.py` to detect `![[*.base]]` and `![[*.base#View]]`.
- [ ] T009 [US1] Implement resolution logic in `VaultIndex.read_note` calling `bases.parse_file` and `bases.execute_base` for standalone targets.
- [ ] T010 [US1] Format and append the `resolved_embeds` list to the `read_note` response dictionary in `src/vault_mcp/index.py`.

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Resolve Inline Code-Block Base Embed (Priority: P2)

**Goal**: An agent reads a file that embeds a base defined as a code block in another `.md` file.

**Independent Test**: Create a note with a `bases` code block, another note embedding it, and verify resolution via `read_note`.

### Tests for User Story 2 (OPTIONAL) ⚠️

- [ ] T011 [P] [US2] Create markdown fixture `tests/fixtures/bases-vault/Database.md` with an inline ````base` block defining `Inventory`.
- [ ] T012 [P] [US2] Create markdown fixture `tests/fixtures/bases-vault/Report.md` embedding `![[Database#Inventory]]`.
- [ ] T013 [US2] Add test function `test_read_note_inline_embed` to `tests/test_index.py` (depends on T011, T012).

### Implementation for User Story 2

- [ ] T014 [US2] Update `VaultIndex.read_note` in `src/vault_mcp/index.py` to handle `![[Target#View]]` where Target is a `.md` file.
- [ ] T015 [US2] Implement resolution logic for inline bases calling `bases.parse_file` on the target `.md` and executing the specific view.

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Handle Missing Targets Gracefully (Priority: P3)

**Goal**: The system should provide clear error information when an embed target is missing or invalid.

**Independent Test**: Embed a non-existent file or a non-existent view and check the error shape in the output.

### Tests for User Story 3 (OPTIONAL) ⚠️

- [ ] T016 [P] [US3] Create fixture `tests/fixtures/bases-vault/MissingTarget.md` with `![[Missing.base]]` and `![[Existing.base#MissingView]]`.
- [ ] T017 [US3] Add test function `test_read_note_missing_embed_targets` to `tests/test_index.py`.

### Implementation for User Story 3

- [ ] T018 [P] [US3] Implement `not_found` error shape for missing `.base` files or target notes in `src/vault_mcp/index.py` inside `read_note`.
- [ ] T019 [P] [US3] Implement `view_not_found` error shape for missing views in `src/vault_mcp/index.py`.
- [ ] T020 [US3] Implement `parse_error` shape for invalid YAML in target bases in `src/vault_mcp/index.py`.

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T021 [P] Ensure type checker (`uv run mypy src/`) passes.
- [ ] T022 [P] Ensure linter (`uv run ruff check src/ tests/`) passes.
- [ ] T023 Run full test suite (`uv run pytest`) and ensure all tests are green.
- [ ] T024 [P] Update `DECISIONS.md` (if applicable) or any relevant documentation for the new `read_note` output contract.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1 but should be independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but should be independently testable

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members
