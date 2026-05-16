# Feature Specification: Bases Summaries

**Feature Branch**: `003-bases-summaries`

**Created**: 2026-05-16

**Status**: Draft

**Input**: User description: "## 003-bases-summaries **Status**: Queued. Dependencies: none (independent of Tier 2). Add summary-formula evaluation to vault-mcp's Bases support. Bases declare a `summaries:` section for aggregations across query results: count, sum, average, min, max, range, and a handful of derived statistics. Phase 1 parses summary configuration into the view structure but `execute_base` ignores it. This brief implements evaluation, so agents can ask aggregated questions (\"how many active plans?\", \"average phase across active plans?\") by reading the summary outputs of bases that already declare them, instead of executing the base and aggregating client-side. ### Initial scope - Summary expression evaluator covering: `count` (number of matched notes), `sum` (numeric sum over a column), `average` (arithmetic mean), `min`, `max`, `range` (max minus min). - View-level and base-level summary expressions both honored. - Summary results returned in `QueryResult` alongside the notes list, keyed by summary name. ### Out of scope - User-defined custom summary functions. - Statistical summaries beyond the core six (median, stddev, percentile) -- addable incrementally. - Summary expressions over Tier 2-evaluated formula columns (depends on 002 shipping). ### Success criteria - A base with `summaries: { Active Count: count }` returns the correct count when executed. - Summary computation completes in the same execution pass as the main query (no double-iteration of the vault index). - Summary results match Obsidian's own rendering for representative bases. ### Context Phase 2 of vault-mcp Bases support. The summary functionality is documented in the kepano obsidian-bases skill and is one of the formula-adjacent capabilities the vault doesn't currently exercise but would benefit from -- the Plans dashboard's \"active / total\" counts are the obvious first use case."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Basic Aggregation (Priority: P1)

As a user/agent, I want to see a count of all notes matching a base's criteria so that I can quickly understand the scale of the dataset without manual counting.

**Why this priority**: Fundamental requirement for the "Summaries" feature. Enables the most common use case (counting items).

**Independent Test**: Execute a base that defines a `count` summary and verify the count matches the number of returned notes.

**Acceptance Scenarios**:

1. **Given** a Base with `summaries: { Total: count }`, **When** executed against a vault, **Then** the result should include a "Total" summary with the integer count of matched notes.
2. **Given** a Base with no matching notes, **When** executed, **Then** the "Total" summary should return 0.

---

### User Story 2 - Numeric Summaries (Priority: P2)

As a user, I want to see the sum or average of numeric fields across matched notes so that I can perform basic quantitative analysis on my vault data.

**Why this priority**: Core analytical value of Bases. Allows users to see totals (e.g., total cost, average duration) directly in the base result.

**Independent Test**: Execute a base with `sum` and `average` summaries over a numeric property and verify the calculations match the vault content.

**Acceptance Scenarios**:

1. **Given** notes with a numeric property `amount`, **When** executing a base with `summaries: { Total Amount: sum(amount) }`, **Then** the result should include the correct arithmetic sum.
2. **Given** a note with a missing or non-numeric property, **When** calculating a summary, **Then** that note should be ignored in the calculation (or treated as 0 depending on the specific function).

---

### User Story 3 - Extremes and Range (Priority: P3)

As a user, I want to identify the minimum, maximum, and spread of values in my notes so that I can understand the boundaries of my data.

**Why this priority**: Completes the core set of summary functions. Useful for identifying oldest/newest entries or the range of values.

**Independent Test**: Execute a base with `min`, `max`, and `range` summaries and verify against the dataset boundaries.

**Acceptance Scenarios**:

1. **Given** a set of notes with dates or numbers, **When** executing a base with `min` and `max` summaries, **Then** the results should match the lowest and highest values found.
2. **Given** values 10 and 50, **When** executing a `range` summary, **Then** the result should be 40.

---

### Edge Cases

- **Mixed Types**: How does the system handle a column that contains both strings and numbers when performing a `sum`? (Assumption: Ignore non-numeric values).
- **Empty Result Set**: What are the results for `average`, `min`, `max`, and `range` when no notes match? (Assumption: `count` is 0, others are likely `null` or 0).
- **Missing Properties**: How are notes handled if they match the query but lack the property being summarized? (Assumption: Ignored for sum/avg/min/max/range, but still counted for `count`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support the following summary functions: `count`, `sum`, `average`, `min`, `max`, `range`.
- **FR-002**: System MUST evaluate summaries declared at both the Base level and the View level.
- **FR-003**: System MUST include summary results in the `QueryResult` object, keyed by the summary's display name.
- **FR-004**: System MUST perform summary calculations during the primary query execution pass to ensure O(N) performance.
- **FR-005**: System MUST support summarizing properties derived from frontmatter or note metadata.
- **FR-006**: System MUST handle non-numeric values gracefully in numeric summaries (`sum`, `average`, `min`, `max`, `range`) by excluding them from the calculation.

### Key Entities *(include if feature involves data)*

- **Base**: The top-level query definition, which can contain a global `summaries` section.
- **View**: A specific representation of the base results, which can have its own `summaries` section.
- **Summary**: An aggregation definition consisting of a display name and a function (e.g., `Active Count: count`).
- **QueryResult**: The final output of a base execution, now augmented with a `summaries` dictionary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A base with `summaries: { Total: count }` returns a count that exactly matches the number of notes in the `notes` list.
- **SC-002**: Summary evaluation adds less than 5% overhead to the total base execution time compared to a base without summaries.
- **SC-003**: 100% of the core six functions (`count`, `sum`, `average`, `min`, `max`, `range`) return correct mathematical results for a known dataset.
- **SC-004**: Results for `sum` and `average` match Obsidian's native implementation for the same vault data (to within standard floating point precision).

## Assumptions

- **Performance**: We assume the vault index is efficient enough that single-pass aggregation is performant for typical vault sizes (up to 10k notes).
- **Data Types**: We assume numeric summaries are only meaningful for numbers (or strings that can be parsed as numbers); otherwise, they are ignored.
- **Out of Scope**: Summaries over Tier 2 formula columns are explicitly out of scope for this phase.
- **Obsidian Compatibility**: We assume the syntax for `summaries:` in the YAML follows the standard Bases/Dataview patterns.
