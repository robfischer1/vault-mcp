# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""The server's commit path against a real temp git vault (synthetic content only).

Oracle is real ``git`` reading what the dulwich committer wrote. The vault IO is
a disk-backed double so a move actually renames files on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vault_mcp import server, verbs_write
from vault_mcp.gate import ConventionGate, ObsidianIOError
from vault_mcp.gitops import GitCommitter
from vault_mcp.schema import load_schema

SCHEMAS = Path(__file__).parent / "fixtures" / "schema"

NOTE = "---\ntitle: Alpha\n---\nbody\n"


class DiskVault:
    """NoteIO over a real directory; delete moves to .trash like RestNoteIO."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def create_note(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    write_note = create_note

    def read_note(self, path: str) -> str:
        target = self.root / path
        if not target.is_file():
            raise ObsidianIOError(f"REST read {path}: not_found: 404")
        return target.read_text(encoding="utf-8")

    def delete_note(self, path: str) -> None:
        target = self.root / path
        if not target.is_file():
            raise ObsidianIOError(f"REST read {path}: not_found: 404")
        trash = self.root / ".trash" / path
        trash.parent.mkdir(parents=True, exist_ok=True)
        target.replace(trash)

    def list_notes(self, directory: str = "", *, recursive: bool = True) -> list[str]:
        return []


def _show_head(repo: Path) -> str:
    return subprocess.run(
        ["/usr/bin/git", "show", "--name-status", "--format=%H", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _rev_parse_head(repo: Path) -> str:
    return subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(root: Path) -> None:
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["/usr/bin/git", "config", "user.name", "Base"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["/usr/bin/git", "config", "user.email", "base@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / ".gitignore").write_text(".trash/\n", encoding="utf-8")
    (root / "Software").mkdir()
    (root / "Software" / "Alpha.md").write_text(NOTE, encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["/usr/bin/git", "commit", "-q", "-m", "init"], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _init_repo(tmp_path)
    schema = load_schema(str(SCHEMAS / "atom.schema.yml"))
    monkeypatch.setattr(server, "_gate", ConventionGate(schema, DiskVault(tmp_path)))
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(server, "_committer", GitCommitter(tmp_path, enabled=True))
    return tmp_path


class TestMoveNoteCommits:
    def test_move_is_committed_with_both_paths(self, vault: Path) -> None:
        before = _rev_parse_head(vault)
        result = verbs_write.move_note("Software/Alpha.md", "References/Alpha.md")
        assert result["ok"], result
        sha = result["commit_sha"]
        assert sha, "move produced no commit"
        assert sha != before
        assert _rev_parse_head(vault) == sha
        shown = _show_head(vault)
        assert "Software/Alpha.md" in shown
        assert "References/Alpha.md" in shown
        # Git sees one rename (or delete+add); the old path is gone from the tree.
        tracked = subprocess.run(
            ["/usr/bin/git", "ls-files"], cwd=vault, check=True, capture_output=True, text=True
        ).stdout.split()
        assert "References/Alpha.md" in tracked
        assert "Software/Alpha.md" not in tracked
