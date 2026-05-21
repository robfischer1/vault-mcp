# Research: Bases Tier 5 — Grouping

## Decision: Single-pass Partitioning with Post-Sort

### Rationale
To meet **SC-003** (overhead <10%), we will perform grouping in a single pass after the initial filtering and formula evaluation of the notes. Instead of re-running the evaluation engine for grouping keys, we will leverage the already evaluated property values in the `note` dictionary.

### Alternatives Considered
- **Double-pass Evaluation**: Evaluating the grouping key separately. *Rejected*: Too expensive, doubles the work for complex formulas.
- **Lazy Grouping**: Grouping only when requested by the client. *Rejected*: The current `execute_base` tool returns the full result set immediately.

## Decision: Group Key Coercion to String

### Rationale
Obsidian Bases groups are rendered as text headings. For consistency and simplicity in the `QueryResult` JSON, all grouping keys (including numbers, booleans, and nulls) will be coerced to strings.

## Decision: "Error" Group for Failed Evaluations

### Rationale
To satisfy **Q7** (Error Isolation), if a grouping formula fails (e.g., regex timeout), the note will be placed in a dedicated "Error" group. This prevents a single bad note from breaking the entire dashboard view.

## Decision: Maintaining Internal Order

### Rationale
The notes will first be sorted by the view's `sort` criteria. Then, they will be partitioned into groups while maintaining their relative order within each group. Finally, the groups themselves will be sorted by the `groupBy.direction`.
