# Feature Specification: Obsidian CLI Integration

**Feature Branch**: `006-obsidian-cli-integration`

**Created**: 2026-05-21

**Status**: Draft

**Input**: Add a layer of vault-mcp tools that wrap Obsidian CLI invocations, complementing the existing REST-augmentation surface. Surface a curated subset of CLI capabilities (plugin/theme dev, vault-level commands) that the REST API doesn't expose.

## Clarifications

### Session 2026-05-21 (Research)

- **Which CLI?**: The specification targets the `obsidian-cli` (often installed via npm as `obsidian`).
- **Availability**: CLI tools should degrade gracefully if the `obsidian` binary is not in the PATH.
- **Whitelist vs Arbitrary**: Like `execute_command` for REST, CLI invocations will be quarantined to a whitelist to prevent arbitrary shell execution or unsafe Obsidian state changes.
- **Primary Value**: Surfacing developer-centric commands (`plugin:reload`, `eval`) and vault management commands that REST doesn't cover or handles poorly.

## User Scenarios & Testing

### User Story 1 - Reload a Plugin during Development (Priority: P1)

An agent is helping a developer iterate on an Obsidian plugin. After the agent modifies the plugin's source code (e.g., via `replace` on the plugin's `main.js`), it needs to tell Obsidian to reload the plugin so the changes take effect. Currently, the REST API doesn't expose a "reload plugin" command. The agent calls the CLI-backed `reload_plugin` tool.

**Why this priority**: This is a unique capability of the CLI that directly supports the "plugin dev workflow" mentioned in the brief.

**Acceptance Scenarios**:

1. **Given** the `obsidian` CLI is installed and Obsidian is running, **When** an agent calls `obsidian_cli_reload_plugin(id="my-plugin-id")`, **Then** the command `obsidian plugin:reload id="my-plugin-id"` is executed and the result is returned.
2. **Given** the `obsidian` CLI is NOT installed, **When** any CLI tool is called, **Then** a structured error `cli_not_found` is returned.

---

### User Story 2 - Execute Arbitrary Logic via Eval (Priority: P2)

An agent needs to perform an action in Obsidian for which no REST endpoint or specific CLI command exists (e.g., "get the name of the current theme" or "toggle a specific setting"). The agent calls the `obsidian_cli_eval` tool with a snippet of JavaScript.

**Why this priority**: `eval` is the "escape hatch" that provides 100% coverage of the Obsidian API, making it extremely powerful for agents.

**Acceptance Scenarios**:

1. **Given** a valid JS snippet, **When** the agent calls `obsidian_cli_eval`, **Then** the code is executed in Obsidian and the result (JSON) is returned.
2. **Given** an invalid JS snippet, **When** executed, **Then** the CLI error is captured and returned.

---

### User Story 3 - Check CLI Availability (Priority: P3)

Before attempting CLI operations, an agent or the server itself checks if the CLI is available and what version is running.

**Acceptance Scenarios**:

1. **Given** the server starts, **When** it probes the environment, **Then** it records whether `obsidian` is available.
2. **When** an agent calls `obsidian_cli_status`, **Then** it returns `{ "available": true, "version": "..." }`.

## Requirements

### Functional Requirements

- **FR-001**: System MUST detect the presence of the `obsidian` executable in the system PATH.
- **FR-002**: System MUST provide a `obsidian_cli_status` tool to report CLI availability and version.
- **FR-003**: System MUST provide a `obsidian_cli_reload_plugin` tool that wraps `obsidian plugin:reload id=<id>`.
- **FR-004**: System MUST provide a `obsidian_cli_eval` tool that wraps `obsidian eval code=<code>`.
- **FR-005**: System MUST provide a `obsidian_cli_command` tool for a whitelist of other useful CLI commands (e.g., `devtools`, `dev:errors`).
- **FR-006**: CLI tools MUST return a uniform error envelope consistent with the REST client (`{ "ok": False, "error": "cli_error", "detail": "..." }`).
- **FR-007**: System MUST handle "silent" execution where possible (CLI parameters like `silent` or flags) to avoid disruptive GUI focus stealing unless intended.
- **FR-008**: System MUST capture and return both stdout and stderr from CLI invocations for debugging.

### Key Entities

- **CLIRunner**: A new internal class/module responsible for safe execution of the `obsidian` binary.
- **CLICommandWhitelist**: A hardcoded set of allowed CLI commands.

## Success Criteria

- **SC-001**: An agent can successfully reload an Obsidian plugin via MCP.
- **SC-002**: `obsidian_cli_status` accurately reflects the environment (available vs missing).
- **SC-003**: CLI tools degrade gracefully (standard error format) when the binary is missing or Obsidian is not running.
- **SC-004**: Execution of a non-whitelisted command is rejected before calling the shell.

## Assumptions

- The user has `obsidian-cli` (npm) installed if they want to use these tools.
- The `obsidian` command refers to the one from `obsidian-cli`.
- Obsidian must be running for most commands to work (as it uses IPC).

## Out of Scope

- Installing `obsidian-cli` automatically.
- Managing multiple vaults via CLI (defaulting to the active/last-focused vault is sufficient for now).
- Arbitrary shell execution (commands are strictly `obsidian <cmd> ...`).
