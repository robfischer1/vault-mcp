"""The Gate's FRONTMATTER collaborator — build, migrate, lint and diff it.

Extracted from ConventionGate under vault-mcp#5294 (gate.py was 1173 LOC over a
600 block). DELEGATION, not inheritance: ConventionGate holds one of these and
forwards to it, so the collaborator is a name a reader can find rather than a
position in an MRO.

It takes the gate itself and reads `self._gate._io`, `._schema`, `._linter`,
`._diff_sink` and the sibling collaborators through it. That back-reference is
the explicit cost of the pattern — the state stays single-sourced on the gate
rather than being copied into each collaborator, where it could drift.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from vault_mcp.gate import (
    _CODE_TO_EXC,
    _escape_inline_tags,
    _slugify,
    _title_case_note_type,
    _today,
)
from vault_mcp.lint import LintCandidate, LintResult
from vault_mcp.provenance import (
    Actor,
    AuthorType,
    Provenance,
    WriteMode,
    author_type_for,
    stamp,
)

# Own logger, not the gate's. Beyond correct provenance in the log record,
# ruff's BLE001 permits a blind except whose body calls log.exception — but only
# when it can SEE that `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vault_mcp.gate import ConventionGate


class FrontmatterBuilder:
    """The Gate's FRONTMATTER collaborator — build, migrate, lint and diff it."""

    def __init__(self, gate: ConventionGate) -> None:
        """Bind to the gate whose state this collaborator operates on."""
        self._gate = gate

    def _raise_first_error(self, result: LintResult) -> None:
        """Reconstruct the legacy GateError for the first blocking finding.

        Preserves the historical exception type + message so existing callers
        and tests that catch a specific GateError subclass keep working, while
        ``lint()`` / ``audit`` consume the full structured ``result``.
        """
        finding = result.first_error()
        if finding is not None:
            raise _CODE_TO_EXC[finding.code](finding.message)

    def _maybe_escape_body(self, directory: str, body: str) -> str:
        """Escape bare inline #tags in imported bodies (References/, Records/) — FR-15."""
        if directory.startswith(("References", "Records")):
            return _escape_inline_tags(body)
        return body

    def _tag_warnings(self, tags: list[str], actor: Actor) -> list[str]:
        """Advisory (non-blocking) warnings when an agent uses reserved tags (FR-13)."""
        if actor is not Actor.AGENT:
            return []
        hits = [t for t in tags if t in self._gate._schema.reserved_tags]
        if not hits:
            return []
        return [f"reserved tag(s) {hits} are normally set by Rob, not agents"]

    def _link_warnings(
        self, directory: str, title: str, fm: dict[str, Any]
    ) -> list[str]:
        """Advisory (non-blocking) warnings for missing recommended links (FR-36)."""
        warns: list[str] = []
        derived = directory.startswith(("Artifacts", "Records"))
        if derived and "isBasedOn" not in fm:
            warns.append(
                "isBasedOn missing for an Artifacts/Records-derived note"
            )
        last = directory.rsplit("/", 1)[-1] if directory else ""
        is_folder_note = title == last  # folder-note / pillar-root exception
        if "up" not in fm and not is_folder_note:
            warns.append(
                "up: missing — consider linking this note to its parent"
            )
        return warns

    def lint_payload(
        self,
        *,
        title: str = "",
        note_type: str | None = None,
        pillar: str | None = None,
        body: str = "",
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        actor: Actor = Actor.AGENT,
    ) -> dict[str, Any]:
        """Validate the note this payload would create, writing nothing.

        Returns the structured lint result (``ok`` + ``errors`` + ``warnings``)
        so a caller can assemble -> lint -> fix -> write without touching disk.
        This is the create-shaped dry run; update-shaped linting arrives with
        ``write_note`` (create-or-update).
        """
        tags = tags or []
        directory = self._gate._schema.resolve_directory(
            note_type=note_type, pillar=pillar, attrs=fields or {}
        )
        author_level = stamp(actor, WriteMode.CREATE)
        resolved_at = author_type_for(author_level, None)
        frontmatter = self._build_frontmatter(
            title=title,
            note_type=note_type,
            pillar=pillar,
            tags=tags,
            author_level=author_level,
            author_type=resolved_at,
            ai_model=None,
            created=_today(),
            extra_fields=fields,
        )
        body = self._maybe_escape_body(directory, body)
        candidate = LintCandidate(
            frontmatter=frontmatter,
            body=body,
            directory=directory,
            filename=title,
            note_type=note_type,
            tags=tags,
            actor=actor,
            mode=WriteMode.CREATE,
            touches_body=True,
            touched_fields=None,
        )

        return self._gate._linter.lint(candidate).to_dict()

    def _build_frontmatter(
        self,
        *,
        title: str,
        note_type: str | None,
        pillar: str | None,
        tags: list[str],
        author_level: Provenance,
        author_type: AuthorType,
        ai_model: str | None,
        created: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema = self._gate._schema
        fm: dict[str, Any] = {
            schema.label_field: title,
            schema.created_field: created,
            "author_type": author_type.value,
            "author_level": author_level.value,
        }
        if ai_model is not None:
            fm["ai_model"] = ai_model
        if schema.updated_field is not None:
            fm[schema.updated_field] = created
        # Governance fields are Gate-stamped, never caller-set (legacy `provenance`
        # stays reserved so a caller cannot reintroduce the retired key).
        reserved = {
            schema.label_field,
            schema.created_field,
            "note_type",
            "author_type",
            "author_level",
            "ai_model",
            "provenance",
            schema.updated_field,
        }
        if note_type is not None:
            fm["note_type"] = _title_case_note_type(
                note_type
            )  # FR: Title-Case note_type
        if pillar is not None:
            fm["pillar"] = pillar
        if tags:
            fm["tags"] = tags
        if extra_fields is not None:
            for key, value in extra_fields.items():
                if key in reserved:
                    continue  # governance fields are Gate-stamped, never caller-set
                fm[key] = value

        self._migrate_keys(
            fm
        )  # rename deprecated keys + drop dead keys (before defaults)

        # identifier defaults to a kebab slug of the label; caller override wins.
        if fm.get("identifier") in (None, ""):
            fm["identifier"] = _slugify(title)
        # status defaults to the schema default on create when a vocabulary exists,
        # then normalizes (repairs) so the linter validates the stored value.
        if "status" not in fm and schema.status_values:
            fm["status"] = schema.status_default
        if "status" in fm:
            fm["status"] = schema.normalize_status(fm["status"])

        # Pillar visual defaults (nn_color / nn_icon) — stamped only when the
        # caller did not supply them, so an explicit value always wins.
        pd = schema.pillar_default(pillar)
        if pd is not None:
            if pd.nn_color is not None and "nn_color" not in fm:
                fm["nn_color"] = pd.nn_color
            if pd.nn_icon is not None and "nn_icon" not in fm:
                fm["nn_icon"] = pd.nn_icon

        # Required-frontmatter / type / value enforcement now lives in the Linter,
        # which validates this finished frontmatter as part of the candidate.
        return fm

    def _migrate_keys(self, fm: dict[str, Any]) -> None:
        """Rename deprecated keys (preserving value) and drop dead keys, in place.

        Renames never clobber an existing canonical key; dead keys are removed
        unconditionally. Driven by the schema so the maps stay config, not code.
        """
        for old, new in self._gate._schema.deprecated_renames:
            if old in fm:
                if new not in fm:
                    fm[new] = fm[old]
                del fm[old]
        for dead in self._gate._schema.dead_keys:
            fm.pop(dead, None)

    def _emit_diff(
        self,
        path: str,
        op: str,
        fields_changed: list[str],
        provenance: Provenance,
    ) -> None:
        """Emit a structured write diff. Emission never blocks or reverts a write."""
        if self._gate._diff_sink is None:
            return
        record = {
            "path": path,
            "op": op,
            "fields_changed": fields_changed,
            "provenance": provenance.value,
        }
        try:
            self._gate._diff_sink(record)
        except Exception:
            # Observability is non-critical: a failed emit must never fail the
            # write that already succeeded (RFC: emission failure never blocks).
            log.exception("diff emission failed for %s", path)
