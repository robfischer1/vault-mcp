# Contract: execute_base Result (Grouped)

## Endpoint: MCP Tool `execute_base`

When a view with `groupBy` is executed, the `QueryResult` JSON returned to the MCP client includes the `groups` array.

### Schema (Partial)

```json
{
  "total": 3,
  "notes": [...],
  "groups": [
    {
      "label": "Active",
      "count": 2,
      "notes": [
        { "file.name": "Alpha", "status": "active" },
        { "file.name": "Beta", "status": "active" }
      ]
    },
    {
      "label": "Archived",
      "count": 1,
      "notes": [
        { "file.name": "Gamma", "status": "archived" }
      ]
    }
  ],
  "summaries": {
    "Total": 3
  }
}
```

### Constraints
- `groups` MUST be ordered by the group label according to `groupBy.direction`.
- Notes within each `group.notes` MUST be ordered according to the view's `sort` criteria.
- Group labels MUST be strings.
- Empty grouping results (no notes matched) MUST return an empty `groups` list.
