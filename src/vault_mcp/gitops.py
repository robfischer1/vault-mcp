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
from typing import TYPE_CHECKING

from dulwich import porcelain
from dulwich.diff_tree import CHANGE_DELETE, tree_changes
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
from dulwich.graph import find_merge_base
from dulwich.index import update_working_tree
from dulwich.merge import recursive_merge
from dulwich.object_store import tree_lookup_path
from dulwich.objects import Commit, ObjectID, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

if TYPE_CHECKING:
    from dulwich.object_store import BaseObjectStore

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

    def commit_exists(self, sha: str) -> bool:
        """Whether ``sha`` names a commit object in the repo (never trust a bare sha)."""
        try:
            with self._open() as repo:
                return isinstance(repo.get_object(ObjectID(sha.encode("ascii"))), Commit)
        except KeyError:
            return False
        except (*_GIT_FAILURES, UnicodeEncodeError) as exc:
            log.warning("commit_exists(%s) failed: %s", sha, exc)
            return False

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
                changed = [path.decode("utf-8") for path in [*status.unstaged, *status.untracked]]
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
                result: dict[str, object] = {"committed": True, "sha": sha, "pushed": False}
                if self.push_enabled:
                    pushed, reason = self._push_outcome()
                    result["pushed"] = pushed
                    if reason:
                        result["push_reason"] = reason
                return result
        except _GIT_FAILURES as exc:
            log.warning("sweep_commit failed: %s", exc)
            return {"committed": False, "reason": f"error: {exc}"}

    def push(self) -> bool:
        """Integrate origin, then push the current branch; fail-safe (False on error).

        See `_push_outcome` for the reason a push was withheld.
        """
        return self._push_outcome()[0]

    def _push_outcome(self) -> tuple[bool, str | None]:
        """Fetch + merge origin, then push; return ``(pushed, reason_if_not)``.

        A push is only ever attempted on top of a clean integration: origin's
        branch is fetched and merged first (see `_integrate_origin`), and any
        refusal there withholds the push instead of forcing it.

        THE STREAMS ARE CAPTURED, and that is not tidiness. dulwich's push
        writes its progress to real stdout by default, and vault-mcp's default
        transport is MCP over stdio — anything this leg printed would land in
        the middle of a JSON-RPC frame and corrupt the session. The subprocess
        form got this for free from `capture_output=True`; here it has to be
        asked for. The fetch and merge legs are held to the same rule.
        """
        try:
            with self._git_lock, self._open() as repo:
                reason = self._integrate_origin(repo)
                if reason:
                    log.warning("push withheld: %s", reason)
                    return False, reason
                porcelain.push(
                    repo,
                    outstream=io.BytesIO(),
                    errstream=io.BytesIO(),
                )
                return True, None
        except _GIT_FAILURES as exc:
            log.warning("push failed: %s", exc)
            return False, f"error: {exc}"

    def _integrate_origin(self, repo: Repo) -> str | None:
        """Fetch origin and merge its branch into the checked-out one.

        Returns None when integrated (or there is nothing to integrate: origin
        lacks the branch, or is already contained), else a reason the push must
        be withheld. Nothing is half-applied on a refusal: the merge is computed
        on objects first and the working tree is touched only once it is clean.

        Two safety rules, both about never losing what is on local disk:

        * **Deletions are not taken.** A path origin deleted that HEAD still
          has is put back into the merged tree, so the result (and the next
          push) keeps it. A note present here is Rob's until he deletes it here.
        * **Conflicts abort.** Any conflict leaves HEAD, the index and the
          working tree exactly as they were.

        The merge commit is the bot's, like every other commit this class makes.
        Requires a clean tree (the sweep commits everything before it pushes);
        a dirty one is refused rather than overwritten.
        """
        ref = repo.refs.read_ref(Ref(b"HEAD"))
        if ref is None or not ref.startswith(b"ref: refs/heads/"):
            return "detached_head"
        branch = ref[len(b"ref: refs/heads/") :]
        porcelain.fetch(
            repo,
            "origin",
            outstream=io.StringIO(),
            errstream=io.BytesIO(),
        )
        theirs_id = repo.refs.as_dict(Ref(b"refs/remotes/origin")).get(Ref(branch))
        if theirs_id is None:
            return None  # origin has no such branch yet: the push creates it
        head_id = repo.head()
        merge_bases = find_merge_base(repo, [head_id, theirs_id])
        if not merge_bases:
            return "unrelated_histories"
        if theirs_id in merge_bases:
            return None  # origin is already contained in local
        status = porcelain.status(repo, untracked_files="all")
        if any(status.staged.values()) or status.unstaged or status.untracked:
            return "dirty_worktree"
        head_commit = repo[head_id]
        theirs_commit = repo[theirs_id]
        if not isinstance(head_commit, Commit) or not isinstance(theirs_commit, Commit):
            return "not_a_commit"
        try:
            merged, conflicts = recursive_merge(
                repo.object_store,
                merge_bases,
                head_commit,
                theirs_commit,
                repo.get_gitattributes(),
                repo.get_config(),
            )
        except ImportError:
            # dulwich's three-way TEXT merge needs `merge3` (the `dulwich[merge]` extra).
            return "merge3_missing"
        store = repo.object_store
        # A modify/delete conflict (origin deleted a file local still has) is
        # the deletion rule again, not a real conflict: local wins. Anything
        # else aborts with nothing touched.
        keep_local: list[bytes] = []
        real: list[bytes] = []
        for path in conflicts:
            try:
                tree_lookup_path(store.__getitem__, theirs_commit.tree, path)
            except KeyError:
                keep_local.append(path)
            else:
                real.append(path)
        if real:
            names = ", ".join(c.decode("utf-8", "replace") for c in real[:5])
            return f"merge_conflict: {names}"
        store.add_object(merged)
        for path in keep_local:
            mode, sha = tree_lookup_path(store.__getitem__, head_commit.tree, path)
            merged = _tree_with(store, merged, path.split(b"/"), mode, sha)
        for change in tree_changes(store, head_commit.tree, merged.id):
            old = change.old
            if change.type == CHANGE_DELETE and old is not None and old.path is not None:
                merged = _tree_with(store, merged, old.path.split(b"/"), old.mode or 0, old.sha)
        store.add_object(merged)
        identity = self._identity()
        merge = Commit()
        merge.tree = merged.id
        merge.parents = [head_id, theirs_id]
        merge.author = merge.committer = identity
        merge.author_time = merge.commit_time = int(time.time())
        merge.author_timezone = merge.commit_timezone = 0
        merge.message = self._message(f"vault: merge origin/{branch.decode()}")
        store.add_object(merge)
        update_working_tree(
            repo,
            head_commit.tree,
            merged.id,
            change_iterator=tree_changes(store, head_commit.tree, merged.id),
            config=repo.get_config_stack(),
        )
        repo.refs[Ref(b"HEAD")] = merge.id
        return None


def _tree_with(
    store: BaseObjectStore, tree: Tree, parts: list[bytes], mode: int, sha: ObjectID
) -> Tree:
    """Return ``tree`` with the entry at nested path ``parts`` set (new Tree objects are stored)."""
    out = Tree()
    for name, m, s in tree.iteritems():
        out.add(name, m, s)
    if len(parts) == 1:
        out.add(parts[0], mode, sha)
    else:
        try:
            child = store[out[parts[0]][1]]
        except KeyError:
            child = Tree()
        sub = child if isinstance(child, Tree) else Tree()
        out.add(parts[0], 0o040000, _tree_with(store, sub, parts[1:], mode, sha).id)
    store.add_object(out)
    return out
