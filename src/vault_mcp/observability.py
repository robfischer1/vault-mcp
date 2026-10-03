# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""OpenTelemetry exporter wiring for vault-mcp (born-with stub).

Every star exports traces/metrics/logs to Hemera (the observability plane's OTLP
collector) so that Nyx (the membership control plane) can read this star's runtime
standing. This module is the wiring point; the real OTel SDK setup is filled in
when the star starts emitting telemetry.

The exporter target resolves from ``Settings.otel_endpoint`` (env
``VAULT_MCP_OTEL_ENDPOINT``), defaulting to Hemera.
"""

from __future__ import annotations

from vault_mcp.settings import get_settings


def configure_telemetry() -> str:
    """Configure OTLP export for this star and return the resolved endpoint.

    Stub: resolves the exporter target from settings. Wire the OpenTelemetry SDK
    (TracerProvider + OTLP exporter) here when instrumenting the star — the
    endpoint contract is already in place so the change is additive.
    """
    settings = get_settings()
    # Wiring point: construct a TracerProvider + OTLPSpanExporter against
    # settings.otel_endpoint and register it as the global tracer provider.
    return settings.otel_endpoint
