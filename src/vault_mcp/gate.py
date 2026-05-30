"""Convention Gate — the deterministic write API for vault-mcp v2.

Every vault write passes through here. The Gate generates compliant
frontmatter, validates tags against the closed glossary, routes the file to
the schema-resolved directory, enforces per-directory write-protection, and
stamps provenance — then hands the finished note to a writer (the Obsidian
CLI in production, a fake in tests). Agents need not know the rules because
they cannot violate them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml

from .provenance import Actor, Provenance, WriteMode, parse, stamp, transition
from .schema import VaultSchema, WriteProtectionRule

log = logging.getLogger(__name__)

# A diff sink receives a structured write record for downstream ingestion.
DiffSink = Callable[[dict[str, Any]], None]


class GateError(Exception):
    """Base class for Convention Gate rejections."""


class TagError(GateError):
    """One or more tags are outside the closed glossary."""


class FieldError(GateError):
    """A required frontmatter field is missing or invalid."""


class ProtectionError(GateError):
    """The target directory is write-protected for this write."""


class NoteIO(Protocol):
    """The vault IO surface the Gate depends on (Obsidian CLI implements it)."""

    def create_note(self, path: str, content: str) -> None: ...
    def read_note(self, path: str) -> str: ...
    def write_note(self, path: str, content: str) -> None: ...


@dataclass
class WriteResult:
    """The structured echo returned for every successful write."""

    path: str
    frontmatter: dict[str, Any]
    provenance: Provenance
    created: bool = field(default=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "path": self.path,
            "created": self.created,
            "frontmatter": self.frontmatter,
            "provenance": self.provenance.value,
        }


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _split_note(text: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter dict, body). Body excludes the fences."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    loaded = yaml.safe_load(text[3:end])
    fm: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def _render_note(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize frontmatter + body into a markdown note."""
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{fm_yaml}\n---\n\n{body}\n"


class ConventionGate:
    """Schema- and provenance-aware write API over an injected NoteIO."""

    def __init__(self, schema: VaultSchema, io: NoteIO, diff_sink: DiffSink | None = None) -> None:
        self._schema = schema
        self._io = io
        self._diff_sink = diff_sink

    # --- Write-protection (Feature: Write-protection enforcement) ----------
    def _protection_for(self, directory: str) -> WriteProtectionRule | None:
        for rule in self._schema.write_protection:
            if directory == rule.directory or directory.startswith(rule.directory + "/"):
                return rule
        return None

    def check_protection(
        self,
        directory: str,
        actor: Actor,
        mode: WriteMode,
        touches_body: bool = True,
    ) -> None:
        """Raise ProtectionError if this write is barred from ``directory``."""
        rule = self._protection_for(directory)
        if rule is None:
            return
        if rule.rule == "fully-immutable":
            raise ProtectionError(rule.error)
        if rule.rule == "body-immutable" and touches_body:
            raise ProtectionError(rule.error)
        if rule.rule == "compute-only" and mode is not WriteMode.COMPUTE:
            raise ProtectionError(rule.error)
        if rule.rule == "voice-only" and actor is not Actor.HUMAN:
            raise ProtectionError(rule.error)

    # --- Tag validation (Feature: Write validation & rejection) ------------
    def _validate_tags(self, tags: list[str]) -> None:
        unknown = [t for t in tags if not self._schema.is_valid_tag(t)]
        if not unknown:
            return
        hints = []
        for tag in unknown:
            near = self._schema.nearest_tags(tag)
            hints.append(f"{tag!r} (did you mean: {', '.join(near) or 'no close match'})")
        raise TagError("unknown tag(s): " + "; ".join(hints))

    # --- Note creation (Feature: Note creation) ----------------------------
    def create_note(
        self,
        *,
        title: str,
        note_type: str | None = None,
        pillar: str | None = None,
        body: str = "",
        tags: list[str] | None = None,
        actor: Actor = Actor.AGENT,
        mode: WriteMode = WriteMode.CREATE,
        created: str | None = None,
        directory: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> WriteResult:
        """Create a compliant note and write it through the Obsidian writer.

        Validates inputs and enforces protection *before* any write; on any
        rejection no file is created. Governance fields (provenance, created)
        are stamped by the Gate, never trusted from the caller. ``directory``
        overrides schema routing (used by the compute path to target a
        compute-only directory); ``extra_fields`` adds extra frontmatter.
        """
        tags = tags or []
        if title.strip() == "":
            raise FieldError("title is required and must be non-empty")

        self._validate_tags(tags)

        if directory is None:
            directory = self._schema.resolve_directory(note_type=note_type, pillar=pillar)
        self.check_protection(directory, actor, mode, touches_body=True)

        provenance = stamp(actor, mode)
        frontmatter = self._build_frontmatter(
            title=title,
            note_type=note_type,
            pillar=pillar,
            tags=tags,
            provenance=provenance,
            created=created if created is not None else _today(),
            extra_fields=extra_fields,
        )

        path = f"{directory}/{title}.md"
        self._io.create_note(path, _render_note(frontmatter, body))
        result = WriteResult(path=path, frontmatter=frontmatter, provenance=provenance)
        self._emit_diff(path, "create", sorted(frontmatter.keys()), provenance)
        return result

    # --- Note update (Feature: Note update) --------------------------------
    def update_note(
        self,
        path: str,
        *,
        fields: dict[str, Any] | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        actor: Actor = Actor.AGENT,
    ) -> WriteResult:
        """Update an existing note, preserving untouched content and lineage.

        Only the requested fields change; provenance advances per the
        transition rules. A body edit triggers body-protection checks while a
        metadata-only edit may be permitted on body-immutable directories.
        """
        touches_body = body is not None
        mode = WriteMode.CREATE if touches_body else WriteMode.METADATA
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        self.check_protection(directory, actor, mode, touches_body=touches_body)

        if tags is not None:
            self._validate_tags(tags)

        current_fm, current_body = _split_note(self._io.read_note(path))

        new_fm: dict[str, Any] = dict(current_fm)
        changed: list[str] = []
        if fields is not None:
            new_fm.update(fields)
            changed.extend(fields.keys())
        if tags is not None:
            new_fm["tags"] = tags
            changed.append("tags")

        raw_prov = current_fm.get("provenance")
        current_prov = (
            parse(raw_prov) if isinstance(raw_prov, str) and raw_prov else Provenance.HUMAN
        )
        new_prov = transition(current_prov, actor)
        new_fm["provenance"] = new_prov.value
        if new_prov is not current_prov:
            changed.append("provenance")

        if self._schema.updated_field is not None:
            new_fm[self._schema.updated_field] = _today()
            if self._schema.updated_field not in changed:
                changed.append(self._schema.updated_field)

        new_body = body if body is not None else current_body
        if touches_body:
            changed.append("body")

        self._io.write_note(path, _render_note(new_fm, new_body))
        result = WriteResult(path=path, frontmatter=new_fm, provenance=new_prov, created=False)
        self._emit_diff(path, "update", changed, new_prov)
        return result

    def _build_frontmatter(
        self,
        *,
        title: str,
        note_type: str | None,
        pillar: str | None,
        tags: list[str],
        provenance: Provenance,
        created: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema = self._schema
        fm: dict[str, Any] = {
            schema.label_field: title,
            schema.created_field: created,
            "provenance": provenance.value,
        }
        if schema.updated_field is not None:
            fm[schema.updated_field] = created
        reserved = {schema.label_field, schema.created_field, "provenance", schema.updated_field}
        if note_type is not None:
            fm["type"] = note_type
        if pillar is not None:
            fm["pillar"] = pillar
        if len(tags) > 0:
            fm["tags"] = tags
        if extra_fields is not None:
            for key, value in extra_fields.items():
                if key in reserved:
                    continue  # governance fields are Gate-stamped, never caller-set
                fm[key] = value

        missing = [req for req in self._schema.required_frontmatter if fm.get(req) in (None, "")]
        if len(missing) > 0:
            raise FieldError(f"missing required frontmatter field(s): {missing}")
        return fm

    # --- Write observability (Feature: Write observability) ----------------
    def _emit_diff(
        self, path: str, op: str, fields_changed: list[str], provenance: Provenance
    ) -> None:
        """Emit a structured write diff. Emission never blocks or reverts a write."""
        if self._diff_sink is None:
            return
        record = {
            "path": path,
            "op": op,
            "fields_changed": fields_changed,
            "provenance": provenance.value,
        }
        try:
            self._diff_sink(record)
        except Exception as exc:
            # Observability is non-critical: a failed emit must never fail the
            # write that already succeeded (RFC: emission failure never blocks).
            log.warning("diff emission failed for %s: %s", path, exc)
