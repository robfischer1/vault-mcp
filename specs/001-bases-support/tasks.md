# Tasks: Obsidian Bases Support

**Input**: Design documents from `specs/001-bases-support/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: Included — constitution §III mandates test-first (red → green → refactor).

**Organization**: Tasks grouped by user story. Each story is independently implementable and testable after the Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3, US4)
- Exact file paths included per task

---

## Phase 1: Setup

**Purpose**: Add dependency, create module skeleton and fixture directory

- [X] T001 Add `pyyaml>=6.0` to dependencies and `types-PyYAML>=6.0` to dev dependencies in pyproject.toml
- [X] T002 Run `uv pip install -e ".[dev]"` to install new dependency
- [X] T003 Create empty `src/vault_mcp/bases.py` with module docstring and `from __future__ import annotations`
- [X] T004 [P] Create `tests/fixtures/bases/` directory with single-file parser fixtures: simple-table.md, multi-view.md, nested-filters.md, has-link-filter.md, link-count-formula.md, tier2-formula.md, cards-view.md, invalid-yaml.md, multiple-bases.md, no-views.md
- [X] T005 [P] Create `tests/fixtures/bases-vault/` — a small multi-file vault for execution tests with 5-6 linked .md files covering: folder filtering, frontmatter properties, wikilink relationships, and file.hasLink scenarios

**Checkpoint**: Dependencies installed, skeleton files in place, all fixtures authored. `uv run pytest` still green (no new tests yet).

---

## Phase 2: Foundational — Parser Core

**Purpose**: Base code block extraction and YAML→dataclass parsing. Blocks ALL user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 Define dataclasses in `src/vault_mcp/bases.py`: FilterNode, Formula, SortDirective, ViewConfig, Base, ParsedFile, QueryResult, ValidationResult per data-model.md
- [X] T007 Write parser unit tests in `tests/test_bases.py`: test extraction of base code blocks (count, line numbers, YAML content), test filter tree parsing (and/or/not nesting, eq/neq predicates, hasLink, bare properties), test formula tier classification (Tier 1 vs Tier 2), test view config parsing (name, type, order, sort, column_sizes), test multi-base files, test invalid YAML error reporting, test no-views edge case, test cards view parsed without error
- [X] T008 Implement `extract_base_blocks()` in `src/vault_mcp/bases.py` — regex extraction of `` ```base `` fenced code blocks, returning list of (yaml_content, line_number) tuples
- [X] T009 Implement `_parse_filter_predicate()` in `src/vault_mcp/bases.py` — splits string predicates like `file.folder == "X"`, `note["key"] != "val"`, `file.hasLink("name")` into FilterNode leaves
- [X] T010 Implement `_build_filter_tree()` in `src/vault_mcp/bases.py` — walks YAML filter dict (and/or/not keys with list values of predicate strings or nested dicts) into recursive FilterNode tree
- [X] T011 Implement `_classify_formula_tier()` in `src/vault_mcp/bases.py` — pattern-match formula expression string to Tier 1 (property access, file.*, filter/length chains) vs Tier 2 (html/if/map/join/replace/toString)
- [X] T012 Implement `parse_base_yaml()` in `src/vault_mcp/bases.py` — takes raw YAML dict from safe_load, returns Base dataclass with FilterNode tree, Formula dict, ViewConfig list
- [X] T013 Implement `parse_file()` in `src/vault_mcp/bases.py` — reads markdown file, calls extract_base_blocks + yaml.safe_load + parse_base_yaml for each block, returns ParsedFile with bases list and errors list
- [X] T014 Verify all parser tests pass: `uv run pytest tests/test_bases.py -xvs`

**Checkpoint**: Parser complete. `parse_file()` correctly extracts and structures any base code block. All parser tests green.

---

## Phase 3: User Story 1 — Execute a Base Query (Priority: P1) 🎯 MVP

**Goal**: An agent can call `execute_base` with a file path and optional view name and receive matching notes with computed formula columns.

**Independent Test**: Point execute at a fixture vault's folder note, verify returned notes match expected filter/sort/formula results.

### Tests for User Story 1

- [X] T015 [US1] Write execution tests in `tests/test_bases.py`: test filter evaluation against fixture vault (folder match, name exclusion, frontmatter equality, hasLink), test Tier 1 formula evaluation (note["key"], file.mtime, file.name, file.folder, file.ext), test link-count formula (file.links.filter().length using fixture vault's link graph), test view selection (base-level only, named view, view-specific filter, sort application), test Tier 2 formula returns null + warning, test empty result set (filter matches nothing), test cards view returns unsupported error, test no-views execution (base filters only)

### Implementation for User Story 1

- [X] T016 [US1] Implement `evaluate_filter()` in `src/vault_mcp/bases.py` — recursive FilterNode evaluation taking (node, path, frontmatter, rel_path, outbound_links) → bool. Handles and/or/not logic, eq/neq on file.folder/file.name/note properties, hasLink against outbound link set
- [X] T017 [US1] Implement `evaluate_formula()` in `src/vault_mcp/bases.py` — pattern-matching evaluator for Tier 1 expressions. Returns (value, warning_or_none). Patterns: `note["key"]` → fm lookup, `file.mtime` → stat, `file.name`/`file.folder`/`file.path`/`file.ext` → path parsing, `file.links.filter(value.asFile().ext == "md").length` → count outbound .md links, `file.backlinks.filter(...)` → count inbound .md links, bare property names → fm lookup. Tier 2 patterns → (None, warning string)
- [X] T018 [US1] Implement `execute_base()` in `src/vault_mcp/bases.py` — takes (Base, VaultIndex, view_name=None) → QueryResult. Orchestrates: select view → merge base+view filters → iterate index.content → evaluate_filter per note → evaluate_formula per matching note → apply sort → build QueryResult with warnings
- [X] T019 [US1] Register `execute_base` MCP tool in `server.py` — parameters: path (str), view (str|None), base_index (int, default 0). Calls parse_file → select base by index → execute_base → serialize QueryResult to response dict. Error handling per contracts/mcp-tools.md.
- [X] T020 [US1] Verify all US1 tests pass: `uv run pytest tests/test_bases.py -k "execute or filter or formula" -xvs`

**Checkpoint**: `execute_base` MCP tool works end-to-end. Agent can query any base in the vault and get matching notes with formula columns. Tier 2 formulas degrade gracefully. MVP complete.

---

## Phase 4: User Story 2 — Parse and Introspect (Priority: P2)

**Goal**: An agent can call `parse_base` to see a base's filter tree, formulas, and views as structured data without executing it.

**Independent Test**: Parse a known fixture file and assert the returned structure matches expected filters, formulas, views.

### Tests for User Story 2

- [X] T021 [US2] Write parse tool tests in `tests/test_bases.py`: test parse_base MCP tool returns correct structure for Plans.md-like fixture, test multi-base file returns all bases with positions, test cards view parsed without error, test empty file returns count=0, test invalid YAML returns error in errors array alongside valid bases

### Implementation for User Story 2

- [X] T022 [US2] Implement `_serialize_base()` in `src/vault_mcp/bases.py` — converts Base dataclass to dict for MCP response (FilterNode tree → nested dicts, Formula → dict with expression+tier, ViewConfig → dict)
- [X] T023 [US2] Register `parse_base` MCP tool in `server.py` — parameter: path (str). Calls parse_file → serialize each Base → return response per contracts/mcp-tools.md
- [X] T024 [US2] Verify all US2 tests pass: `uv run pytest tests/test_bases.py -k "parse_tool or serialize" -xvs`

**Checkpoint**: `parse_base` MCP tool returns structured base data. Agent can introspect any base's filters, formulas, and views.

---

## Phase 5: User Story 3 — Write or Update a Base (Priority: P3)

**Goal**: An agent can create a new base or update an existing one in a markdown file, with byte-identical surrounding markdown preservation.

**Independent Test**: Write a base into a fixture file, read it back, verify YAML is valid and all non-base markdown is byte-identical.

### Tests for User Story 3

- [X] T025 [P] [US3] Create write-specific test fixtures in `tests/fixtures/bases/`: write-empty.md (no existing base), write-single.md (one existing base), write-multi.md (multiple bases)
- [X] T026 [US3] Write writer tests in `tests/test_bases.py`: test append new base to file with no bases, test replace single existing base (surrounding markdown byte-identical), test replace specific base by index in multi-base file, test ambiguous target error (null index with multiple bases), test generated YAML parses back correctly (round-trip), test newline handling at file boundaries

### Implementation for User Story 3

- [X] T027 [US3] Implement `_base_dict_to_yaml()` in `src/vault_mcp/bases.py` — converts base dict to YAML string using yaml.dump with block style, sorted_keys=False, allow_unicode=True
- [X] T028 [US3] Implement `write_base_to_file()` in `src/vault_mcp/bases.py` — reads file, finds base code blocks by position, inserts new or replaces existing block, writes file back. Byte-identical preservation of non-base content via string slicing around code fence positions
- [X] T029 [US3] Register `write_base` MCP tool in `server.py` — parameters: path (str), base (dict), base_index (int|None), validate (bool, default True). Calls validate_base if enabled → write_base_to_file → return response per contracts/mcp-tools.md
- [X] T030 [US3] Verify all US3 tests pass: `uv run pytest tests/test_bases.py -k "write" -xvs`

**Checkpoint**: `write_base` MCP tool creates and updates bases. Round-trip writes preserve surrounding markdown.

---

## Phase 6: User Story 4 — Validate a Base (Priority: P4)

**Goal**: An agent can validate a base configuration before writing, catching YAML errors, undefined formula references, and unquoted special characters.

**Independent Test**: Submit known-bad bases and verify specific diagnostics for each error class.

### Tests for User Story 4

- [X] T031 [US4] Write validator tests in `tests/test_bases.py`: test valid base returns valid=True with no errors, test invalid YAML structure returns invalid_yaml error, test undefined formula reference in view order returns undefined_formula_ref error, test unquoted special characters return unquoted_special_char warning, test undefined sort property returns undefined_sort_ref warning, test multiple errors reported together

### Implementation for User Story 4

- [X] T032 [US4] Implement `validate_base()` in `src/vault_mcp/bases.py` — takes base dict, returns ValidationResult. Checks: (a) yaml.safe_load succeeds on yaml.dump round-trip, (b) all `formula.*` refs in view order lists have matching formula definitions, (c) string values with YAML special chars (`:`, `{`, `}`, `[`, `]`, `#`, `&`, `*`, `!`, `|`, `>`, `'`, `"`, `%`, `@`, `` ` ``) are flagged, (d) view sort properties reference valid columns
- [X] T033 [US4] Register `validate_base` MCP tool in `server.py` — parameter: base (dict). Calls validate_base → return ValidationResult as dict per contracts/mcp-tools.md
- [X] T034 [US4] Wire validation into `write_base` tool — when validate=True (default), run validate_base before writing; reject write if validation returns errors
- [X] T035 [US4] Verify all US4 tests pass: `uv run pytest tests/test_bases.py -k "validate" -xvs`

**Checkpoint**: `validate_base` MCP tool catches error classes. `write_base` auto-validates by default.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, regression check, documentation

- [X] T036 Run `uv run mypy src/vault_mcp/bases.py --strict` and fix all type errors in `src/vault_mcp/bases.py`
- [X] T037 [P] Run `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/` — fix all lint issues
- [X] T038 Run full test suite `uv run pytest -xvs` — verify no regressions in existing tests (test_index.py, test_rest_client.py, test_rest_smoke.py)
- [X] T039 Verify quickstart.md examples work against the fixtures vault or the live vault
- [X] T040 Verify server.py imports and tool registrations work: `uv run python -c "from vault_mcp.bases import parse_file, execute_base, validate_base; print('OK')"`
- [X] T041 Add a lightweight performance smoke test in `tests/test_bases.py`: time `execute_base` against the fixtures vault and assert it completes within 2x the wall-clock time of a comparable `find_notes_by_frontmatter` call (SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 Execute (Phase 3)**: Depends on Foundational — MVP target
- **US2 Parse (Phase 4)**: Depends on Foundational — can run in parallel with US1
- **US3 Write (Phase 5)**: Depends on Foundational — can run in parallel with US1/US2
- **US4 Validate (Phase 6)**: Depends on Foundational — can run in parallel with US1/US2. Must complete before US3's T034 (validate wiring into write)
- **Polish (Phase 7)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: Foundational only. No dependency on other stories.
- **US2 (P2)**: Foundational only. No dependency on other stories. Shares serialization with US1's execute tool.
- **US3 (P3)**: Foundational only for core implementation. T034 (validate wiring) depends on US4's T032.
- **US4 (P4)**: Foundational only. No dependency on other stories.

### Within Each User Story

- Tests written FIRST and verified to FAIL (constitution §III)
- Core logic before MCP tool registration
- MCP tool registration last (depends on logic being implemented)

### Parallel Opportunities

Within Setup:
- T004 (parser fixtures) and T005 (vault fixtures) can run in parallel

After Foundational completes, all 4 user stories can start in parallel:
- US1, US2, US3, US4 all depend only on Phase 2

Within each user story:
- Test writing is first (blocking within the story)
- Once tests exist, implementation tasks within a story are sequential (each builds on prior)

---

## Parallel Example: After Foundational

```
# All four user stories can launch in parallel after Phase 2:
Agent A: US1 — Execute (T015→T020)  ← MVP, highest priority
Agent B: US2 — Parse (T021→T024)    ← thin wrapper, fast
Agent C: US3 — Write (T025→T030)    ← independent of read path
Agent D: US4 — Validate (T031→T035) ← independent, wire into write last
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T005)
2. Complete Phase 2: Foundational parser (T006-T014)
3. Complete Phase 3: US1 Execute (T015-T020)
4. **STOP and VALIDATE**: Test execute_base against real vault folder notes (Plans, Personal History, Self, Dialogues, Atlas topics)
5. MVP delivered — agents can query any declared base

### Incremental Delivery

1. Setup + Foundational → Parser ready
2. Add US1 Execute → Agents can query bases (MVP!)
3. Add US2 Parse → Agents can introspect base structure
4. Add US4 Validate → Pre-write safety net
5. Add US3 Write → Agents can create/update bases (full feature)
6. Polish → Quality gates, regression check

### Single-Developer Sequential (Recommended)

Given one developer and test-first discipline:
1. Phase 1 + 2: ~45 min (setup + parser)
2. Phase 3 (US1): ~60 min (filter eval + formula eval + execute orchestrator + MCP tool)
3. Phase 4 (US2): ~15 min (serializer + thin MCP tool)
4. Phase 5 (US3): ~30 min (writer + round-trip tests)
5. Phase 6 (US4): ~20 min (validator + wiring)
6. Phase 7: ~15 min (mypy + ruff + regression check)
Total: ~3 hours

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Constitution §III: tests MUST be written and FAIL before implementation within each phase
- Constitution §V: `mypy --strict` + `ruff` clean before declaring done
- All fixtures use synthetic content — no real vault PII (Constitution §IV)
- One new module (`bases.py`) justified in plan.md Complexity Tracking
- Commit after each phase checkpoint for clean git history
