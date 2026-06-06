"""Tests for the note<->typed-payload translator (VDV F2)."""

from __future__ import annotations

from vault_mcp.translator import (
    DOC_ENDPOINT,
    PLAN_ENDPOINT,
    note_to_payloads,
    row_to_payload,
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


def test_records_types_preserved() -> None:
    # Records manifests carry @type Dataset / CollectionPage — must survive.
    for t in ("Dataset", "CollectionPage"):
        p = note_to_payloads({"@type": t, "name": "X"}, "b", "/vault/x.md")
        assert p[0]["payload"]["schema_type"] == t


def test_target_tables_derivation() -> None:
    payloads = note_to_payloads(PLAN_FM, "b", "/vault/p.md")
    assert target_tables(payloads) == ["documents", "plans"]
    doc_only = note_to_payloads({"@type": "Article", "name": "A"}, "b", "/vault/a.md")
    assert target_tables(doc_only) == ["documents"]


def test_row_to_payload_document() -> None:
    row = {"subject": "My Doc", "schema_type": "Article", "body_text": "the body"}
    p = row_to_payload(row, "documents", directory="Garden")
    assert p == {"title": "My Doc", "note_type": "Article", "directory": "Garden",
                 "body": "the body", "frontmatter": {}}


def test_frontmatter_dates_pass_through_as_mtime_ctime() -> None:
    fm = {"@type": "Article", "name": "Dated", "created": "2025-01-15", "updated": "2026-06-05"}
    payloads = note_to_payloads(fm, "body", "/vault/dated.md")
    doc = payloads[0]["payload"]
    assert doc["ctime"] == "2025-01-15"
    assert doc["mtime"] == "2026-06-05"


def test_missing_dates_omitted_from_payload() -> None:
    fm = {"@type": "Article", "name": "No Dates"}
    payloads = note_to_payloads(fm, "body", "/vault/nodates.md")
    doc = payloads[0]["payload"]
    assert "mtime" not in doc
    assert "ctime" not in doc


def test_row_to_payload_plan_carries_prose_not_legacy_metadata() -> None:
    row = {"name": "P", "status": "active", "phase": "3", "effort": "L",
           "description": "a plan"}
    p = row_to_payload(row, "plans", directory="System/Plans", paired_body="paired prose")
    assert p["title"] == "P"
    assert p["note_type"] == "Plan"
    assert p["directory"] == "System/Plans"
    assert p["body"] == "paired prose"  # prose from the paired documents row
    # Structured/validated metadata stays DB-canonical — only safe free-text carries.
    assert p["frontmatter"] == {"description": "a plan"}
