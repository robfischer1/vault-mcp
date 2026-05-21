# Feature Specification: Bases Tier 5 — Grouping

**Feature Branch**: `005-add-groupby-evaluation`

**Created**: 2026-05-21

**Status**: Draft

**Input**: User description: "Add groupBy evaluation, including the case where the grouping key is itself a computed formula, to vault-mcp's Bases support. Bases support `groupBy: { property: <name>, direction: ASC|DESC }` on views, where the property can be a static frontmatter field, a file metadata field, or a computed formula reference. Phase 1 parses groupBy into the view structure but does not honor it during execution; this brief adds evaluation. The user value is enabling \"group by status,\" \"group by year-created,\" \"group by author\" view configurations. Most useful when the grouping key is a derived value (year extracted from a date, status icon from a status string) rather than a raw frontmatter field."

## Table of Contents

- [User Scenarios & Testing](#user-scenarios-testing-mandatory)
- [Requirements](#requirements-mandatory)
- [Success Criteria](#success-criteria-mandatory)
- [Assumptions](#assumptions)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Grouping by Raw Property (Priority: P1)

As a user, I want to see my notes grouped by a simple property like "status" so I can easily distinguish between active and archived items.

**Why this priority**: Foundational grouping capability. Essential for basic dashboard views.

**Independent Test**: Create a base with `groupBy: { property: status, direction: ASC }`. Verify that the execution result contains segments for "active", "completed", etc., in alphabetical order.

**Acceptance Scenarios**:

1. **Given** a vault with notes having `status: active` and `status: archived`, **When** I execute a base grouped by `status`, **Then** I receive a result set partitioned into "active" and "archived" groups.
2. **Given** the same vault, **When** I set `direction: DESC`, **Then** the "archived" group appears before the "active" group.

---

### User Story 2 - Grouping by Computed Formula (Priority: P2)

As a user, I want to group my notes by a derived value, such as the year they were created or a "Priority" icon based on a score.

**Why this priority**: Key differentiator for Bases. Allows for high-level organization without polluting frontmatter with redundant fields.

**Independent Test**: Define a formula `Year` as `file.ctime.format("YYYY")`. Set `groupBy: { property: formula.Year, direction: DESC }`. Verify results are grouped by year, with the most recent year first.

**Acceptance Scenarios**:

1. **Given** a base with a formula `Category` that uses an `if()` statement to map tags to broader groups, **When** I group by `formula.Category`, **Then** the results are grouped by the evaluated category string.
2. **Given** a note where the formula evaluates to `null` or an error, **When** grouped, **Then** the note is placed in a fallback "Other" or "None" group.

---

### User Story 3 - Preserving Group Member Order (Priority: P3)

As a user, I want the notes within each group to be sorted according to the view's `sort` criteria, so that each section is internally organized.

**Why this priority**: Critical for usability. A grouped view with chaotic internal ordering is difficult to read.

**Independent Test**: Create a base grouped by `status` and sorted by `file.mtime`. Verify that within the "active" group, notes are ordered by their last modification time.

**Acceptance Scenarios**:

1. **Given** a view with both `groupBy` and `sort` defined, **When** executed, **Then** the groups are ordered by the `groupBy.direction`, and notes within each group are ordered by the `sort` directives.

---

### Edge Cases

- **Missing Property**: If a note is missing the grouping property, it should be placed in a consistent "None" or "(Empty)" group.
- **Formula Error**: If the grouping formula crashes (e.g., regex timeout or recursion limit), the note should be placed in an "Error" group rather than failing the entire query.
- **Non-String Keys**: Grouping by numeric or boolean values should convert the key to a string for the group label.
- **Empty Result Set**: If the query returns no notes, the `groups` list should be empty.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST evaluate the grouping key for every note in the result set before partitioning.
- **FR-002**: System MUST support grouping by static frontmatter fields (e.g., `author`).
- **FR-003**: System MUST support grouping by file metadata fields (e.g., `file.name`, `file.ctime`).
- **FR-004**: System MUST support grouping by computed formula references (e.g., `formula.StatusIcon`).
- **FR-005**: System MUST sort the resulting groups based on the `groupBy.direction` (ASC or DESC).
- **FR-006**: System MUST maintain the view's internal `sort` order for notes within each group.
- **FR-007**: System MUST provide a consistent label for groups where the key evaluates to `null`, `undefined`, or empty string (default: "(None)").
- **FR-008**: System MUST update the `QueryResult` structure to include a `groups` field that maps group labels to their constituent notes or indices.

### Key Entities

- **Group**: A logical partition of the query results.
  - **Label**: The string representation of the evaluated grouping key.
  - **Notes**: An ordered list of notes belonging to this partition.
- **Grouping Configuration**: Part of the `ViewConfig`.
  - **Property**: The field or formula to group by.
  - **Direction**: Sort order for the groups (ASC/DESC).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested `groupBy` configurations return the correct number of groups based on the unique values of the key in the result set.
- **SC-002**: Grouping by complex formulas (Tier 2 features like `if`, `map`, `replace`) evaluates correctly and groups by the resulting string.
- **SC-003**: Execution overhead for grouping is minimal (<10% increase in `execute_base` time) by using a single-pass partitioning strategy.
- **SC-004**: Results are verifiable against Obsidian's native Bases rendering for the same vault and configuration.

## Assumptions

- **Single Level**: Only one level of grouping is supported (no nested `groupBy`).
- **String Keys**: All grouping keys are coerced to strings for labeling purposes.
- **Memory Safety**: Grouping is performed in-memory on the filtered result set, respecting existing memory limits for large queries.
