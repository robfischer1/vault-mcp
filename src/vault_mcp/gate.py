"""Convention Gate — the deterministic write API for vault-mcp v2.

Every vault write passes through here. The Gate generates compliant
frontmatter, validates tags against the closed glossary, routes the file to
the schema-resolved directory, enforces per-directory write-protection, and
stamps provenance — then hands the finished note to a writer (the Obsidian
CLI in production, a fake in tests). Agents need not know the rules because
they cannot violate them.

The full deny/protection surface is documented in
``docs/security/write-protections.md``.
"""

# VERIFY: `dict[str, Any]` at the JSON boundary, and only there.
#
# An MCP tool return IS a JSON object, so the value type is open by the
# protocol's own contract — pinning it to a TypedDict per verb would encode a
# wire shape the client is free to ignore, and would still be `Any` one level
# down where Obsidian's REST payloads and YAML frontmatter arrive untyped.
# Measured 2026-08-22: of 276 `Any` in this package, 127 are `-> dict[str, Any]`
# verb returns and 34 are `list[dict[str, Any]]` rows of the same. This is a
# stated decision at the boundary, not an unexamined default.
#
# What is NOT excused by it: a BARE `: Any` or `-> Any` on anything that is not
# that boundary. Those were audited to zero in this package on the same date —
# the survivors are three sites in the Bases formula evaluator, each carrying
# its own VERIFY where it sits.

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml

from .lint import Code, Linter
from .provenance import (
    Actor,
    AuthorType,
    Provenance,
    WriteMode,
)

# RUNTIME import, despite ruff's TC001. These names are not annotation-only:
# `_protection_for` returns a WriteProtectionRule and the collaborators import
# them from here at runtime, so moving them into TYPE_CHECKING breaks the
# import chain. TC001 reads this module in isolation and cannot see that.
from .schema import VaultSchema, WriteProtectionRule  # noqa: TC001

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


class BodyError(GateError):
    """The note body violates a content rule (placeholder / stub / template)."""


class LinkError(GateError):
    """A frontmatter link (prev/next) does not resolve to an existing note."""


class WriteModeError(GateError):
    """The note's @type write-mode forbids this write path (materialize-only / pure-DB)."""


class FilenameError(GateError):
    """The target filename or path uses a forbidden convention."""


# Finding codes map 1:1 onto the GateError subclasses above, so the write path
# can reconstruct the historical exception + message from a linter Finding.
_CODE_TO_EXC: dict[Code, type[GateError]] = {
    Code.TAG: TagError,
    Code.FIELD: FieldError,
    Code.PROTECTION: ProtectionError,
    Code.BODY: BodyError,
    Code.LINK: LinkError,
    Code.WRITE_MODE: WriteModeError,
    Code.FILENAME: FilenameError,
}


class NoteIO(Protocol):
    """The vault IO surface the Gate depends on (Obsidian CLI implements it)."""

    def create_note(self, path: str, content: str) -> None:
        """Create a note at `path` with `content`."""

    def read_note(self, path: str) -> str:
        """Read and return the note body at `path`."""
        ...

    def write_note(self, path: str, content: str) -> None:
        """Overwrite the note at `path` with `content`."""

    def delete_note(self, path: str) -> None:
        """Delete the note at `path`."""

    def list_notes(
        self, directory: str = "", *, recursive: bool = True
    ) -> list[str]:
        """List note paths under `directory`."""
        ...


@dataclass
class WriteResult:
    """The structured echo returned for every successful write.

    ``provenance`` is retained as a back-compat alias of ``author_level`` (V2D
    3-property model); ``to_dict`` emits author_type / author_level / ai_model.
    """

    path: str
    frontmatter: dict[str, Any]
    provenance: Provenance  # == author_level (back-compat alias)
    author_type: AuthorType = AuthorType.AI
    ai_model: str | None = None
    created: bool = field(default=True)
    warnings: list[str] = field(default_factory=list)

    @property
    def author_level(self) -> Provenance:
        """Return the author level (alias of provenance)."""
        return self.provenance

    def to_dict(self) -> dict[str, Any]:
        """Return this write result as a JSON-serializable dict."""
        return {
            "ok": True,
            "path": self.path,
            "created": self.created,
            "frontmatter": self.frontmatter,
            "author_type": self.author_type.value,
            "author_level": self.provenance.value,
            "ai_model": self.ai_model,
            "warnings": self.warnings,
        }


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _slugify(text: str) -> str:
    """Kebab-case slug of ``text`` for identifier autogen (lower, alnum + hyphens)."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _title_case_note_type(value: str) -> str:
    """Capitalize the first letter without lowercasing the rest (``plan`` -> ``Plan``, ``TVSeries`` preserved)."""
    return value[:1].upper() + value[1:] if value else value


# Keys beginning with '@' are YAML-reserved; PyYAML single-quotes them. FR-7 wants
# them double-quoted in emitted frontmatter.
_AT_KEY_RE = re.compile(r"(?m)^(\s*)'(@[^']+)':")

# Inline-tag escaping for imported bodies (FR-15): a '#tag' at a word boundary
# (so URL fragments like 'x#frag' are skipped). #activity/processed is exempt.
_INLINE_TAG_RE = re.compile(r"(?<!\S)#([A-Za-z][\w/-]*)")
_TAG_ESCAPE_EXEMPT = ("activity/processed",)


def _escape_inline_tags(body: str) -> str:
    r"""Escape bare ``#tag`` to ``\\#tag`` in imported body text, outside fenced code."""

    def repl(m: re.Match[str]) -> str:
        tag = m.group(1)
        if tag.startswith(_TAG_ESCAPE_EXEMPT):
            return m.group(0)
        return "\\#" + tag

    parts = re.split(r"(```.*?```)", body, flags=re.DOTALL)
    for i in range(0, len(parts), 2):  # even indices are outside fenced code
        parts[i] = _INLINE_TAG_RE.sub(repl, parts[i])
    return "".join(parts)


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
    fm_yaml = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    fm_yaml = _AT_KEY_RE.sub(r'\1"\2":', fm_yaml)  # FR-7: '@type' -> "@type"
    return f"---\n{fm_yaml}\n---\n\n{body}\n"


class ConventionGate:
    """Schema- and provenance-aware write API over an injected NoteIO."""

    def __init__(
        self, schema: VaultSchema, io: NoteIO, diff_sink: DiffSink | None = None
    ) -> None:
        """Build a Gate over a schema and NoteIO, with an optional diff sink."""
        self._schema = schema
        self._io = io
        self._diff_sink = diff_sink
        self._linter = Linter(schema, self._note_exists)
        # Materialize/compute writes come from a trusted DB source; lint them
        # anyway by default (the safety net during DB drift), off-able via the
        # env flag once the DB stabilizes — no code change to flip it.
        self._lint_on_compute = (
            os.environ.get("VAULT_MCP_MATERIALIZE_LINT", "1") != "0"
        )

        # DELEGATION (vault-mcp#5294): the write, audit and frontmatter
        # behaviours live in named collaborators rather than as 900 lines of
        # methods here. Each takes `self` and reads gate state through it, so
        # the state stays single-sourced and a reader looking for `audit` finds
        # GateAuditor by name instead of walking an MRO.
        #
        # Imported inside __init__ rather than at module scope: each
        # collaborator imports ConventionGate for typing, so a module-scope
        # import would be a cycle.
        from vault_mcp.gate_auditor import GateAuditor
        from vault_mcp.gate_frontmatter import FrontmatterBuilder
        from vault_mcp.gate_writer import GateWriter

        self._writer = GateWriter(self)
        self._auditor = GateAuditor(self)
        self._frontmatter = FrontmatterBuilder(self)

    # --- Single internal write layer (Feature: Write Pipeline) -------------

    # --- Linter integration (Feature: Linter Core) -------------------------

    # --- Write-protection (Feature: Write-protection enforcement) ----------
    def _protection_for(self, directory: str) -> WriteProtectionRule | None:
        for rule in self._schema.write_protection:
            if directory == rule.directory or directory.startswith(
                rule.directory + "/"
            ):
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
        if rule.rule == "exempt":
            # A carve-out: _protection_for is first-match, so an exempt entry
            # ordered before a broader rule lifts it for its subtree.
            return
        if rule.rule == "fully-immutable":
            raise ProtectionError(rule.error)
        if rule.rule == "body-immutable" and touches_body:
            raise ProtectionError(rule.error)
        if rule.rule == "compute-only" and mode is not WriteMode.COMPUTE:
            raise ProtectionError(rule.error)
        if rule.rule == "voice-only" and actor is not Actor.HUMAN:
            raise ProtectionError(rule.error)

    # --- Note creation (Feature: Note creation) ----------------------------

    # --- Filename conventions (Feature: Filename Conventions) --------------

    # --- Tag enhancements (Feature: Tag Enhancements) ----------------------

    # --- Note update (Feature: Note update) --------------------------------

    # --- Unified write surface (Feature: write_note) -----------------------

    # --- Delete to trash (Feature: delete) ---------------------------------

    # --- Move / relocate (Feature: move_note) --------------------------------

    # --- Drift audit + heal (Feature: Audit) -------------------------------

    # --- Dry-run validation (Feature: lint() tool) -------------------------

    # --- Deprecated key migration (Feature: Deprecated Key Migration) ------

    # --- Write observability (Feature: Write observability) ----------------

    # --- delegated surface --------------------------------------------------
    # Thin forwards, so ConventionGate's public API is unchanged: every caller
    # and every test still says `gate.create_note(...)`. The collaborators are
    # reachable directly (`gate._writer`) when a caller wants to be explicit.

    def create_note(self, *args: Any, **kwargs: Any) -> WriteResult:
        """Create a compliant note through the Gate. Delegates to GateWriter."""
        return self._writer.create_note(*args, **kwargs)

    def update_note(self, *args: Any, **kwargs: Any) -> WriteResult:
        """Update an existing note through the Gate. Delegates to GateWriter."""
        return self._writer.update_note(*args, **kwargs)

    def write_note(self, *args: Any, **kwargs: Any) -> WriteResult:
        """Create-or-update through the Gate. Delegates to GateWriter."""
        return self._writer.write_note(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delete a note through the Gate. Delegates to GateWriter."""
        return self._writer.delete(*args, **kwargs)

    def move_note(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Move a note through the Gate. Delegates to GateWriter."""
        return self._writer.move_note(*args, **kwargs)

    def audit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Scan the vault for drift and optionally heal it. Delegates to GateAuditor."""
        return self._auditor.audit(*args, **kwargs)

    def lint_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Lint a prospective write. Delegates to FrontmatterBuilder."""
        return self._frontmatter.lint_payload(*args, **kwargs)

    def _note_exists(self, target: str) -> bool:
        """Link-resolution predicate, used by the linter. Delegates to GateWriter."""
        return self._writer._note_exists(target)
