# vault-mcp

FastMCP server exposing an Obsidian vault as read-only query tools over MCP.

## Features

- **4 modules** in `src/vault_mcp/` layout
- **MCP server** for AI assistant integration
- **Quality tooling** — pytest, ruff, mypy (strict)
- **Python 3.11+**
- **Apache-2.0 licensed**

## Quickstart

```bash
# Clone and install
git clone https://github.com/robfischer/vault-mcp.git
cd vault-mcp
uv venv && uv pip install -e ".[dev]"

# Run the MCP server
uv run python server.py
```

See [docs/fresh-start.md](docs/fresh-start.md) for a complete walkthrough.

## Architecture

```
vault-mcp/
├── src/vault_mcp/
│   ├── index.py        # TTL-cached vault index
│   ├── parsers.py      # Vault parsing helpers — canonical home
│   ├── rest_client.py  # HTTP client for the Obsidian Local REST API
│   └── watcher.py      # Filesystem watcher for incremental index invalidation
├── server.py
├── tests/
├── pyproject.toml
```

See [docs/architecture.md](docs/architecture.md) for the full design.

## MCP Server

See [docs/configuration.md](docs/configuration.md) for setup instructions.

## Development

```bash
uv run pytest  # 84 tests
uv run ruff check src/ tests/  # lint
uv run mypy src/               # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.

## Documentation

- [Architecture](docs/architecture.md) — module map, data flow
- [Configuration](docs/configuration.md) — settings reference, MCP server setup
- [Fresh Start](docs/fresh-start.md) — zero-to-working walkthrough

## License

[Apache-2.0](LICENSE)
