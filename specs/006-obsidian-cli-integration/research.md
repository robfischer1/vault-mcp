# Research: Obsidian CLI Surface Survey

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
