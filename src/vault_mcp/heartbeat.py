# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0


"""Heartbeat publisher — emits {live, ready, metrics} to redpanda periodically.

The async-worker arm of the universal op-contract: a background thread publishes
this star's :class:`stellar_core.HealthState` to the ``{star}._ops.heartbeat``
topic on the redpanda broker every interval, so Nyx and operators can
read the star's standing off the event backbone rather than only by dialing it.

Wiring (the reference seam for the fleet):

* ``stellar_core.AsyncHeartbeatPublisher`` builds the ``{star, live, ready,
  metrics, ts}`` payload and hands it to a ``publish(topic, payload)`` callable;
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
import threading
import time
from typing import TYPE_CHECKING, Any

from stellar_core import AsyncHeartbeatPublisher

from vault_mcp.health import current_health
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

    publisher = AsyncHeartbeatPublisher(
        _STAR_NAME, publish, topic=settings.heartbeat_topic
    )

    def _loop() -> None:
        # Beat immediately, then every interval. A publish fault (broker down) is
        # logged and the loop continues — the next beat retries; the producer's
        # own buffer + retry covers a transient broker outage.
        while True:
            try:
                publisher.publish(current_health())
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
