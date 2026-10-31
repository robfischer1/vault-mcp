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
    schema = load_schema(str(SCHEMAS / "inbox.schema.yml"))
    monkeypatch.setattr(server, "_gate", ConventionGate(schema, DiskVault(tmp_path)))
    monkeypatch.setattr(server, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(server, "_committer", GitCommitter(tmp_path, enabled=True))
    return tmp_path


class TestWriteNoteCommitSha:
    def test_sha_is_the_real_head_commit(self, vault: Path) -> None:
        result = verbs_write.write_note(title="Beta", note_type="idea", pillar="Journal", body="b")
        assert result["ok"], result
        sha = result["commit_sha"]
        assert sha == _rev_parse_head(vault)
        assert _show_head(vault).startswith(sha)
        assert "commit_skipped" not in result

    def test_fabricated_sha_is_never_reported(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        committer = server._get_committer()
        bogus = "d0e334897056e0009400aaf46fe50d20fdee8569"
        monkeypatch.setattr(committer, "commit_paths", lambda *_a, **_k: bogus)
        result = verbs_write.write_note(title="Gamma", note_type="idea", pillar="Journal", body="b")
        assert result["ok"], result
        assert result["commit_sha"] is None
        assert result["commit_skipped"] == "unverified_sha"

    def test_no_sha_carries_a_reason_when_disabled(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server, "_committer", GitCommitter(vault, enabled=False))
        result = verbs_write.write_note(title="Delta", note_type="idea", pillar="Journal", body="b")
        assert result["ok"], result
        assert result["commit_sha"] is None
        assert result["commit_skipped"] == "disabled"

    def test_no_sha_carries_a_reason_when_nothing_was_committed(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server._get_committer(), "commit_paths", lambda *_a, **_k: None)
        result = verbs_write.write_note(title="Eps", note_type="idea", pillar="Journal", body="b")
        assert result["ok"], result
        assert result["commit_sha"] is None
        assert result["commit_skipped"] == "nothing_committed"


class TestInboxIsNotSilentlyMisfiled:
    def test_pillar_without_a_route_is_refused_and_nothing_is_written(self, vault: Path) -> None:
        before = _rev_parse_head(vault)
        result = verbs_write.write_note(
            title="Inventory", note_type="ItemList", pillar="Inbox", body="b"
        )
        assert result["ok"] is False, result
        assert result["error"] == "no_route"
        assert "Inbox" in result["detail"]
        assert not (vault / "Outputs").exists()
        assert _rev_parse_head(vault) == before

    def test_type_only_route_still_serves_a_write_without_a_pillar(self, vault: Path) -> None:
        result = verbs_write.write_note(title="Inventory", note_type="ItemList", body="b")
        assert result["ok"], result
        assert result["path"].startswith("Outputs/Self/")
