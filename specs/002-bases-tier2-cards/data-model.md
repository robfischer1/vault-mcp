# Data Model: Bases Tier 2 Expressions and Cards View

## Entities

### Tier 2 Formula
- **Expression**: String containing logic (`if`), list methods (`map`, `join`, `replace`), or operators (`+`).
- **Context**: The set of available data for evaluation (frontmatter, file metadata, links).
- **Constraints**:
    - Max Nesting: 10 levels.
    - Max Regex Execution: 100ms.

### Cards View (ViewConfig Extension)
Extends the existing `ViewConfig` to include card-specific metadata extracted from the `extra` field.

| Property | Type | Description |
|----------|------|-------------|
| `cardSize` | string | Desired size of the card (e.g., "small", "medium", "large"). |
| `image` | string | The property name or path to the image to display. |
| `imageAspectRatio` | string | Aspect ratio for the image (e.g., "16/9"). |
| `indentProperties` | boolean| Whether to indent properties under the card. |

### Query Result (Extended)
The response from `execute_base` now includes view-specific properties if the view type is `cards`.

```json
{
  "notes": [
    {
      "path": "string",
      "frontmatter": "object",
      "formulas": "object"
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
    "type": "cards",
    "cardSize": "string",
    "image": "string",
    "imageAspectRatio": "string",
    "indentProperties": "boolean"
  },
  "total": "integer"
}
```

## Relationships
- A **Base** contains multiple **Formulas** (Tier 1 or Tier 2).
- A **Base** contains multiple **ViewConfigs** (Table or Cards).
- **execute_base** matches a specific **ViewConfig** and evaluates all associated **Formulas** for the filtered set of notes.
