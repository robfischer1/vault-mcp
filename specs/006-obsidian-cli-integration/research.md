# Research: Obsidian CLI Surface Survey

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

## Overview

The `obsidian-cli` (npm package `obsidian-cli`) provides a command-line interface to a running Obsidian instance. It communicates via IPC (Inter-Process Communication).

## High-Value Commands for `vault-mcp`

| Command | Action | Value |
| :--- | :--- | :--- |
| `plugin:reload id=<id>` | Reloads a specific plugin | Essential for plugin development workflows. |
| `eval code=<code>` | Runs JS in Obsidian | Absolute flexibility; allows agents to drive any Obsidian API. |
| `devtools` | Opens Chrome DevTools | Debugging UI for developers. |
| `dev:errors` | Lists recent console errors | Quick diagnostic for failed operations. |
| `dev:screenshot` | Takes a screenshot | visual debugging/verification for agents. |
| `search query=<q>` | Full-text search | Returns JSON with scores; better than raw FS search for some cases. |
| `daily` | Opens daily note | Quick navigation. |

## CLI Syntax Patterns

Commands use `key=value` for parameters.

Example: `obsidian plugin:reload id="vault-mcp"`

## Security Considerations

- `eval` is extremely powerful and potentially dangerous if passed malicious code. However, `vault-mcp` is a local tool used by the vault owner. We should still treat it as a "privileged" tool.
- Whitelisting ensures we only call `obsidian <cmd>` and not `rm -rf /`.

## Detection Strategy

- Check `shutil.which("obsidian")`.
- If found, run `obsidian --version` (or similar) to confirm it's the right tool and not another binary named `obsidian`.

## Prototype Invocation (Python)

```python
import subprocess
import shutil


def run_obsidian_cli(command, **kwargs):
    binary = shutil.which("obsidian")
    if not binary:
        return {"ok": False, "error": "cli_not_found"}

    args = [binary, command]
    for k, v in kwargs.items():
        args.append(f"{k}={v}")

    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode != 0:
        return {"ok": False, "error": "cli_error", "detail": res.stderr}

    return {"ok": True, "data": res.stdout}
```
