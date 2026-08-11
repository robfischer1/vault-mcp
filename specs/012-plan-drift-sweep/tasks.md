# Tasks: Re-dissolve the stale plan copies

**Feature**: `specs/012-plan-drift-sweep` | **Branch**: `feat/plan-freshness`

## Format: `[ID] [P?] [Story] Description`

`[P]` = parallelisable (different file, no ordering dependency).

## Path Conventions

Single project. Source `src/vault_mcp/`, tests `tests/`. Paths below are
repo-root-relative.

---

## Phase 1: Setup

- [ ] **T001** Create `src/vault_mcp/plan_freshness.py` with the module docstring
      stating the one-directional invariant (vault → store, never the reverse)
      and why `dissolve_note` is not reusable here (it deletes the source).

## Phase 2: Foundational (blocking prerequisites)

- [ ] **T002** Define `PlanDriftState` (str enum: current/drifted/missing/orphaned/error),
      `StoredCopy`, `PlanDriftRecord` (frozen), `PlanSweepReport` + `to_dict()`
      per `data-model.md`. Counts derived from records, never tracked separately.
- [ ] **T003** Add `read_document_by_source_path()` to `src/vault_mcp/hades_client.py`
      — calls calliope `read_documents(source_path=…)`, returns the newest row or
      `None`. Mirrors the existing `read_document(doc_id)` contract: never raises,
      returns `{ok: False, error}` on transport failure.

## Phase 3: User Story 1 — See which plans the store is behind on (P1) 🎯 MVP

### Tests

- [ ] **T004** [P] [US1] `test_drift_report_flags_stale_plan` — stored hash ≠ vault
      hash → `drifted`, delta reported. (FR-001, FR-003)
- [ ] **T005** [P] [US1] `test_matching_plan_reports_current` — equal hashes →
      `current`, not listed as drifted. (FR-002)
- [ ] **T006** [P] [US1] `test_plan_absent_from_store_reports_missing` (FR-008)
- [ ] **T007** [P] [US1] `test_stored_copy_without_vault_file_reports_orphaned` (FR-008)
- [ ] **T008** [P] [US1] `test_report_only_writes_nothing` — fake writer asserts
      zero calls. (FR-004, G-1)

### Implementation

- [ ] **T009** [US1] `compare_plan()` — pure function: stripped vault body + stored
      copy → `PlanDriftRecord`. Hash equality only; no timestamp comparison.
- [ ] **T010** [US1] `sweep_plans()` per the `contracts/` signature, `refresh=False`
      path only: enumerate, compare, collect, count.

**Checkpoint**: the drift report works standalone and answers the master-plan's
"how many other plans are stale" gap. Independently shippable.

## Phase 4: User Story 2 — Bring a stale plan's stored copy up to date (P1)

### Tests

- [ ] **T011** [P] [US2] `test_refresh_writes_new_version_for_drifted_plan` (FR-005)
- [ ] **T012** [P] [US2] `test_refresh_skips_current_plan` — writer not called for
      a `current` plan even with `refresh=True`. (FR-006, G-3)
- [ ] **T013** [P] [US2] `test_refresh_never_touches_vault_file` — assert file
      still exists, mtime and bytes unchanged. (G-2)
- [ ] **T014** [P] [US2] `test_write_failure_is_per_plan_and_sweep_continues` (FR-007, G-5)
- [ ] **T015** [P] [US2] `test_refresh_is_idempotent` — second run writes nothing. (G-4)

### Implementation

- [ ] **T016** [US2] `refresh=True` path in `sweep_plans()` — build payload, write,
      mark `refreshed`. Guard: never write a `current` record.
- [ ] **T017** [US2] Per-plan `try/except` → `error` record; sweep continues.

**Checkpoint**: the live defect is fixable.

## Phase 5: User Story 3 — Sweep every plan in one pass (P2)

### Tests

- [ ] **T018** [P] [US3] `test_sweep_covers_every_md_once` — each `.md` appears
      exactly once; non-`.md` ignored. (SC-001)
- [ ] **T019** [P] [US3] `test_limit_caps_acted_on_plans`

### Implementation

- [ ] **T020** [US3] Directory enumeration (default `System/Pantheon/WBS`,
      overridable per FR-009), `source_path` single-plan mode, `limit` cap.

## Phase 6: Wiring & polish

- [ ] **T021** Register the `plan_freshness` MCP verb in `src/vault_mcp/server.py`
      — thin wiring over `sweep_plans()`, docstring naming the report-only default
      and the no-vault-writes guarantee. Wire real callables: `_vault_md_paths`-style
      enumeration, `hades_client` read/write, `translator.note_to_payloads` for the
      payload.
- [ ] **T022** Pre-flight: refuse when `HADES_URL` is unset, reusing
      `carve.carve_preflight`'s precedent (the documented trap — the write leg
      would otherwise fall back to the retired phdb route).
- [ ] **T023** Run `ruff check`, `ruff format --check`, `mypy`, `pytest` — all green.

## Phase 7: Verification (Constitution V — Verify-Before-Done)

- [ ] **T024** Live report-only sweep against the real vault + store. Record the
      drifted count — this is the master-plan's unmeasured gap, answered.
- [ ] **T025** Live refresh sweep. Then **re-read** and confirm: zero `drifted`,
      and the Aglaia plan's stored body now hashes equal to its vault body.
      Report both the stripped-body size and the full-file size (109,593) so
      SC-003 is checked against the right number.
- [ ] **T026** Confirm every WBS `.md` file still exists on disk after the refresh
      (`git status` on the vault shows no deletions).

---

## Dependencies & Execution Order

- **Phase 1 → 2 → {3,4,5} → 6 → 7.**
- T003 (store read) blocks T010; T002 blocks everything in Phases 3–5.
- US1 (Phase 3) is the MVP and ships alone. US2 depends on US1's compare +
  enumerate. US3 is additive to both.
- Within a phase, `[P]` tests are independent (one test file, distinct functions —
  written together).

## Implementation Strategy

Build US1 first and run it live before writing US2. The report is the measurement
the master-plan asks for; seeing the real drifted count before writing any
refresh code means the fix is applied to a known population rather than an
assumed one.

## Notes

- Provenance quality on the written copy (`mtime`, `schema_type`) is deliberately
  **not** fixed here — F2 and F3 own it, and both land in `build_payload` alone.
  Rows written by this feature carry today's payload quality and are re-tagged by
  F3's pass. This is the master-plan's own sequencing.
- No new dependency. No vault write. No `confirm` token (see contracts/ for why).
