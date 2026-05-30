"""Convention Gate — the deterministic write API for vault-mcp v2.

Every vault write passes through here. The Gate generates compliant
frontmatter, validates tags against the closed glossary, routes the file to
the schema-resolved directory, enforces per-directory write-protection, and
stamps provenance — then hands the finished note to a writer (the Obsidian
CLI in production, a fake in tests). Agents need not know the rules because
they cannot violate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml

from .provenance import Actor, Provenance, WriteMode, stamp
from .schema import VaultSchema, WriteProtectionRule


class GateError(Exception):
    """Base class for Convention Gate rejections."""


class TagError(GateError):
    """One or more tags are outside the closed glossary."""


class FieldError(GateError):
    """A required frontmatter field is missing or invalid."""


class ProtectionError(GateError):
    """The target directory is write-protected for this write."""


class NoteWriter(Protocol):
    """The write surface the Gate depends on (Obsidian CLI implements it)."""

    def create_note(self, path: str, content: str) -> None: ...


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
            "frontmatter": self.frontmatter,
            "provenance": self.provenance.value,
        }


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class ConventionGate:
    """Schema- and provenance-aware write API over an injected NoteWriter."""

    def __init__(self, schema: VaultSchema, writer: NoteWriter) -> None:
        self._schema = schema
        self._writer = writer

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
    ) -> WriteResult:
        """Create a compliant note and write it through the Obsidian writer.

        Validates inputs and enforces protection *before* any write; on any
        rejection no file is created. Governance fields (provenance, created)
        are stamped by the Gate, never trusted from the caller.
        """
        tags = tags or []
        if title.strip() == "":
            raise FieldError("title is required and must be non-empty")

        self._validate_tags(tags)

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
        )

        path = f"{directory}/{title}.md"
        self._writer.create_note(path, _render_note(frontmatter, body))
        return WriteResult(path=path, frontmatter=frontmatter, provenance=provenance)

    def _build_frontmatter(
        self,
        *,
        title: str,
        note_type: str | None,
        pillar: str | None,
        tags: list[str],
        provenance: Provenance,
        created: str,
    ) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "title": title,
            "created": created,
            "provenance": provenance.value,
        }
        if note_type is not None:
            fm["type"] = note_type
        if pillar is not None:
            fm["pillar"] = pillar
        if len(tags) > 0:
            fm["tags"] = tags

        missing = [req for req in self._schema.required_frontmatter if fm.get(req) in (None, "")]
        if len(missing) > 0:
            raise FieldError(f"missing required frontmatter field(s): {missing}")
        return fm


def _render_note(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize frontmatter + body into a markdown note."""
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{fm_yaml}\n---\n\n{body}\n"
