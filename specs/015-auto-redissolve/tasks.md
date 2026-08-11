# Tasks: Automatic re-dissolve on change

**Feature**: `specs/015-auto-redissolve` | **Branch**: `feat/plan-freshness`

## Phase 1: The cheap gate (FR-005, FR-006)

- [x] **T001** `plan_freshness.is_probably_stale()` — one string comparison over
      F2's ISO-8601 clock; answers `True` whenever unsure.
- [x] **T002** `sweep_plans(cheap_gate=…)` — skip the store read for gated-out
      plans; mark the record `gated` so a cheap sweep never reads as an audit.
- [x] **T003** `StoredCopy.mtime` / `PlanDriftRecord.stored_mtime` — carry the
      clock back so callers can cache it.

## Phase 2: One pass (FR-002, FR-003, FR-004)

- [x] **T004** `plan_reconcile.reconcile_once(sweep)` — injected sweep; INFO per
      re-stored plan, DEBUG when nothing changed, WARNING per failed plan;
      never raises.

## Phase 3: The loop (FR-001, FR-007, FR-008, FR-009)

- [x] **T005** `reconcile_enabled()` / `reconcile_interval()` — env-driven, with
      garbage and non-positive values falling back rather than busy-looping.
- [x] **T006** `start_plan_reconcile()` — daemon thread, waits one interval
      before its first pass.
- [x] **T007** `server._start_plan_reconcile_once()` — started alongside the
      index, once per process, skipped when `HADES_URL` is unset.
- [x] **T008** `server._plan_sweep()` — extracted so the verb and the loop share
      one configuration; `_PLAN_CLOCKS` cache populated from each sweep.

## Phase 4: Tests

- [x] **T009** Gate: stale / fresh / unknown-clock / unstattable (4 tests)
- [x] **T010** `test_gated_records_are_marked_and_skip_the_store_read` (FR-005/006)
- [x] **T011** `test_gate_never_suppresses_a_write_on_its_own` — the boundary
- [x] **T012** Pass: quiet when nothing changed; names each re-store; reports
      per-plan errors; survives a raising sweep (4 tests)
- [x] **T013** Config: default on; disableable; interval validation (3 tests)
- [x] **T014** Thread: disabled starts nothing; enabled is a daemon and runs no
      pass at startup (2 tests)

## Phase 5: Verification (Constitution V)

- [x] **T015** `ruff`, `mypy`, `pytest` green — 526 passed, 25 skipped. The 2
      `test_gitops::BotIdentity` failures are pre-existing on clean `main`
      (a global git identity overrides the fixture) and are untouched here.
- [x] **T016** Gate verified against the LIVE store: both stored plans, whose
      clocks now equal disk after F2's backfill, gate to `stale=False` — so a
      reconcile tick fetches neither body. Both conservative directions
      (unknown clock, ancient clock) answer `True`.

## Notes

- **Not verified in-process**: the running vault-mcp server is a separate
  process that does not carry this branch, so the *thread actually ticking in
  production* is unproven — the loop's logic, config and gate are proven by test
  and against live data, but the first real tick happens when this deploys.
  Stated plainly rather than implied.
- The `plan_freshness` verb remains the manual path and is unaffected.
