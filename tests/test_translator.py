"""Tests for the note<->typed-payload translator (VDV F2)."""

from __future__ import annotations

from vault_mcp.translator import (
    DOC_ENDPOINT,
    PLAN_ENDPOINT,
    note_to_payloads,
    row_to_create_args,
    target_tables,
)

PLAN_FM = {
    "@type": "DigitalDocument",
    "note_type": "Plan",
    "name": "Roadmap Skill",
    "identifier": "roadmap-skill-plan",
    "description": "Plan for /roadmap.",
    "status": "complete",
    "phase": "complete",
    "effort": "L",
}

# A body with a fenced block surrounded by prose — must pass through verbatim.
FENCED_BODY = "Context before.\n\n```\nmaster (Sonnet)\n```\n\nGoal after.\n"


def test_plan_note_yields_document_and_plan() -> None:
    payloads = note_to_payloads(PLAN_FM, FENCED_BODY, "/vault/Plans/Roadmap Skill.md",
                                file_path="Roadmap Skill.md")
    endpoints = [p["endpoint"] for p in payloads]
    assert endpoints == [DOC_ENDPOINT, PLAN_ENDPOINT]  # document first

    doc = payloads[0]["payload"]
    assert doc["body_text"] == FENCED_BODY  # verbatim — no fence-extraction
    assert doc["schema_type"] == "DigitalDocument"
    assert doc["subject"] == "Roadmap Skill"
    assert doc["source_path"] == "/vault/Plans/Roadmap Skill.md"

    plan = payloads[1]["payload"]
    assert plan["name"] == "Roadmap Skill"
    assert plan["identifier"] == "roadmap-skill-plan"
    assert plan["status"] == "complete"
    assert plan["phase"] == "complete"
    assert plan["effort"] == "L"


def test_non_plan_note_yields_only_document() -> None:
    fm = {"@type": "DigitalDocument", "note_type": "Decisions", "name": "Foo DECISIONS"}
    payloads = note_to_payloads(fm, "decision prose", "/vault/Plans/Foo DECISIONS.md")
    assert len(payloads) == 1
    assert payloads[0]["endpoint"] == DOC_ENDPOINT
    assert payloads[0]["payload"]["subject"] == "Foo DECISIONS"


def test_unknown_type_falls_back_to_digitaldocument() -> None:
    fm = {"@type": "WeirdType", "name": "X"}
    payloads = note_to_payloads(fm, "body", "/vault/x.md")
    assert payloads[0]["payload"]["schema_type"] == "DigitalDocument"


def test_title_used_when_name_absent() -> None:
    fm = {"@type": "CreativeWork", "title": "Titled Note"}
    payloads = note_to_payloads(fm, "b", "/vault/t.md")
    assert payloads[0]["payload"]["subject"] == "Titled Note"
    assert payloads[0]["payload"]["schema_type"] == "CreativeWork"


def test_target_tables_derivation() -> None:
    payloads = note_to_payloads(PLAN_FM, "b", "/vault/p.md")
    assert target_tables(payloads) == ["documents", "plans"]
    doc_only = note_to_payloads({"@type": "Article", "name": "A"}, "b", "/vault/a.md")
    assert target_tables(doc_only) == ["documents"]


def test_row_to_create_args_document() -> None:
    row = {"subject": "My Doc", "schema_type": "Article", "body_text": "the body"}
    args = row_to_create_args(row, "documents")
    assert args == {"title": "My Doc", "note_type": "Article", "body": "the body"}


def test_row_to_create_args_plan_carries_metadata() -> None:
    row = {"name": "P", "status": "active", "phase": "3", "body": "paired prose"}
    args = row_to_create_args(row, "plans")
    assert args["title"] == "P"
    assert args["note_type"] == "Plan"
    assert args["body"] == "paired prose"
    assert args["extra_fields"]["status"] == "active"
    assert args["extra_fields"]["phase"] == "3"
