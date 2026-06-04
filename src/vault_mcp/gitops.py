"""Git committer for vault-mcp — the single-writer commit surface (VG initiative).

vault-mcp is the sole AI writer to the vault, so it also owns vault git. Every
Gate write commits the written path and returns the commit sha (the session's
checkpoint handshake); a scheduled sweep commits + pushes the whole tree to
capture the human Obsidian edits the Gate never sees.

Commits are **fail-safe**: a git error is logged and never propagated, so a
commit failure can't break the write that already succeeded (mirrors the Gate's
diff-sink contract — "emission failure never blocks the write"). Identity is set
per-commit via ``-c user.name/-c user.email`` so the global git config is never
touched and the bot author stays visually distinct from Rob's hand commits.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BOT_NAME = "vault-mcp[bot]"
DEFAULT_BOT_EMAIL = "vault-mcp[bot]@users.noreply.github.com"


def committer_from_env(repo_root: Path | str) -> GitCommitter:
    """Build a GitCommitter from ``VAULT_MCP_GIT_*`` env (called once at startup).

    Commit + push both default OFF — the live service opts in by setting
    ``VAULT_MCP_GIT_COMMIT=1`` (and ``VAULT_MCP_GIT_PUSH=1``) once the behavior
    is proven, so deploying this code is inert until the flag is flipped.
    """
    return GitCommitter(
        repo_root,
        author_name=os.environ.get("VAULT_MCP_GIT_AUTHOR_NAME", DEFAULT_BOT_NAME),
        author_email=os.environ.get("VAULT_MCP_GIT_AUTHOR_EMAIL", DEFAULT_BOT_EMAIL),
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
        git_bin: str = "git",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.author_name = author_name
        self.author_email = author_email
        self.enabled = enabled
        self.push_enabled = push_enabled
        self._git = git_bin
        self._git_lock = threading.RLock()  # serializes git index ops
        self._inflight = 0  # writes between begin_write/end_write
        self._inflight_lock = threading.Lock()

    # -- subprocess plumbing ------------------------------------------------
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        # ``safe.directory=*`` defuses git's dubious-ownership guard: the service
        # runs as LocalSystem while the vault repo is owned by the interactive
        # user, which otherwise blocks every command. It is scoped by
        # construction — every invocation also passes ``-C repo_root``, so git
        # only ever operates on the one trusted repo.
        return subprocess.run(
            [self._git, "-C", str(self.repo_root), "-c", "safe.directory=*", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _identity_args(self) -> list[str]:
        # Sets BOTH author and committer: git derives author from user.* unless
        # GIT_AUTHOR_* / --author override, so these two -c flags cover both.
        return [
            "-c",
            f"user.name={self.author_name}",
            "-c",
            f"user.email={self.author_email}",
        ]

    def head_sha(self) -> str | None:
        try:
            cp = self._run("rev-parse", "HEAD")
            return cp.stdout.strip() if cp.returncode == 0 else None
        except Exception as exc:  # noqa: BLE001 - fail-safe
            log.warning("git head_sha failed: %s", exc)
            return None

    def _has_staged(self) -> bool:
        # `diff --cached --quiet` exits 1 when staged changes exist, 0 when none.
        return self._run("diff", "--cached", "--quiet").returncode != 0

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
        with self._inflight_lock:
            self._inflight += 1

    def end_write(self) -> None:
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
        """
        if not self.enabled or not paths:
            return None
        try:
            with self._git_lock:
                if wait_for_create:
                    for rel in paths:
                        self._wait_for_path(self.repo_root / rel)
                add = self._run("add", "--", *paths)
                if add.returncode != 0:
                    log.warning("git add failed for %s: %s", paths, add.stderr.strip())
                    return None
                if not self._has_staged():
                    return None
                commit = self._run(*self._identity_args(), "commit", "-m", message)
                if commit.returncode != 0:
                    log.warning("git commit failed for %s: %s", paths, commit.stderr.strip())
                    return None
                return self.head_sha()
        except Exception as exc:  # noqa: BLE001 - a commit failure must never break the write
            log.warning("commit_paths failed for %s: %s", paths, exc)
            return None

    # -- whole-tree sweep (captures human Obsidian edits) -------------------
    def sweep_commit(self, message: str) -> dict[str, object]:
        """Stage the whole tree, commit under the bot identity, optionally push.

        Checkpoint-silent by design (no session, no checkpoint emit). Defers if a
        Gate write is in flight so it never captures a half-written transaction.
        Returns a structured result; never raises.
        """
        if not self.enabled:
            return {"committed": False, "reason": "disabled"}
        with self._inflight_lock:
            if self._inflight > 0:
                return {"committed": False, "reason": "deferred_inflight_write"}
        try:
            with self._git_lock:
                add = self._run("add", "-A")
                if add.returncode != 0:
                    log.warning("sweep git add -A failed: %s", add.stderr.strip())
                    return {"committed": False, "reason": "add_failed"}
                if not self._has_staged():
                    return {"committed": False, "reason": "nothing_to_commit"}
                commit = self._run(*self._identity_args(), "commit", "-m", message)
                if commit.returncode != 0:
                    log.warning("sweep commit failed: %s", commit.stderr.strip())
                    return {"committed": False, "reason": "commit_failed"}
                sha = self.head_sha()
                pushed = self.push() if self.push_enabled else False
                return {"committed": True, "sha": sha, "pushed": pushed}
        except Exception as exc:  # noqa: BLE001 - fail-safe
            log.warning("sweep_commit failed: %s", exc)
            return {"committed": False, "reason": f"error: {exc}"}

    def push(self) -> bool:
        """Push the current branch; fail-safe (logs + returns False on error)."""
        try:
            with self._git_lock:
                cp = self._run("push")
                if cp.returncode != 0:
                    log.warning("git push failed: %s", cp.stderr.strip())
                    return False
                return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            log.warning("push failed: %s", exc)
            return False
