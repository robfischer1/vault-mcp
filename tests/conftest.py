"""Shared fixtures.

This file did not exist before 2026-08-22, which is the whole explanation for
how the same `FakeVault` came to be hand-rolled five times in five test
modules, no two of them agreeing. Fixtures that more than one module needs
belong here; test doubles belong in `tests/substrate/`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

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
        *, schema: str = "valid", **kwargs: object
    ) -> tuple[ConventionGate, FakeVault]:
        vault = FakeVault(**kwargs)  # type: ignore[arg-type]
        loaded = load_schema(str(FIXTURES / "schema" / f"{schema}.schema.yml"))
        return ConventionGate(loaded, vault), vault

    return build
