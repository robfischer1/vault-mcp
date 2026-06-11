# Fresh Start Guide

A complete walkthrough from zero to running vault-mcp.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- An MCP-compatible AI assistant (Claude Code, Claude Desktop, etc.)

## 1. Install the project

```bash
git clone <repo-url>
cd vault-mcp
uv venv
uv pip install -e ".[dev]"
```

## 2. Run

Start the MCP server:

```bash
uv run vault-mcp
```

See [configuration.md](configuration.md) for Claude Code and Claude Desktop setup.

## 3. Verify

Run the test suite to confirm everything works:

```bash
uv run pytest
```

## Troubleshooting

### Import errors

Make sure you installed in editable mode (`-e`) and the virtual environment is active.

### MCP connection refused

Verify the server is running and the path in your MCP config matches the project location.

## Next steps

- Read [architecture.md](architecture.md) for the design overview
- Read [configuration.md](configuration.md) for settings reference
- See [CONTRIBUTING.md](../CONTRIBUTING.md) for the development guide
