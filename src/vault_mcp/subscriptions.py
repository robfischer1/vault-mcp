"""Live-update subscriptions — the handle registry and its change notifier.

Split out of server.py under vault-mcp#5294. NOT a registration module: these
are classes, not verbs. `subscribe_base` / `unsubscribe_base` live in
verbs_bases and reach the manager through server.py's `_get_sub_manager()`,
which stays there because it is a lazy singleton tests monkeypatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, cast

# Module-qualified, not name-imported: this reads `server._active_sessions` and
# calls `server._get_index()`, both of which tests rebind on the server module.
# server.py imports THIS module lazily (inside _get_sub_manager), so importing
# it here at module scope is not a cycle.
from vault_mcp import server
from vault_mcp.bases import execute_base as _execute_base_impl
from vault_mcp.bases import parse_file as _parse_file_impl

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.server.fastmcp import FastMCP

    from vault_mcp.bases import QueryResult

log = logging.getLogger(__name__)


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

        idx = server._get_index()

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
        for session in server._active_sessions:
            try:
                await session.send_notification(cast("Any", notification))
            except Exception:
                self.log.exception(
                    "send_notification failed; treating session as gone"
                )
                disconnected.append(session)

        for session in disconnected:
            server._active_sessions.discard(session)
