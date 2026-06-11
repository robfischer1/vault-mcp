"""Tests for dissolve/materialize orchestration (VDV F3)."""

from __future__ import annotations

from typing import Any

from vault_mcp.lifecycle_verbs import dissolve_note

PLAN_NOTE = """---
"@type": DigitalDocument
note_type: Plan
name: Roadmap Skill
identifier: roadmap-skill-plan
status: complete
phase: complete
---

## Roadmap Skill

Context before the fence.

```
master (Sonnet)
```

Goal after the fence.
"""


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


def _run(poster: FakePoster) -> tuple[dict[str, Any], dict[str, bool]]:
    state = {"deleted": False}

    def _delete() -> None:
        state["deleted"] = True

    res = dissolve_note(
        source_path=r"C:\vault\Plans\Roadmap Skill.md",
        raw_text=PLAN_NOTE,
        file_path="Roadmap Skill.md",
        plan_slug="plans-cleanup",
        rationale="board-native",
        post=poster,
        delete_file=_delete,
    )
    return res, state


def test_dissolve_writes_both_then_declares_then_deletes() -> None:
    poster = FakePoster()
    res, state = _run(poster)

    assert res["ok"] is True and res["deleted"] is True
    assert res["dissolution_id"] == 7
    assert [w["table"] for w in res["written"]] == ["documents", "plans"]
    # Ordering: both writes + declare happened, delete is last (state set only at end).
    assert poster.calls == [
        "/write/document",
        "/write/plan",
        "/dissolution/declare",
    ]
    assert state["deleted"] is True


def test_write_failure_does_not_delete() -> None:
    poster = FakePoster(fail_endpoint="/write/plan")
    res, state = _run(poster)

    assert res["ok"] is False and res["stage"] == "write"
    assert res["endpoint"] == "/write/plan"
    # The document write succeeded but the file must remain (content recoverable).
    assert state["deleted"] is False
    assert "/dissolution/declare" not in poster.calls  # never reached declare


def test_declare_failure_does_not_delete() -> None:
    poster = FakePoster(fail_endpoint="/dissolution/declare")
    res, state = _run(poster)

    assert res["ok"] is False and res["stage"] == "declare"
    assert (
        state["deleted"] is False
    )  # writes happened, but no declare -> no delete


def test_non_plan_note_writes_one_document() -> None:
    poster = FakePoster()
    state = {"deleted": False}
    note = '---\n"@type": DigitalDocument\nnote_type: Decisions\nname: Foo DECISIONS\n---\n\nprose body\n'
    res = dissolve_note(
        source_path="/vault/Foo DECISIONS.md",
        raw_text=note,
        file_path="Foo DECISIONS.md",
        plan_slug="plans-cleanup",
        rationale="board-native",
        post=poster,
        delete_file=lambda: state.__setitem__("deleted", True),
    )
    assert res["ok"] is True
    assert poster.calls == ["/write/document", "/dissolution/declare"]
    assert [w["table"] for w in res["written"]] == ["documents"]
