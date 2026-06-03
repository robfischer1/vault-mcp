"""Note <-> typed-payload translator (VDV F2).

The translation half of the vault-DB dissolve/materialize verbs. Pure mapping,
no I/O and no phdb calls — it turns a parsed note (frontmatter + verbatim body)
into the payloads phdb's typed-write HTTP routes expect, and back. vault-mcp owns
this because it owns note-shape (the Convention Gate / frontmatter authority);
phdb stays note-ignorant and just receives typed payloads.

Forward (dissolve): ``note_to_payloads`` -> a list of {endpoint, payload} the
orchestrator POSTs to phdb. A Plan note yields TWO payloads — its full prose to
``documents`` and its metadata to ``plans`` — matching the dissolution pilot;
every other note yields one ``documents`` payload. The body is passed through
verbatim (the staged_md fence-extraction bug is designed out by never extracting).

Reverse (materialize): ``row_to_create_args`` -> the kwargs for the Convention
Gate's ``create_note``, so the rendered note re-validates on the way back in.
"""

from __future__ import annotations

from typing import Any

# Schema.org @types the documents table accepts (mirrors phdb's staged_md
# _VALID_TYPES); anything else falls back to DigitalDocument.
_DOC_SCHEMA_TYPES = frozenset({
    "DigitalDocument", "CreativeWork", "Article", "Message",
    "SocialMediaPosting", "EmailMessage", "Book", "Observation",
})

DOC_ENDPOINT = "/write/document"
PLAN_ENDPOINT = "/write/plan"


def _schema_type(frontmatter: dict[str, Any]) -> str:
    at = frontmatter.get("@type") or frontmatter.get("type")
    return at if at in _DOC_SCHEMA_TYPES else "DigitalDocument"


def _subject(frontmatter: dict[str, Any]) -> str | None:
    return frontmatter.get("name") or frontmatter.get("title")


def note_to_payloads(
    frontmatter: dict[str, Any],
    body: str,
    source_path: str,
    *,
    file_path: str | None = None,
) -> list[dict[str, Any]]:
    """Translate one parsed note into the typed-write payloads phdb expects.

    Always emits a ``documents`` payload carrying the body verbatim. A note whose
    ``note_type`` is ``Plan`` additionally emits a ``plans`` metadata payload.
    Returns ``[{"endpoint": str, "payload": dict}, ...]`` in write order
    (document first, then metadata).
    """
    note_type = str(frontmatter.get("note_type") or "").strip().lower()

    payloads: list[dict[str, Any]] = [{
        "endpoint": DOC_ENDPOINT,
        "payload": {
            "source_path": source_path,
            "body_text": body,
            "schema_type": _schema_type(frontmatter),
            "subject": _subject(frontmatter),
            "file_path": file_path,
        },
    }]

    if note_type == "plan":
        payloads.append({
            "endpoint": PLAN_ENDPOINT,
            "payload": {
                "source_path": source_path,
                "name": _subject(frontmatter) or "",
                "identifier": frontmatter.get("identifier") or None,
                "description": frontmatter.get("description"),
                "status": frontmatter.get("status") or "draft",
                "phase": frontmatter.get("phase"),
                "effort": frontmatter.get("effort"),
                "maintenance": frontmatter.get("maintenance"),
                "created": frontmatter.get("created"),
                "updated": frontmatter.get("updated"),
                "file_path": file_path,
            },
        })

    return payloads


def target_tables(payloads: list[dict[str, Any]]) -> list[str]:
    """The phdb table names a payload set lands in — for the dissolution wave's
    ``target_tables``. Derived from the endpoints, deduped, order-preserving."""
    mapping = {DOC_ENDPOINT: "documents", PLAN_ENDPOINT: "plans"}
    seen: list[str] = []
    for p in payloads:
        t = mapping.get(p["endpoint"])
        if t and t not in seen:
            seen.append(t)
    return seen


def row_to_create_args(row: dict[str, Any], table: str) -> dict[str, Any]:
    """Reverse direction (materialize): map a typed phdb row to the Convention
    Gate ``create_note`` kwargs. ``documents`` rows render directly (subject +
    body). ``plans`` rows are metadata-only — the caller supplies the paired
    ``documents`` body via ``body`` since the plans table stores none.
    """
    if table == "documents":
        return {
            "title": row.get("subject") or "Untitled",
            "note_type": row.get("schema_type") or "DigitalDocument",
            "body": row.get("body_text") or "",
        }
    if table == "plans":
        return {
            "title": row.get("name") or "Untitled Plan",
            "note_type": "Plan",
            "body": row.get("body") or "",
            "extra_fields": {
                k: row[k] for k in
                ("identifier", "description", "status", "phase",
                 "effort", "maintenance", "created", "updated")
                if row.get(k) is not None
            },
        }
    raise ValueError(f"no materialize mapping for table {table!r}")
