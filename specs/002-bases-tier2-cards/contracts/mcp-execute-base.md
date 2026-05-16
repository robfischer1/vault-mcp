# Contract: execute_base (MCP Tool)

## Interface: MCP `execute_base` Tool

The `execute_base` tool executes a specific view within a Base and returns the computed results.

### Input Schema

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `path` | string | Yes | The absolute or project-relative path to the markdown file containing the Base. |
| `base_index` | integer| No | The 0-based index of the base block in the file (defaults to 0 if only one block exists). |
| `view_name` | string | No | The name of the view to execute (defaults to the first defined view). |

### Output Schema (Success)

The output is a JSON string representing the `QueryResult`.

```json
{
  "notes": [
    {
      "path": "string",
      "frontmatter": "object",
      "formulas": {
        "formula_name": "any"
      }
    }
  ],
  "warnings": [
    {
      "formula": "string",
      "reason": "string"
    }
  ],
  "view_name": "string",
  "view_properties": {
    "type": "table | cards",
    "cardSize": "string (optional)",
    "image": "string (optional)",
    "imageAspectRatio": "string (optional)",
    "indentProperties": "boolean (optional)"
  },
  "total": "integer"
}
```

### Constraints & Invariants
- **Compatibility**: If `view_name` is not provided, the base-level filters and formulas are executed without view-specific overrides.
- **Degradation**: If a formula cannot be evaluated (Tier 3+ or error), the value is `null` and a warning is added to the `warnings` list.
- **Safety**: Regex evaluations are terminated after 100ms.
- **Depth**: Recursive or deeply nested `if()` expressions are terminated after 10 levels.
