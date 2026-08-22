"""A CLIRunner double: captures the eval code and returns a canned envelope.

Lives here rather than in the test module because
`[tool.forge_testkit_lint] substrate_pkg = "tests.substrate"` makes this the
only sanctioned home for a `Fake*`/`Stub*` class — the rule that stops one
double being hand-rolled once per module, which is how five separate
`FakeVault` classes came to exist.
"""

from __future__ import annotations

from typing import Any


class FakeCLI:
    """Stand-in for ObsidianCLI.run capturing eval calls."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[str] = []

    def run(self, command: str, **params: Any) -> dict[str, Any]:
        assert command == "eval"
        self.calls.append(params["code"])
        return self.result
