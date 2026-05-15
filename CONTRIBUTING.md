# Contributing

## Development setup

```bash
git clone <repo-url>
cd vault-mcp
uv venv
uv pip install -e ".[dev]"
```

## Running checks

```bash
uv run pytest  # 84 tests
uv run pytest tests/test_example.py    # single module
uv run pytest -k "test_something"       # by name pattern
uv run pytest -xvs                      # verbose, stop on first failure

uv run ruff check src/ tests/     # lint
uv run ruff check --fix src/      # auto-fix safe issues
uv run ruff format src/ tests/    # format

uv run mypy src/                  # type check (strict mode)

```

All checks must pass before submitting a PR.

## Project structure

```text
src/vault_mcp/
├── index.py    TTL-cached vault index
├── parsers.py    Vault parsing helpers — canonical home
├── rest_client.py    HTTP client for the Obsidian Local REST API
├── watcher.py    Filesystem watcher for incremental index invalidation
```

## Conventions

- **Python 3.11+** with strict mypy
- **ruff** for linting: rules `[E, F, W, I, UP, B, SIM]`, line length 99
- **Test fixtures are synthetic** — never commit real user data

## Testing

Tests live in `tests/` with synthetic fixtures.

```python
def test_example():
    # Arrange
    ...
    # Act
    ...
    # Assert
    assert result == expected
```
