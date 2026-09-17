# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""The no-network guard is proven, not assumed.

A guard nobody exercises is a guard that can silently stop working — which is
precisely the failure stellar-core taught the fleet (infra#9758: a suite was
"offline" only because of a value it happened to pass, and a mutant flipped it).
These tests assert the fixture's behaviour directly rather than trusting that
importing it did something.

Three legs, because the guard has three claims:

1. a real outbound dial (AF_INET) is REFUSED;
2. AF_INET6 is refused too, so an IPv6 dial is not a way around it;
3. a local AF_UNIX socketpair — asyncio's event-loop self-pipe — still works,
   so the guard does not false-fail any test that merely spins a loop.

The opt-out leg is deliberately NOT tested by dialling anything: this file must
stay offline itself. `allow_network` is exercised by constructing the socket the
guard would otherwise refuse and closing it without connecting.
"""

from __future__ import annotations

import socket

import pytest
from forge_testkit.config import NetworkBlocked


def test_outbound_ipv4_is_refused() -> None:
    """The guard raises before a socket object exists at all."""
    with pytest.raises(NetworkBlocked, match="no_network"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_outbound_ipv6_is_refused() -> None:
    """IPv6 is blocked on the same footing — not an escape hatch."""
    with pytest.raises(NetworkBlocked, match="no_network"):
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM)


def test_a_real_dial_cannot_be_reached() -> None:
    """`create_connection` never gets far enough to emit a SYN.

    An IP LITERAL, deliberately, and this is a measured limitation of the
    guard rather than a stylistic choice: `no_network()` patches
    `socket.socket`, not `socket.getaddrinfo`, so `create_connection` to a
    NAME resolves DNS first and only then hits the guard. Written against
    `("redpanda", 29092)` this test fails off-cluster with `gaierror` (the
    name does not resolve) and would pass in-cluster for the wrong reason.
    Against a literal there is no lookup, so the refusal is the guard's and
    the test measures the same thing everywhere it runs.

    The port is the fleet broker's — the exact target the 2026-09-17 incident
    published onto — and no packet reaches it.
    """
    with pytest.raises(NetworkBlocked, match="no_network"):
        socket.create_connection(("10.255.255.1", 29092), timeout=0.01)


def test_local_socketpair_still_works() -> None:
    """AF_UNIX passes through: an event loop's self-pipe is not a network dial."""
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"local")
        assert right.recv(5) == b"local"


@pytest.mark.allow_network
def test_the_marker_opts_a_test_out() -> None:
    """The escape hatch works, proven without dialling anything.

    Constructing an AF_INET socket is what the guard refuses; here it must
    succeed. The socket is closed without connect(), so this test is still
    fully offline — an opt-out proven by construction, not by reaching out.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.family is socket.AF_INET
