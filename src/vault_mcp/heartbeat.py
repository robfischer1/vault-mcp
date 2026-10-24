# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0


"""Heartbeat publisher — emits {live, ready, metrics} to redpanda periodically.

The async-worker arm of the universal op-contract: a background thread publishes
this star's :class:`vault_mcp.health.HealthState` to the
``{star}._ops.heartbeat`` topic on the redpanda broker every interval, so Nyx and
operators can read the star's standing off the event backbone rather than only by
dialing it.

Wiring (the reference seam for the fleet):

* :func:`beat` builds the ``{star, live, ready, metrics, ts, boot_id,
  revision}`` payload — the ``ops.heartbeat-value`` contract nyx decodes —
  and the loop hands it to a ``publish(topic, payload)`` callable. This was
  ``stellar_core.AsyncHeartbeatPublisher`` until that SDK retired
  (2026-10-18); the shape is the contract's, not the SDK's;
* this module backs that callable with a ``confluent_kafka.Producer`` (the same
  client the consumer uses), JSON-encoding the payload (the ``_ops`` topics are
  schemaless JSON — no schema registry);
* ``confluent_kafka`` is lazy-imported (the optional ``kafka`` extra) and the
  whole thing degrades gracefully — a missing extra or an unreachable broker
  never stops the MCP server. The producer batches in its own background thread;
  ``poll(0)`` services delivery callbacks without blocking.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from vault_mcp.health import HealthState, current_health
from vault_mcp.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Beat cadence — frequent enough that a missed beat is a quick liveness signal,
# sparse enough to be negligible load (this is operator/Nyx liveness, not metrics
# granularity — the OTel meter carries the fine-grained metrics).
DEFAULT_INTERVAL_S = 30.0

# Star name for the heartbeat payload (matches star.toml).
_STAR_NAME = "vault-mcp"

# Minted once per PROCESS — not per beat, not per client. The fleet's deploy
# edge: the moment it changes is the moment a new instance took over.
BOOT_ID = uuid.uuid4().hex

# The image's build revision, stamped by the build; absent on an unstamped image.
REVISION_ENV = "STELLAR_REVISION"


def beat(state: HealthState, star: str = _STAR_NAME) -> dict[str, Any]:
    """Return one heartbeat payload for *state*: the ``ops.heartbeat-value`` shape.

    ``ts`` is RFC 3339 with a ``Z`` offset; ``revision`` is ``None`` on an
    unstamped image rather than absent, which the contract declares nullable.
    """
    revision = os.environ.get(REVISION_ENV, "").strip() or None
    return {
        "star": star,
        "live": state.live,
        "ready": state.ready,
        "metrics": dict(state.metrics),
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "boot_id": BOOT_ID,
        "revision": revision,
    }


def _kafka_publish(bootstrap: str) -> Callable[[str, dict[str, Any]], None]:
    """Build a ``(topic, payload) -> None`` publish callable backed by Kafka.

    Lazy-imports ``confluent_kafka`` (the optional ``kafka`` extra) so the module
    imports in environments without it (the CI gate). Raises ``ImportError`` when
    the extra is absent — the caller treats that as "no heartbeat", not a crash.
    """
    import confluent_kafka

    producer = confluent_kafka.Producer({"bootstrap.servers": bootstrap})

    def publish(topic: str, payload: dict[str, Any]) -> None:
        producer.produce(topic, json.dumps(payload).encode())
        producer.poll(0)  # service delivery callbacks; non-blocking

    return publish


def start_heartbeat(
    interval_s: float = DEFAULT_INTERVAL_S,
) -> threading.Thread | None:
    """Start a daemon thread publishing this star's heartbeat to redpanda.

    Returns the started thread, or ``None`` if the heartbeat could not start
    (the ``kafka`` extra is absent) — a missing heartbeat must never stop the
    MCP server from serving. The thread is a daemon: it dies with the process.
    """
    settings = get_settings()
    try:
        publish = _kafka_publish(settings.kafka_bootstrap)
    except ImportError:
        logger.warning(
            "heartbeat: confluent-kafka not installed (the `kafka` extra) — "
            "no heartbeat will be published to %s",
            settings.heartbeat_topic,
        )
        return None

    topic = settings.heartbeat_topic

    def _loop() -> None:
        # Beat immediately, then every interval. A publish fault (broker down) is
        # logged and the loop continues — the next beat retries; the producer's
        # own buffer + retry covers a transient broker outage.
        while True:
            try:
                publish(topic, beat(current_health()))
            except Exception:
                logger.exception("heartbeat: publish failed")
            time.sleep(interval_s)

    thread = threading.Thread(
        target=_loop, name=f"{_STAR_NAME}-heartbeat", daemon=True
    )
    thread.start()
    logger.info(
        "heartbeat: publishing to %s every %.0fs",
        settings.heartbeat_topic,
        interval_s,
    )
    return thread
