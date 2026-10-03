# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Oneiroi memory-emission hook for vault-mcp (born-with stub).

Stars emit memory atoms to Oneiroi — the constellation's memory plane — so that
runtime experience is captured for later recall. This module defines the single
emission point; the real transport is wired when the Oneiroi plane lands.
"""

from __future__ import annotations

from typing import Any


def emit_memory(event: str, payload: dict[str, Any] | None = None) -> None:
    """Emit a memory atom to Oneiroi.

    Stub: a no-op until the Oneiroi plane is live. Call this at the points a
    star wants to remember (a decision, an anomaly, a milestone); the transport
    wiring is additive and does not change this signature.

    Args:
        event: Short event slug identifying what happened.
        payload: Optional structured context for the memory atom.

    """
    # Wiring point: publish {star, event, payload, ts} to the memory plane.
    _ = (event, payload)
