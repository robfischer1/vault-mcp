# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Internal client for the Obsidian CLI (obsidian-cli).

Wraps subprocess.run to communicate with a running Obsidian instance via IPC.
Provides uniform error envelopes and allowlisted command execution — allowlisted
in BOTH directions: which commands may run, and which parameters may reach the
argument vector.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

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

#: The parameter names each allowlisted command accepts.
#:
#: THE COMMAND ALLOWLIST WAS ONLY HALF THE DOOR. `obsidian_cli_command` is an
#: MCP verb whose `params` dict comes straight from the caller, and every key
#: went into the argument vector unexamined: `{"id": True}` appended the BARE
#: token `id`, which means `{"--config": True}` appended `--config` — a flag on
#: obsidian-cli's own command line, chosen by whoever called the verb. The
#: command name was checked; the thing that turns into an option was not.
#:
#: FAIL-CLOSED, and the empty entries are deliberate rather than lazy. Two
#: commands have a parameter surface this repo actually drives and can therefore
#: vouch for — `plugin:reload` takes `id`
#: (verbs_query.obsidian_cli_reload_plugin) and `eval` takes `code`
#: (obsidian_cli_eval, and the ObsidianNoteIO write path). For the other
#: six, guessing a surface would either invent parameters that do not
#: exist or bless ones nobody has read; refusing is the honest default, and the
#: refusal envelope names the parameter and this table, so the first caller who
#: needs one learns exactly where to add it.
CLI_PARAM_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "plugin:reload": frozenset({"id"}),
    "eval": frozenset({"code"}),
    "devtools": frozenset(),
    "dev:errors": frozenset(),
    "dev:screenshot": frozenset(),
    "daily": frozenset(),
    "templates": frozenset(),
    "bookmarks": frozenset(),
}

#: A parameter name is a lowercase word. THE LEADING CHARACTER IS THE WHOLE
#: POINT: a name that cannot begin with `-` cannot be read as an option, which
#: is the injection this closes independently of any allowlist. The rest of the
#: class keeps a name from being empty, from carrying whitespace, or from being
#: a path fragment like `..`.
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class ParamRejectedError(Exception):
    """A CLI parameter failed validation before reaching the argument vector."""

    def __init__(self, detail: str) -> None:
        """Carry the caller-facing detail for the error envelope."""
        super().__init__(detail)
        self.detail = detail


def render_params(command: str, params: Mapping[str, object]) -> list[str]:
    """Render `params` as validated ``key=value`` argv tokens for `command`.

    A pure function on purpose — the validation is the security boundary, so it
    is testable without a subprocess, a binary, or a running Obsidian.

    Every token is ``key=value``. THE BARE-NAME FORM IS GONE: the old code
    appended just the key when a value was `True`, on the stated theory that it
    was a flag. It was not a flag — obsidian-cli takes `key=value` — so the
    branch produced a stray positional argument on a good day and an injected
    option on a bad one. `True` and `False` now render as `true` / omitted.

    NUL is the only character refused inside a value, and that is a considered
    line rather than a nervous one. Each parameter becomes ONE element of the
    argument vector, so there is no delimiter to break out of: spaces, quotes,
    newlines and semicolons are all just bytes to `execve`. A newline is
    ordinary in the one value that matters here — `eval`'s JavaScript — so
    refusing control characters wholesale would break the feature to buy
    nothing. NUL is different: it terminates a C string, so it truncates the
    argument silently (CPython raises deep inside `subprocess` instead, which
    is a stack trace where an envelope belongs).
    """
    allowed = CLI_PARAM_ALLOWLIST.get(command, frozenset())
    tokens: list[str] = []
    for key, value in params.items():
        if not _PARAM_NAME.match(key):
            raise ParamRejectedError(
                f"Parameter name {key!r} is not a lowercase word "
                "([a-z][a-z0-9_-]*); a name that could be read as an option "
                "never reaches the command line."
            )
        if key not in allowed:
            raise ParamRejectedError(
                f"Command {command!r} accepts no parameter {key!r}. "
                f"Allowed: {sorted(allowed) or 'none'} "
                "(vault_mcp.cli_client.CLI_PARAM_ALLOWLIST)."
            )
        if value is False:
            continue
        if value is True:
            rendered = "true"
        elif isinstance(value, str):
            rendered = value
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            raise ParamRejectedError(
                f"Parameter {key!r} must be a string, number or boolean, "
                f"not {type(value).__name__}."
            )
        if "\x00" in rendered:
            raise ParamRejectedError(
                f"Parameter {key!r} contains a NUL byte, which would silently "
                "truncate the argument."
            )
        tokens.append(f"{key}={rendered}")
    return tokens


class ObsidianCLI:
    """Wrapper for the 'obsidian' binary."""

    def __init__(self, binary_path: str | None = None):
        """Resolve the obsidian binary: explicit arg, then VAULT_MCP_OBSIDIAN_BIN, then PATH."""
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
                check=False,
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
        except (OSError, subprocess.SubprocessError) as exc:
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

        try:
            args = [self._binary, command, *render_params(command, params)]
        except ParamRejectedError as exc:
            return {
                "ok": False,
                "error": "cli_invalid_param",
                "detail": exc.detail,
            }

        try:
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "cli_timeout",
                "detail": "Command timed out after 15 seconds.",
            }
        except (OSError, subprocess.SubprocessError) as exc:
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


class CLIRunner(Protocol):
    """The one CLI method `ObsidianNoteIO` actually depends on.

    Narrowed from the concrete `ObsidianCLI` so the seam can be doubled. It
    could not be before: `ObsidianNoteIO.__init__` named the concrete class,
    so a test double was an arg-type error against it and the only reason the
    suite stayed green was that mypy's `check_untyped_defs` was off for tests
    and never looked inside the bodies making those calls.
    """

    def run(self, command: str, **params: Any) -> dict[str, Any]:
        """Execute a CLI command and return the result envelope."""
        ...


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

    def __init__(self, cli: CLIRunner) -> None:
        """Wrap a CLI runner for note read/write via eval."""
        self._cli = cli

    def create_note(self, path: str, content: str) -> None:
        """Create a note at `path` with `content` via the CLI."""
        self._eval_write(build_create_js(path, content), path)

    def write_note(self, path: str, content: str) -> None:
        """Overwrite the note at `path` with `content` via the CLI."""
        self._eval_write(build_modify_js(path, content), path)

    def read_note(self, path: str) -> str:
        """Read and return the note body at `path` via the CLI."""
        res = self._eval(build_read_js(path), path)
        data = _eval_value(res.get("data"))
        if not isinstance(data, str):
            raise ObsidianIOError(
                f"unexpected read result for {path}: {data!r}"
            )
        return data

    def delete_note(self, path: str) -> None:
        """Delete the note at `path` via the CLI."""
        self._eval_write(build_delete_js(path), path)

    def list_notes(
        self, directory: str = "", *, recursive: bool = True
    ) -> list[str]:
        """List markdown note paths under `directory` (recursive by default)."""
        res = self._eval(
            build_list_js(directory.strip("/")), directory or "<root>"
        )
        data = _eval_value(res.get("data"))
        paths = (
            [p for p in data if isinstance(p, str)]
            if isinstance(data, list)
            else []
        )
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
            raise ObsidianIOError(
                res.get("detail") or f"eval failed for {path}"
            )
        return res
