"""A scriptable HTTP transport: records calls, returns queued (status, text).

Lives here rather than in the test module because
`[tool.forge_testkit_lint] substrate_pkg = "tests.substrate"` makes this the
only sanctioned home for a `Fake*`/`Stub*` class — the rule that stops one
double being hand-rolled once per module, which is how five separate
`FakeVault` classes came to exist.
"""

from __future__ import annotations

from typing import Any


class FakeTransport:
    """Scriptable transport: records calls, returns queued (status, text)."""

    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        self.calls.append((url, body))
        assert headers["Authorization"].startswith("Bearer ")
        return self.responses.pop(0)
