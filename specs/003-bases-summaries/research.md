# Research: Bases Summaries

**Feature**: 003-bases-summaries | **Date**: 2026-05-16

## Summary Expression Syntax

Bases summaries follow a `{ Display Name: expression }` format in YAML.

### Findings
- `count`: Aggregates the number of matched notes. No property argument.
- `sum(property)`: Numeric sum of the specified property across matched notes.
- `average(property)`: Arithmetic mean of the specified property.
- `min(property)`: Minimum value found in the specified property.
- `max(property)`: Maximum value found in the specified property.
- `range(property)`: Difference between `max(property)` and `min(property)`.

### Decision: Expression Parser
We will use a regex-based parser to extract the function name and the optional property argument.

```python
_SUMMARY_RE = re.compile(r"^(\w+)(?:\((.+)\))?$")
```

- If function is `count`, property is ignored.
- For others, property is required.
- If property starts with `formula.`, it refers to a calculated formula (though Tier 2 formulas are out of scope for Phase 1, the infrastructure should support it).
- Otherwise, it refers to a frontmatter/metadata property.

## Single-Pass Evaluation Strategy

To meet **SC-002** (<5% overhead) and avoid double-iteration, summaries must be updated while iterating notes in `execute_base`.

### State Management
We will initialize an accumulator for each summary:
- `count`: counter (int)
- `sum`: total (float)
- `average`: `sum` and `count_with_value` (to divide at the end)
- `min`: current_min (float, init to +inf)
- `max`: current_max (float, init to -inf)
- `range`: tracks `min` and `max`

### Null/Type Handling
- Notes missing the property are ignored for numeric summaries but included in `count`.
- Non-numeric values found in numeric properties are ignored (logged as warnings if necessary, but spec says "exclude from calculation").

## Integration Points

### Data Structures
- `Summary` dataclass to store parsed definition.
- `Base` and `ViewConfig` updated to hold `list[Summary]`.
- `QueryResult` updated to return `summaries: dict[str, Any]`.

### Execution Flow
1. Pre-collect all relevant summaries (Base-level + View-level if applicable).
2. Initialize accumulators.
3. During note loop:
   - For each summary, extract the property value.
   - Update accumulators.
4. After loop:
   - Finalize `average` and `range`.
   - Populate `QueryResult.summaries`.

## Consistency with Obsidian
Obsidian's Bases implementation returns summaries in a dedicated section. Our `QueryResult` mapping to MCP tools will naturally expose this dictionary.
