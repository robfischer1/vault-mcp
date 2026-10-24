# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Operational health surface for vault-mcp (born-with).

The universal operational contract — {live, ready, metrics} — so this star is
reachable for health over its native transport: the operator's 3am check and
Nyx's heartbeat both read it. Local and stdlib-only: the shape came from the
stellar_core SDK until that SDK retired (2026-10-18), and three fields do not
need a dependency.

Replace the static ``current_health`` body with real liveness/readiness checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: A metric value as the heartbeat wire carries it: open, heterogeneous.
MetricValue = int | float | str | bool


@dataclass(frozen=True)
class HealthState:
    """The {live, ready, metrics} contract value."""

    live: bool
    ready: bool
    metrics: dict[str, MetricValue] = field(default_factory=dict)

    def introspect(self) -> dict[str, object]:
        """Return {live, ready, metrics} as a JSON-serialisable dict."""
        return {
            "live": self.live,
            "ready": self.ready,
            "metrics": dict(self.metrics),
        }


def current_health() -> HealthState:
    """Return this star's {live, ready, metrics} state.

    Stub: reports healthy with no metrics. Wire real dependency probes (DB,
    upstream seams, queue depth) into ``ready`` and ``metrics`` as the star grows.
    """
    return HealthState(live=True, ready=True)
