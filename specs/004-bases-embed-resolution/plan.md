# Implementation Plan: Bases Embed Resolution

**Branch**: `004-featurename-bases-embed-resolution` | **Date**: 2026-05-16 | **Spec**: [specs/004-bases-embed-resolution/spec.md](spec.md)

**Input**: Feature specification from `/specs/004-bases-embed-resolution/spec.md`

## Summary
Add support for resolving `![[some.base#View]]` and `![[some.base]]` embeds in Markdown notes. When `read_note` is called, any detected base embeds will be executed, and their structured results inlined in the response. This includes support for standalone `.base` files and inline `bases` code blocks in notes.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastMCP, PyYAML

**Storage**: Read-only vault access (Filesystem)

**Testing**: pytest

**Target Platform**: Any (FastMCP server)

**Project Type**: Library / FastMCP server

**Performance Goals**: Resolution overhead < 100ms per embed.

**Constraints**: First-level resolution only (no recursion).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1.  **Sibling, Not Subordinate**: Use relative paths from `VaultIndex`. Do not assume vault structure. (PASS)
2.  **REST API Augments**: Embed resolution must work without Obsidian running (Parser/Index layer). (PASS)
3.  **Test-First, Single-File Fixtures**: Create individual fixtures for standalone and inline base embeds. (PASS)
4.  **Publish-Ready Hygiene**: Use synthetic data in fixtures. (PASS)
5.  **Strict Quality Tooling**: Maintain `mypy --strict` and `ruff` compliance. (PASS)

## Project Structure

### Documentation (this feature)

```text
specs/004-bases-embed-resolution/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
src/
└── vault_mcp/
    ├── bases.py         # Update parse_file to handle .base extension
    ├── index.py         # Update read_note to resolve embeds
    └── parsers.py       # (Optional) Add regex for embed detection

tests/
├── fixtures/
│   └── bases/           # Add standalone .base and embed test notes
└── test_bases.py        # Add tests for embed resolution
```

**Structure Decision**: Single project. Modifications concentrated in `bases.py` and `index.py`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*No violations identified.*
