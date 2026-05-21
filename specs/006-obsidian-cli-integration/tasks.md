# Tasks: Obsidian CLI Integration

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
