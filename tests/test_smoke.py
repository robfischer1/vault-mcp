# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for vault-mcp."""

import re

from vault_mcp import __version__
from vault_mcp.oneiroi import emit_memory
from vault_mcp.server import mcp
from vault_mcp.settings import get_settings


def test_version() -> None:
    # Assert a valid X.Y.Z version rather than a frozen literal, so the test
    # survives version bumps instead of rotting into a CI break.
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_settings_load() -> None:
    assert get_settings().log_level == "INFO"



def test_oneiroi_hook() -> None:
    # Born-with Oneiroi memory hook is a safe no-op until the plane is live —
    # calling it must not raise (it is typed `-> None`, so asserting `is None`
    # is a mypy func-returns-value error; the no-raise call is the real check).
    # forge-testkit: assert-exempt: no-op memory hook; the no-raise call is the check
    emit_memory("smoke", {"ok": True})


def test_server_name() -> None:
    assert mcp.name == "vault-mcp"
