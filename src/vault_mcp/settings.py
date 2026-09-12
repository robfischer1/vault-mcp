# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Typed runtime configuration for vault-mcp.

Settings resolve from (lowest to highest precedence): the code defaults below
-> environment variables prefixed `VAULT_MCP_`. Validation runs
at construction, so a malformed value fails fast at startup instead of deep in a
request path. Add fields below; type any secret as `pydantic.SecretStr` so it
never surfaces in logs or repr.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, validated from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="VAULT_MCP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    # OTel exporter target — every star ships telemetry to Hemera, the
    # observability plane's OTLP collector (live container `hemera-otelcol`), so
    # Nyx can read its standing. observability.py appends /v1/traces + /v1/metrics.
    otel_endpoint: str = "http://hemera-otelcol:4318"
    # Set False to silence OTLP export (e.g. local dev with no collector).
    otel_enabled: bool = True
    # Secure by default (S104: binding all interfaces is an operator CHOICE, not
    # a code default). Loopback-only until told otherwise; a star reachable over
    # the network (behind the Hades gateway, container-to-container) MUST set
    # `VAULT_MCP_HOST=0.0.0.0` explicitly in its deploy env —
    # that env injection lives in rob/infra (this template stopped shipping a
    # Dockerfile/compose.yaml at the Flame C4 recentralization; there is no
    # in-repo file left to default it for you). README's env table says the same.
    host: str = "127.0.0.1"
    port: int = 8000
    # Heartbeat → Pontus: the Redpanda broker the {live,ready,metrics} beat ships
    # to (overridden to the in-network listener at deploy, e.g. chronos-redpanda:29092)
    # and the per-star topic. confluent-kafka is the optional `kafka` extra.
    kafka_bootstrap: str = "localhost:9092"
    heartbeat_topic: str = "vault-mcp._ops.heartbeat"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (constructed once)."""
    return Settings()
