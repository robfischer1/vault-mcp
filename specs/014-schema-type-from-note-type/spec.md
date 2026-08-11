# Feature Specification: Correct schema_type tagging for dissolved plans

**Feature Branch**: `feat/plan-freshness`

**Created**: 2026-08-11

**Status**: Draft

**Input**: Master-plan feature node F3 — "The Plan Stops Being Prose"

> **Brief:** Dissolved plans carry `schema_type: DigitalDocument`, the table default, so filtering `read_documents` by `Master-plan` returns **empty** — you cannot enumerate which plans are even in the store. Tag from the source's frontmatter `note_type`. For Rob, because the type filter is currently dead and there is no way to ask "which plans do you have."
>
> **Consumer:** any consumer enumerating plans; F4's sweep; future audits.
>
> **Success:** a dissolved plan carries `schema_type: Master-plan`; `read_documents(schema_type="Master-plan")` returns the set; existing rows are re-tagged.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask which plans are in the store (Priority: P1)

A caller filters stored documents by type and gets the master-plans back.

**Why this priority**: The filter is dead today — it returns empty for every
vault type, so "which plans do you have" is unanswerable. Everything else in
this feature exists to make this one question work.

**Independent Test**: Filter for the plan type and confirm the known stored
plans come back.

**Acceptance Scenarios**:

1. **Given** a note declaring its type as a master-plan, **When** dissolved, **Then** the stored copy carries that type rather than the generic default.
2. **Given** stored plans, **When** filtered by the plan type, **Then** they are returned.
3. **Given** a note declaring no type at all, **When** dissolved, **Then** it carries the generic default.

---

### User Story 2 - Already-stored copies gain their real type (Priority: P2)

Copies stored before this change carry the generic default. They are re-tagged
from their source.

**Why this priority**: Without it the filter is only correct for notes captured
after the change — the enumeration would silently under-report.

**Acceptance Scenarios**:

1. **Given** an existing stored copy tagged with the generic default and a live source declaring a type, **When** re-tagged, **Then** the stored type matches the source.
2. **Given** re-tagging, **Then** the stored body is not rewritten and no new version is created.

---

### User Story 3 - The type survives the round trip (Priority: P3)

A note dissolved and later restored comes back with the type it started with.

**Why this priority**: The restore path already maps the stored type back onto
the note's declared type, so today a master-plan restores as a generic document
— a silent downgrade. Fixing the capture side fixes the round trip for free, and
this story is what proves it.

**Acceptance Scenarios**:

1. **Given** a note of a given declared type, **When** dissolved and then restored, **Then** its declared type is unchanged.

### Edge Cases

- A note declaring a type that is not a recognised Schema.org name (e.g. a
  vault-native plan type) must still be stored faithfully — the point is to stop
  flattening types, not to flatten a different set of them.
- A note declaring conflicting types in different fields: one field is
  authoritative and the choice must be deterministic.
- Notes that route somewhere other than the document store must keep routing
  exactly as they do today — this feature changes what a document is *labelled*,
  never *where it goes*.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST tag a stored copy with the type its source note declares.
- **FR-002**: System MUST treat the note's own type field as authoritative when present, and fall back to the generic type field only when it is absent.
- **FR-003**: System MUST store a declared vault-native type faithfully, not coerce it to the generic default.
- **FR-004**: System MUST record the generic default when the source declares no type.
- **FR-005**: System MUST NOT change which store or table any note routes to.
- **FR-006**: System MUST be able to re-tag existing stored copies from their live sources without rewriting their bodies.

### Key Entities

- **Declared type**: the note's own statement of what it is (`note_type`), a Gate-controlled vocabulary.
- **Stored type**: the label the store keeps, used for filtering and for the restore path's type restoration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A dissolved master-plan carries the master-plan type, not the generic default.
- **SC-002**: Filtering the store by the master-plan type returns the stored master-plans, where it previously returned an empty set.
- **SC-003**: After re-tagging, every stored plan with a live source carries its source's declared type.
- **SC-004**: Re-tagging creates no new stored version and changes no stored body.
- **SC-005**: Routing is unchanged: the same notes reach the same stores as before.

## Assumptions

- The note's declared type is a controlled vocabulary (the Gate validates it on write and drives it from a schema), so it does not need the allowlist filtering applied to the free-form generic type field, which any note could set to anything.
- The vault schema's own statement — "the Gate's `note_type` carries the @type value" — means consulting it is honouring the existing convention, not inventing one.
- Re-tagging can ride the existing provenance-reconcile path, which corrects attributes without minting a body version.
