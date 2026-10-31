# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for vault_mcp.gitops — the single-writer git committer (VG initiative).

Throwaway temp repo, no network, no live vault. Covers Commit-on-Write (#226),
bot identity (#230), and the sweep (#227).

THE ORACLE IS A DIFFERENT IMPLEMENTATION FROM THE SUBJECT, and since the
dulwich port that is worth saying out loud: the committer under test writes
objects with dulwich, while every assertion below reads them back with real
``git``. A round-trip bug that both halves shared would be invisible; here they
cannot share one, because they share no code.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gitops import (
    DEFAULT_BOT_EMAIL,
    DEFAULT_BOT_NAME,
    GitCommitter,
)


# ONE READER PER GIT INVOCATION, instead of one variadic `_git(repo, *args)`.
#
# THE VARIADIC HELPER GUARANTEED THE FINDING IT THEN HAD TO SUPPRESS. ruff's
# S603 fires unless every argv element is an inline literal AT THE CALL SITE —
# routing argv through `*args` can never satisfy that, no matter how literal
# every caller looks, because the rule reads the `subprocess.run`. narcissus
# names this exact shape as the wrong one and records rewriting its own call
# sites rather than excusing them; this is the same move at smaller scale.
#
# So each distinct invocation gets its own small function whose argv is written
# out in full, and the only thing that varies — the repository — rides `cwd=`,
# a keyword the rule cannot see. `/usr/bin/git` is absolute, which closes S607
# at the same time: nothing walks PATH, so whoever controls PATH no longer
# controls which binary runs.
#
# The call sites stay one-liners and read BETTER than `_git(repo, "rev-list",
# "--count", "HEAD")` did, because each one now says what it means.
def _out(cp: subprocess.CompletedProcess[str]) -> str:
    return cp.stdout.strip()


def _commit_count(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "rev-list", "--count", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


def _paths_in_head(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "show", "--name-only", "--format=", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


def _head_subject(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "log", "-1", "--format=%s"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


def _tracked(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "ls-files"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


def _head_identity(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "log", "-1", "--format=%an|%ae|%cn|%ce"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


def _head_author_name(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "log", "-1", "--format=%an"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A temp git repo with one initial commit and a base identity.

    The identity is written as real `git config` rather than handed in through
    the environment ON PURPOSE: TestBotIdentity asserts that the committer's bot
    identity OVERRIDES a configured one, so the base identity has to live where
    a real repo would keep it.
    """
    subprocess.run(
        ["/usr/bin/git", "init", "-q"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/git", "config", "user.name", "Base User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/git", "config", "user.email", "base@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", "-A"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/git", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _committer(repo: Path, **kw: Any) -> GitCommitter:
    kw.setdefault("enabled", True)
    return GitCommitter(repo, **kw)


class TestCommitPaths:
    def test_returns_sha_and_single_commit(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "note.md").write_text("hello\n", encoding="utf-8")
        sha = gc.commit_paths(["note.md"], "vault: create note.md")
        assert sha is not None
        assert len(sha) == 40
        assert _commit_count(repo) == "2"  # init + this
        # the path is in the new commit
        assert "note.md" in _paths_in_head(repo)

    def test_sequential_writes_n_commits(self, repo: Path) -> None:
        gc = _committer(repo)
        shas = []
        for i in range(3):
            (repo / f"n{i}.md").write_text(f"body {i}\n", encoding="utf-8")
            shas.append(gc.commit_paths([f"n{i}.md"], f"vault: create n{i}.md"))
        assert all(s is not None for s in shas)
        assert len(set(shas)) == 3  # three distinct commits
        assert _commit_count(repo) == "4"  # init + 3

    def test_message_used_verbatim(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "outputs: a rich caller-supplied subject")
        assert _head_subject(repo) == "outputs: a rich caller-supplied subject"

    def test_no_change_returns_none(self, repo: Path) -> None:
        gc = _committer(repo)
        # committing an unmodified, already-tracked file stages nothing
        sha = gc.commit_paths(["README.md"], "vault: noop")
        assert sha is None
        assert _commit_count(repo) == "1"

    def test_commits_a_deletion(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "doomed.md").write_text("bye\n", encoding="utf-8")
        gc.commit_paths(["doomed.md"], "vault: create doomed.md")
        (repo / "doomed.md").unlink()
        sha = gc.commit_paths(["doomed.md"], "vault: dissolve doomed.md", wait_for_create=False)
        assert sha is not None
        assert "doomed.md" not in _tracked(repo)

    def test_disabled_is_noop(self, repo: Path) -> None:
        gc = _committer(repo, enabled=False)
        (repo / "note.md").write_text("hello\n", encoding="utf-8")
        assert gc.commit_paths(["note.md"], "vault: create") is None
        assert _commit_count(repo) == "1"

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
        an, ae, cn, ce = _head_identity(repo).split("|")
        assert an == DEFAULT_BOT_NAME
        assert ae == DEFAULT_BOT_EMAIL
        assert cn == DEFAULT_BOT_NAME  # committer is the bot too, not the base identity
        assert ce == DEFAULT_BOT_EMAIL

    def test_custom_identity(self, repo: Path) -> None:
        gc = _committer(repo, author_name="custom[bot]", author_email="c@x.dev")
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "vault: create n.md")
        assert _head_author_name(repo) == "custom[bot]"

    def test_bot_identity_survives_a_hostile_environment(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GIT_AUTHOR_NAME in the environment must not reach the commit.

        THE REGRESSION THIS PINS was live and shipped. `-c user.name=` sets a
        DEFAULT, and git resolves an author from GIT_AUTHOR_NAME first, so a
        service started from a shell that exported one committed under that
        name — and the module docstring's promise that the bot stays visually
        distinct from Rob's hand commits was false exactly when it mattered, on
        the NSSM service that inherits whatever environment it is given.

        It cannot regress quietly now: dulwich takes the identity as an
        argument and consults no configuration and no environment at all. This
        test is the executable form of that claim.
        """
        monkeypatch.setenv("GIT_AUTHOR_NAME", "Impostor")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "impostor@example.com")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "Impostor")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "impostor@example.com")
        gc = _committer(repo)
        (repo / "n.md").write_text("x\n", encoding="utf-8")
        gc.commit_paths(["n.md"], "vault: create n.md")
        an, ae, cn, ce = _head_identity(repo).split("|")
        assert an == DEFAULT_BOT_NAME
        assert ae == DEFAULT_BOT_EMAIL
        assert cn == DEFAULT_BOT_NAME
        assert ce == DEFAULT_BOT_EMAIL


class TestSweep:
    def test_sweep_commits_whole_tree(self, repo: Path) -> None:
        gc = _committer(repo)
        (repo / "a.md").write_text("a\n", encoding="utf-8")
        (repo / "b.md").write_text("b\n", encoding="utf-8")  # two untracked files
        result = gc.sweep_commit("vault: periodic sweep")
        assert result["committed"] is True
        assert result["sha"] is not None
        tracked = _tracked(repo)
        assert "a.md" in tracked
        assert "b.md" in tracked

    def test_sweep_commits_a_human_deletion(self, repo: Path) -> None:
        """The half of `-A` nothing else covers: a note deleted OUTSIDE the Gate.

        This is the sweep's entire reason to exist — Rob removes a note in
        Obsidian, the Gate never sees it, and the periodic sweep has to capture
        the removal. dulwich reports a deleted tracked file under
        `status.unstaged`, NOT `untracked`, so a sweep that staged only
        untracked paths would commit every addition and leave the deleted file
        tracked forever, looking green the whole time.
        """
        gc = _committer(repo)
        (repo / "gone.md").write_text("bye\n", encoding="utf-8")
        assert gc.sweep_commit("vault: create gone.md")["committed"] is True
        assert "gone.md" in _tracked(repo)
        (repo / "gone.md").unlink()
        result = gc.sweep_commit("vault: periodic sweep")
        assert result["committed"] is True
        assert "gone.md" not in _tracked(repo)

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


# -- origin integration (fetch + merge before push) ---------------------------
# A bare origin and a second clone ("other", standing in for the Windows host or
# a human) are driven with dulwich porcelain; every ASSERTION reads back through
# real `git`, so the oracle stays a different implementation from the subject.
# Local paths only, no network.
def _head_tree(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    )


def _merge_count(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "rev-list", "--count", "--merges", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    )


def _parent_count(repo: Path) -> int:
    return len(
        subprocess.run(
            ["/usr/bin/git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[1:]
    )


def _dirty(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "status", "--porcelain"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    )


def _head_sha(repo: Path) -> str:
    return _out(
        subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    )


_OTHER = b"Other <other@example.com>"


def _other_commit(other: Path, message: str, *, add: list[str], remove: list[str]) -> None:
    """Commit as the other clone and push to origin."""
    from dulwich import porcelain

    if add:
        porcelain.add(other, paths=[str(other / name) for name in add])
    if remove:
        porcelain.remove(other, paths=[str(other / name) for name in remove])
    porcelain.commit(other, message=message.encode(), author=_OTHER, committer=_OTHER)
    porcelain.push(other, outstream=io.BytesIO(), errstream=io.BytesIO())


@pytest.fixture
def wired(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """(local, bare origin, other clone), base commit pushed, local tracking origin."""
    from dulwich import porcelain
    from dulwich.repo import Repo

    origin = tmp_path_factory.mktemp("origin")
    porcelain.init(origin, bare=True)
    (repo / "keep.md").write_text("keep\n", encoding="utf-8")
    (repo / "shared.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    gc = _committer(repo)
    assert gc.commit_paths(["keep.md", "shared.md"], "base")
    with Repo(str(repo)) as r:
        cfg = r.get_config()
        cfg.set((b"remote", b"origin"), b"url", str(origin).encode())
        cfg.set((b"remote", b"origin"), b"fetch", b"+refs/heads/*:refs/remotes/origin/*")
        cfg.write_to_path()
        branch = porcelain.active_branch(r)
    porcelain.push(repo, "origin", branch.decode(), outstream=io.BytesIO(), errstream=io.BytesIO())
    other = tmp_path_factory.mktemp("other")
    porcelain.clone(str(origin), str(other), errstream=io.BytesIO()).close()
    return repo, origin, other


class TestOriginIntegration:
    def test_origin_ahead_new_file_lands_and_push_succeeds(
        self, wired: tuple[Path, Path, Path], capfd: pytest.CaptureFixture[str]
    ) -> None:
        local, origin, other = wired
        (other / "remote.md").write_text("from origin\n", encoding="utf-8")
        _other_commit(other, "remote note", add=["remote.md"], remove=[])
        (local / "local.md").write_text("mine\n", encoding="utf-8")
        result = _committer(local, push_enabled=True).sweep_commit("vault: sweep")
        assert result["committed"] is True
        assert result["pushed"] is True
        captured = capfd.readouterr()  # fetch/merge/push must not write to MCP's stdio
        assert captured.out == ""
        assert (local / "remote.md").read_text(encoding="utf-8") == "from origin\n"
        assert "remote.md" in _tracked(local)
        tree = _head_tree(origin)
        assert "remote.md" in tree
        assert "local.md" in tree
        # the merge commit is the bot's, like every other commit here
        assert _head_identity(local).split("|")[0] == DEFAULT_BOT_NAME
        assert _parent_count(local) == 2

    def test_origin_deletion_never_removes_a_file_on_local_disk(
        self, wired: tuple[Path, Path, Path]
    ) -> None:
        local, origin, other = wired
        _other_commit(other, "origin deletes keep.md", add=[], remove=["keep.md"])
        (local / "local.md").write_text("mine\n", encoding="utf-8")
        result = _committer(local, push_enabled=True).sweep_commit("vault: sweep")
        assert result["pushed"] is True
        assert (local / "keep.md").read_text(encoding="utf-8") == "keep\n"
        assert "keep.md" in _head_tree(local)
        # and the kept file went back out to origin
        assert "keep.md" in _head_tree(origin)

    def test_origin_deletion_of_nested_and_locally_edited_files_is_not_taken(
        self, wired: tuple[Path, Path, Path]
    ) -> None:
        from dulwich import porcelain

        local, _origin, other = wired
        gc = _committer(local, push_enabled=True)
        (local / "dir").mkdir()
        (local / "dir" / "deep.md").write_text("deep\n", encoding="utf-8")
        assert gc.commit_paths(["dir/deep.md"], "deep")
        assert gc.push() is True
        porcelain.pull(other, outstream=io.BytesIO(), errstream=io.BytesIO())
        _other_commit(
            other, "origin deletes dir and keep.md", add=[], remove=["dir/deep.md", "keep.md"]
        )
        (local / "keep.md").write_text("keep, edited here\n", encoding="utf-8")
        result = gc.sweep_commit("vault: sweep")
        assert result["pushed"] is True, result
        assert (local / "dir" / "deep.md").read_text(encoding="utf-8") == "deep\n"
        assert (local / "keep.md").read_text(encoding="utf-8") == "keep, edited here\n"
        head_tree = _head_tree(local)
        assert "dir/deep.md" in head_tree
        assert "keep.md" in head_tree

    def test_conflict_aborts_without_push_or_half_merge(
        self, wired: tuple[Path, Path, Path]
    ) -> None:
        local, origin, other = wired
        (other / "shared.md").write_text("one\nTHEIRS\nthree\n", encoding="utf-8")
        _other_commit(other, "theirs", add=["shared.md"], remove=[])
        origin_before = _head_sha(origin)
        (local / "shared.md").write_text("one\nMINE\nthree\n", encoding="utf-8")
        result = _committer(local, push_enabled=True).sweep_commit("vault: sweep")
        assert result["committed"] is True
        assert result["pushed"] is False
        assert "conflict" in str(result["push_reason"])
        assert (local / "shared.md").read_text(encoding="utf-8") == "one\nMINE\nthree\n"
        assert _dirty(local) == ""
        assert _merge_count(local) == "0"
        assert _head_sha(origin) == origin_before
