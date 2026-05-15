# Configuration Reference

## Environment variables

Environment variables use the `VAULT_MCP_` prefix.

| Variable | Purpose | Example |
|:---|:---|:---|
| `VAULT_MCP_LOG_LEVEL` | Logging level | `DEBUG`, `INFO` |

## MCP server setup

### Claude Code

Add to your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "vault-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/vault-mcp", "run", "python", "server.py"]
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vault-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/vault-mcp", "run", "python", "server.py"]
    }
  }
}
```
