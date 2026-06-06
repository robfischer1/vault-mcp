Add a layer of vault-mcp tools that wrap Obsidian CLI invocations, complementing the existing REST-augmentation surface. The Obsidian CLI provides a command-line interface for interacting with a running Obsidian instance -- alternative to the REST API for some operations, and the primary interface for plugin/theme development workflows. This brief surfaces a curated subset of CLI capabilities through vault-mcp.

The user value is that agents can drive Obsidian operations that the REST API doesn't expose (plugin install/dev workflows, theme management, vault-level commands), and have an alternative transport on hosts where REST is unavailable but the CLI is.

### Initial scope

- Survey the CLI's command surface and select a small whitelist of high-value commands to wrap (exact list determined during specification and refined in clarification).
- Detect CLI availability at vault-mcp startup; degrade gracefully when not installed.
- Quarantined invocation -- whitelist-only, no arbitrary command execution, matching the REST-side `execute_command` allowlist pattern.

### Out of scope

- Plugin development workflow automation beyond surfacing relevant CLI commands.
- Theme editing UI.
- Vault creation or destruction (mutation surface; defer to a separate brief if needed).

### Success criteria

- An agent can detect CLI availability via vault-mcp.
- An agent can invoke at least 3 useful CLI commands through vault-mcp (specific commands TBD via the CLI capability survey).
- CLI-derived information is consistent with REST-derived information for any overlapping queries (e.g., active note).

### Context

This was the original feature suggestion from the 2026-05-15 Cowork session that lost the priority slot to Bases. Now that Bases shipped, this is the natural next standalone feature track. The vault-mcp constitution's "REST API Augments, Never Foundations" principle extends naturally: CLI also augments, never replaces filesystem parsers as the foundation. The survey step is important -- if CLI and REST overlap 80%, the brief should narrow to the unique CLI surface (plugin/theme dev).
