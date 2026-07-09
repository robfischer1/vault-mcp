# vault-mcp

MCP server exposing an Obsidian-compatible vault as query + governed-write tools for AI assistants.

## What it is

`vault-mcp` is a FastMCP server that sits between an AI coding/writing assistant and an Obsidian
vault. It has two halves:

- **Read side** — a TTL-cached index over frontmatter, filenames, and the wikilink graph, plus
  an optional bridge to Obsidian's Local REST API (active note, search, Dataview/JSONLogic
  queries, Bases execution) and the `obsidian-cli` (plugin reload, eval, whitelisted commands).
- **Write side — the Convention Gate** — the single write chokepoint for the vault. Every write
  is generated as compliant frontmatter, checked against a closed tag glossary, routed to the
  schema-resolved directory, checked against per-directory write-protection rules
  (`fully-immutable` / `body-immutable` / `compute-only` / `voice-only`), and stamped with
  provenance — before it ever reaches disk. Callers don't need to know the vault's filing rules;
  the Gate can't be talked out of them. See `docs/security/write-protections.md` for the full
  deny surface.

Governance lives in an external, machine-readable schema file (`VAULT_MCP_SCHEMA` env var
pointing at a `vault-mcp.schema.yml`) rather than prose loaded into every AI session — the same
server works against any Obsidian-compatible vault.

No LLM runs inside vault-mcp. It is a deterministic query/write layer; anything that looks like
synthesis (compute rollups, materialized notes) is rendered by pure template substitution from a
structured payload produced elsewhere.

### Role in the fleet

In the source specs (`specs/010-vault-mcp-v2/spec.md`), vault-mcp is the **Create** layer of a
Create/Consume/Do split of MCP servers over a personal knowledge system — vault-mcp owns vault
writes, `personal-history-db` (phdb) owns the queryable history/entity database. It reaches phdb
directly over HTTP for atom-emit and triple writes (`phdb_client.py`), and reaches other
constellation services over an MCP-over-HTTP seam (`hades_client.py`).

## Quickstart

```bash
git clone https://github.com/robfischer1/vault-mcp.git
cd vault-mcp
uv venv && uv pip install -e ".[dev]"

# Point at a vault (or let it walk up from cwd looking for .obsidian/)
export VAULT_MCP_PATH=/path/to/vault

# Run the MCP server
uv run vault-mcp
```

See [docs/fresh-start.md](docs/fresh-start.md) for a full walkthrough and
[docs/configuration.md](docs/configuration.md) for MCP client setup (Claude Code, Claude Desktop).

The Convention Gate's write tools (`write_note`, `delete`, `move_note`, `dissolve`, `materialize`,
...) additionally require `VAULT_MCP_SCHEMA` pointing at a governance schema YAML file — without
it, `schema.py` raises rather than writing ungoverned.

### Smoke-test CLI

`vault-mcp-cli` runs a single read-only tool from the shell without an MCP client:

```bash
uv run vault-mcp-cli --tool read_note --json '{"stem_or_path": "AGENTS"}'
uv run vault-mcp-cli --tool recent_edits --json '{"since": "2026-05-01"}'
```

## Project structure

```text
server.py                # transitional root shim -> vault_mcp.server:main (see docstring)
src/vault_mcp/
├── server.py             # FastMCP entry point; all @mcp.tool() definitions
├── index.py              # TTL-cached vault index (frontmatter, filenames, link graph)
├── parsers.py             # frontmatter / wikilink parsing helpers — canonical home
├── watcher.py             # filesystem watcher, incremental index invalidation
├── rest_client.py         # HTTP client for the Obsidian Local REST API
├── cli_client.py          # subprocess wrapper for obsidian-cli (whitelisted commands)
├── bases.py               # Obsidian Bases parser / formula evaluator / writer
├── gate.py                # Convention Gate — the governed write API
├── schema.py              # schema engine: loads/validates the external governance YAML
├── lint.py                # standalone validation core shared by write / lint / audit
├── provenance.py          # the human <-> ai-computed provenance spectrum
├── lifecycle.py           # materialize verb (structured payload -> durable note)
├── lifecycle_verbs.py      # dissolve / materialize orchestration (vault <-> DB lifecycle)
├── translator.py          # note <-> typed-payload mapping (no I/O)
├── compute.py              # Compute Receiver: phdb payload -> rendered note via template
├── gitops.py               # git committer — vault-mcp is the sole AI writer to vault git
├── hades_client.py         # MCP-over-HTTP client for constellation services (Hades gateway)
├── phdb_client.py          # HTTP client for phdb's atom-emit / triple write routes
└── cli.py                  # vault-mcp-cli — local smoke-test runner for individual tools
tests/                       # 461 tests across 21 files, synthetic fixtures only
docs/                        # architecture, configuration, fresh-start, write-protections
specs/                        # spec-kit feature specs (numbered, DAG-ordered)
```

## Development

```bash
uv run pytest                      # full suite
uv run pytest tests/test_gate.py   # single module
uv run pytest -k "test_something"  # by name pattern

uv run ruff check src/ tests/      # lint (opt-out: ALL rules enabled, see pyproject.toml)
uv run mypy src/ tests/            # strict type check
uv run pyright                     # strict peer type check (server.py excluded; mypy-only there)
uv run pre-commit run --all-files  # lint + secret scan + hygiene, same gate as CI
```

CI (`.forgejo/workflows/ci.yml`) runs on a self-hosted Forgejo runner (`nas01`) and gates on
`pre-commit`, `mypy`, `pyright`, and `pytest`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor guide (note: its module list and test
count predate the v2 Convention Gate layer — trust this README and the source over it).

## Documentation

- [Architecture](docs/architecture.md) — module map, Bases/formula evaluator design (predates
  the Convention Gate layer; module list there is v1)
- [Configuration](docs/configuration.md) — MCP client setup
- [Fresh Start](docs/fresh-start.md) — zero-to-working walkthrough
- [Write protections](docs/security/write-protections.md) — the Gate's full deny surface

## License

[Apache-2.0](LICENSE)
