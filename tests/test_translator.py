"""Tests for the note<->typed-payload translator (VDV F2)."""

from __future__ import annotations

from vault_mcp.translator import (
    DOC_ENDPOINT,
    ENTITY_ENDPOINT,
    calliope_document_to_row,
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


def test_plan_note_yields_document_only() -> None:
    payloads = note_to_payloads(
        PLAN_FM,
        FENCED_BODY,
        "/vault/Plans/Roadmap Skill.md",
        file_path="Roadmap Skill.md",
    )
    endpoints = [p["endpoint"] for p in payloads]
    # C3: document only — the plan sink is RETIRED-superseded.
    assert endpoints == [DOC_ENDPOINT]

    doc = payloads[0]["payload"]
    assert doc["body_text"] == FENCED_BODY  # verbatim — no fence-extraction
    assert doc["schema_type"] == "DigitalDocument"
    assert doc["subject"] == "Roadmap Skill"
    assert doc["source_path"] == "/vault/Plans/Roadmap Skill.md"


def test_non_plan_note_yields_only_document() -> None:
    fm = {
        "@type": "DigitalDocument",
        "note_type": "Decisions",
        "name": "Foo DECISIONS",
    }
    payloads = note_to_payloads(
        fm, "decision prose", "/vault/Plans/Foo DECISIONS.md"
    )
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
    # C3: a plan note emits NO plan payload — the plans typed-table is
    # RETIRED-superseded (plan structure lives on the graph; prose is the doc).
    payloads = note_to_payloads(PLAN_FM, "b", "/vault/p.md")
    assert target_tables(payloads) == ["documents"]
    doc_only = note_to_payloads(
        {"@type": "Article", "name": "A"}, "b", "/vault/a.md"
    )
    assert target_tables(doc_only) == ["documents"]


def test_row_to_payload_document() -> None:
    row = {
        "subject": "My Doc",
        "schema_type": "Article",
        "body_text": "the body",
    }
    p = row_to_payload(row, "documents", directory="Garden")
    assert p == {
        "title": "My Doc",
        "note_type": "Article",
        "directory": "Garden",
        "body": "the body",
        "frontmatter": {},
    }


def test_frontmatter_dates_pass_through_as_mtime_ctime() -> None:
    fm = {
        "@type": "Article",
        "name": "Dated",
        "created": "2025-01-15",
        "updated": "2026-06-05",
    }
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
    row = {
        "name": "P",
        "status": "active",
        "phase": "3",
        "effort": "L",
        "description": "a plan",
    }
    p = row_to_payload(
        row, "plans", directory="System/Plans", paired_body="paired prose"
    )
    assert p["title"] == "P"
    assert p["note_type"] == "Plan"
    assert p["directory"] == "System/Plans"
    assert p["body"] == "paired prose"  # prose from the paired documents row
    # Structured/validated metadata stays DB-canonical — only safe free-text carries.
    assert p["frontmatter"] == {"description": "a plan"}


# ── Entity routing ──────────────────────────────────────────────────────────


def test_entity_type_routes_to_entity_endpoint() -> None:
    fm = {
        "@type": "VideoGame",
        "name": "FFXIV",
        "genre": "MMORPG",
        "game_platform": "PC",
        "publisher": "Square Enix",
    }
    payloads = note_to_payloads(
        fm, "", "/vault/Entities/Games/FFXIV.md", file_path="FFXIV.md"
    )
    assert len(payloads) == 1
    assert payloads[0]["endpoint"] == ENTITY_ENDPOINT
    p = payloads[0]["payload"]
    assert p["schema_type"] == "VideoGame"
    assert p["fields"]["name"] == "FFXIV"
    assert p["fields"]["genre"] == "MMORPG"
    assert p["fields"]["game_platform"] == "PC"


def test_entity_body_maps_to_description() -> None:
    fm = {"@type": "Book", "name": "Meditations"}
    payloads = note_to_payloads(fm, "Stoic philosophy classic.", "/vault/b.md")
    fields = payloads[0]["payload"]["fields"]
    assert fields["description"] == "Stoic philosophy classic."


def test_entity_explicit_description_wins_over_body() -> None:
    fm = {"@type": "Movie", "name": "Dune", "description": "Sci-fi epic"}
    payloads = note_to_payloads(fm, "Long review body here.", "/vault/m.md")
    fields = payloads[0]["payload"]["fields"]
    assert fields["description"] == "Sci-fi epic"


def test_entity_title_falls_back_to_name() -> None:
    fm = {"@type": "VideoGame", "title": "Elden Ring"}
    payloads = note_to_payloads(fm, "", "/vault/g.md")
    assert payloads[0]["payload"]["fields"]["name"] == "Elden Ring"


def test_entity_vault_internal_keys_excluded() -> None:
    fm = {
        "@type": "TVSeries",
        "name": "Breaking Bad",
        "note_type": "entity",
        "author_type": "ai-compiled",
        "tags": ["media"],
        "genre": "Drama",
    }
    payloads = note_to_payloads(fm, "", "/vault/s.md")
    fields = payloads[0]["payload"]["fields"]
    assert "note_type" not in fields
    assert "author_type" not in fields
    assert "tags" not in fields
    assert fields["genre"] == "Drama"


def test_non_entity_type_still_routes_to_documents() -> None:
    fm = {"@type": "Person", "name": "Alice"}
    payloads = note_to_payloads(fm, "Bio text.", "/vault/p.md")
    assert payloads[0]["endpoint"] == DOC_ENDPOINT


def test_entity_target_tables_resolves_table_name() -> None:
    fm = {"@type": "VideoGame", "name": "FFXIV"}
    payloads = note_to_payloads(fm, "", "/vault/g.md")
    assert target_tables(payloads) == ["games"]


def test_restaurant_preserves_schema_type() -> None:
    fm = {"@type": "Restaurant", "name": "Casa Bella"}
    payloads = note_to_payloads(fm, "Open until 10", "/vault/r.md")
    assert payloads[0]["endpoint"] == DOC_ENDPOINT
    assert payloads[0]["payload"]["schema_type"] == "Restaurant"


def test_software_application_preserves_schema_type() -> None:
    fm = {"@type": "SoftwareApplication", "name": "Discord"}
    payloads = note_to_payloads(fm, "Chat app.", "/vault/s.md")
    assert payloads[0]["endpoint"] == DOC_ENDPOINT
    assert payloads[0]["payload"]["schema_type"] == "SoftwareApplication"


# -- calliope_document_to_row (the C6 reverse / materialize normalizer) --------


def test_calliope_document_to_row_maps_title_to_subject() -> None:
    # Calliope names the title column `title`; row_to_payload reads `subject`.
    doc = {
        "id": 9,
        "title": "The Dissolved Note",
        "schema_type": "DigitalDocument",
        "body_text": "verbatim prose",
        "source_path": "Brain Soup/Note.md",
    }
    row = calliope_document_to_row(doc)
    assert row["subject"] == "The Dissolved Note"
    assert row["body_text"] == "verbatim prose"
    # And it feeds row_to_payload cleanly (the materialize path).
    payload = row_to_payload(row, "documents", directory="Brain Soup")
    assert payload["title"] == "The Dissolved Note"
    assert payload["body"] == "verbatim prose"
    assert payload["note_type"] == "DigitalDocument"


def test_calliope_document_to_row_prefers_subject_when_present() -> None:
    doc = {"subject": "Already-subject", "title": "fallback", "body_text": "x"}
    assert calliope_document_to_row(doc)["subject"] == "Already-subject"
