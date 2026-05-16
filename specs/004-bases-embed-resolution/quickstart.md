# Quickstart: Bases Embed Resolution

## Overview
This feature allows `vault-mcp` to resolve Obsidian Bases embeds in Markdown files. When you read a note that contains an embed like `![[MyProjects.base#Active]]`, the tool will execute that query and include the results in the response.

## Usage Example

1.  **Create a standalone base** (`Projects.base`):
    ```yaml
    filters:
      note["type"] == "project"
    views:
      - name: Active
        filters:
          note["status"] == "active"
    ```

2.  **Embed it in a note** (`Dashboard.md`):
    ```markdown
    # Dashboard
    
    Current active projects:
    ![[Projects.base#Active]]
    ```

3.  **Read the note via MCP**:
    Call `read_note(stem_or_path="Dashboard")`.
    
    **Response**:
    ```json
    {
      "path": "Dashboard.md",
      "body": "...",
      "resolved_embeds": [
        {
          "token": "![[Projects.base#Active]]",
          "path": "Projects.base",
          "results": {
            "notes": [...],
            "total": 5
          }
        }
      ]
    }
    ```

## Development
To verify the feature, run:
```bash
uv run pytest tests/test_bases.py -k "embed"
```
