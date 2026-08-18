"""Unit tests for vault_mcp.gitops — the single-writer git committer (VG initiative).

Runs real ``git`` against a throwaway temp repo (no network, no live vault).
Covers Commit-on-Write (#226), bot identity (#230), and the sweep (#227).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gitops import (
    DEFAULT_BOT_EMAIL,
    DEFAULT_BOT_NAME,
    GitCommitter,
)


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return cp.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A temp git repo with one initial commit and a base identity."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.name", "Base User")
    _git(tmp_path, "config", "user.email", "base@example.com")
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _committer(repo: Path, **kw: object) -> GitCommitter:
    kw.setdefault("enabled", True)
    return GitCommitter(repo, **kw)


class TestCommitPaths:
    def test_returns_sha_and_single_commit(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "note.md").write_text("hello\n", encoding="utf-8")
        sha = gc.commit_paths(["note.md"], "vault: create note.md")
        assert sha is not None and len(sha) == 40
        assert _git(repo, "rev-list", "--count", "HEAD") == "2"  # init + this
        # the path is in the new commit
        assert "note.md" in _git(
            repo, "show", "--name-only", "--format=", "HEAD"
        )

    def test_sequential_writes_n_commits(self, repo: Path) -> None:
        gc = _committer(repo)
        shas = []
        for i in range(3):
            (repo / f"n{i}.md").write_text(f"body {i}\n", encoding="utf-8")
            shas.append(gc.commit_paths([f"n{i}.md"], f"vault: create n{i}.md"))
        assert all(s is not None for s in shas)
        assert len(set(shas)) == 3  # three distinct commits
        assert _git(repo, "rev-list", "--count", "HEAD") == "4"  # init + 3

    def test_message_used_verbatim(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "outputs: a rich caller-supplied subject")
        assert (
            _git(repo, "log", "-1", "--format=%s")
            == "outputs: a rich caller-supplied subject"
        )

    def test_no_change_returns_none(self, repo: Path) -> None:
        gc = _committer(repo)
        # committing an unmodified, already-tracked file stages nothing
        sha = gc.commit_paths(["README.md"], "vault: noop")
        assert sha is None
        assert _git(repo, "rev-list", "--count", "HEAD") == "1"

    def test_commits_a_deletion(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "doomed.md").write_text("bye\n", encoding="utf-8")
        gc.commit_paths(["doomed.md"], "vault: create doomed.md")
        (repo / "doomed.md").unlink()
        sha = gc.commit_paths(
            ["doomed.md"], "vault: dissolve doomed.md", wait_for_create=False
        )
        assert sha is not None
        assert "doomed.md" not in _git(repo, "ls-files")

    def test_disabled_is_noop(self, repo: Path) -> None:
        gc = _committer(repo, enabled=False)
        (repo / "note.md").write_text("hello\n", encoding="utf-8")
        assert gc.commit_paths(["note.md"], "vault: create") is None
        assert _git(repo, "rev-list", "--count", "HEAD") == "1"

    def test_non_repo_is_failsafe(self, tmp_path: Path) -> None:
        # not a git repo -> every op fails-safe, never raises
        gc = _committer(tmp_path / "not-a-repo")
        (tmp_path).mkdir(exist_ok=True)
        assert gc.commit_paths(["x.md"], "msg") is None
        assert gc.sweep_commit("sweep")["committed"] is False
        assert gc.push() is False


class TestBotIdentity:
    def test_bot_author_and_committer(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "vault: create n.md")
        an, ae, cn, ce = _git(
            repo, "log", "-1", "--format=%an|%ae|%cn|%ce"
        ).split("|")
        assert an == DEFAULT_BOT_NAME
        assert ae == DEFAULT_BOT_EMAIL
        assert (
            cn == DEFAULT_BOT_NAME
        )  # committer is the bot too, not the base identity
        assert ce == DEFAULT_BOT_EMAIL

    def test_custom_identity(self, repo: Path) -> None:
        gc = _committer(repo, author_name="custom[bot]", author_email="c@x.dev")
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "vault: create n.md")
        assert _git(repo, "log", "-1", "--format=%an") == "custom[bot]"


class TestSweep:
    def test_sweep_commits_whole_tree(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "a.md").write_text("a\n", encoding="utf-8")
        (repo / "b.md").write_text(
            "b\n", encoding="utf-8"
        )  # two untracked files
        result = gc.sweep_commit("vault: periodic sweep")
        assert result["committed"] is True
        assert result["sha"] is not None
        tracked = _git(repo, "ls-files")
        assert "a.md" in tracked and "b.md" in tracked

    def test_sweep_nothing_to_commit(self, repo: Path) -> None:
        gc = _committer(repo)
        result = gc.sweep_commit("vault: periodic sweep")
        assert result["committed"] is False
        assert result["reason"] == "nothing_to_commit"

    def test_sweep_defers_on_inflight_write(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "a.md").write_text("a\n", encoding="utf-8")
        gc.begin_write()
        try:
            result = gc.sweep_commit("vault: periodic sweep")
            assert result["committed"] is False
            assert result["reason"] == "deferred_inflight_write"
        finally:
            gc.end_write()
        # once the write completes the sweep proceeds
        assert gc.sweep_commit("vault: periodic sweep")["committed"] is True

    def test_sweep_disabled_is_noop(self, repo: Path) -> None:
        gc = _committer(repo, enabled=False)
        (repo / "a.md").write_text("a\n", encoding="utf-8")
        assert gc.sweep_commit("sweep")["reason"] == "disabled"
