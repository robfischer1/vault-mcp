# Tasks: Correct schema_type tagging for dissolved plans

**Feature**: `specs/014-schema-type-from-note-type` | **Branch**: `feat/plan-freshness`

## Phase 1: Implementation

- [x] **T001** `translator._schema_type()` — consult `note_type` first, verbatim;
      fall back to `@type`/`type` against `_DOC_SCHEMA_TYPES`, then the default.
      Docstring records why the allowlist is bypassed for `note_type` and why
      `_raw_type` is deliberately NOT taught about it.

## Phase 2: Tests

- [x] **T002** `test_master_plan_stores_its_vault_native_type` (SC-001)
- [x] **T003** `test_note_type_beats_at_type` (FR-002)
- [x] **T004** `test_at_type_is_the_fallback_when_note_type_absent` (FR-002)
- [x] **T005** `test_unknown_at_type_still_falls_back_to_the_default` — the
      allowlist still guards free-form `@type`
- [x] **T006** `test_no_declared_type_stores_the_default` (FR-004)
- [x] **T007** `test_vault_native_types_are_not_coerced` (FR-003)
- [x] **T008** `test_note_type_does_not_change_entity_routing` (FR-005) — pins
      the boundary: `note_type: Book` still routes to documents
- [x] **T009** `test_entity_at_type_still_routes_to_entities` (FR-005)
- [x] **T010** `test_type_survives_the_dissolve_restore_round_trip` (US3)

## Phase 3: Existing-behaviour reconciliation

- [x] **T011** `test_plan_note_yields_document_only` asserted
      `schema_type == "DigitalDocument"` for a note declaring `note_type: Plan`.
      That assertion **encoded the defect**. Updated to `"Plan"` with a comment
      naming what changed and why. This is the only pre-existing assertion the
      feature moves; the other 20 translator tests pass untouched, which is the
      evidence for SC-005 (routing unchanged).

## Phase 4: Re-tagging existing rows (FR-006)

- [x] **T012** No new code. F2's `backfill` mode re-sends the payload and
      calliope reconciles attributes without minting a body generation, so a
      corrected `schema_type` lands through the path that corrected `mtime`.

## Phase 5: Verification (Constitution V)

- [x] **T013** Re-tagged both stored plans live via `backfill`.
- [x] **T014** `read_documents(schema_type="Master-plan")` — returned `[]` at
      session start, now returns **both** plans (ids 5546, 5547), each carrying
      `schema_type: "Master-plan"`. **SC-002 met.**
- [x] **T015** Confirmed SC-004: both rows still hold one version each and their
      `raw_hash` is unchanged — the re-tag wrote no body.
- [x] **T016** `ruff`, `mypy`, `pytest` green (511 passed; the 2 `test_gitops`
      `BotIdentity` failures are pre-existing on clean `main` — a global git
      identity overriding the fixture — and are not touched by this work).

## Notes

- **Left open deliberately** (the master-plan's own "Gaps surfaced"): the same
  flattening affects every note vault-mcp dissolves, not just plans — fixed here
  for all of them — but **other writers to the store are unaudited**. The
  fleet-wide case stays unscoped, as the master-plan states.
- The 173 WBS plans with no stored copy are unaffected: this feature corrects the
  label on what is stored, it does not populate.
