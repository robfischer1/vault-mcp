"""Guard: no hand-rolled permissive Hades-gateway double in this repo's tests.

The Hades gateway seam is ``call(verb, arguments) -> result``. A test double
that implements *that* shape with canned, arg-ignoring responses is the
permissive ``chaos.client.FakeTransport`` anti-pattern the fleet-wide Wave-4
sweep retired: it lets a test go green against a verb contract it never actually
exercised. The sanctioned replacement lives in ``chaos.client.testing``
(``ReplayTransport`` — keyed by ``(verb, args)``, raise-on-untaped).

vault-mcp does **not** consume the ``call(verb, args)`` gateway seam: it reaches
Hades through its own minimal HTTP client (``hades_client.call_verb``), whose
injected double is ``FakeTransport(url, headers, body)`` — a *different* layer
(raw HTTP), scriptable and args-preserving, not a permissive canned double. The
repo's other fakes (``FakeVault``, ``FakePoster``, ``FakeCLI``) are legitimate
domain/port doubles. This guard keeps that clean: it fails only if a test class
grows a ``call(self, verb, ...)`` method — the precise gateway-double shape —
which would be someone importing or copying the permissive pattern from a
sibling repo that *does* use the chaos gateway client.

AST-only (no import, no execution), narrowly keyed on ``call`` + a ``verb``
parameter, so the legitimate domain/port fakes and the HTTP-layer
``FakeTransport`` are never flagged. Scoped to ``tests/`` — a permissive
gateway double is, by definition, a test double.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).parent

#: The one sanctioned home for a ``call(verb, args)`` double, per the fleet
#: contract. No such path exists in this repo today; kept for fidelity so a
#: vendored ``chaos.client.testing`` tree would never be flagged.
_SANCTIONED_PATH = "chaos/client/testing"


def _defines_gateway_call(node: ast.ClassDef) -> bool:
    """True if ``node`` defines a ``call(...)`` method taking a ``verb`` param.

    That is the Hades gateway seam shape — the permissive-double tell. Keyed on
    the ``verb`` parameter name (not merely a ``call`` method) so unrelated
    ``call``/``__call__`` transports (``url, headers, body``) do not match.
    """
    return any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "call"
        and "verb"
        in {
            arg.arg
            for arg in (
                *item.args.posonlyargs,
                *item.args.args,
                *item.args.kwonlyargs,
            )
        }
        for item in node.body
    )


def test_no_permissive_gateway_double_in_tests() -> None:
    """Fail if any test class hand-rolls a ``call(verb, args)`` gateway double."""
    offenders: list[str] = []
    for py in sorted(_TESTS_DIR.rglob("*.py")):
        if _SANCTIONED_PATH in py.as_posix():
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        offenders.extend(
            f"{py.name}:{node.lineno}: {node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and _defines_gateway_call(node)
        )
    assert not offenders, (
        "Permissive Hades-gateway double(s) found: a call(verb, args) canned "
        "transport must live in chaos.client.testing (ReplayTransport, keyed by "
        "(verb, args), raise-on-untaped), never hand-rolled in this repo's "
        f"tests. Offenders: {offenders}"
    )
