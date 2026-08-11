"""The automatic re-dissolve trigger (F4).

Calliope's document store versions insert-only on ``(source_path, raw_hash)``,
so a re-dissolve *would* land a new version — but nothing ever issued one. That
is the root cause the master-plan measured: a cache with no invalidation. F1
built the repair and F2 gave it a clock; this is what stops the drift recurring
without a human remembering to run it.

**Scheduled reconcile, not a watcher hook.** vault-mcp already runs a watchdog
observer for index invalidation, and hanging a network store-write off it was
the obvious move — but the watcher fires on *every* ``.md`` save, so a plan
edited over ten minutes would write ten versions, and an editor's
autosave would write dozens. The store's history would become a keystroke log.
F2's clock made the periodic check cheap (one string comparison per plan, no
body read), so polling is the shape that fits: it debounces by construction and
its cost does not scale with how fast Rob types.

The loop is deliberately two-stage, cheap gate then expensive test:

    every plan → is_probably_stale()      (one timestamp comparison)
                      ↓ only survivors
                 compare_plan()            (reads the stored body, hashes)
                      ↓ only drifted
                 write_document()

The gate may produce false "stale" (a touched-but-unchanged file); that costs
one wasted read. Only the hash decides a write, so it can never produce a bad
version. A run that changes nothing writes nothing.

**Transitional by design.** The master-plan flags that F9 may make re-dissolve
unnecessary altogether — once a plan *is* blocks in the store, editing it is the
write and there is nothing to reconcile. This module is deliberately small and
self-contained so that retiring it is a deletion, not an unpicking.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from vault_mcp.plan_freshness import PlanSweepReport

log = logging.getLogger(__name__)

#: How often the reconcile runs, in seconds. Generous on purpose — the defect
#: it prevents took 22 days to be noticed, so minutes-scale latency is ample.
DEFAULT_INTERVAL_SECONDS = 900


def reconcile_interval(env: dict[str, str] | None = None) -> int:
    """Resolve the reconcile interval from the environment."""
    src = os.environ if env is None else env
    raw = src.get("VAULT_MCP_PLAN_RECONCILE_SECONDS", "")
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return val if val > 0 else DEFAULT_INTERVAL_SECONDS


def reconcile_enabled(env: dict[str, str] | None = None) -> bool:
    """Is the automatic reconcile on? Default yes; ``0``/``false`` disables."""
    src = os.environ if env is None else env
    raw = src.get("VAULT_MCP_PLAN_RECONCILE", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def reconcile_once(
    sweep: Callable[[], PlanSweepReport],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Run one reconcile pass and report what it did — observably.

    ``sweep`` is injected (a configured :func:`plan_freshness.sweep_plans`
    call), so this is testable with no filesystem, no network and no clock.

    The trigger's observability is a success criterion, not a nicety: an
    automatic writer nobody can see is how the *next* silent-drift defect gets
    built. A pass that writes logs at INFO naming each plan; a pass that writes
    nothing logs at DEBUG, so a quiet system stays quiet.
    """
    try:
        report = sweep()
    except Exception as exc:  # the loop must survive one bad pass
        log.exception("plan-reconcile: pass failed")
        return {"ok": False, "error": str(exc), "refreshed": 0, "at": now}

    refreshed = [r for r in report.records if r.refreshed]
    errored = [r for r in report.records if r.error]

    if refreshed:
        for r in refreshed:
            log.info(
                "plan-reconcile: re-dissolved %s (%+d bytes)",
                r.source_path,
                r.delta_bytes,
            )
    else:
        log.debug(
            "plan-reconcile: no change (%d plans checked)", report.scanned
        )
    for r in errored:
        log.warning("plan-reconcile: %s failed: %s", r.source_path, r.error)

    return {
        "ok": True,
        "at": now,
        "scanned": report.scanned,
        "refreshed": len(refreshed),
        "errors": len(errored),
        "plans": [r.source_path for r in refreshed],
    }


def start_plan_reconcile(
    sweep: Callable[[], PlanSweepReport],
    *,
    interval: int | None = None,
    enabled: bool | None = None,
) -> threading.Thread | None:
    """Start the background reconcile loop; ``None`` when disabled.

    Daemon thread, so it never holds the server open on shutdown. One bad pass
    is logged and the loop continues — a transient store outage must not
    silently stop the trigger, which would restore exactly the "nothing ever
    re-writes" condition this exists to prevent.
    """
    on = reconcile_enabled() if enabled is None else enabled
    if not on:
        log.info("plan-reconcile: disabled by environment")
        return None

    every = interval if interval is not None else reconcile_interval()
    stop = threading.Event()

    def _loop() -> None:
        # Wait first: never write during server startup.
        while not stop.wait(every):
            reconcile_once(sweep)

    thread = threading.Thread(target=_loop, name="plan-reconcile", daemon=True)
    thread.start()
    log.info("plan-reconcile: started, every %ds", every)
    return thread
