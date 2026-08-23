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

import logging
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from vault_mcp.cli_client import ObsidianCLI
    from vault_mcp.compute import ComputeReceiver
    from vault_mcp.gate import ConventionGate
    from vault_mcp.gitops import GitCommitter
    from vault_mcp.lifecycle import Materializer
    from vault_mcp.rest_client import ObsidianRESTClient
    from vault_mcp.subscriptions import SubscriptionManager

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


# Subscription / SubscriptionManager MOVED to vault_mcp/subscriptions.py
# (vault-mcp#5294). `_get_sub_manager()` below imports them lazily.


_sub_manager: SubscriptionManager | None = None


def _get_sub_manager() -> SubscriptionManager:
    # DEFERRED: subscriptions.py imports this module, so a module-scope
    # import would be a hard cycle. Same pattern as the other lazy accessors.
    from vault_mcp.subscriptions import SubscriptionManager

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

    # DEFERRED on purpose: _plan_sweep lives in verbs_plan, which this module
    # registers at its own foot. A module-scope import here would be the cycle
    # the registration pattern exists to avoid; by call time it is loaded.
    from vault_mcp.verbs_plan import _plan_sweep

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


# _phdb_post MOVED to vault_mcp/phdb_client.py (vault-mcp#5294) — that
# module's own docstring already described it as living there.


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


# MOVED to vault_mcp/verbs_write.py (vault-mcp#5294).


# MOVED to vault_mcp/verbs_dissolve.py (vault-mcp#5294).


#: F4 — {source_path: stored mtime}, populated by each sweep. Lets the periodic
# MOVED to vault_mcp/verbs_plan.py (vault-mcp#5294).


# MOVED to vault_mcp/verbs_compute.py (vault-mcp#5294).


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
import vault_mcp.verbs_compute  # noqa: E402  (registration side effect)
import vault_mcp.verbs_dissolve  # noqa: E402  (registration side effect)
import vault_mcp.verbs_plan  # noqa: E402  (registration side effect)
import vault_mcp.verbs_query  # noqa: E402  (registration side effect)
import vault_mcp.verbs_rest  # noqa: E402  (registration side effect)
import vault_mcp.verbs_write  # noqa: E402, F401  (registration side effect)


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
