# Data Model: Bases Summaries

**Feature**: 003-bases-summaries | **Date**: 2026-05-16

## New Entities

### Summary (Dataclass)
Represents a single aggregation rule.

- `name`: str (Display name, e.g., "Active Count")
- `function`: str (One of: `count`, `sum`, `average`, `min`, `max`, `range`)
- `property`: str | None (The property to aggregate, e.g., `amount` or `formula.Phase`)

## Entity Updates

### Base
- `summaries`: list[Summary] (Default: empty list)

### ViewConfig
- `summaries`: list[Summary] (Default: empty list)

### QueryResult
- `summaries`: dict[str, Any] (Map of summary names to their computed values)

## Validation Rules

- **FR-001 (Functions)**: Valid functions are strictly `count`, `sum`, `average`, `min`, `max`, `range`. Others result in a validation warning/error.
- **FR-006 (Types)**: If a non-numeric value is encountered for a numeric function (sum, average, etc.), it MUST be ignored for that specific calculation.
- **FR-002 (Overlap)**: If a View defines a summary with the same name as a Base-level summary, the View-level summary takes precedence in the result dictionary.
