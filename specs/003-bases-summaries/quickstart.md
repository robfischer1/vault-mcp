# Quickstart: Bases Summaries

This guide shows how to add summaries to your Bases to get aggregated insights directly in your query results.

## Example Base with Summaries

```yaml
filters:
  and:
    - file.folder == "Projects"
formulas:
  Phase: note["phase"]
summaries:
  Total Projects: count
views:
  - name: Active
    filters:
      and:
        - note["status"] == "active"
    summaries:
      Active Count: count
      Average Phase: average(formula.Phase)
```

## How to Query via MCP

When you execute a base (e.g., via the `execute_base` tool), the `QueryResult` will now contain a `summaries` object:

```json
{
  "notes": [...],
  "summaries": {
    "Total Projects": 15,
    "Active Count": 4,
    "Average Phase": 1.5
  },
  "total": 4,
  "view_name": "Active"
}
```

## Supported Functions

| Function | Description | Example |
|----------|-------------|---------|
| `count` | Count matching notes | `count` |
| `sum` | Sum of numeric property | `sum(amount)` |
| `average`| Mean of numeric property | `average(score)` |
| `min` | Minimum value | `min(cost)` |
| `max` | Maximum value | `max(cost)` |
| `range` | Max minus Min | `range(score)` |
