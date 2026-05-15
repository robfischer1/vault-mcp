# vault-mcp Constitution

vault-mcp is a FastMCP server that exposes an Obsidian-compatible vault as
read-only (and selectively write-allowlisted) query tools over MCP. This
constitution governs how the project is specified, planned, and built. It is
**self-contained**: it does not import from any external governance system. That
isolation is deliberate — vault-mcp is publish-track (MIT) and must stand on its
own when consumed outside its current workspace.

## Core Principles

### I. Sibling, Not Subordinate

vault-mcp is a standalone Python package. The Obsidian vault it most often
consumes is *a* consumer, not its parent. No vault-specific paths, identifiers,
or assumptions may be hardcoded in `src/vault_mcp/`. Fixtures and tests must
work against any Obsidian-compatible directory, not a specific personal vault.
Publishing readiness is checked continuously, not retrofitted at release.

### II. REST API Augments, Never Foundations

The parser layer (Phases 1–5) must work without an Obsidian instance running.
The REST-backed tools added in Phase 6 — `active_note`, `obsidian_search`,
allowlisted `execute_command` — are *additive* capabilities. When the Local
REST API at `127.0.0.1:27123` is unreachable (e.g., from a sandbox or a host
where Obsidian isn't running), REST-backed tools must degrade gracefully with a
clear error, not crash, and the rest of the server must continue functioning.

### III. Test-First, Single-File Fixtures (NON-NEGOTIABLE)

Pure parsers and index logic get unit tests with single-file fixtures —
exactly one fixture file per test where possible. No multi-file or multi-DB
test setups for parser logic. REST-backed tools require integration tests
against a recorded fixture or mock — **never** a live Obsidian instance.
Tests precede implementation: red → green → refactor. CI must be green on
every commit to `main`.

### IV. Publish-Ready Hygiene

MIT-licensed. Every fixture must be either originally authored for this repo
or generic — no real PII, no personal vault notes, no identifying paths. Docs
use synthetic examples. Before any release tag: run the publish-readiness
checks inherited from the personal-history-db pattern (PII audit, docs walk,
import-graph dup detection, cross-platform smoke). Public surface area changes
require an entry in `DECISIONS.md`.

### V. Strict Quality Tooling, No Warnings

Type-checked with `mypy --strict`; lint-clean under `ruff`; tests green under
`pytest`. CI fails on any warning, not just errors. No `# type: ignore`
without a one-line comment justifying it. No `noqa` without a reason.
Dependency management is `uv` only — never global `pip`, always per-project
`.venv/`.

## Technical Constraints

- **Python 3.11+** (typing.Self, exception groups, asyncio-task-groups available).
- **FastMCP** framework (not raw stdio MCP); the server stays inside the
  FastMCP idioms rather than dropping to low-level transport.
- **HTTP client centralized** in `src/vault_mcp/rest_client.py`; no scattered
  `requests`/`httpx` calls across modules.
- **Module count discipline** — current shape is 4 modules
  (`index`, `parsers`, `rest_client`, `watcher`) + `server.py`. Splitting or
  adding modules requires explicit justification in a plan.
- **Filesystem watcher** invalidates the TTL-cached index incrementally; the
  index is never re-scanned in full on a single-file change.

## Development Workflow

Substantive feature work flows through spec-kit:

```
/speckit-constitution   (only when amending the constitution itself)
/speckit-specify        → spec.md in specs/<NNN-feature>/
/speckit-clarify        → de-risk ambiguities before planning
/speckit-plan           → plan.md, research.md, data-model.md, contracts/, quickstart.md
/speckit-tasks          → tasks.md with [P] parallel markers
/speckit-analyze        → cross-artifact consistency check
/speckit-implement      → executes tasks.md in dependency order
```

Quick fixes, typo corrections, single-file refactors, dependency bumps, and
documentation-only changes skip the full ceremony. The threshold is roughly:
*if it would take more than 30 minutes to write or touches more than 3 files,
flow through spec-kit*. A TinySpec preset may be adopted later for a middle tier.

Before merging any feature work:
- `uv run pytest` green
- `uv run ruff check src/ tests/` clean
- `uv run mypy src/` strict-clean
- Constitution-compliance verified by `/speckit-analyze`

## Governance

This constitution supersedes other practices in this repo. Amendments require:
1. A `/speckit-constitution` invocation that updates this file.
2. A corresponding entry in `DECISIONS.md` (to be created when the first
   amendment lands) recording the why.
3. A version bump per semver: PATCH for typo/clarification, MINOR for
   non-breaking principle addition, MAJOR for principle removal or
   incompatible change.

**Pilot Status (2026-05-15):** vault-mcp is the spec-kit pilot repo for this
workspace. The decision to keep this constitution self-contained — rather than
importing the parent workspace's AGENTS.md governance — is intentional, so the
spec-kit methodology can be evaluated on its own ergonomics. If the pilot
succeeds and spec-kit becomes the default for new substantive projects, future
repos may follow the same self-contained pattern.

**Version**: 1.0.0 | **Ratified**: 2026-05-15 | **Last Amended**: 2026-05-15
