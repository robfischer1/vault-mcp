"""Shared fixtures.

This file did not exist before 2026-08-22, which is the whole explanation for
how the same `FakeVault` came to be hand-rolled five times in five test
modules, no two of them agreeing. Fixtures that more than one module needs
belong here; test doubles belong in `tests/substrate/`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

# SET BEFORE ANY vault_mcp.server IMPORT. server.py resolves the vault at MODULE
# scope (server.py:90) and raises FileNotFoundError when it cannot find one, so
# the module is unimportable without this — which is the structural reason the
# repo had no tests/test_server.py at all and why that 3,074-line module sat at
# 32% coverage. conftest is imported before any test module, so this lands first.
os.environ.setdefault(
    "VAULT_MCP_PATH", str(Path(__file__).parent / "fixtures" / "mini-vault")
)

from tests.substrate import FakeVault
from vault_mcp.gate import ConventionGate
from vault_mcp.schema import load_schema

FIXTURES = Path(__file__).parent / "fixtures"

GateFactory = Callable[..., tuple[ConventionGate, FakeVault]]


@pytest.fixture
def vault() -> FakeVault:
    """An empty pinned vault double."""
    return FakeVault()


@pytest.fixture
def gate_factory() -> GateFactory:
    """Build a real ConventionGate over the pinned double.

    Uses the repo's own schema fixtures rather than the live vault schema, so
    the suite does not depend on a file outside the checkout. Pass
    `schema="atom"` for the atom-slug fixture; the default is the general one.
    """

    def build(
        *,
        schema: str = "valid",
        store: dict[str, str] | None = None,
        refuse_create_over_existing: bool = False,
        fail: set[str] | None = None,
    ) -> tuple[ConventionGate, FakeVault]:
        # Named parameters rather than **kwargs: the kwargs form needed a
        # `# type: ignore[arg-type]` to pass mypy, because `object` cannot
        # satisfy FakeVault's typed fields. The standard forbids an un-VERIFIED
        # ignore, and the honest fix is to state the signature rather than
        # annotate around it — None-sentinels for the mutable defaults, per the
        # same rule.
        vault = FakeVault(
            store=store if store is not None else {},
            refuse_create_over_existing=refuse_create_over_existing,
            fail=fail if fail is not None else set(),
        )
        loaded = load_schema(str(FIXTURES / "schema" / f"{schema}.schema.yml"))
        return ConventionGate(loaded, vault), vault

    return build
