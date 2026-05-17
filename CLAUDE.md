# CLAUDE.md — vault-mcp

Per-project guidance for Claude Code in the vault-mcp repository.

This project uses **[spec-kit](https://github.com/github/spec-kit)** for
spec-driven development. The constitution is authoritative — read it before
substantive work.

@.specify/memory/constitution.md

## Build & Development

```bash
uv venv && uv pip install -e ".[dev]"

uv run pytest                     # all tests
uv run pytest tests/test_parsers.py
uv run pytest -k "test_index"
uv run pytest -xvs

uv run ruff check src/ tests/     # lint
uv run ruff format src/ tests/    # auto-format
uv run mypy src/                  # strict type check

uv run python server.py           # run the MCP server
```

Always invoke via `uv run` — never activate the venv manually, never use global pip.

## Workflow (spec-kit)

For substantive feature work — anything that would take more than ~30 minutes
or touches more than 3 files:

```
/speckit-specify    → spec.md (the what/why, no tech stack)
/speckit-clarify    → de-risk ambiguous areas (recommended)
/speckit-plan       → plan.md + research.md + data-model.md + contracts/
/speckit-tasks      → tasks.md with [P] parallel markers
/speckit-analyze    → cross-artifact consistency (optional)
/speckit-implement  → execute tasks.md
```

Quick fixes, typo corrections, doc-only changes, and dependency bumps skip
the ceremony and just go straight to a commit.

## Constitution non-negotiables

These five are pinned by the constitution; do not work around them:

1. **No vault-specific paths** in `src/vault_mcp/`. The vault is a consumer,
   not a parent. Fixtures must be generic.
2. **REST API is optional augmentation.** Phase 1–5 parsers must work without
   Obsidian running. REST-backed tools (Phase 6) degrade gracefully when
   `127.0.0.1:27123` is unreachable.
3. **Test-first, single-file fixtures.** One fixture per parser test where
   possible. REST tools test against recorded fixtures or mocks, never a live
   Obsidian instance.
4. **Publish-ready hygiene.** MIT-licensed; no real PII; no personal-vault
   leakage into tests or docs.
5. **Strict tooling, no warnings.** `mypy --strict`, `ruff` clean, `pytest`
   green. No `# type: ignore` without a justifying comment.

## Pilot context

vault-mcp is the **spec-kit pilot** for Rob's workspace (declared 2026-05-15).
The constitution is intentionally self-contained — it does **not** import
vault-side governance — so spec-kit can be evaluated on its own merits. If
this pilot proves out, future substantive programming projects in this
workspace will adopt spec-kit by default.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
