# Tasks: Obsidian CLI Integration

> **⚑ THIS ENTIRE FEATURE IS RETIRED — 2026-09-10.** `vault_mcp/cli_client.py`,
> `ObsidianNoteIO` and all four `obsidian_cli_*` verbs are deleted. Kept as the
> dated design record of what was built and why it was withdrawn; nothing below
> describes shipping behaviour.
>
> **Why.** obsidian-cli reaches Obsidian over SAME-SESSION IPC. vault-mcp runs
> as a session-0 service and the desktop Obsidian is session-1, so the bridge
> could not work in production — `server._get_gate()` recorded exactly that, and
> the Convention Gate was built unconditionally on `RestNoteIO` with no
> fallback. `ObsidianNoteIO` was never constructed anywhere in `src/`; the only
> four constructions in the repo were in its own test file. `obsidian_cli_eval`
> had three invocations across the entire transcript corpus (two sessions) and
> the other three verbs had none.
>
> The write path is unaffected: it was always REST (`PUT/GET /vault/{path}`), so
> Obsidian's indexing and plugins still fire. `ObsidianIOError` survives in
> `gate.py` as the `NoteIO` protocol's error.

## Phase 1: Infrastructure

- [x] Create `src/vault_mcp/cli_client.py` with `ObsidianCLI` class.
- [x] Implement `ObsidianCLI.probe()` using `shutil.which`.
- [x] Implement `ObsidianCLI.run(command, **params)` with `subprocess.run`.
- [x] Add error mapping for `cli_not_found`, `cli_error`.

## Phase 2: Unit Testing (Mocked)

- [x] Create `tests/test_cli.py`.
- [x] **Test**: `test_cli_probe_success` (mocks `shutil.which` and `subprocess.run`).
- [x] **Test**: `test_cli_probe_failure` (mocks binary missing).
- [x] **Test**: `test_cli_run_success_json` (verifies JSON parsing of stdout).
- [x] **Test**: `test_cli_run_success_text` (verifies raw text return).
- [x] **Test**: `test_cli_run_error` (verifies error envelope on non-zero exit).

## Phase 3: MCP Tools

- [x] Register `obsidian_cli_status` in `server.py`.
- [x] Register `obsidian_cli_reload_plugin` in `server.py`.
- [x] Register `obsidian_cli_eval` in `server.py`.
- [x] Register `obsidian_cli_command` with whitelist in `server.py`.

## Phase 4: Verification

- [x] Run `uv run pytest tests/test_cli.py` and confirm all 5 tests pass.
- [x] Run `uv run ruff check src/ tests/`.
- [x] Run `uv run mypy src/`.
