"""Internal client for the Obsidian CLI (obsidian-cli).

Wraps subprocess.run to communicate with a running Obsidian instance via IPC.
Provides uniform error envelopes and whitelisted command execution.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

CLI_COMMAND_ALLOWLIST: frozenset[str] = frozenset({
    "plugin:reload",
    "eval",
    "devtools",
    "dev:errors",
    "dev:screenshot",
    "daily",
    "templates",
    "bookmarks",
})


class ObsidianCLI:
    """Wrapper for the 'obsidian' binary."""

    def __init__(self, binary_path: str | None = None):
        self._binary = binary_path or shutil.which("obsidian")
        self._available: bool | None = None
        self._version: str | None = None

    def probe(self) -> dict[str, Any]:
        """Check if the CLI is available and responsive."""
        if not self._binary:
            self._available = False
            return {
                "available": False,
                "version": None,
                "error": "cli_not_found",
                "detail": "The 'obsidian' binary was not found in the system PATH.",
            }

        try:
            # We use 'obsidian --version' or similar to probe.
            # obsidian-cli --version returns the version string.
            res = subprocess.run(
                [self._binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res.returncode == 0:
                self._available = True
                self._version = res.stdout.strip()
                return {
                    "available": True,
                    "version": self._version,
                    "error": None,
                }

            # Some versions might not support --version or return non-zero
            self._available = True
            return {
                "available": True,
                "version": "unknown",
                "error": None,
            }
        except Exception as exc:
            self._available = False
            return {
                "available": False,
                "version": None,
                "error": "cli_error",
                "detail": str(exc),
            }

    def run(self, command: str, **params: Any) -> dict[str, Any]:
        """Execute an Obsidian CLI command.

        Args:
            command: The command name (e.g., 'plugin:reload').
            **params: Key-value parameters passed as key=value.

        Returns:
            {"ok": bool, "data": Any, "error": str|None, "detail": str|None}
        """
        if command not in CLI_COMMAND_ALLOWLIST:
            return {
                "ok": False,
                "error": "cli_invalid_command",
                "detail": f"Command '{command}' not in allowlist: {sorted(CLI_COMMAND_ALLOWLIST)}",
            }

        if self._available is False or not self._binary:
            return {
                "ok": False,
                "error": "cli_not_found",
                "detail": "CLI binary not available.",
            }

        args = [self._binary, command]
        for k, v in params.items():
            # Boolean flags are handled specially if needed, but obsidian-cli
            # uses key=value for most things.
            if v is True:
                args.append(k)
            elif v is False:
                continue
            else:
                args.append(f"{k}={v}")

        try:
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "cli_timeout",
                "detail": "Command timed out after 15 seconds.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": "cli_error",
                "detail": str(exc),
            }

        if res.returncode != 0:
            return {
                "ok": False,
                "error": "cli_error",
                "detail": res.stderr.strip()
                or res.stdout.strip()
                or f"Exit code {res.returncode}",
            }

        stdout = res.stdout.strip()

        # Try to parse JSON if output looks like it
        if stdout.startswith(("{", "[")):
            try:
                data = json.loads(stdout)
                return {"ok": True, "data": data}
            except json.JSONDecodeError:
                pass

        return {"ok": True, "data": stdout}
