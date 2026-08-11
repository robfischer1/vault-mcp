# Feature Specification: Track mtime on dissolved document rows

**Feature Branch**: `feat/plan-freshness`

**Created**: 2026-08-11

**Status**: Draft

**Input**: Master-plan feature node F2 — "The Plan Stops Being Prose"

> **Brief:** `mtime` is null on the stored row, so the store holds **no clock for its own copy** and nothing can compute staleness. Populate it at dissolve from the source file, and expose it on `read_documents`. For Rob, so the drift in F1 becomes detectable instead of requiring a hand comparison.
>
> **Consumer:** F4's automatic trigger; any freshness check.
>
> **Success:** every dissolved row carries the source file's mtime; existing rows are backfilled where the source still exists; a staleness query is one comparison.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask whether a stored copy is stale, cheaply (Priority: P1)

A caller asks "is the store's copy of this note behind the vault's?" and gets the
answer by comparing two timestamps, without reading or hashing the body.

**Why this priority**: This is the capability the whole feature exists for. F1's
drift report must read every stored body to hash it — 175 store reads returning
~10MB. A timestamp comparison makes the same question answerable from an index.

**Independent Test**: Store a note, modify its file, and confirm the stored
timestamp is older than the file's — without reading the body.

**Acceptance Scenarios**:

1. **Given** a note dissolved from a file, **When** the row is written, **Then** its recorded modification time is the source file's, not a value copied from the note's frontmatter.
2. **Given** a stored copy and a source file modified since capture, **When** the two times are compared, **Then** the copy is identifiable as stale by that comparison alone.
3. **Given** a stored copy whose source file has not changed since capture, **When** compared, **Then** it is identifiable as current.

---

### User Story 2 - A source with no file still stores something sensible (Priority: P2)

Not every dissolved document comes from a file on disk. Those still record a
best-available modification time rather than nothing.

**Why this priority**: The master-plan names this as an open gap ("what mtime
means for a document dissolved from a non-file source"). Leaving it unhandled
would reintroduce the null the feature exists to remove.

**Independent Test**: Translate a note with no resolvable file and confirm a
modification time is still recorded when the note declares one.

**Acceptance Scenarios**:

1. **Given** a source with no accessible file but a declared update time, **When** dissolved, **Then** the declared time is recorded and the fallback is not silent.
2. **Given** a source with neither, **When** dissolved, **Then** no modification time is recorded rather than a fabricated one.

---

### User Story 3 - Existing stored copies gain the clock (Priority: P2)

Copies stored before this feature carry a hand-maintained time or none. Where the
source file still exists, they are corrected.

**Why this priority**: Without it the new comparison is only trustworthy for
notes captured after the change — a silent two-tier store.

**Acceptance Scenarios**:

1. **Given** an existing stored copy and a live source file, **When** backfilled, **Then** its recorded modification time becomes the source file's.
2. **Given** an existing stored copy whose source file is gone, **When** backfilled, **Then** it is reported as unbackfillable and left unchanged.

### Edge Cases

- The recorded time must be the file's time **at capture**, not at read — otherwise every copy always looks current.
- A file whose declared update time disagrees with its filesystem time: the filesystem time wins (it is mechanical; the declared one is hand-maintained and drifts — measured: Aglaia declares `2026-07-24` while the file was modified `2026-08-11`).
- Timestamps must be comparable across sources, so they need one format and one timezone basis.
- A source file that is unreadable or unstattable must not fail the dissolve.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST record, on each stored copy, the modification time of the source file as of the moment of capture.
- **FR-002**: System MUST prefer the source file's filesystem modification time over any time declared inside the note.
- **FR-003**: System MUST fall back to the note's declared update time when no source file is accessible, and MUST record nothing when neither is available.
- **FR-004**: System MUST record the time in a single, comparable, timezone-explicit format.
- **FR-005**: System MUST expose the recorded time on the read path so a caller can compare it without fetching the body.
- **FR-006**: System MUST be able to backfill existing stored copies from their live source files, reporting those it cannot.
- **FR-007**: System MUST NOT fail a dissolve because a source file could not be stat'd.

### Key Entities

- **Source modification time**: when the source file was last written, as of capture. Mechanical, not declared.
- **Declared update time**: the note's own claim about when it was updated. Hand-maintained; the fallback only.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every newly stored copy of a file-backed note carries the source file's modification time.
- **SC-002**: Determining staleness for one note requires exactly one timestamp comparison and zero body reads.
- **SC-003**: After backfill, every stored copy whose source file exists carries that file's modification time; each one that could not be backfilled is named.
- **SC-004**: A note whose declared update time disagrees with its file's modification time stores the file's.
- **SC-005**: No dissolve fails because of a stat error.

## Assumptions

- The filesystem modification time is the honest signal for "when did this source last change"; the note's declared `updated` is documentation, not a clock. Measured evidence: the Aglaia plan declares `2026-07-24` and was last written `2026-08-11`.
- Recorded times are stored as ISO-8601 strings in UTC — the format already used for the store's other provenance times.
- Backfill is an operator-invoked pass, not a continuous process; continuous triggering is F4.
- Redefining the recorded time is safe for round-trips: the materialize path does not read it (verified — it restores an empty frontmatter).
