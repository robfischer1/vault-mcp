#!/usr/bin/env python3
"""MCP server for vault-mcp.

Read-only query tools over an Obsidian vault's frontmatter, filenames,
and link graph. Boilerplate follows personal-history-db/server.py.

Config resolution (highest priority first):
    VAULT_MCP_PATH          explicit path to vault root
    OBSIDIAN_VAULT_PATH     shared env var (also used by other tools)
    (walk up)               find nearest .obsidian/ parent

    VAULT_MCP_TTL_SECONDS   index TTL in seconds (default: 300)
"""

# VERIFY: `dict[str, Any]` at the JSON boundary, and only there.
#
# An MCP tool return IS a JSON object, so the value type is open by the
# protocol's own contract — pinning it to a TypedDict per verb would encode a
# wire shape the client is free to ignore, and would still be `Any` one level
# down where Obsidian's REST payloads and YAML frontmatter arrive untyped.
# Measured 2026-08-22: of 276 `Any` in this package, 127 are `-> dict[str, Any]`
# verb returns and 34 are `list[dict[str, Any]]` rows of the same. This is a
# stated decision at the boundary, not an unexamined default. The mypy override
# for this module records the same trade from the checker's side.
#
# What is NOT excused by it: a BARE `: Any` or `-> Any` on anything that is not
# that boundary. Those were audited to zero in this package on the same date —
# the survivors are three sites in the Bases formula evaluator, each carrying
# its own VERIFY where it sits.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from vault_mcp.bases import QueryResult
    from vault_mcp.cli_client import ObsidianCLI
    from vault_mcp.compute import ComputeReceiver
    from vault_mcp.gate import ConventionGate
    from vault_mcp.gitops import GitCommitter
    from vault_mcp.lifecycle import Materializer
    from vault_mcp.plan_freshness import PlanSweepReport, StoredCopy
    from vault_mcp.rest_client import ObsidianRESTClient

from vault_mcp.bases import (
    execute_base as _execute_base_impl,
)
from vault_mcp.bases import (
    parse_file as _parse_file_impl,
)
from vault_mcp.index import VaultIndex
from vault_mcp.rest_client import DEFAULT_REST_URL

# The phdb sibling-repo sys.path import is GONE (2026-08-04). It resolved
# ../personal-history-db/src off the Forge workspace so the triple and
# vault_notes verbs could import phdb directly — the cross-tree import that
# SRSC isolation #1317 asked to remove. Both verb families are now retired and
# the monolith is dissolved, so there is nothing left to import: #1317 is
# closed by deletion rather than by the config-addressed call it proposed.

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _resolve_vault_path() -> Path:
    if p := os.environ.get("VAULT_MCP_PATH"):
        return Path(p).resolve()
    if p := os.environ.get("OBSIDIAN_VAULT_PATH"):
        return Path(p).resolve()
    # Walk up from this file looking for .obsidian/
    cursor = Path(__file__).resolve().parent
    while cursor != cursor.parent:
        if (cursor / ".obsidian").is_dir():
            return cursor
        cursor = cursor.parent
    raise FileNotFoundError(
        "Could not locate an Obsidian vault. "
        "Set VAULT_MCP_PATH or OBSIDIAN_VAULT_PATH env var."
    )


VAULT_PATH = _resolve_vault_path()
TTL_SECONDS = int(os.environ.get("VAULT_MCP_TTL_SECONDS", "300"))
WATCH_ENABLED = os.environ.get("VAULT_MCP_WATCH", "1") != "0"

# REST API config (Phase 6)
REST_DISABLE = os.environ.get("VAULT_MCP_REST_DISABLE", "0") == "1"
REST_URL = os.environ.get("VAULT_MCP_REST_URL", DEFAULT_REST_URL)
REST_KEY_PATH = os.environ.get(
    "VAULT_MCP_REST_KEY_PATH",
    "",
)
# Direct key injection (e.g. from a secrets manager); takes precedence over the path.
REST_KEY = os.environ.get("VAULT_MCP_REST_KEY", "")

# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

_active_sessions: set[Any] = set()


@dataclass
class Subscription:
    """A live-update subscription: a handle bound to a base path/view plus its last result hash."""

    handle: str
    path: str
    view: str | None
    base_index: int
    last_result_hash: str | None = None


class SubscriptionManager:
    """Manages Bases live update subscriptions and pushes notifications."""

    def __init__(self, mcp_server: FastMCP):
        """Initialize the subscription manager bound to the FastMCP server."""
        self.mcp = mcp_server
        self.subscriptions: dict[str, Subscription] = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger(__name__)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Path] | None = None
        self._worker_task: asyncio.Task[None] | None = None

    def _ensure_worker(self) -> None:
        """Ensure the background worker is running."""
        if self._worker_task is not None:
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not in an event loop yet
            return

        self._queue = asyncio.Queue()
        self._worker_task = self._loop.create_task(self._worker())
        self.log.info("Subscription worker started")

    async def _worker(self) -> None:
        while True:
            if self._queue is None:
                break
            path = await self._queue.get()
            try:
                await self.notify_all(path)
            except Exception:
                self.log.exception("Error in subscription worker for %s", path)
            finally:
                self._queue.task_done()

    def on_file_invalidated(self, path: Path) -> None:
        """Sync callback for VaultIndex."""
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    def add(self, path: str, view: str | None, base_index: int) -> str:
        """Register a subscription for a base path/view and return its handle."""
        self._ensure_worker()
        handle = f"sub_{uuid.uuid4().hex[:8]}"
        with self.lock:
            self.subscriptions[handle] = Subscription(
                handle=handle,
                path=path,
                view=view,
                base_index=base_index,
            )
        return handle

    def remove(self, handle: str) -> bool:
        """Remove the subscription with `handle`; return True if it existed."""
        with self.lock:
            if handle in self.subscriptions:
                del self.subscriptions[handle]
                return True
        return False

    def _hash_result(self, result: QueryResult) -> str:
        """Create a stable hash of a QueryResult.

        Typed concretely rather than `Any`. Both call sites pass the return of
        `_execute_base_impl`, which is a QueryResult — so the previous
        `asdict(x) if hasattr(x, "__dataclass_fields__") else x` branch was
        dead defensive weight, and the `Any` that permitted it was hiding the
        fact. `.get()` on the else-branch would have raised on any non-mapping
        that ever reached it.
        """
        data = asdict(result)

        hash_data = {
            "notes": [
                {"path": n["path"], "formulas": n["formulas"]}
                for n in data.get("notes", [])
            ],
            "summaries": data.get("summaries", {}),
            "total": data.get("total", 0),
        }
        dump = json.dumps(hash_data, sort_keys=True)
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    async def notify_all(self, _changed_path: Path) -> None:
        """Re-evaluate every subscription and push notifications for changed result sets."""
        subs_to_check = []
        with self.lock:
            subs_to_check = list(self.subscriptions.values())

        if not subs_to_check:
            return

        idx = _get_index()

        for sub in subs_to_check:
            file_path = idx.vault / sub.path
            if not file_path.exists():
                continue

            try:
                pf = _parse_file_impl(file_path)
                if sub.base_index >= len(pf.bases):
                    continue
                base = pf.bases[sub.base_index]

                if sub.view:
                    matched = [v for v in base.views if v.name == sub.view]
                    if not matched:
                        continue

                result = _execute_base_impl(base, idx, view_name=sub.view)
                current_hash = self._hash_result(result)

                if current_hash == sub.last_result_hash:
                    continue

                sub.last_result_hash = current_hash
                await self._push_notification(sub, result)
            except Exception:
                self.log.exception("Error updating subscription %s", sub.handle)

    async def _push_notification(
        self, sub: Subscription, result: QueryResult
    ) -> None:
        payload = {
            "handle": sub.handle,
            "path": sub.path,
            "view": sub.view,
            "results": asdict(result),
        }

        from mcp.types import JSONRPCNotification

        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/bases/update",
            params=payload,
        )

        disconnected = []
        for session in _active_sessions:
            try:
                await session.send_notification(cast("Any", notification))
            except Exception:
                self.log.exception(
                    "send_notification failed; treating session as gone"
                )
                disconnected.append(session)

        for session in disconnected:
            _active_sessions.discard(session)


_sub_manager: SubscriptionManager | None = None


def _get_sub_manager() -> SubscriptionManager:
    global _sub_manager
    if _sub_manager is None:
        _sub_manager = SubscriptionManager(mcp)
        _get_index().on_invalidate.append(_sub_manager.on_file_invalidated)
    return _sub_manager


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_index: VaultIndex | None = None
_observer = None


def _get_index() -> VaultIndex:
    global _index, _observer
    if _index is None:
        _index = VaultIndex(VAULT_PATH, ttl_seconds=TTL_SECONDS)
        if WATCH_ENABLED:
            from vault_mcp.watcher import start_watcher

            _index.enable_watcher()
            _observer = start_watcher(_index)
        _start_plan_reconcile_once()
    return _index


#: F4 — the background re-dissolve loop, started once alongside the index.
_plan_reconcile_thread: object | None = None


def _start_plan_reconcile_once() -> None:
    """Start the periodic plan reconcile (F4), at most once per process.

    Deliberately NOT hung off the vault watcher: that fires on every ``.md``
    save, so a plan edited over ten minutes would land ten stored versions and
    an editor's autosave would land dozens — the store's history would become a
    keystroke log. F2's clock made the periodic check cheap, so polling
    debounces by construction and its cost does not scale with typing speed.

    Skipped when ``HADES_URL`` is unset: without it the write leg would fall
    back to the retired phdb route, so an enabled-but-unwired loop would log a
    failure every tick forever.
    """
    global _plan_reconcile_thread
    if _plan_reconcile_thread is not None or not HADES_URL:
        return
    from vault_mcp.plan_reconcile import start_plan_reconcile

    _plan_reconcile_thread = start_plan_reconcile(
        lambda: _plan_sweep(refresh=True, cheap_gate=True)
    )


# REST client (Phase 6)
_rest_client: ObsidianRESTClient | None = None


def _get_rest_client() -> ObsidianRESTClient:
    global _rest_client
    if _rest_client is None:
        from vault_mcp.rest_client import ObsidianRESTClient

        _rest_client = ObsidianRESTClient(
            base_url=REST_URL,
            key_path=REST_KEY_PATH or None,
            api_key=REST_KEY or None,
        )
        _rest_client.probe()
    return _rest_client


# CLI client (Phase 006)
_cli_client: ObsidianCLI | None = None


def _get_cli_client() -> ObsidianCLI:
    global _cli_client
    if _cli_client is None:
        from vault_mcp.cli_client import ObsidianCLI

        _cli_client = ObsidianCLI()
        _cli_client.probe()
    return _cli_client


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------
mcp = FastMCP("vault-mcp")


@mcp.tool()
def obsidian_cli_status() -> dict[str, Any]:
    """Check Obsidian CLI availability and version.

    Returns:
        {"available": bool, "version": str|None, "error": str|None, "detail": str|None}

    """
    return _get_cli_client().probe()


# ---------------------------------------------------------------------------
# Query / graph / governance verbs
#
# MOVED to vault_mcp/verbs_query.py (vault-mcp#5294). Registered by the import
# at the foot of this file.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 8 — Bases tools
#
# MOVED to vault_mcp/verbs_bases.py (vault-mcp#5294). Registered by the import
# at the foot of this file.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 6 — REST-backed tools
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 6 — REST-backed tools
#
# MOVED to vault_mcp/verbs_rest.py (vault-mcp#5294). The 16 verbs and their
# `if not REST_DISABLE` guard live there; the import that registers them is at
# the foot of this file, where every name they close over already exists.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 9 — Predicate table (triple store) tools
# ---------------------------------------------------------------------------

# PHDB_DB_PATH is retired (2026-08-04) along with the direct-DB read seam it
# fed. It opened a phdb SQLite snapshot frozen 2026-06-10 whose vault_notes
# index had not been written since 2026-05-27; every verb that read it is now
# de-tooled, so the env var is no longer consulted and can be dropped from the
# service definition.
# phdb's plain-HTTP base URL — the dissolve verb POSTs typed writes here (VDV F3
# transport decision: writes go over HTTP, not the legacy direct-DB seam). Only
# /write/plan still falls through to it, and only when HADES_URL is unset; the
# monolith is gone, so that path fails fast rather than half-writing.
PHDB_HTTP_URL = os.environ.get("PHDB_HTTP_URL", "http://localhost:8101").rstrip(
    "/"
)
# The Hades gateway — strangled phdb concerns route here instead (entity
# writes -> harmonia_write_entity_typed). Unset HADES_URL = legacy phdb
# routing for everything (safe: post-deregister phdb answers 404 and the
# dissolve halts rather than half-writing).
HADES_URL = os.environ.get("HADES_URL", "").rstrip("/")
HADES_TOKEN = os.environ.get("HADES_TOKEN", "")

# ---------------------------------------------------------------------------
# RETIRED VERB TOMBSTONES REMOVED 2026-08-22 (vault-mcp#5294).
#
# Seventeen de-tooled functions lived here — the phdb note verbs, the
# typed-graph/triple verbs, the file-revision verbs and the dissolution-registry
# verbs — each an inert stub returning a `{"error": "retired"}` sentinel behind
# a full Args:/Returns: docstring for behaviour that no longer existed. None was
# registered with @mcp.tool(), so none cost a session anything on the wire; they
# were 536 lines of weight in a module 5x over the 600-LOC block.
#
# Their rationale (what was measured, why each was retired, and the rollback
# sha) is preserved in the commit that removed them and in the history of the
# banners it deleted. `_phdb_post` survives just above: it is LIVE, called by
# dissolve() and atom().
# ---------------------------------------------------------------------------


def _phdb_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a typed-write/declare payload to phdb's HTTP route; structured result.

    Never raises across the verb boundary — an unreachable phdb or a non-200
    becomes {"ok": False, "error": ...} so dissolve halts before deleting.

    Strangled concerns route to their sovereign star over Hades instead:
    entity writes (``/write/entity``) call ``harmonia_write_entity_typed``
    when ``HADES_URL`` is configured — same payload, same result contract.
    Atom emits (``/emit``) call Terpsichore's ``fleet_emit`` (C1), the last
    route that kept the retired monolith's :8101 surface load-bearing.
    """
    if endpoint == "/write/entity" and HADES_URL:
        from vault_mcp.hades_client import write_entity_typed

        return write_entity_typed(
            payload, url=f"{HADES_URL}/", token=HADES_TOKEN
        )

    if endpoint == "/write/document" and HADES_URL:
        from vault_mcp.hades_client import write_document

        return write_document(payload, url=f"{HADES_URL}/", token=HADES_TOKEN)

    if endpoint == "/emit" and HADES_URL:
        from vault_mcp.hades_client import emit_session_event

        return emit_session_event(
            payload, url=f"{HADES_URL}/", token=HADES_TOKEN
        )

    import httpx

    try:
        resp = httpx.post(
            f"{PHDB_HTTP_URL}{endpoint}", json=payload, timeout=30.0
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"phdb unreachable: {e}"}
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text)
        except ValueError, KeyError, AttributeError:
            detail = resp.text
        return {"ok": False, "error": f"phdb HTTP {resp.status_code}: {detail}"}
    return resp.json()


# ---------------------------------------------------------------------------
# vault-mcp v2 — Convention Gate write tools (schema-driven, provenance-stamped)
# ---------------------------------------------------------------------------


def _prune_empty_parents(child_path: Path, root: Path) -> list[str]:
    """Walk up from child_path's parent, removing empty dirs until root."""
    pruned: list[str] = []
    d = child_path.parent
    while d != root and d.is_relative_to(root):
        try:
            if not any(d.iterdir()):
                d.rmdir()
                pruned.append(str(d.relative_to(root)))
            else:
                break
        except OSError:
            break
        d = d.parent
    return pruned


_gate: ConventionGate | None = None
_compute_receiver: ComputeReceiver | None = None


def _get_gate() -> ConventionGate:
    """Lazily build the Convention Gate from VAULT_MCP_SCHEMA + the Obsidian REST API.

    Writes go through the REST API (HTTP on loopback) rather than the CLI: as a
    session-0 service vault-mcp cannot reach the desktop (session-1) Obsidian via
    the CLI's same-session IPC, but the REST API crosses that boundary.
    """
    global _gate
    if _gate is None:
        from vault_mcp.gate import ConventionGate
        from vault_mcp.rest_client import RestNoteIO
        from vault_mcp.schema import load_schema

        _gate = ConventionGate(load_schema(), RestNoteIO(_get_rest_client()))
    return _gate


# ---------------------------------------------------------------------------
# Single-writer git committer (VG initiative — vault-mcp owns vault git)
# ---------------------------------------------------------------------------
_committer: GitCommitter | None = None


def _get_committer() -> GitCommitter:
    """Lazily build the git committer from VAULT_MCP_GIT_* env, rooted at the vault.

    Commit + push default OFF (flip VAULT_MCP_GIT_COMMIT=1 / VAULT_MCP_GIT_PUSH=1
    once proven), so this code is inert until the flag is set.
    """
    global _committer
    if _committer is None:
        from vault_mcp.gitops import committer_from_env

        _committer = committer_from_env(VAULT_PATH)
    return _committer


def _commit_write(
    result: dict[str, Any],
    op: str,
    commit_message: str | None = None,
    *,
    is_delete: bool = False,
) -> dict[str, Any]:
    """Commit a successful Gate write and attach ``commit_sha`` (the checkpoint handshake).

    Fail-safe: a commit failure leaves the write intact, sha None.
    A caller-supplied ``commit_message`` is used verbatim; otherwise a
    ``vault: {op} {path}`` fallback is generated. The in-flight markers let the
    scheduled sweep defer rather than race this per-transaction commit.
    """
    if not result.get("ok"):
        return result
    path = result.get("path")
    if not isinstance(path, str) or not path:
        return result
    committer = _get_committer()
    message = commit_message or f"vault: {op} {path}"
    committer.begin_write()
    try:
        result["commit_sha"] = committer.commit_paths(
            [path], message, wait_for_create=not is_delete
        )
    finally:
        committer.end_write()
    return result


def _start_sweep_scheduler() -> None:
    """Start the whole-tree sweep daemon (captures human Obsidian edits).

    No-op unless VAULT_MCP_GIT_COMMIT=1. Interval via VAULT_MCP_GIT_SWEEP_SECONDS
    (default 3600s). Sweep commits are checkpoint-silent by design.
    """
    import time as _time
    from datetime import datetime

    log = logging.getLogger(__name__)
    committer = _get_committer()
    if not committer.enabled:
        print(
            "vault-mcp: git sweep disabled (VAULT_MCP_GIT_COMMIT != 1)",
            file=sys.stderr,
        )
        return
    interval = int(os.environ.get("VAULT_MCP_GIT_SWEEP_SECONDS", "3600"))

    def _loop() -> None:
        while True:
            _time.sleep(interval)
            try:
                stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
                res = committer.sweep_commit(f"vault: periodic sweep {stamp}")
                if res.get("committed"):
                    log.info(
                        "git sweep committed %s (pushed=%s)",
                        res.get("sha"),
                        res.get("pushed"),
                    )
            except Exception:
                log.exception("git sweep tick failed")

    threading.Thread(target=_loop, name="vault-git-sweep", daemon=True).start()
    push_state = "on" if committer.push_enabled else "off"
    print(
        f"vault-mcp: git sweep every {interval}s (push={push_state})",
        file=sys.stderr,
    )


def _load_templates() -> dict[str, str]:
    d = os.environ.get("VAULT_MCP_TEMPLATES")
    if not d:
        return {}
    base = Path(d)
    if not base.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in base.glob("*.md")}


def _get_compute_receiver() -> ComputeReceiver:
    global _compute_receiver
    if _compute_receiver is None:
        from vault_mcp.compute import ComputeReceiver

        _compute_receiver = ComputeReceiver(_get_gate(), _load_templates())
    return _compute_receiver


_materializer: Materializer | None = None


def _get_materializer() -> Materializer:
    global _materializer
    if _materializer is None:
        from vault_mcp.lifecycle import Materializer

        _materializer = Materializer(_get_gate(), _load_templates())
    return _materializer


def _gate_error_envelope(exc: Exception) -> dict[str, Any]:
    """Map Gate/schema/IO exceptions to a structured tool error."""
    from vault_mcp.cli_client import ObsidianIOError
    from vault_mcp.gate import GateError
    from vault_mcp.schema import RouteError, SchemaError

    if isinstance(exc, SchemaError) and not isinstance(exc, RouteError):
        return {"ok": False, "error": "schema_unavailable", "detail": str(exc)}
    if isinstance(exc, RouteError):
        return {"ok": False, "error": "no_route", "detail": str(exc)}
    if isinstance(exc, GateError):
        return {"ok": False, "error": "rejected", "detail": str(exc)}
    if isinstance(exc, ObsidianIOError):
        return {"ok": False, "error": "io_error", "detail": str(exc)}
    raise exc


@mcp.tool()
def write_note(
    title: str,
    note_type: str | None = None,
    pillar: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
    mode: str = "upsert",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Create-or-update a vault note through the Convention Gate — the write surface.

    Resolves the target path from title + routing, then creates a missing note or
    updates an existing one (the caller need not know which). The Gate generates
    correct frontmatter, validates against the closed tag glossary and per-type
    rules, enforces write-protection, and stamps provenance. On update, body/tags/
    fields left null are untouched; a pre-existing invalid value in a field this
    write does not touch surfaces as a warning, not a rejection.

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing (e.g., 'note').
        pillar: Schema pillar used for routing (e.g., 'Knowledge').
        body: Markdown body. Null on update leaves the existing body untouched.
        tags: Tags; each must be in the closed glossary. Null on update = untouched.
        fields: Extra frontmatter fields (required / constrained values, etc.).
        actor: 'agent' (default) or 'human' — drives the provenance stamp.
        mode: 'upsert' (default), 'create' (refuse if exists), 'update' (refuse if missing).
        commit_message: Commit message recording this write; auto-generated when omitted.

    Returns:
        {"ok": True, "path", "frontmatter", "created", "warnings", ...} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.write_note(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags,
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
            mode=mode,
        )
        return _commit_write(
            result.to_dict(),
            "create" if result.created else "update",
            commit_message,
        )
    except Exception as exc:
        log.exception("write_note failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def delete(
    path: str, actor: str = "agent", commit_message: str | None = None
) -> dict[str, Any]:
    """Move a vault note to Obsidian's .trash/ (reversible) through the Gate.

    Enforces the same write-protection as a write — an agent cannot trash a
    voice-only, compute-only, fully-immutable, or body-immutable note. The note
    remains recoverable from Obsidian's trash.

    Returns:
        {"ok": True, "path", "deleted": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.delete(
            path, actor=Actor.HUMAN if actor == "human" else Actor.AGENT
        )
        committed = _commit_write(
            result, "delete", commit_message, is_delete=True
        )
        if committed.get("ok"):
            abs_path = (VAULT_PATH / path).resolve()
            committed["dirs_pruned"] = _prune_empty_parents(
                abs_path, VAULT_PATH
            )
        return committed
    except Exception as exc:
        log.exception("delete failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def move_note(
    src: str,
    dst: str,
    actor: str = "agent",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Move a vault note from src to dst, preserving content and frontmatter.

    Reads the source note, writes it to the destination path, then deletes the
    source. Checks write-protection on both directories. Does NOT update
    backlinks — use ``backlinks_to`` to enumerate inbound references and update
    them separately via ``write_note``.

    Args:
        src: Vault-relative source path (e.g. 'Software/README.md').
        dst: Vault-relative destination path (e.g. 'References/README.md').
        actor: 'agent' (default) or 'human'.
        commit_message: Optional git commit message.

    Returns:
        {"ok": True, "src", "dst", "moved": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        result = gate.move_note(
            src,
            dst,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
        committed = _commit_write(result, "move", commit_message)
        if committed.get("ok"):
            abs_src = (VAULT_PATH / src).resolve()
            committed["dirs_pruned"] = _prune_empty_parents(abs_src, VAULT_PATH)
        return committed
    except Exception as exc:
        log.exception("move_note failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def lint(
    title: str = "",
    note_type: str | None = None,
    pillar: str | None = None,
    body: str = "",
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
) -> dict[str, Any]:
    """Dry-run a note payload through the Convention Gate's validator — no write.

    Runs the full validation pipeline and returns every finding at once
    (collect-all), so a caller can assemble -> lint -> fix -> write without
    touching disk. Each finding carries a code, the offending field/value, a
    message, and a severity ('error' blocks a write; 'warning' is advisory).

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing.
        pillar: Schema pillar used for routing.
        body: Markdown body.
        tags: Tags to validate against the closed glossary.
        fields: Extra frontmatter fields (e.g. required/constrained values).
        actor: 'agent' (default) or 'human'.

    Returns:
        {"ok": bool, "errors": [...], "warnings": [...]} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = _get_gate()
        return gate.lint_payload(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags or [],
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
    except Exception as exc:
        log.exception("lint failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def list_types() -> dict[str, Any]:
    """List every schema @type with its write-mode and required-field summary.

    Returns:
        {"ok": True, "types": [{"name", "write_mode", "required", "body_empty", "atom_slug"}, ...]}

    """
    try:
        return {"ok": True, "types": _get_gate()._schema.list_types()}
    except Exception as exc:
        log.exception("list_types failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def list_tags() -> dict[str, Any]:
    """List the closed tag glossary, grouped by prefix.

    Returns:
        {"ok": True, "tags": {"<prefix>": ["<prefix>/<leaf>", ...], ...}}

    """
    try:
        return {"ok": True, "tags": _get_gate()._schema.list_tags()}
    except Exception as exc:
        log.exception("list_tags failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def list_keys() -> dict[str, Any]:
    """List the global union of frontmatter property keys across all @types.

    Returns:
        {"ok": True, "keys": ["<key>", ...]}

    """
    try:
        return {"ok": True, "keys": _get_gate()._schema.list_keys()}
    except Exception as exc:
        log.exception("list_keys failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def query(note_type: str) -> dict[str, Any]:
    """Describe a note type's authoring contract — the spec sheet for a @type.

    Returns the fields a caller may set, their value constraints and formats,
    freeform fields, where the type routes (including discriminators), and body
    guidance (schema ``body_guidance`` plus a Templater template pointer when one
    exists). Use it to assemble a compliant note without reading governance prose.

    Args:
        note_type: The schema @type to describe (e.g., 'Person').

    Returns:
        {"ok": True, ...spec...} or {"ok": False, "error": "unknown_type"}.

    """
    try:
        spec = _get_gate()._schema.describe_type(note_type)
        if spec is None:
            return {
                "ok": False,
                "error": "unknown_type",
                "detail": f"no @type {note_type!r} in the schema",
            }
        templates = (
            _load_templates()
        )  # the one vault-read: locate a matching template
        spec["body_template"] = note_type if note_type in templates else None
        return {"ok": True, **spec}
    except Exception as exc:
        log.exception("query failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def audit(
    directory: str = "", resolve: bool = False, all_dirs: bool = False
) -> dict[str, Any]:
    """Scan a vault directory for schema drift; optionally auto-correct it.

    resolve=False reports every drifted note (missing required fields, unknown
    tags, deprecated/dead keys, off-vocabulary values, routing misplacement,
    untyped notes) and writes nothing. resolve=True applies deterministic
    corrections (key renames, dead-key drops, status repairs, routing
    relocation), re-lints the corrected note, and rewrites it only when clean —
    uncorrectable faults are reported for manual handling, never guessed.
    Healed writes land through the Gate (Obsidian) and are captured by the
    git-sweep daemon.

    Args:
        directory: Pillar/directory to scan (e.g., 'Inbox'). Ignored if all_dirs.
        resolve: False = report only; True = heal what is deterministically fixable.
        all_dirs: Scan the whole vault instead of a single directory.

    Returns:
        {"ok": True, "scanned", "drifted", "corrected", "notes": [...]} or an error.

    """
    try:
        return _get_gate().audit(directory, resolve=resolve, all_dirs=all_dirs)
    except Exception as exc:
        log.exception("audit failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def dissolve(
    path: str,
    plan_slug: str,
    rationale: str,
    declared_by: str = "code",
    repo: str = "vault",
) -> dict[str, Any]:
    """Dissolve a vault note into phdb and remove the original (VDV F3).

    Writes the note's full prose verbatim to phdb's documents table (and, for a
    ``note_type: Plan``, its metadata to plans) over HTTP, declares a dissolution
    wave, then deletes the vault file — ordering is write -> verify -> declare ->
    delete, so a failure never deletes the file and a re-run is idempotent (phdb
    dedups on content). The body is passed through verbatim (no fence-extraction).

    Args:
        path: Vault-relative path to the note (e.g. 'System/Plans/Foo.md').
        plan_slug: The dissolution wave's driving plan identifier.
        rationale: Why this content is dissolved (recorded on the wave).
        declared_by: 'code' / 'cowork' / 'backfill'.
        repo: Registry repo scope (default 'vault').

    Returns:
        {"ok": True, "written": [...], "dissolution_id", "deleted": True}, or a
        structured error naming the failing stage (the file is left in place).

    """
    from vault_mcp.lifecycle_verbs import dissolve_note

    abs_path = (VAULT_PATH / path).resolve()
    try:
        abs_path.relative_to(VAULT_PATH.resolve())
    except ValueError:
        return {"ok": False, "error": "outside_vault", "detail": path}
    if not abs_path.is_file():
        return {"ok": False, "error": "not_found", "detail": str(abs_path)}

    raw_text = abs_path.read_text(encoding="utf-8")
    res = dissolve_note(
        source_path=str(abs_path),
        raw_text=raw_text,
        file_path=abs_path.name,
        vault_rel_path=path,
        plan_slug=plan_slug,
        rationale=rationale,
        post=_phdb_post,
        delete_file=abs_path.unlink,
        declared_by=declared_by,
        repo=repo,
    )
    # Commit the deletion through the single writer (the file is already gone, so
    # skip the disk-landing poll); attach the sha for the checkpoint handshake.
    if res.get("ok"):
        committer = _get_committer()
        committer.begin_write()
        try:
            res["commit_sha"] = committer.commit_paths(
                [path], f"vault: dissolve {path}", wait_for_create=False
            )
        finally:
            committer.end_write()
        res["dirs_pruned"] = _prune_empty_parents(abs_path, VAULT_PATH)
    return res


def _vault_md_paths() -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` in the vault."""
    root = VAULT_PATH.resolve()
    for p in sorted(root.rglob("*.md")):
        yield p.relative_to(root).as_posix()


@mcp.tool()
def dissolve_sweep(
    dry_run: bool = True,
    limit: int | None = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Bulk-carve the vault into Calliope under the scope policy (C6).

    Takes the one-note ``dissolve`` (write -> verify -> delete, idempotent,
    fail-safe) to a whole-vault sweep gated by a scope policy — governance /
    System stays; the named prose/records/entities pillars dissolve.

    **DRY-RUN by default.** A dry run classifies every ``.md`` and reports what
    *would* dissolve, touching nothing. A LIVE sweep (``dry_run=False``) is
    DESTRUCTIVE — it deletes each dissolvable file after a verified Calliope
    write — and additionally requires ``confirm="DISSOLVE"``.

    Pre-flight refuses the sweep when ``HADES_URL`` is unset (the documented
    trap: the dissolve leg would fall back to the retired phdb
    ``/write/document`` and halt). The scope policy is CONFIG
    (``carve_policy.DEFAULT_SCOPE_POLICY``); the exact pillar cut and the
    Obsidian vestigial-vs-retired end-state are OPEN Rob-decisions, surfaced in
    the report's ``open_decisions``. ``limit`` caps acted-on files (bounded
    live batches).

    Returns the structured :class:`~vault_mcp.carve.CarveReport`.
    """
    from vault_mcp.carve import bulk_carve, carve_preflight
    from vault_mcp.carve_policy import DEFAULT_SCOPE_POLICY

    pf = carve_preflight(hades_url=HADES_URL)
    if not pf.ok:
        return bulk_carve(
            list_files=_vault_md_paths,
            dissolve_one=lambda _p: {"ok": False, "error": "preflight"},
            policy=DEFAULT_SCOPE_POLICY,
            dry_run=dry_run,
            preflight=pf,
            limit=limit,
        ).to_dict()

    if not dry_run and confirm != "DISSOLVE":
        return {
            "ok": False,
            "error": "confirm_required",
            "detail": (
                "A live sweep DELETES vault files after writing them to "
                "Calliope. Re-run with dry_run=False and confirm='DISSOLVE'."
            ),
        }

    def _dissolve_one(rel_path: str) -> dict[str, Any]:
        return dissolve(
            path=rel_path,
            plan_slug="vault-carve",
            rationale="C6 bulk vault carve into Calliope",
        )

    report = bulk_carve(
        list_files=_vault_md_paths,
        dissolve_one=_dissolve_one,
        policy=DEFAULT_SCOPE_POLICY,
        dry_run=dry_run,
        preflight=pf,
        limit=limit,
    )
    return report.to_dict()


#: F4 — {source_path: stored mtime}, populated by each sweep. Lets the periodic
#: reconcile skip the store read for plans whose disk clock has not advanced.
_PLAN_CLOCKS: dict[str, str] = {}


def _plan_md_paths(directory: str) -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` under *directory*."""
    root = VAULT_PATH.resolve()
    base = (root / directory).resolve()
    if not base.is_dir():
        return
    if not base.is_relative_to(root):  # never escape the vault
        return
    for p in sorted(base.rglob("*.md")):
        yield p.relative_to(root).as_posix()


def _read_stored_copy(source_path: str) -> StoredCopy | None:
    """Newest stored copy for *source_path*, as the freshness engine's projection."""
    from vault_mcp.hades_client import read_document_by_source_path
    from vault_mcp.plan_freshness import StoredCopy

    res = read_document_by_source_path(
        source_path, url=f"{HADES_URL}/", token=HADES_TOKEN
    )
    if res.get("ok") is False:
        raise RuntimeError(str(res.get("error", "read_documents failed")))
    docs = res.get("documents") or []
    if not docs:
        return None
    newest = docs[0]  # Calliope answers newest-first
    body = newest.get("body_text") or ""
    stored_mtime = newest.get("mtime")
    return StoredCopy(
        source_path=source_path,
        raw_hash=str(newest.get("raw_hash") or ""),
        body_bytes=len(body.encode("utf-8")),
        mtime=str(stored_mtime) if stored_mtime else None,
    )


def _build_plan_payload(source_path: str, raw_text: str) -> dict[str, Any]:
    """Build the ``write_document`` payload for one plan.

    This is the seam F2 (``mtime`` from the source file) and F3 (``schema_type``
    from the source's ``note_type``) extend — the comparison engine and the verb
    are untouched by either.
    """
    from vault_mcp.parsers import parse_frontmatter, strip_frontmatter
    from vault_mcp.plan_freshness import file_mtime_iso
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    frontmatter = parse_frontmatter(raw_text)
    body = strip_frontmatter(raw_text)
    file_path = str(VAULT_PATH.resolve() / source_path)
    payloads = note_to_payloads(
        frontmatter,
        body,
        source_path,
        file_path=file_path,
        source_mtime=file_mtime_iso(file_path),
    )
    doc = next(
        (p["payload"] for p in payloads if p["endpoint"] == DOC_ENDPOINT), None
    )
    if doc is None:
        msg = f"{source_path} does not translate to a document payload"
        raise ValueError(msg)
    return doc


@mcp.tool()
def plan_freshness(
    refresh: bool = False,
    directory: str = "System/Pantheon/WBS",
    source_path: str | None = None,
    include_missing: bool = False,
    backfill: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Report (and optionally repair) drift between vault plans and their stored copies.

    A master-plan lives in the vault AND as a copy in Calliope's document store,
    which ``athena orchestrate_plan`` reads by reference. The store versions
    insert-only, so a re-write *would* land a new version — but nothing ever
    issued one, and stored copies are frozen at first write. Measured
    2026-08-10: the Aglaia plan's copy was 22 days and 18,584 bytes behind disk,
    which is why its A21/A22 amendment never reached the board.

    **REPORT-ONLY by default.** ``refresh=True`` writes the vault's current bytes
    to the store as a new version for each drifted or missing plan. Unlike
    ``dissolve_sweep`` this is **not destructive and takes no confirm token**: it
    never creates, modifies, moves or deletes a vault file under any argument,
    and the store is insert-only, so a refresh appends and converges. Direction
    is an invariant — vault to store, never the reverse.

    A plan already current is never written. One plan's failure is reported
    against that plan and the sweep continues.

    ``refresh`` repairs **drift only**. The store is not a mirror of the vault —
    ``carve_policy`` keeps ``System/`` in the vault, so a plan reaches the store
    only when something explicitly puts it there. Measured 2026-08-11: 173 of
    175 WBS plans have no stored copy at all, so a refresh that also populated
    would push ~10MB of prose into the store as a side effect of a freshness
    repair. Populating is a separate, deliberate act: ``include_missing=True``.

    Args:
        refresh: Write drifted copies (default False — report only).
        directory: Vault-relative directory to sweep.
        source_path: Sweep exactly this one plan instead of the directory.
        include_missing: Also write plans with no stored copy (populate).
        backfill: Reconcile provenance (mtime / schema_type) on plans whose
            body is already current, without minting a new body version.
        limit: Cap the number of plans acted on.

    Returns:
        The sweep report: ``{ok, dry_run, directory, scanned, refreshed,
        counts, records[]}``.

    """
    return _plan_sweep(
        refresh=refresh,
        directory=directory,
        source_path=source_path,
        include_missing=include_missing,
        backfill=backfill,
        limit=limit,
    ).to_dict()


def _plan_sweep(
    *,
    refresh: bool = False,
    directory: str = "System/Pantheon/WBS",
    source_path: str | None = None,
    include_missing: bool = False,
    backfill: bool = False,
    cheap_gate: bool = False,
    limit: int | None = None,
) -> PlanSweepReport:
    """Configure and run one sweep — shared by the verb and F4's reconcile."""
    from vault_mcp.carve import carve_preflight
    from vault_mcp.hades_client import write_document
    from vault_mcp.plan_freshness import is_probably_stale, sweep_plans

    pf = carve_preflight(hades_url=HADES_URL)

    def _list(d: str) -> Iterator[str]:
        if source_path is not None:
            yield source_path
        else:
            yield from _plan_md_paths(d)

    def _read_vault(p: str) -> str:
        return (VAULT_PATH.resolve() / p).read_text(encoding="utf-8")

    def _write(payload: dict[str, Any]) -> dict[str, Any]:
        return write_document(payload, url=f"{HADES_URL}/", token=HADES_TOKEN)

    root = str(VAULT_PATH.resolve())

    def _gate(p: str) -> bool:
        # A path we have never resolved must be checked properly; the sweep
        # then caches its clock for later ticks.
        if p not in _PLAN_CLOCKS:
            return True
        return is_probably_stale(
            p, vault_root=root, stored_mtime=_PLAN_CLOCKS[p]
        )

    gate: Callable[[str], bool] | None = _gate if cheap_gate else None

    report = sweep_plans(
        list_files=_list,
        read_vault=_read_vault,
        read_stored=_read_stored_copy,
        build_payload=_build_plan_payload,
        write_stored=_write,
        directory=directory,
        refresh=refresh,
        include_missing=include_missing,
        backfill=backfill,
        cheap_gate=gate,
        limit=limit,
        preflight=pf.to_dict(),
    )
    # Cache each plan's stored clock so later reconcile ticks can skip the
    # store read entirely. This cache is the ONLY thing that makes the
    # periodic reconcile affordable: calliope has no index read by
    # source_path — read_documents always materialises the body — so without
    # it every tick would pull ~10MB.
    for rec in report.records:
        if not rec.gated and rec.stored_mtime is not None:
            _PLAN_CLOCKS[rec.source_path] = rec.stored_mtime
        elif rec.refreshed or rec.backfilled:
            _PLAN_CLOCKS.pop(rec.source_path, None)
    return report


def _read_dissolved_row(table: str, row_id: int) -> dict[str, Any]:
    """Read a dissolved row for materialize — from Calliope, not the dead phdb (C6).

    Reverse-symmetry: dissolve writes documents to Calliope (over Hades when
    ``HADES_URL`` is set), so un-dissolve READS from Calliope too — the retired
    phdb is never the go-forward source. When ``HADES_URL`` is set, a
    ``documents`` read routes to Calliope's ``read_documents`` and the row is
    normalized to the phdb documents-row shape the mapper expects. With
    ``HADES_URL`` unset (or for the legacy ``plans`` table, which has no Calliope
    home), it falls back to the phdb HTTP read. Returns
    ``{ok, row, paired_body}`` or ``{ok: False, error, detail}``.
    """
    if table == "documents" and HADES_URL:
        from vault_mcp.hades_client import read_document
        from vault_mcp.translator import calliope_document_to_row

        res = read_document(row_id, url=f"{HADES_URL}/", token=HADES_TOKEN)
        if res.get("ok") is False:
            return {
                "ok": False,
                "error": "calliope_read_failed",
                "detail": str(res.get("error")),
            }
        docs = res.get("documents") or []
        if not docs:
            return {
                "ok": False,
                "error": "row_not_found",
                "detail": f"documents#{row_id} (Calliope)",
            }
        return {
            "ok": True,
            "row": calliope_document_to_row(docs[0]),
            "paired_body": None,
        }

    import httpx

    try:
        resp = httpx.get(
            f"{PHDB_HTTP_URL}/read/{table}", params={"id": row_id}, timeout=30.0
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"phdb unreachable: {e}"}
    if resp.status_code == 404:
        return {
            "ok": False,
            "error": "row_not_found",
            "detail": f"{table}#{row_id}",
        }
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"phdb HTTP {resp.status_code}",
            "detail": resp.text,
        }
    data = resp.json()
    return {
        "ok": True,
        "row": data.get("row") or {},
        "paired_body": data.get("paired_body"),
    }


@mcp.tool()
def materialize(table: str, row_id: int) -> dict[str, Any]:
    """Materialize a dissolved row back into a Convention-Gate note (VDV F3).

    The inverse of ``dissolve``: reads a typed row from the go-forward prose
    store — **Calliope** (C6 reverse-symmetry), not the retired phdb — and writes
    it back through the shared ``Materializer`` with ``mode=COMPUTE``, the
    sanctioned path for materialize-only ``@type``s (e.g. ``Plan``) that ordinary
    agent-create rejects. The target directory is resolved from the note_type's
    schema route. Returns the Gate write result or a structured error.

    Shares the ``Materializer`` backend with ``compute_receiver`` (which renders a
    fresh compute payload); this verb builds the payload from a dissolved row.
    """
    from vault_mcp.translator import row_to_payload

    if table not in ("documents", "plans"):
        return {"ok": False, "error": "bad_table", "detail": table}

    # 1. Read the row from Calliope (documents) / phdb fallback (plans).
    read = _read_dissolved_row(table, row_id)
    if not read.get("ok"):
        return read
    row = read["row"]
    paired_body: str | None = read.get("paired_body")

    note_type = (
        "Plan"
        if table == "plans"
        else (row.get("schema_type") or "DigitalDocument")
    )

    try:
        # 2. Resolve the target directory from the note_type's schema route.
        directory = _get_gate()._schema.resolve_directory(note_type)
        # 3. Build the Materializer payload and write via mode=COMPUTE.
        payload = row_to_payload(
            row, table, directory=directory, paired_body=paired_body
        )
        result = _get_materializer().materialize(payload)
        return _commit_write(result.to_dict(), "materialize")
    except Exception as exc:
        log.exception("materialize failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def compute_receive(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Render a structured compute payload into an ai-computed vault note.

    Accepts a payload (template, title, directory, data, frontmatter) from a
    periodic compute job, renders it through a named template (pure
    substitution, no LLM), and writes it via the Gate's compute-only path.

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.compute import ComputePayloadError

    try:
        result = _get_compute_receiver().receive(payload, created=created)
        return _commit_write(result.to_dict(), "compute")
    except ComputePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:
        log.exception("compute_receive failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def compute_receiver(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Receive a compute result and write it as a durable note (Compute Receiver).

    Accepts a structured payload (title, note_type, directory, body, frontmatter,
    optional template/data) and writes it through the Gate with mode=COMPUTE —
    the sanctioned path for materialize-only @types, which the ordinary
    agent-create path rejects. Rendering is deterministic (no LLM): the same
    payload yields a byte-identical note.

    (Renamed from ``materialize`` — that verb now belongs to the VDV dissolve
    inverse, which rehydrates a dissolved phdb row back into a note.)

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.lifecycle import MaterializePayloadError

    try:
        result = _get_materializer().materialize(payload, created=created)
        return _commit_write(result.to_dict(), "compute")
    except MaterializePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:
        log.exception("compute_receiver failed")
        return _gate_error_envelope(exc)


@mcp.tool()
def atom(
    atom_type: str,
    payload: dict[str, Any],
    ts: str | None = None,
) -> dict[str, Any]:
    """Emit an AI-observed atom (decision/reversal/tension/pushback) to phdb.

    Records the atom directly into phdb's session_events table, skipping the
    vault filesystem entirely (lifecycle verb). The payload contract is
    per-type. When phdb is not configured (PHDB_DB_PATH unset or the DB file is
    missing), returns a structured 'phdb_unavailable' error rather than failing.

    Args:
        atom_type: One of 'decision', 'reversal', 'tension', 'pushback'.
        payload: Type-specific fields — decision: {polarity, reversed_by?};
            reversal: {reverses, position_before, position_after, trigger?,
            captured_when?}; tension: {position_a, position_b, held_since?,
            resolution?, captured_when?}; pushback: {from, challenge, response,
            position_changed?, captured_when?}.
        ts: Optional ISO-8601 event time; defaults to the payload's
            captured_when when present.

    Returns:
        {"ok": True, "atom_type", "event_id", "born_token", "ts"} or a
        structured error. Exactly one identifier is populated: ``born_token``
        on the Terpsichore fleet plane (the C1 default), ``event_id`` on the
        legacy phdb route when ``HADES_URL`` is unset.

    """
    from vault_mcp.phdb_client import (
        AtomError,
        PhdbUnavailableError,
        emit_atom,
    )

    try:
        result = emit_atom(atom_type, payload, ts=ts, post=_phdb_post)
        return result.to_dict()
    except AtomError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except PhdbUnavailableError as exc:
        return {"ok": False, "error": "phdb_unavailable", "detail": str(exc)}


# ---------------------------------------------------------------------------
# VERB REGISTRATION — imported for SIDE EFFECT, at the foot of the module.
#
# Each module below does `from vault_mcp.server import mcp, ...` at its own top.
# That is a cycle only if it runs too early: by this point every name they close
# over — the FastMCP instance, the config constants, the lazy accessors — is
# already bound, so the partially-initialised module in sys.modules is complete
# enough to import from. Moving these imports to the top of the file would break
# that and is the one edit to make carefully.
#
# A dropped import here does not raise. It silently unregisters that module's
# verbs and the server still starts, which is why
# tests/test_server.py::test_every_verb_is_registered pins the count at 54.
# ---------------------------------------------------------------------------
import vault_mcp.verbs_bases  # noqa: E402  (registration side effect)
import vault_mcp.verbs_query  # noqa: E402  (registration side effect)
import vault_mcp.verbs_rest  # noqa: E402, F401  (registration side effect)


def main() -> None:
    """Parse CLI args and run the MCP server over the chosen transport."""
    import argparse

    parser = argparse.ArgumentParser(description="vault-mcp MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("VAULT_MCP_HOST", "127.0.0.1"),
        help=(
            "Bind host for HTTP transports (env VAULT_MCP_HOST; default "
            "127.0.0.1 — set VAULT_MCP_HOST=0.0.0.0 explicitly to bind all "
            "interfaces)"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8100")),
        help="Bind port for HTTP transports (env PORT; default 8100)",
    )
    args = parser.parse_args()

    watch_status = "watch=on" if WATCH_ENABLED else "watch=off"
    rest_status = "rest=off" if REST_DISABLE else f"rest={REST_URL}"
    print(
        f"vault-mcp: vault={VAULT_PATH}, ttl={TTL_SECONDS}s, "
        f"{watch_status}, {rest_status}, "
        f"transport={args.transport}",
        file=sys.stderr,
    )

    if args.transport != "stdio":
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        print(
            f"vault-mcp: listening on {args.host}:{args.port}", file=sys.stderr
        )

    _start_sweep_scheduler()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
