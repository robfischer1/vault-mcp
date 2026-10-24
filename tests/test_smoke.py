# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for vault-mcp."""

import re

from vault_mcp import __version__
from vault_mcp.health import current_health
from vault_mcp.server import mcp
from vault_mcp.settings import get_settings


def test_version() -> None:
    # Assert a valid X.Y.Z version rather than a frozen literal, so the test
    # survives version bumps instead of rotting into a CI break.
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_settings_load() -> None:
    assert get_settings().log_level == "INFO"


def test_health_surface() -> None:
    # Born-with operational contract from the local health module.
    state = current_health()
    assert state.live is True
    assert state.ready is True


def test_server_name() -> None:
    assert mcp.name == "vault-mcp"
