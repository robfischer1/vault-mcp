# Implementation Plan: Obsidian CLI Integration

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

1. **Detection**: Implement `ObsidianCLI.probe()` to check for the `obsidian` binary and its basic responsiveness.
2. **Runner**: Create a robust `subprocess` wrapper that handles:
    - `key=value` parameter encoding.
    - JSON parsing of stdout (if the CLI returns JSON).
    - Error code mapping.
    - Timeouts.

## Phase 2: Core Tools

1. **`obsidian_cli_status`**: Expose the probe results.
2. **`obsidian_cli_reload_plugin(id)`**: Specifically wrapped for ease of use.
3. **`obsidian_cli_eval(code)`**: Wrapped with clear documentation on its power.

## Phase 3: Generic Command Wrapper

1. **Whitelist**: Define the `CLI_COMMAND_ALLOWLIST`.
2. **`obsidian_cli_command(command, params)`**: A generic tool that checks the whitelist and executes.

## Phase 4: Integration & Testing

1. **Mocking**: Create a mock for `subprocess.run` to test CLI tools without needing the binary in CI.
2. **Server Integration**: Register tools in `server.py`, mirroring the `if not REST_DISABLE` pattern (maybe `if CLI_AVAILABLE`).

## Verification Plan

- **Unit Tests**: `tests/test_cli.py` (new file).
    - Mock binary not found -> `cli_not_found`.
    - Mock binary error -> `cli_error`.
    - Mock success -> `ok: True, data: ...`.
- **Manual Verification**:
    - Run `obsidian_cli_status` on a machine with/without the CLI.
    - Run `obsidian_cli_reload_plugin` and observe Obsidian's behavior (requires local setup).
