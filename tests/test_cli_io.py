"""Unit tests for the Obsidian-CLI NoteIO adapter.

The eval JS builders are pure and unit-tested here; the subprocess call is
exercised only through a fake CLI (Constitution III: never a live instance).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.cli_client import (  # noqa: E402
    WRITE_OK_SENTINEL,
    ObsidianIOError,
    ObsidianNoteIO,
    build_create_js,
    build_modify_js,
    build_read_js,
)


class TestJsBuilders:
    def test_create_js_json_encodes_args(self):
        js = build_create_js('Notes/My "Note".md', "line1\nline2")
        assert "app.vault.create(" in js
        # JSON-encoding makes quotes/newlines JS-safe
        assert json.dumps('Notes/My "Note".md') in js
        assert json.dumps("line1\nline2") in js

    def test_modify_js_targets_existing_file(self):
        js = build_modify_js("a.md", "x")
        assert "getAbstractFileByPath" in js
        assert "app.vault.modify(" in js

    def test_read_js_returns_content(self):
        js = build_read_js("a.md")
        assert "app.vault.read(" in js
        assert "return" in js


class FakeCLI:
    """Stand-in for ObsidianCLI.run capturing eval calls."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[str] = []

    def run(self, command: str, **params: Any) -> dict[str, Any]:
        assert command == "eval"
        self.calls.append(params["code"])
        return self.result


class TestObsidianNoteIO:
    def test_create_invokes_eval(self):
        cli = FakeCLI({"ok": True, "data": WRITE_OK_SENTINEL})
        io = ObsidianNoteIO(cli)  # type: ignore[arg-type]
        io.create_note("Notes/x.md", "body")
        assert len(cli.calls) == 1
        assert "app.vault.create(" in cli.calls[0]

    def test_silent_noop_write_raises(self):
        # Exit-0 with no sentinel (e.g. the GUI launcher) must NOT look like success.
        cli = FakeCLI({"ok": True, "data": ""})
        io = ObsidianNoteIO(cli)  # type: ignore[arg-type]
        with pytest.raises(ObsidianIOError) as exc:
            io.create_note("Notes/x.md", "body")
        assert "not confirmed" in str(exc.value)

    def test_read_returns_data(self):
        cli = FakeCLI({"ok": True, "data": "file contents"})
        io = ObsidianNoteIO(cli)  # type: ignore[arg-type]
        assert io.read_note("a.md") == "file contents"

    def test_failed_eval_raises(self):
        cli = FakeCLI({"ok": False, "error": "cli_error", "detail": "boom"})
        io = ObsidianNoteIO(cli)  # type: ignore[arg-type]
        with pytest.raises(ObsidianIOError) as exc:
            io.create_note("a.md", "x")
        assert "boom" in str(exc.value)
