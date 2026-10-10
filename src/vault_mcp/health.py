# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Operational health surface for vault-mcp (born-with).

Implements the universal operational contract ({live, ready, metrics}) from the
``stellar_core`` SDK's op-contract so this star is reachable for health over its
native transport — the operator's 3am check and Nyx's telemetry source both read it.

MCP star: exposes health via an introspection adapter (McpOpsAdapter).
Replace the static ``current_health`` body with real liveness/readiness checks.
"""

from __future__ import annotations

from stellar_core import (
    HealthState,
    McpOpsAdapter,
)


def current_health() -> HealthState:
    """Return this star's {live, ready, metrics} state.

    Stub: reports healthy with no metrics. Wire real dependency probes (DB,
    upstream seams, queue depth) into ``ready`` and ``metrics`` as the star grows.
    """
    return HealthState(live=True, ready=True, metrics={})


def ops_adapter() -> McpOpsAdapter:
    """Return the MCP ops adapter exposing introspect() over the current health."""
    return McpOpsAdapter(current_health())
