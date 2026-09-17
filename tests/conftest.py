# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures, and the autouse no-network guard for the whole suite.

This file did not exist before 2026-08-22, which is the whole explanation for
how the same `FakeVault` came to be hand-rolled five times in five test
modules, no two of them agreeing. Fixtures that more than one module needs
belong here; test doubles belong in `tests/substrate/`.

THE NO-NETWORK GUARD (fail-loud die, C3). pyproject registered the
``allow_network`` marker on 2026-08-22 with the rest of the fail-loud settings,
but nothing ever applied the fixture the marker opts OUT of — the escape hatch
existed without the wall. stellar-core#49 closed the same gap there after its
heartbeat suite could publish real beats onto live stars' topics under a single
mutated condition (infra#9758); this suite's REST client talks to the Obsidian
Local REST API on 127.0.0.1:27123, a live service on the workstation that runs
it, and the ``live`` marker is a convention a test must REMEMBER. A guard a
future test INHERITS beats one it must remember, and no source mutant can undo
an autouse fixture.

``forge_testkit.config.no_network`` ships as a context manager + settings DATA,
NOT a pytest plugin, so every repo owns this autouse fixture (the deliberate
design: no plugin a consumer is forced to install). It blocks AF_INET/AF_INET6
only — a local AF_UNIX socketpair (asyncio's event-loop self-pipe) passes
through, so merely spinning a loop is not a false failure. A test that
legitimately opens a real socket opts out with ``@pytest.mark.allow_network``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from forge_testkit.config import no_network

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


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Block real outbound sockets for every test, unless marked allow_network."""
    if request.node.get_closest_marker("allow_network") is not None:
        yield
        return
    with no_network():
        yield


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
        # blanket mypy arg-type waiver to typecheck, because `object` cannot
        # satisfy FakeVault's typed fields. The standard forbids an un-VERIFIED
        # waiver, and the honest fix is to state the signature rather than
        # annotate around it — None-sentinels for the mutable defaults, per the
        # same rule. (Spelled in prose: a comment that quotes the directive
        # verbatim reads as one to every scanner that greps for it.)
        vault = FakeVault(
            store=store if store is not None else {},
            refuse_create_over_existing=refuse_create_over_existing,
            fail=fail if fail is not None else set(),
        )
        loaded = load_schema(str(FIXTURES / "schema" / f"{schema}.schema.yml"))
        return ConventionGate(loaded, vault), vault

    return build
