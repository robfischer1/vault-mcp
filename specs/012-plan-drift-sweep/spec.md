# Feature Specification: Re-dissolve the stale plan copies

**Feature Branch**: `feat/plan-freshness`

**Created**: 2026-08-11

**Status**: Draft

**Input**: Master-plan feature node F1 — "The Plan Stops Being Prose — Work Nodes Over Calliope Blocks"

> **Brief:** Calliope's stored copy of the Aglaia master-plan is 18,584 bytes short of disk and 22 days behind it — one version, dissolved once, never again. Re-dissolve it and sweep every other plan in `System/Pantheon/WBS/` for the same drift. For Rob, because this single stale copy is why A21 and A22 never reached the board.
>
> **Consumer:** `athena orchestrate_plan`; every session that trusts the graph's view of a plan.
>
> **Success:** every WBS plan's stored `raw_hash` matches disk; the Aglaia copy is 109,593 bytes; a drift report names any plan that was behind and by how much.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See which plans the store is behind on (Priority: P1)

Rob (or any session) asks which master-plans the store's copy no longer matches
the vault's copy. The answer names each drifted plan and the size of the gap,
without changing anything.

**Why this priority**: The measurement is the whole point. Today the only way to
learn a plan is stale is to hand-compare byte counts, which is why a 22-day drift
went unnoticed. A read-only drift report is independently valuable even if no
refresh ever runs — it converts an invisible failure into a visible one.

**Independent Test**: Run the report against the vault with no writes enabled and
confirm it names the known-stale Aglaia plan and reports a non-zero byte delta.

**Acceptance Scenarios**:

1. **Given** a plan whose vault file differs from the stored copy, **When** the drift report runs, **Then** the plan is listed with its stored size, its vault size, and the delta.
2. **Given** a plan whose stored copy matches the vault byte-for-byte, **When** the drift report runs, **Then** it is reported as current and not listed as drifted.
3. **Given** a plan present in the vault but absent from the store, **When** the drift report runs, **Then** it is reported as missing rather than as drifted.
4. **Given** the report runs, **Then** no vault file is modified and no stored copy is written.

---

### User Story 2 - Bring a stale plan's stored copy up to date (Priority: P1)

A drifted plan is refreshed so the store holds the vault's current bytes. The
vault file is never touched — refreshing is a read of the vault and a write to
the store, in that direction only.

**Why this priority**: This is the fix for the live defect. Equal priority to the
report because a report with no remedy leaves the bleeding unstopped.

**Independent Test**: Refresh the known-stale Aglaia plan, then re-run the drift
report and confirm it reports current.

**Acceptance Scenarios**:

1. **Given** a plan on disk newer than its stored copy, **When** the refresh runs, **Then** a new version lands in the store and the delta is reported.
2. **Given** a plan already current, **When** the refresh runs, **Then** it is a no-op and nothing is written.
3. **Given** a refresh of any plan, **When** it completes, **Then** the vault source file still exists and is unmodified.
4. **Given** a store write fails, **When** the refresh runs, **Then** the failure is reported per-plan and the sweep continues with the remaining plans.

---

### User Story 3 - Sweep every plan in one pass (Priority: P2)

The whole `System/Pantheon/WBS/` directory is checked in a single run, so drift
is caught across all plans rather than one at a time.

**Why this priority**: Aglaia is the plan we measured, but nothing suggests it is
the only one. The sweep converts a known one-off into a bounded, repeatable check.

**Independent Test**: Run the sweep over the WBS directory and confirm the report
accounts for every `.md` plan found there.

**Acceptance Scenarios**:

1. **Given** the WBS directory, **When** the sweep runs, **Then** every plan file in it appears in the report exactly once.
2. **Given** the sweep runs in report-only mode, **Then** it is safe to run at any time and writes nothing.

### Edge Cases

- A vault file exists but the store has no copy at all → reported as `missing`, not silently skipped.
- A stored copy exists whose vault file has been deleted or renamed → reported as `orphaned`; not deleted (the store is insert-only and this feature does not remove history).
- Two stored versions exist for one source path → the newest is the comparison basis.
- A plan file that is empty or unreadable → reported as an error for that plan; the sweep continues.
- A non-`.md` file in the WBS directory → ignored.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST report, for each plan in the vault's WBS directory, whether the stored copy matches the vault copy.
- **FR-002**: System MUST determine "matches" by comparing the stored content hash against the hash of the vault file's current body, not by comparing timestamps.
- **FR-003**: System MUST report the byte delta between the stored copy and the vault copy for each drifted plan.
- **FR-004**: System MUST default to report-only, performing no writes unless a refresh is explicitly requested.
- **FR-005**: System MUST, when refreshing, write the vault's current bytes to the store as a new version without deleting or modifying the vault source file.
- **FR-006**: System MUST treat a refresh of an already-current plan as a no-op that writes nothing.
- **FR-007**: System MUST continue the sweep when an individual plan fails, reporting that plan's error rather than aborting the run.
- **FR-008**: System MUST distinguish the outcomes `current`, `drifted`, `missing`, `orphaned`, and `error` for each plan.
- **FR-009**: System MUST allow the swept directory to be specified, defaulting to the WBS plan directory.

### Key Entities

- **Plan file**: a markdown note in the vault's WBS directory. Has a vault-relative source path, a body, and a size.
- **Stored copy**: the store's record of a plan, keyed by source path. Has a content hash, a size, and provenance attributes.
- **Drift record**: the per-plan comparison outcome — source path, state, stored size, vault size, delta, and any error.
- **Sweep report**: the collection of drift records plus counts per state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every plan in the WBS directory appears in the sweep report exactly once, with a state assigned.
- **SC-002**: After a refresh sweep, every plan reports `current` — the stored hash equals the vault hash for all of them.
- **SC-003**: The Aglaia master-plan's stored copy measures 109,593 bytes after refresh, up from 91,009.
- **SC-004**: A drifted plan is named in the report together with how far behind it was, before any fix is applied.
- **SC-005**: Running the sweep in its default mode leaves the vault and the store byte-identical to before the run.
- **SC-006**: A single failing plan reduces the report's success count by exactly one and does not prevent the remaining plans from being processed.

## Assumptions

- The vault's WBS directory is the authoritative source for plan prose; the store is a copy of it. Refresh is one-directional, vault → store.
- The store is insert-only and versioned; refreshing adds a version rather than mutating one, so a refresh is never destructive to history.
- Comparing hashes of the body (frontmatter excluded) is the correct equality test, because that is what the store holds.
- The sweep is operated deliberately by a session or an operator; continuous/automatic triggering is a separate concern (master-plan F4) and out of scope here.
- Populating provenance correctly on the written copy (modification time, document type) is a separate concern (master-plan F2 and F3); this feature writes whatever the existing payload builder produces.
