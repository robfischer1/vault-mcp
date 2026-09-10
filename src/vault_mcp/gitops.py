# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Git committer for vault-mcp — the single-writer commit surface (VG initiative).

vault-mcp is the sole AI writer to the vault, so it also owns vault git. Every
Gate write commits the written path and returns the commit sha (the session's
checkpoint handshake); a scheduled sweep commits + pushes the whole tree to
capture the human Obsidian edits the Gate never sees.

IN-PROCESS, NOT A SUBPROCESS. This module drives dulwich (a pure-Python git
implementation) rather than spawning the `git` binary, and three problems went
away with the spawn:

* **Identity.** `-c user.name=` only set a DEFAULT, which `GIT_AUTHOR_NAME` in
  the inherited environment silently beat — so the bot author was whoever
  started the service. dulwich takes author and committer as explicit bytes
  arguments with no configuration fallback, which is what makes the docstring's
  promise (the bot stays visually distinct from Rob's hand commits) TRUE rather
  than merely intended.
* **`safe.directory`.** The old `-c safe.directory=*` defused git's
  dubious-ownership guard, needed because the service runs as LocalSystem while
  the vault repo is owned by the interactive user. dulwich has no such guard, so
  the flag — a repo-wide relaxation carried on every invocation — is simply gone.
* **The binary.** Nothing walks `PATH` or depends on where git is installed, and
  no caller-supplied string ever reaches an argument vector. ruff's S603/S607
  had nothing left to say about this module once the spawn went, which is the
  honest way to answer a lint rather than excuse it.

Commits are **fail-safe**: a git error is logged and never propagated, so a
commit failure can't break the write that already succeeded (mirrors the Gate's
diff-sink contract — "emission failure never blocks the write").
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path

from dulwich import porcelain
from dulwich.errors import (
    CommitError,
    GitProtocolError,
    HangupException,
    HookError,
    NotGitRepository,
    ObjectFormatException,
    PackedRefsException,
    RefFormatError,
    SendPackError,
)
from dulwich.repo import Repo

log = logging.getLogger(__name__)

DEFAULT_BOT_NAME = "vault-mcp[bot]"
DEFAULT_BOT_EMAIL = "vault-mcp[bot]@users.noreply.github.com"

# THE FAIL-SOFT BOUNDARY, NAMED IN FULL rather than caught as `Exception`. The
# git sidecar must never break a vault write, so every handler below logs and
# degrades — but "must not propagate" was never a reason to swallow everything,
# and a blind except here would hide a TypeError in this module's own code.
#
# Each entry is a failure this layer can actually produce:
#   NotGitRepository  repo_root is not a checkout — and it is ALSO what a push
#                     to an unconfigured remote raises ("no git repository was
#                     found at origin").
#   porcelain.Error   the porcelain layer's own refusals.
#   CommitError       the object could not be written.
#   HookError         a repo hook (pre-commit / commit-msg) rejected the commit.
#   SendPackError,    the push leg: protocol, transport, and a server that hung
#   GitProtocolError, up mid-negotiation.
#   HangupException
#   ObjectFormatException, PackedRefsException, RefFormatError
#                     a corrupt or unreadable object / refs file.
#   ValueError        dulwich's own guard on a path handed in absolute when it
#                     must be repository-relative.
#   OSError           the filesystem underneath all of it.
_GIT_FAILURES = (
    NotGitRepository,
    porcelain.Error,
    CommitError,
    HookError,
    SendPackError,
    GitProtocolError,
    HangupException,
    ObjectFormatException,
    PackedRefsException,
    RefFormatError,
    ValueError,
    OSError,
)


def committer_from_env(repo_root: Path | str) -> GitCommitter:
    """Build a GitCommitter from ``VAULT_MCP_GIT_*`` env (called once at startup).

    Commit + push both default OFF — the live service opts in by setting
    ``VAULT_MCP_GIT_COMMIT=1`` (and ``VAULT_MCP_GIT_PUSH=1``) once the behavior
    is proven, so deploying this code is inert until the flag is flipped.
    """
    return GitCommitter(
        repo_root,
        author_name=os.environ.get(
            "VAULT_MCP_GIT_AUTHOR_NAME", DEFAULT_BOT_NAME
        ),
        author_email=os.environ.get(
            "VAULT_MCP_GIT_AUTHOR_EMAIL", DEFAULT_BOT_EMAIL
        ),
        enabled=os.environ.get("VAULT_MCP_GIT_COMMIT", "0") == "1",
        push_enabled=os.environ.get("VAULT_MCP_GIT_PUSH", "0") == "1",
    )


class GitCommitter:
    """Serialized, fail-safe git committer over a working-tree repo root."""

    def __init__(
        self,
        repo_root: Path | str,
        *,
        author_name: str = DEFAULT_BOT_NAME,
        author_email: str = DEFAULT_BOT_EMAIL,
        enabled: bool = True,
        push_enabled: bool = False,
    ) -> None:
        """Initialize a serialized committer over a repo root with bot identity and enable/push flags."""
        self.repo_root = Path(repo_root)
        self.author_name = author_name
        self.author_email = author_email
        self.enabled = enabled
        self.push_enabled = push_enabled
        self._git_lock = threading.RLock()  # serializes git index ops
        self._inflight = 0  # writes between begin_write/end_write
        self._inflight_lock = threading.Lock()

    # -- dulwich plumbing ---------------------------------------------------
    def _open(self) -> Repo:
        """Open ``repo_root`` itself as a checkout.

        EXACT, NOT DISCOVERED. `git -C <dir>` walks up until it finds a
        repository, so a misconfigured ``repo_root`` used to commit into
        whatever enclosing checkout it happened to land in. `Repo(path)` refuses
        instead, which is the safer failure for a component whose whole job is
        writing commits: a wrong root now raises NotGitRepository and the
        fail-safe boundary turns it into a logged no-op.
        """
        return Repo(str(self.repo_root))

    def _identity(self) -> bytes:
        """Return the bot identity as git's ``Name <email>`` byte string.

        Passed to BOTH author and committer on every commit. There is no
        configuration fallback behind it — that is the point, and the reason
        this is a value rather than a `-c` flag or an environment variable.
        """
        return f"{self.author_name} <{self.author_email}>".encode()

    @staticmethod
    def _has_staged(repo: Repo) -> bool:
        """Whether the index differs from HEAD (the `diff --cached` question)."""
        status = porcelain.status(repo, untracked_files="no")
        return any(status.staged.values())

    @staticmethod
    def _message(message: str) -> bytes:
        # git normalises a `-m` message to end in a newline; dulwich stores
        # exactly what it is given, so do it here or the history diverges in a
        # way `%s` hides and a raw `cat-file` does not.
        text = message if message.endswith("\n") else message + "\n"
        return text.encode("utf-8")

    def head_sha(self) -> str | None:
        """Return the repo's current HEAD sha, or None on failure."""
        try:
            with self._open() as repo:
                return repo.head().decode("ascii")
        except KeyError:
            # An unborn branch: the ref exists in HEAD but points at nothing.
            log.warning("git head_sha: no commit on the current branch yet")
            return None
        except _GIT_FAILURES as exc:
            log.warning("git head_sha failed: %s", exc)
            return None

    def _wait_for_path(self, abs_path: Path, timeout: float = 2.0) -> bool:
        """Poll briefly for a just-written file to land (REST write → disk)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abs_path.exists():
                return True
            time.sleep(0.05)
        return abs_path.exists()

    # -- in-flight write tracking (sweep guard) -----------------------------
    def begin_write(self) -> None:
        """Mark a Gate write as in flight so the sweep defers."""
        with self._inflight_lock:
            self._inflight += 1

    def end_write(self) -> None:
        """Mark a Gate write as finished."""
        with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)

    # -- per-transaction commit (the handshake's first half) ----------------
    def commit_paths(
        self,
        paths: list[str],
        message: str,
        *,
        wait_for_create: bool = True,
    ) -> str | None:
        """Stage + commit ``paths`` under the bot identity; return the new sha.

        Returns None when disabled, on any git failure (fail-safe), or when the
        write changed nothing (no staged diff — e.g. an idempotent rewrite).
        ``wait_for_create=False`` skips the disk-landing poll (used for deletes).

        ``paths`` are repository-relative, as they always were. One `stage()`
        covers creates, modifications AND deletions: dulwich drops an index
        entry whose file is gone, which is what makes the dissolve path work
        without a second `git rm` call.
        """
        if not self.enabled or not paths:
            return None
        try:
            with self._git_lock, self._open() as repo:
                if wait_for_create:
                    for rel in paths:
                        self._wait_for_path(self.repo_root / rel)
                worktree = repo.get_worktree()
                worktree.stage(paths)
                if not self._has_staged(repo):
                    return None
                identity = self._identity()
                sha = worktree.commit(
                    message=self._message(message),
                    author=identity,
                    committer=identity,
                )
                return sha.decode("ascii")
        except _GIT_FAILURES as exc:
            log.warning("commit_paths failed for %s: %s", paths, exc)
            return None

    # -- whole-tree sweep (captures human Obsidian edits) -------------------
    def sweep_commit(self, message: str) -> dict[str, object]:
        """Stage the whole tree, commit under the bot identity, optionally push.

        Checkpoint-silent by design (no session, no checkpoint emit). Defers if a
        Gate write is in flight so it never captures a half-written transaction.
        Returns a structured result; never raises.

        The `git add -A` equivalent is `unstaged + untracked`: dulwich reports a
        tracked file that was modified OR deleted under ``unstaged``, and a new
        file under ``untracked`` with .gitignore already applied. Staging that
        union reproduces `-A` exactly, deletions included — which matters,
        because a note Rob deletes in Obsidian is the case the sweep exists for.
        """
        if not self.enabled:
            return {"committed": False, "reason": "disabled"}
        with self._inflight_lock:
            if self._inflight > 0:
                return {"committed": False, "reason": "deferred_inflight_write"}
        try:
            with self._git_lock, self._open() as repo:
                status = porcelain.status(repo, untracked_files="all")
                changed = [
                    path.decode("utf-8")
                    for path in [*status.unstaged, *status.untracked]
                ]
                worktree = repo.get_worktree()
                if changed:
                    worktree.stage(changed)
                if not self._has_staged(repo):
                    return {"committed": False, "reason": "nothing_to_commit"}
                identity = self._identity()
                sha = worktree.commit(
                    message=self._message(message),
                    author=identity,
                    committer=identity,
                ).decode("ascii")
                pushed = self.push() if self.push_enabled else False
                return {"committed": True, "sha": sha, "pushed": pushed}
        except _GIT_FAILURES as exc:
            log.warning("sweep_commit failed: %s", exc)
            return {"committed": False, "reason": f"error: {exc}"}

    def push(self) -> bool:
        """Push the current branch; fail-safe (logs + returns False on error).

        THE STREAMS ARE CAPTURED, and that is not tidiness. dulwich's push
        writes its progress to real stdout by default, and vault-mcp's default
        transport is MCP over stdio — anything this leg printed would land in
        the middle of a JSON-RPC frame and corrupt the session. The subprocess
        form got this for free from `capture_output=True`; here it has to be
        asked for.
        """
        try:
            with self._git_lock, self._open() as repo:
                porcelain.push(
                    repo,
                    outstream=io.BytesIO(),
                    errstream=io.BytesIO(),
                )
                return True
        except _GIT_FAILURES as exc:
            log.warning("push failed: %s", exc)
            return False
