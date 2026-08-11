# Feature Specification: Automatic re-dissolve on change

**Feature Branch**: `feat/plan-freshness`

**Created**: 2026-08-11

**Status**: Draft

**Input**: Master-plan feature node F4 — "The Plan Stops Being Prose"

> **Brief:** Versioning is insert-only (`UNIQUE (source_path, raw_hash)`), so a re-dissolve *would* land a new row — nothing ever triggers the write. Add a watcher or a scheduled reconcile that re-dissolves a plan whose source has moved. For Rob, because F1 is a one-off and this is what stops it recurring.
>
> **Consumer:** the projection pipeline; every plan.
>
> **Success:** a plan edited on disk is re-dissolved without human action; the trigger is observable (it logs what it did); a plan with no change is a no-op.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An edited plan reaches the store on its own (Priority: P1)

Rob edits a master-plan in the vault and does nothing else. Some time later the
store holds those bytes.

**Why this priority**: This is the feature. F1 repairs drift on demand; without
a trigger, the repair only ever happens when someone remembers — which is the
condition that let 22 days pass unnoticed.

**Independent Test**: Change a plan, wait one interval, and confirm the stored
copy matches without any human action.

**Acceptance Scenarios**:

1. **Given** a plan whose file has changed since capture, **When** the reconcile runs, **Then** a new stored version lands.
2. **Given** no plan has changed, **When** the reconcile runs, **Then** nothing is written.
3. **Given** the reconcile has run, **Then** what it did is discoverable after the fact.

---

### User Story 2 - The trigger survives a bad day (Priority: P2)

A store outage or one broken plan does not stop the trigger permanently.

**Why this priority**: A trigger that dies silently restores exactly the "nothing
ever re-writes" condition the feature exists to remove — and it would look
healthy while doing it.

**Acceptance Scenarios**:

1. **Given** a pass that fails entirely, **When** the next interval arrives, **Then** the trigger runs again.
2. **Given** one plan fails, **When** the pass runs, **Then** the other plans still reconcile.

---

### User Story 3 - The trigger is affordable (Priority: P2)

Running continuously does not cost a full read of every plan every time.

**Why this priority**: The naive loop re-reads every stored plan on every tick.
Measured, that is ~10MB per tick against the store forever, for a condition that
is almost always "nothing changed".

**Acceptance Scenarios**:

1. **Given** a plan whose file has not changed since capture, **When** a pass runs, **Then** its stored copy is not fetched.
2. **Given** a pass skips a plan cheaply, **Then** the result distinguishes that from a fully verified check.

### Edge Cases

- Startup must not trigger a write storm — the first pass waits.
- The cheap check must never wrongly conclude "unchanged"; when unsure it must defer to the expensive one.
- A misconfigured interval must not produce a busy loop.
- With no store configured, the trigger must not run and log a failure every interval forever.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST re-store a plan whose source has changed, without human action.
- **FR-002**: System MUST write nothing when no source has changed.
- **FR-003**: System MUST record what each pass did, naming each plan it re-stored.
- **FR-004**: System MUST continue running after a failed pass.
- **FR-005**: System MUST avoid fetching the stored copy of a plan whose source clock has not advanced.
- **FR-006**: System MUST mark results reached by the cheap check as distinct from fully verified ones.
- **FR-007**: System MUST NOT run a pass during startup.
- **FR-008**: System MUST be disableable, and its interval configurable, without a code change.
- **FR-009**: System MUST NOT start when no store is configured.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An edited plan is re-stored with no human action within one interval.
- **SC-002**: A pass over unchanged plans writes zero versions and fetches zero stored bodies.
- **SC-003**: Every re-store is individually named in the record of the pass.
- **SC-004**: A pass that raises does not stop later passes.
- **SC-005**: A quiet system produces no routine noise.

## Assumptions

- Latency of minutes is acceptable: the defect this prevents took 22 days to notice.
- The cheap check is only ever an optimisation — the content comparison remains the sole authority for whether to write.
- This trigger is transitional. The master-plan notes F9 may remove the need for re-dissolve entirely, so it is built to be deleted.
