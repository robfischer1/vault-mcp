Codebase orientation for AI sessions. Posture and governance live in AGENTS.md
(furnace-compiled); this file is the repo-specific map, read on demand.

## Overview

`vault-mcp` is a FastMCP server exposing an Obsidian-compatible vault to AI assistants over MCP.
Two halves:

- **Read side**: TTL-cached index (`index.py`) over frontmatter/filenames/link graph, plus an
  optional Obsidian Local REST API bridge (`rest_client.py`) and `obsidian-cli` wrapper
  (`cli_client.py`).
- **Write side — the Convention Gate** (`gate.py`): the single write chokepoint. Generates
  frontmatter, validates tags against a closed glossary, routes to the schema-resolved directory,
  enforces per-directory write-protection, stamps provenance. Governance is data (an external
  YAML schema resolved via `VAULT_MCP_SCHEMA`), not prose — see `schema.py`.

No LLM runs inside this process. Per the source specs (`specs/010-vault-mcp-v2/spec.md`,
`specs/011-vault-mcp-v2-delta/spec.md`) vault-mcp is the **Create** layer of a
Create/Consume/Do split: it owns vault writes; `personal-history-db` (phdb, a sibling repo) owns
the queryable history/entity DB. Anything that looks like synthesis (compute rollups,
materialized notes) is pure template rendering of a payload phdb already produced — vault-mcp
never calls a model.

## Architecture / module map

`src/vault_mcp/` (19 modules; `server.py` is the only one that isn't a plain library module):

| Module | Responsibility |
|:---|:---|
| `server.py` | FastMCP entry point — every `@mcp.tool()` lives here (~80 tools, ~2.8k lines) |
| `index.py` | TTL-cached vault index; wraps `parsers.build_content_index` |
| `parsers.py` | Frontmatter + wikilink extraction — canonical parsing home (ported from vault-propagation) |
| `watcher.py` | `watchdog`-based filesystem watcher; invalidates only the changed file's index entries |
| `rest_client.py` | `httpx` client for Obsidian's Local REST API; uniform `{"ok": ..., "data"/"error": ...}` envelope, closed error-code vocabulary |
| `cli_client.py` | Subprocess wrapper around `obsidian-cli`; whitelisted-command allowlist (defense in depth — extending CLI integration means extending this allowlist) |
| `bases.py` | Obsidian Bases parser/evaluator/writer/validator; restricted AST formula evaluator (Tier 1 property access, Tier 2 `if`/`map`/`join`/string ops); regex timeout 100ms, nesting depth limit 10 |
| `gate.py` | Convention Gate — see Overview. `GateError` subclasses (`TagError`, `FieldError`, `ProtectionError`, `BodyError`, `LinkError`, `WriteModeError`, `FilenameError`) map 1:1 to `lint.py` `Code` values |
| `schema.py` | Loads/validates the external governance schema YAML (env var `VAULT_MCP_SCHEMA`); answers "is this tag valid" / "what directory does this @type route to" |
| `lint.py` | Standalone collect-all validator extracted from the Gate; same rules drive `write_note`, the `lint()` dry-run tool, and the `audit` healer — never writes, never raises, returns every `Finding` |
| `provenance.py` | The provenance spectrum: `human <- human-edited <- human-revised <- [collaboration] -> ai-assisted -> ai-compiled -> ai-metadata -> ai-computed` |
| `lifecycle.py` | `materialize` verb — structured payload -> durable note via `mode=COMPUTE`, the only path allowed to create `materialize-only` `@type`s |
| `lifecycle_verbs.py` | `dissolve`/`materialize` orchestration; sequences `translator.py` + phdb's typed-write HTTP routes |
| `translator.py` | Pure note <-> typed-payload mapping, no I/O, no phdb calls |
| `compute.py` | Compute Receiver — phdb's periodic compute payloads rendered through a named template, written via the Gate stamped `ai-computed` |
| `gitops.py` | Git committer; vault-mcp is the sole AI writer to vault git — every Gate write commits + returns a sha, a scheduled sweep commits/pushes the whole tree for out-of-band human edits |
| `hades_client.py` | Minimal MCP-over-streamable-HTTP client (one `initialize` + one `tools/call` per write, stateless) for constellation services reached through the Hades gateway |
| `phdb_client.py` | HTTP client that POSTs to phdb's `/emit` route for atom writes (decisions/reversals/tensions/pushback observed during a session) — vault-mcp never opens phdb's DB directly |
| `cli.py` | `vault-mcp-cli` — argparse smoke-test runner that dispatches a single tool call from the shell |

`server.py` sibling-imports phdb from `Path(__file__).resolve().parents[3] / "personal-history-db" / "src"`
(sys.path insert, not a declared dependency) — this repo assumes `personal-history-db` is checked
out as a sibling under the same Forge workspace root. Tracked as TODO SRSC #1317 to replace with a
config-addressed call.

## Entry points

- **Console scripts** (`pyproject.toml` `[project.scripts]`): `vault-mcp` ->
  `vault_mcp.server:main` (the MCP server); `vault-mcp-cli` -> `vault_mcp.cli:main` (single-tool
  smoke runner).
- **`server.py` at repo root** — a thin transitional shim (`from vault_mcp.server import main`)
  kept only so the live nas01 NSSM service's `python server.py` invocation keeps working until
  it's cut over to the `vault-mcp` console script. Not the canonical entry point; don't add logic
  to it.
- **MCP tools** (`src/vault_mcp/server.py`, all `@mcp.tool()`) fall into families:
  - Read/query: `find_notes_by_frontmatter`, `find_by_filename`, `recent_edits`, `read_note`,
    `note_search`, `note_lookup`, `note_list`, `backlinks_to`, `outbound_links`, `find_orphans`,
    `find_dangling_links`, `tag_glossary_check`, `all_tags`, `vault_stats`, `reindex`
  - Obsidian REST bridge: `rest_health`, `active_note`, `periodic_note`, `unsaved_buffer`,
    `obsidian_search`, `execute_command`, `dataview_query`, `jsonlogic_search`, `vault_tags`,
    `list_directory`, `open_in_obsidian`, `document_map`
  - `obsidian-cli` bridge: `obsidian_cli_status`, `obsidian_cli_reload_plugin`,
    `obsidian_cli_eval`, `obsidian_cli_command`
  - Bases: `subscribe_base` / `unsubscribe_base` (live updates), `parse_base`, `execute_base`,
    `write_base`, `validate_base_tool`
  - Governed writes (Convention Gate): `write_note`, `delete`, `move_note`, `lint`
  - Governance introspection: `list_types`, `list_tags`, `list_keys`, `query`, `audit`
  - Lifecycle verbs: `dissolve`, `materialize`, `compute_receive`, `compute_receiver`, `atom`
  - phdb triple tools: `query_triples`, `add_triple`, `delete_triple`, `delete_node`,
    `triple_stats`, `node_neighborhood`, `list_predicates`
  - Revision tools: `materialize_revision`, `list_file_revisions`, `revision_triple_deltas`

## Build / Test / Run

From `pyproject.toml`, `.pre-commit-config.yaml`, `.forgejo/workflows/ci.yml`:

```bash
uv venv && uv pip install -e ".[dev]"

uv run vault-mcp                    # run the MCP server (needs VAULT_MCP_PATH or a walk-up-able .obsidian/)
uv run vault-mcp-cli --tool <name> --json '{...}'   # single-tool smoke test, no MCP client needed

uv run pytest                       # 461 tests, 21 files, tests/ (pythonpath=src)
uv run pytest tests/test_gate.py    # single module (test_gate.py is the largest at 98 tests)
uv run pytest -k "pattern"          # by name

uv run ruff check src/ tests/       # lint — [tool.ruff.lint] select = ["ALL"], explicit ignore list in pyproject.toml
uv run mypy src/ tests/             # strict; server.py has a no-any-return override (dynamic REST/phdb boundary)
uv run pyright                      # strict peer gate; excludes src/vault_mcp/server.py (mypy-only there, phdb import)
uv run pre-commit run --all-files   # ruff --fix, detect-secrets (.secrets.baseline), trailing-whitespace/EOF/yaml/large-file checks
```

CI is Forgejo-native: `.forgejo/workflows/ci.yml` runs on the self-hosted `nas01` runner label
(container `catthehacker/ubuntu:act-22.04`), single Python version, `uv sync --all-extras` then
the same pre-commit/mypy/pyright/pytest gate. A `.github/workflows/ci.yml` mirror (matrix 3.11-3.13)
exists for the GitHub mirror but doesn't run here.

## Conventions and gotchas

- **`server.py` lives at `src/vault_mcp/server.py`, not repo root.** The root `server.py` is a
  historical-compat shim only. Import as `from vault_mcp.server import ...`; tests set
  `pythonpath = ["src"]`.
- **Ruff is opt-out, not opt-in**: `select = ["ALL"]` in `pyproject.toml`, then an explicit
  `ignore` list with rationale comments per entry. A `ruff` version bump auto-adopts new rules —
  treat `pre-commit autoupdate` as a review-new-rules event, not a routine bump.
- **mypy is the authoritative type gate; pyright is a strict peer**, not the other way around.
  `src/vault_mcp/server.py` is excluded from pyright (`[tool.pyright] exclude`) because pyright
  can't resolve the sibling `phdb` import on a clean checkout — mypy handles it via
  `ignore_missing_imports` for `phdb.*`.
- **The phdb sibling import is a `sys.path` hack, not a dependency.** `server.py` inserts
  `parents[3]/personal-history-db/src` at import time. Breaks if `personal-history-db` isn't
  checked out as a sibling at the expected depth under the Forge workspace root.
- **Governance is schema data, not prose.** Nothing in this repo should encode vault-specific
  filing rules in Python or in agent-facing prose — that lives in the YAML schema pointed to by
  `VAULT_MCP_SCHEMA`, loaded by `schema.py`. Writing tools raise `SchemaConfigError` if the env
  var is unset.
- **`GEMINI.md` and `CONTRIBUTING.md` are stale (v1-era).** Both describe a 5-6-module
  architecture (`index/parsers/rest_client/watcher/bases/cli_client`) that predates the entire
  Convention Gate layer (`gate.py`, `schema.py`, `lint.py`, `provenance.py`, `lifecycle*.py`,
  `translator.py`, `compute.py`, `hades_client.py`, `phdb_client.py`). `docs/architecture.md` is
  also v1-only (5 modules). Treat `server.py`'s tool list and `specs/010`/`specs/011` as current
  truth over any of these three docs.
- **Not containerized.** No `Dockerfile`/`compose.yaml`/`star.toml` in this repo — per the root
  `server.py` docstring, containerization (SRSC F2.2) is deferred; production today is an NSSM
  service on `nas01` invoking `python server.py`.
- **Env var surface** (all `VAULT_MCP_` prefixed, grep `src/vault_mcp/*.py` for the current set):
  `VAULT_MCP_PATH`, `VAULT_MCP_TTL_SECONDS`, `VAULT_MCP_WATCH`, `VAULT_MCP_REST_DISABLE`,
  `VAULT_MCP_REST_URL`, `VAULT_MCP_REST_KEY_PATH`, `VAULT_MCP_REST_KEY`, `VAULT_MCP_SCHEMA`,
  `VAULT_MCP_TEMPLATES`, `VAULT_MCP_MATERIALIZE_LINT`, `VAULT_MCP_OBSIDIAN_BIN`,
  `VAULT_MCP_HOST`, `VAULT_MCP_GIT_COMMIT`, `VAULT_MCP_GIT_PUSH`, `VAULT_MCP_GIT_SWEEP_SECONDS`,
  `VAULT_MCP_GIT_AUTHOR_NAME`, `VAULT_MCP_GIT_AUTHOR_EMAIL`. `docs/configuration.md` documents
  only one of these (`VAULT_MCP_LOG_LEVEL`, which doesn't even appear in source) — don't trust it,
  grep source instead.
- **Test fixtures are synthetic, single-file per test** (constitution principle III) — never
  commit real vault content or PII into `tests/fixtures/`.
- **Spec-kit workflow**: this repo uses `.specify/` + `specs/NNN-slug/` (spec.md/plan.md/
  tasks.md/research.md/data-model.md/contracts/) via the `speckit-*` skills in `.claude/skills/`.
  `.specify/memory/constitution.md` is the plan-stage gate (5 principles: Spec-Is-Law,
  Deferral-Terminates, Contracts-Named, Conformance-Checkable, Verify-Before-Done). Spec numbering
  is assigned by scanning `specs/` for the next free slot, not by a brief's intended number — two
  `006-*` directories exist (`006-bases-live-subscriptions`, `006-bases-map-views`,
  `006-obsidian-cli-integration`) from exactly this collision; check the actual directory after a
  pipeline run rather than trusting a brief filename.
- **`ARG002`, `PLC0415`, `T201`, `SLF001` etc. are deliberately off** in ruff — see the commented
  ignore list in `pyproject.toml` for the rationale on each (unused-args-as-interface-conformance,
  deferred optional-dependency imports, deliberate CLI prints, private-member access between
  sibling modules).

## Related repos

- **`personal-history-db` (phdb)** — sibling repo, sys.path-imported for the predicate table and
  reached over HTTP for atom-emit/triple writes (`phdb_client.py`) and typed-write routes
  (`lifecycle_verbs.py`/`translator.py`). Must be checked out as a sibling directory.
- **Hades gateway / constellation services** — reached via `hades_client.py`'s MCP-over-HTTP
  client for write paths that have moved off phdb's plain HTTP onto sovereign-star MCP verbs.
- **`vault-propagation`** — `parsers.py` was ported from there; per its docstring, that repo now
  imports parsing helpers from here instead of carrying its own copy.
- **`service-repo-template`** — named in `.specify/memory/constitution.md` as the target shape
  for new container-ready Forge MCP services (vault-mcp itself predates and doesn't yet conform —
  see "Not containerized" above).
