# Contract: execute_base Tool Response

**Feature**: 003-bases-summaries | **Date**: 2026-05-16

## Interface: MCP execute_base

The `execute_base` tool returns a structured JSON object.

### Response Schema

```json
{
  "total": "integer (number of matched notes)",
  "view": "string or null (the name of the view executed)",
  "notes": [
    {
      "path": "string (vault-relative path)",
      "frontmatter": "object (parsed frontmatter)",
      "formulas": "object (keyed by formula name, values are evaluated results)"
    }
  ],
  "warnings": [
    {
      "formula": "string (name of formula with warning, or empty for general warnings)",
      "reason": "string (warning message)"
    }
  ],
  "summaries": {
    "Display Name": "any (computed value based on summary function)"
  }
}
```

### Summary Value Types
- `count`: Integer
- `sum`: Number (float/int)
- `average`: Number (float/int) or `null` (if 0 items)
- `min`: Number or `null`
- `max`: Number or `null`
- `range`: Number (max - min) or `null`

### Precedence
If both Base-level and View-level define a summary with the same key, the **View-level** value wins.
