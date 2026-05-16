# GEMINI.md — vault-mcp

vault-mcp is a FastMCP server exposing an Obsidian-compatible vault as
read-only query tools over MCP. Standalone Python package, MIT-licensed.

## Constitution

The project constitution at `.specify/memory/constitution.md` is authoritative.
Read it before any substantive work. Five non-negotiable principles:

1. **Sibling, Not Subordinate** — no vault-specific paths in `src/vault_mcp/`.
2. **REST API Augments** — parsers work without Obsidian running.
3. **Test-First, Single-File Fixtures** — one fixture per test, tests before code.
4. **Publish-Ready Hygiene** — MIT, no PII, no personal vault data in fixtures.
5. **Strict Quality Tooling** — `mypy --strict`, `ruff` clean, `pytest` green.

## Build & Development

```bash
uv venv && uv pip install -e ".[dev]"

uv run pytest                     # all tests
uv run pytest tests/test_bases.py # bases only
uv run pytest -xvs               # verbose, stop on first failure

uv run ruff check src/ tests/     # lint
uv run ruff format src/ tests/    # auto-format
uv run mypy src/                  # strict type check
```

Always use `uv run`. Never activate the venv manually. Never use global pip.

## Workflow (spec-kit)

Substantive features (>30 min or >3 files) go through spec-kit:

```
/speckit-specify    → spec.md
/speckit-clarify    → de-risk ambiguities
/speckit-plan       → plan.md + research.md + data-model.md + contracts/
/speckit-tasks      → tasks.md
/speckit-analyze    → consistency check (optional)
/speckit-implement  → execute tasks.md
```

Quick fixes skip the ceremony and go straight to commit.

## Architecture

```
src/vault_mcp/
├── bases.py        # Obsidian Bases parser, evaluator, writer, validator
├── index.py        # TTL-cached vault index with link graph
├── parsers.py      # YAML frontmatter + wikilink extraction
├── rest_client.py  # Centralized HTTP client for Local REST API
└── watcher.py      # Filesystem watcher for incremental invalidation

server.py           # FastMCP server entry point (tool definitions)

tests/
├── fixtures/       # Single-file test fixtures (generic, no PII)
│   ├── bases/      # Bases-specific fixtures
│   └── bases-vault/# Mini vault for integration tests
├── test_bases.py   # Bases tests (71 tests)
├── test_index.py   # Index + query tests
├── test_parsers.py # Parser tests
├── test_rest_client.py  # REST client unit tests
└── test_rest_smoke.py   # REST integration tests (mocked)
```

Module count is disciplined. Adding modules requires justification in a plan.

## What's Shipped

| Spec | Feature | Status |
|------|---------|--------|
| 001 | Bases Tier 1 — parse, execute, write, validate | Complete |
| 002 | Bases Tier 2 — `if()`, `.map()`, `.join()`, `.replace()`, `html()`, `.toString()`, `+`, cards view | Complete (after fix pass) |

## Lessons from 002 (Read This)

The initial 002 implementation had three critical failures:

1. **6 of 10 test tasks were checked off but the test functions didn't exist.**
   The tasks.md showed `[x]` complete, but the corresponding test methods were
   never written in `test_bases.py`. This is the worst failure mode — it wastes
   review time and erodes trust. **Always verify test functions exist after
   writing them.**

2. **Arrow function parsing was fundamentally broken.** The `evaluate()` method
   used a naive `split("=>")` that destroyed method chains like
   `tags.map(t => "#" + t).join(", ")`. The fix was a proper regex tokenizer
   that handles string literals, regex literals, `if(`, and `var =>` as
   separate groups.

3. **Depth counter counted all AST nodes, not `if()` nesting.** The spec said
   "10 levels of nested `if()`" but the implementation incremented on every
   `_visit` call. The fix moved the counter inside the `_if_` call handler only.

**Takeaway**: After implementing, read the test file back and count the test
functions. If tasks.md has N test tasks, the file must have N corresponding
test methods. Run the tests and confirm they pass. Do not check off tasks
based on intent — check them off based on observed output.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
[specs/003-bases-summaries/plan.md](specs/003-bases-summaries/plan.md)
<!-- SPECKIT END -->
