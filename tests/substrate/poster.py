"""Records POSTs in order and fails at a configured endpoint.

Lives here rather than in the test module because
`[tool.forge_testkit_lint] substrate_pkg = "tests.substrate"` makes this the
only sanctioned home for a `Fake*`/`Stub*` class — the rule that stops one
double being hand-rolled once per module, which is how five separate
`FakeVault` classes came to exist.
"""

from __future__ import annotations

from typing import Any


class FakePoster:
    """Records POSTs in order; fails at a configured endpoint."""

    def __init__(self, fail_endpoint: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_endpoint = fail_endpoint
        self._next_id = 100

    def __call__(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(endpoint)
        if endpoint == self.fail_endpoint:
            return {"ok": False, "error": f"boom at {endpoint}"}
        if endpoint == "/dissolution/declare":
            return {"ok": True, "dissolution_id": 7}
        self._next_id += 1
        table = "plans" if endpoint.endswith("plan") else "documents"
        return {
            "ok": True,
            "table": table,
            "id": self._next_id,
            "deduped": False,
        }
