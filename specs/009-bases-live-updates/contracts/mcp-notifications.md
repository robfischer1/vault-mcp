# MCP Notifications: Bases Live Updates

## Notification: `notifications/bases/update`

This is a custom notification pushed from the server to the client.

### Payload Schema

```json
{
  "method": "notifications/bases/update",
  "params": {
    "handle": "sub_12345",
    "path": "Projects.md",
    "view": "Active",
    "results": {
      "total": 12,
      "view": "Active",
      "notes": [
        {
          "path": "Projects/ProjectA.md",
          "frontmatter": { ... },
          "formulas": { ... },
          "note_warnings": { ... }
        },
        ...
      ],
      "warnings": [ ... ],
      "summaries": { ... }
    }
  }
}
```

## Tool: `subscribe_base`

### Arguments
*   `path` (str): Vault-relative path to the base file.
*   `view` (str, optional): Named view.
*   `base_index` (int, optional): Default 0.

### Returns
```json
{
  "handle": "sub_12345",
  "initial_results": { ... }
}
```

## Tool: `unsubscribe_base`

### Arguments
*   `handle` (str): The subscription handle.

### Returns
```json
{
  "ok": true
}
```
