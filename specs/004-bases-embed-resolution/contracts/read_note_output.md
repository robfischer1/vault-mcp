# Contract: read_note Response (Augmented)

## Interface
- **Tool**: `read_note`
- **Component**: `VaultIndex`

## Augmented Response Schema

The response from `read_note` is extended with a `resolved_embeds` field.

```json
{
  "path": "string (vault-relative path)",
  "frontmatter": "object (parsed YAML)",
  "body": "string (raw markdown body)",
  "outbound_links": [
    {
      "stem": "string",
      "path": "string",
      "resolution": "string (optional)"
    }
  ],
  "resolved_embeds": [
    {
      "token": "string (original embed token)",
      "path": "string (resolved target path)",
      "results": {
        "notes": "list (query results)",
        "warnings": "list (evaluation warnings)",
        "view_name": "string | null",
        "summaries": "object",
        "total": "number"
      },
      "error": {
        "type": "string (not_found | view_not_found | parse_error)",
        "message": "string"
      }
    }
  ]
}
```

## Error Shapes
- **File Not Found**: `{"error": {"type": "not_found", "message": "File not found: target.base"}}`
- **View Not Found**: `{"error": {"type": "view_not_found", "message": "View 'Missing' not found in target.base"}}`
- **Parse Error**: `{"error": {"type": "parse_error", "message": "YAML error..."}}`
