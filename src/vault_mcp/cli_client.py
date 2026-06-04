"""Internal client for the Obsidian CLI (obsidian-cli).

Wraps subprocess.run to communicate with a running Obsidian instance via IPC.
Provides uniform error envelopes and whitelisted command execution.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

CLI_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {
        "plugin:reload",
        "eval",
        "devtools",
        "dev:errors",
        "dev:screenshot",
        "daily",
        "templates",
        "bookmarks",
    }
)


class ObsidianCLI:
    """Wrapper for the 'obsidian' binary."""

    def __init__(self, binary_path: str | None = None):
        # Explicit arg wins; else VAULT_MCP_OBSIDIAN_BIN (so a service that does
        # not inherit the user PATH can be pointed at the binary directly); else
        # discover on PATH.
        self._binary = (
            binary_path
            or os.environ.get("VAULT_MCP_OBSIDIAN_BIN")
            or shutil.which("obsidian")
        )
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
                "detail": "The 'obsidian' binary was not found. Set VAULT_MCP_OBSIDIAN_BIN "
                          "to its full path, or put it on the service PATH.",
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


class ObsidianIOError(Exception):
    """A vault write/read through the Obsidian CLI failed."""


# Returned by write eval so the adapter can confirm the write actually ran.
# Guards against a binary (e.g. the Obsidian GUI launcher) that exits 0
# without evaluating the JS — which would otherwise look like a silent success.
WRITE_OK_SENTINEL = "__vault_mcp_write_ok__"


# obsidian-cli's `eval` evaluates code as a plain script and returns the value
# of the LAST EXPRESSION, prefixed with "=> " on stdout. Top-level `return` and
# `await` are both illegal, but the harness awaits a returned promise — so each
# builder is a single async-IIFE expression (no leading `return`), and the
# adapter strips the "=> " prefix off the result.
_EVAL_PREFIX = "=> "


def build_create_js(path: str, content: str) -> str:
    """Build eval JS that creates a new note. Args are JSON-encoded (JS-safe)."""
    return (
        f"(async () => {{ "
        f"await app.vault.create({json.dumps(path)}, {json.dumps(content)}); "
        f"return {json.dumps(WRITE_OK_SENTINEL)}; }})()"
    )


def build_modify_js(path: str, content: str) -> str:
    """Build eval JS that overwrites an existing note."""
    return (
        f"(async () => {{ "
        f"const f = app.vault.getAbstractFileByPath({json.dumps(path)}); "
        f"await app.vault.modify(f, {json.dumps(content)}); "
        f"return {json.dumps(WRITE_OK_SENTINEL)}; }})()"
    )


def build_read_js(path: str) -> str:
    """Build eval JS that returns an existing note's content."""
    return (
        f"(async () => {{ "
        f"const f = app.vault.getAbstractFileByPath({json.dumps(path)}); "
        f"return await app.vault.read(f); }})()"
    )


def build_list_js(directory: str) -> str:
    """Build eval JS returning markdown note paths under ``directory`` (recursive)."""
    return (
        f"(async () => {{ "
        f"const pre = {json.dumps(directory)}; "
        f"return app.vault.getMarkdownFiles().map(f => f.path)"
        f".filter(p => !p.startsWith('.trash') && "
        f"(pre === '' || p === pre || p.startsWith(pre + '/'))); }})()"
    )


def build_delete_js(path: str) -> str:
    """Build eval JS that moves a note to Obsidian's local ``.trash/`` folder.

    ``app.vault.trash(f, false)`` uses Obsidian's own ``.trash/`` (the ``false``
    selects the vault-local trash over the OS trash), so a mistaken delete is
    recoverable from inside Obsidian.
    """
    return (
        f"(async () => {{ "
        f"const f = app.vault.getAbstractFileByPath({json.dumps(path)}); "
        f"await app.vault.trash(f, false); "
        f"return {json.dumps(WRITE_OK_SENTINEL)}; }})()"
    )


def _eval_value(data: object) -> object:
    """Strip obsidian-cli's '=> ' result prefix from eval stdout."""
    if isinstance(data, str) and data.startswith(_EVAL_PREFIX):
        return data[len(_EVAL_PREFIX) :]
    return data


class ObsidianNoteIO:
    """Implements the Convention Gate's NoteIO protocol over the Obsidian CLI.

    Writes route through the ``eval`` command so Obsidian's own indexing and
    plugins fire (the v2 write-path decision). Per Constitution III this is
    exercised against a mock, never a live instance; the JS builders above are
    unit-tested independently of the subprocess call.
    """

    def __init__(self, cli: ObsidianCLI) -> None:
        self._cli = cli

    def create_note(self, path: str, content: str) -> None:
        self._eval_write(build_create_js(path, content), path)

    def write_note(self, path: str, content: str) -> None:
        self._eval_write(build_modify_js(path, content), path)

    def read_note(self, path: str) -> str:
        res = self._eval(build_read_js(path), path)
        data = _eval_value(res.get("data"))
        if not isinstance(data, str):
            raise ObsidianIOError(f"unexpected read result for {path}: {data!r}")
        return data

    def delete_note(self, path: str) -> None:
        self._eval_write(build_delete_js(path), path)

    def list_notes(self, directory: str = "", *, recursive: bool = True) -> list[str]:
        res = self._eval(build_list_js(directory.strip("/")), directory or "<root>")
        data = _eval_value(res.get("data"))
        paths = [p for p in data if isinstance(p, str)] if isinstance(data, list) else []
        if recursive:
            return paths
        # immediate children only: no '/' beyond the directory prefix
        pre = directory.strip("/")
        depth = pre.count("/") + 1 if pre else 0
        return [p for p in paths if p.count("/") == depth]

    def _eval_write(self, code: str, path: str) -> None:
        res = self._eval(code, path)
        value = _eval_value(res.get("data"))
        if value != WRITE_OK_SENTINEL:
            raise ObsidianIOError(
                f"write to {path} not confirmed (got {value!r}); "
                f"is obsidian-cli connected and the path's parent folder present?"
            )

    def _eval(self, code: str, path: str) -> dict[str, Any]:
        res = self._cli.run("eval", code=code)
        if not res.get("ok"):
            raise ObsidianIOError(res.get("detail") or f"eval failed for {path}")
        return res
