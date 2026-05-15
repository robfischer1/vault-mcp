# MCP Tool Contracts: Obsidian Bases Support

**Feature**: [../spec.md](../spec.md) | **Date**: 2026-05-15

4 new MCP tools registered in `server.py`, following the existing pattern (verb_noun naming, dict returns, no exceptions at tool boundary).

---

## `parse_base`

Parse a markdown file for Obsidian Bases code blocks and return their structured representation.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Vault-relative file path. Example: `"Outputs/Plans/Plans.md"` |

### Response

```json
{
  "path": "Outputs/Plans/Plans.md",
  "count": 1,
  "bases": [
    {
      "line_number": 12,
      "filters": {
        "op": "and",
        "children": [
          {"op": "eq", "field": "file.folder", "value": "Outputs/Plans"},
          {"op": "neq", "field": "file.name", "value": "Plans"}
        ]
      },
      "formulas": {
        "Status": {"expression": "note[\"status\"]", "tier": 1},
        "Phase": {"expression": "note[\"phase\"]", "tier": 1},
        "Updated": {"expression": "file.mtime", "tier": 1}
      },
      "views": [
        {
          "name": "Active",
          "type": "table",
          "filters": {"op": "neq", "field": "note.status", "value": "complete"},
          "order": ["file.name", "formula.Status", "formula.Phase", "formula.Updated"],
          "sort": [{"property": "formula.Updated", "direction": "DESC"}]
        },
        {
          "name": "All",
          "type": "table",
          "filters": null,
          "order": ["file.name", "formula.Status", "formula.Phase", "formula.Updated"],
          "sort": [{"property": "formula.Updated", "direction": "DESC"}]
        }
      ]
    }
  ],
  "errors": []
}
```

### Error Cases

| Condition | Response |
|-----------|----------|
| File not found | `{"error": "not_found", "path": "..."}` |
| File has no base code blocks | `{"path": "...", "count": 0, "bases": [], "errors": []}` |
| Invalid YAML in a code block | Returned in `errors` array: `{"line_number": 12, "message": "..."}`. Other valid bases still returned. |

---

## `execute_base`

Execute a base's filters and formulas against the vault index and return matching notes.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Vault-relative file path containing the base. |
| `view` | `str \| null` | no | Named view to restrict to. If null, base-level filters only. |
| `base_index` | `int` | no | 0-based index of which base to execute (default 0). For files with multiple base code blocks. |

### Response

```json
{
  "total": 3,
  "view": "Active",
  "notes": [
    {
      "path": "Outputs/Plans/phdb Records Layer.md",
      "frontmatter": {"note_type": "plan", "status": "active", "phase": 1},
      "formulas": {"Status": "active", "Phase": "1", "Updated": "2026-05-15T14:30:00"}
    }
  ],
  "warnings": []
}
```

### Warning Shape (for unsupported formulas)

```json
{
  "total": 5,
  "view": null,
  "notes": [{"path": "...", "frontmatter": {}, "formulas": {"ComplexOne": null}}],
  "warnings": [
    {"formula": "ComplexOne", "reason": "Tier 2 expression not supported: contains html()"}
  ]
}
```

### Error Cases

| Condition | Response |
|-----------|----------|
| File not found | `{"error": "not_found", "path": "..."}` |
| No base at given index | `{"error": "invalid_base_index", "path": "...", "index": 2, "available": 1}` |
| Named view not found | `{"error": "view_not_found", "path": "...", "view": "Missing", "available": ["Active", "All"]}` |
| View type unsupported | `{"error": "unsupported_view_type", "view": "Cards", "type": "cards", "detail": "Only table views are executable in Phase 1"}` |
| Parse error in base YAML | `{"error": "parse_error", "path": "...", "detail": "..."}` |

---

## `write_base`

Write or update an inline base code block in a markdown file.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Vault-relative file path. |
| `base` | `dict` | yes | Base configuration as a dict with keys: `filters`, `formulas`, `views`. Matches the YAML structure Obsidian expects. |
| `base_index` | `int \| null` | no | 0-based index of which base to replace. If null and file has no bases, appends a new base. If null and file has exactly one base, replaces it. |
| `validate` | `bool` | no | Run validation before writing (default true). If validation fails, write is rejected. |

### Response (success)

```json
{
  "written": true,
  "path": "Outputs/Plans/Plans.md",
  "action": "updated",
  "base_index": 0
}
```

### Response (validation failure)

```json
{
  "written": false,
  "path": "Outputs/Plans/Plans.md",
  "validation": {
    "valid": false,
    "errors": [{"type": "undefined_formula_ref", "message": "View 'Active' references formula.Missing but no formula 'Missing' is defined"}],
    "warnings": []
  }
}
```

### Error Cases

| Condition | Response |
|-----------|----------|
| File not found | `{"error": "not_found", "path": "..."}` |
| Ambiguous target (null index, multiple bases) | `{"error": "ambiguous_target", "path": "...", "count": 3, "detail": "Specify base_index when file has multiple bases"}` |
| Invalid base_index | `{"error": "invalid_base_index", "path": "...", "index": 5, "available": 2}` |

---

## `validate_base`

Validate a base configuration without writing it.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `base` | `dict` | yes | Base configuration dict (same shape as `write_base.base`). |

### Response

```json
{
  "valid": true,
  "errors": [],
  "warnings": []
}
```

### Validation Checks

| Check | Type | Severity |
|-------|------|----------|
| YAML structure is valid | `invalid_yaml` | error |
| All `formula.*` refs in view `order` have a matching formula definition | `undefined_formula_ref` | error |
| String values with YAML special characters are properly quoted | `unquoted_special_char` | warning |
| View `sort` properties reference valid columns | `undefined_sort_ref` | warning |
