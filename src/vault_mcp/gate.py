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

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml

from .cli_client import ObsidianIOError
from .lint import Code, LintCandidate, Linter, LintResult
from .provenance import (
    Actor,
    AuthorType,
    Provenance,
    WriteMode,
    author_type_for,
    parse,
    parse_author_type,
    stamp,
    transition,
    transition_author_type,
)
from .schema import RouteError, VaultSchema, WriteProtectionRule

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

    def create_note(self, path: str, content: str) -> None: ...
    def read_note(self, path: str) -> str: ...
    def write_note(self, path: str, content: str) -> None: ...
    def delete_note(self, path: str) -> None: ...
    def list_notes(self, directory: str = "", *, recursive: bool = True) -> list[str]: ...


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
        return self.provenance

    def to_dict(self) -> dict[str, Any]:
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
    """Ensure a leading uppercase without lowercasing the rest (``plan`` -> ``Plan``,
    ``TVSeries`` preserved).
    """
    return value[:1].upper() + value[1:] if value else value


# Keys beginning with '@' are YAML-reserved; PyYAML single-quotes them. FR-7 wants
# them double-quoted in emitted frontmatter.
_AT_KEY_RE = re.compile(r"(?m)^(\s*)'(@[^']+)':")

# Inline-tag escaping for imported bodies (FR-15): a '#tag' at a word boundary
# (so URL fragments like 'x#frag' are skipped). #activity/processed is exempt.
_INLINE_TAG_RE = re.compile(r"(?<!\S)#([A-Za-z][\w/-]*)")
_TAG_ESCAPE_EXEMPT = ("activity/processed",)


def _escape_inline_tags(body: str) -> str:
    """Escape bare ``#tag`` to ``\\#tag`` in imported body text, outside fenced code."""

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
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip("\n")
    fm_yaml = _AT_KEY_RE.sub(r'\1"\2":', fm_yaml)  # FR-7: '@type' -> "@type"
    return f"---\n{fm_yaml}\n---\n\n{body}\n"


class ConventionGate:
    """Schema- and provenance-aware write API over an injected NoteIO."""

    def __init__(self, schema: VaultSchema, io: NoteIO, diff_sink: DiffSink | None = None) -> None:
        self._schema = schema
        self._io = io
        self._diff_sink = diff_sink
        self._linter = Linter(schema, self._note_exists)
        # Materialize/compute writes come from a trusted DB source; lint them
        # anyway by default (the safety net during DB drift), off-able via the
        # env flag once the DB stabilizes — no code change to flip it.
        self._lint_on_compute = os.environ.get("VAULT_MCP_MATERIALIZE_LINT", "1") != "0"

    # --- Single internal write layer (Feature: Write Pipeline) -------------
    def _write(self, path: str, content: str, *, created: bool) -> None:
        """The one disk-write chokepoint — the only Gate code that issues a write.

        create_note, update_note, write_note, and the materialize/compute path
        all converge here, so there is exactly one place a note reaches disk.
        """
        if created:
            self._io.create_note(path, content)
        else:
            self._io.write_note(path, content)

    # --- Linter integration (Feature: Linter Core) -------------------------
    def _note_exists(self, target: str) -> bool:
        """Link-resolution predicate for the linter, backed by the injected IO."""
        try:
            self._io.read_note(target)
        except (KeyError, OSError, ObsidianIOError):
            return False
        return True

    def _raise_first_error(self, result: LintResult) -> None:
        """Reconstruct the legacy GateError for the first blocking finding.

        Preserves the historical exception type + message so existing callers
        and tests that catch a specific GateError subclass keep working, while
        ``lint()`` / ``audit`` consume the full structured ``result``.
        """
        finding = result.first_error()
        if finding is not None:
            raise _CODE_TO_EXC[finding.code](finding.message)

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
        author_type: str | None = None,
        ai_model: str | None = None,
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

        if directory is None:
            directory = self._schema.resolve_directory(
                note_type=note_type, pillar=pillar, attrs=extra_fields or {}
            )

        author_level = stamp(actor, mode)
        declared = parse_author_type(author_type) if author_type is not None else None
        resolved_author_type = author_type_for(author_level, declared)
        model = ai_model if actor is Actor.AGENT else None
        frontmatter = self._build_frontmatter(
            title=title,
            note_type=note_type,
            pillar=pillar,
            tags=tags,
            author_level=author_level,
            author_type=resolved_author_type,
            ai_model=model,
            created=created if created is not None else _today(),
            extra_fields=extra_fields,
        )
        body = self._maybe_escape_body(directory, body)

        tc = self._schema.type_config(note_type) if note_type is not None else None
        if tc is not None and tc.atom_slug and note_type is not None:
            filename = self._atom_filename(created if created is not None else _today(), note_type, directory)
        else:
            filename = title

        # One validation pass through the shared linter; reconstruct the legacy
        # exception for the first blocking finding (back-compat).
        candidate = LintCandidate(
            frontmatter=frontmatter,
            body=body,
            directory=directory,
            filename=filename,
            note_type=note_type,
            tags=tags,
            actor=actor,
            mode=mode,
            touches_body=True,
            touched_fields=None,  # create: every fault is an error
        )
        if candidate.mode is WriteMode.COMPUTE and not self._lint_on_compute:
            lint_result = LintResult()  # trusted DB source: lint disabled by flag
        else:
            lint_result = self._linter.lint(candidate)
            self._raise_first_error(lint_result)

        path = f"{directory}/{filename}.md"
        self._write(path, _render_note(frontmatter, body), created=True)
        result = WriteResult(
            path=path,
            frontmatter=frontmatter,
            provenance=author_level,
            author_type=resolved_author_type,
            ai_model=model,
            warnings=self._tag_warnings(tags, actor)
            + self._link_warnings(directory, title, frontmatter)
            + [f.message for f in lint_result.warnings],
        )
        self._emit_diff(path, "create", sorted(frontmatter.keys()), author_level)
        return result

    # --- Filename conventions (Feature: Filename Conventions) --------------
    def _atom_filename(self, created: str, note_type: str, directory: str) -> str:
        """Dated atom slug YYYY-MM-DD-{type}.{seq}; seq probes for the next free name."""
        base = f"{created}-{note_type}"
        seq = 0
        while True:
            candidate = f"{base}.{seq}"
            try:
                self._io.read_note(f"{directory}/{candidate}.md")
            except (KeyError, OSError):
                return candidate
            seq += 1

    # --- Tag enhancements (Feature: Tag Enhancements) ----------------------
    def _tag_warnings(self, tags: list[str], actor: Actor) -> list[str]:
        """Advisory (non-blocking) warnings when an agent uses reserved tags (FR-13)."""
        if actor is not Actor.AGENT:
            return []
        hits = [t for t in tags if t in self._schema.reserved_tags]
        if len(hits) == 0:
            return []
        return [f"reserved tag(s) {hits} are normally set by Rob, not agents"]

    def _maybe_escape_body(self, directory: str, body: str) -> str:
        """Escape bare inline #tags in imported bodies (References/, Records/) — FR-15."""
        if directory.startswith("References") or directory.startswith("Records"):
            return _escape_inline_tags(body)
        return body

    def _link_warnings(self, directory: str, title: str, fm: dict[str, Any]) -> list[str]:
        """Advisory (non-blocking) warnings for missing recommended links (FR-36)."""
        warns: list[str] = []
        derived = directory.startswith("Artifacts") or directory.startswith("Records")
        if derived and "isBasedOn" not in fm:
            warns.append("isBasedOn missing for an Artifacts/Records-derived note")
        last = directory.rsplit("/", 1)[-1] if directory else ""
        is_folder_note = title == last  # folder-note / pillar-root exception
        if "up" not in fm and not is_folder_note:
            warns.append("up: missing — consider linking this note to its parent")
        return warns

    # --- Note update (Feature: Note update) --------------------------------
    def update_note(
        self,
        path: str,
        *,
        fields: dict[str, Any] | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        actor: Actor = Actor.AGENT,
        ai_model: str | None = None,
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

        current_fm, current_body = _split_note(self._io.read_note(path))

        new_fm: dict[str, Any] = dict(current_fm)
        changed: list[str] = []
        if fields is not None:
            new_fm.update(fields)
            changed.extend(fields.keys())
        if tags is not None:
            new_fm["tags"] = tags
            changed.append("tags")

        self._migrate_keys(new_fm)  # clean deprecated/dead keys on every write

        # Read current author_level, falling back to the legacy single-axis
        # `provenance:` key so notes that predate the 3-property model keep working.
        raw_level = current_fm.get("author_level")
        if not (isinstance(raw_level, str) and raw_level):
            raw_level = current_fm.get("provenance")
        current_level = (
            parse(raw_level) if isinstance(raw_level, str) and raw_level else Provenance.HUMAN
        )
        raw_at = current_fm.get("author_type")
        current_at = (
            parse_author_type(raw_at)
            if isinstance(raw_at, str) and raw_at
            else author_type_for(current_level)
        )

        # Provenance-based body protection (FR-29): an AI agent may not rewrite the
        # body of human/external-authored content (metadata edits stay OK). Exception:
        # Outputs/ Articles (@type=Article) remain AI-body-mutable.
        if touches_body and actor is Actor.AGENT and current_at in (
            AuthorType.HUMAN,
            AuthorType.EXTERNAL,
        ):
            is_outputs_article = directory.startswith("Outputs") and (
                current_fm.get("@type") == "Article" or current_fm.get("note_type") == "Article"
            )
            if not is_outputs_article:
                raise ProtectionError(
                    f"{path} is {current_at.value}-authored; an AI agent may edit metadata "
                    f"only, not the body"
                )

        new_level = transition(current_level, actor)
        new_at = transition_author_type(current_at, actor)
        new_fm["author_level"] = new_level.value
        new_fm["author_type"] = new_at.value
        if "provenance" in new_fm:
            del new_fm["provenance"]  # retire the legacy single-axis key
            changed.append("provenance")
        if new_level is not current_level:
            changed.append("author_level")
        if new_at is not current_at:
            changed.append("author_type")
        if ai_model is not None and actor is Actor.AGENT:
            new_fm["ai_model"] = ai_model
            changed.append("ai_model")

        if self._schema.updated_field is not None:
            new_fm[self._schema.updated_field] = _today()
            if self._schema.updated_field not in changed:
                changed.append(self._schema.updated_field)

        if "status" in new_fm:
            new_fm["status"] = self._schema.normalize_status(new_fm["status"])

        new_body = body if body is not None else current_body
        if touches_body:
            new_body = self._maybe_escape_body(directory, new_body)
            changed.append("body")

        title = path.rsplit("/", 1)[-1].removesuffix(".md")
        tags_val = new_fm.get("tags")
        if isinstance(tags_val, list):
            tags_list = tags_val
        elif tags_val in (None, ""):
            tags_list = []
        else:
            tags_list = [tags_val]
        touched = set(changed)
        if touches_body:
            touched.add("body")

        # Lint the merged target state; faults on fields this write did not
        # touch are pre-existing drift and surface as warnings, not blocks — so
        # an unrelated edit is never rejected by a note's existing bad value.
        candidate = LintCandidate(
            frontmatter=new_fm,
            body=new_body,
            directory=directory,
            filename=title,
            note_type=current_fm.get("note_type"),
            tags=tags_list,
            actor=actor,
            mode=mode,
            touches_body=touches_body,
            touched_fields=touched,
        )
        lint_result = self._linter.lint(candidate)
        self._raise_first_error(lint_result)

        self._write(path, _render_note(new_fm, new_body), created=False)
        result = WriteResult(
            path=path,
            frontmatter=new_fm,
            provenance=new_level,
            author_type=new_at,
            ai_model=new_fm.get("ai_model"),
            created=False,
            warnings=self._tag_warnings(tags if tags is not None else [], actor)
            + self._link_warnings(directory, title, new_fm)
            + [f.message for f in lint_result.warnings],
        )
        self._emit_diff(path, "update", changed, new_level)
        return result

    # --- Unified write surface (Feature: write_note) -----------------------
    def write_note(
        self,
        *,
        title: str,
        note_type: str | None = None,
        pillar: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        actor: Actor = Actor.AGENT,
        fields: dict[str, Any] | None = None,
        ai_model: str | None = None,
        author_type: str | None = None,
        created: str | None = None,
        directory: str | None = None,
        mode: str = "upsert",
    ) -> WriteResult:
        """Create-or-update a note by its routed path — the single write surface.

        Resolves the target path from ``title`` + routing (or ``directory``),
        then dispatches: an existing note is *updated* (merge ``fields``/``body``/
        ``tags``, advance provenance), a missing one is *created*. The caller need
        not know which. ``mode`` guards accidental clobber: ``upsert`` (default)
        does either, ``create`` refuses an existing path, ``update`` refuses a
        missing one.

        On update, ``body``/``tags``/``fields`` left ``None`` stay untouched (a
        metadata-only edit); ``note_type``/``pillar`` are fixed at create and
        ignored on update (re-routing an existing note is not a write).
        """
        if mode not in ("upsert", "create", "update"):
            raise FieldError(f"mode must be 'upsert', 'create', or 'update', not {mode!r}")

        tc = self._schema.type_config(note_type) if note_type is not None else None
        if tc is not None and tc.atom_slug:
            # Dated atom slugs are append-only — always a fresh create.
            if mode == "update":
                raise FieldError("atom-slug types are create-only; mode='update' is invalid")
            return self.create_note(
                title=title, note_type=note_type, pillar=pillar, body=body or "",
                tags=tags, actor=actor, created=created, directory=directory,
                extra_fields=fields, author_type=author_type, ai_model=ai_model,
            )

        target_dir = directory
        if target_dir is None:
            target_dir = self._schema.resolve_directory(
                note_type=note_type, pillar=pillar, attrs=fields or {}
            )
        path = f"{target_dir}/{title}.md"
        exists = self._note_exists(path)

        if exists and mode == "create":
            raise FieldError(f"{path} already exists (mode='create' forbids overwrite)")
        if not exists and mode == "update":
            raise FieldError(f"{path} does not exist (mode='update' requires it)")

        if exists:
            return self.update_note(
                path, fields=fields, body=body, tags=tags, actor=actor, ai_model=ai_model
            )
        return self.create_note(
            title=title, note_type=note_type, pillar=pillar, body=body or "",
            tags=tags, actor=actor, created=created, directory=directory,
            extra_fields=fields, author_type=author_type, ai_model=ai_model,
        )

    # --- Delete to trash (Feature: delete) ---------------------------------
    def delete(self, path: str, *, actor: Actor = Actor.AGENT) -> dict[str, Any]:
        """Move a note to Obsidian's ``.trash/`` (reversible), if permitted.

        Runs the same write-protection rules as a write — an agent cannot trash a
        voice-only, compute-only, fully-immutable, or body-immutable note — then
        delegates to the IO's trash move. No lint (there is no payload).
        """
        if not self._note_exists(path):
            raise FieldError(f"{path} does not exist")
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        self.check_protection(directory, actor, WriteMode.CREATE, touches_body=True)
        self._io.delete_note(path)
        self._emit_diff(path, "delete", [], stamp(actor, WriteMode.METADATA))
        return {"ok": True, "path": path, "deleted": True}

    # --- Move / relocate (Feature: move_note) --------------------------------
    def move_note(
        self, src: str, dst: str, *, actor: Actor = Actor.AGENT,
    ) -> dict[str, Any]:
        """Move a note from src to dst, preserving content and frontmatter.

        Checks write-protection on both the source directory (must allow
        deletion) and the destination directory (must allow creation).
        Raises FieldError if source doesn't exist or destination already exists.
        """
        if not self._note_exists(src):
            raise FieldError(f"source {src!r} does not exist")
        if self._note_exists(dst):
            raise FieldError(f"destination {dst!r} already exists")

        src_dir = src.rsplit("/", 1)[0] if "/" in src else ""
        dst_dir = dst.rsplit("/", 1)[0] if "/" in dst else ""
        self.check_protection(src_dir, actor, WriteMode.CREATE, touches_body=True)
        self.check_protection(dst_dir, actor, WriteMode.CREATE, touches_body=False)

        content = self._io.read_note(src)
        self._write(dst, content, created=True)
        self._io.delete_note(src)
        self._emit_diff(src, "delete", [], stamp(actor, WriteMode.METADATA))
        self._emit_diff(dst, "create", [], stamp(actor, WriteMode.METADATA))
        return {"ok": True, "src": src, "dst": dst, "moved": True}

    # --- Drift audit + heal (Feature: Audit) -------------------------------
    def audit(
        self, directory: str = "", *, resolve: bool = False, all_dirs: bool = False
    ) -> dict[str, Any]:
        """Scan a directory (or the whole vault) for schema drift; optionally heal.

        ``resolve=False`` reports every drifted note and writes nothing.
        ``resolve=True`` corrects each note's deterministic faults (deprecated-key
        renames, dead-key drops, status repairs, routing relocation), re-lints the
        corrected target state, and rewrites the note only when that state is
        clean — uncorrectable faults (unknown tags, missing required fields,
        untyped notes) are reported for a human, never guessed.
        """
        scope = "" if all_dirs else directory
        paths = self._io.list_notes(scope, recursive=True)
        notes: list[dict[str, Any]] = []
        corrected = 0
        for note_path in paths:
            try:
                fm, body = _split_note(self._io.read_note(note_path))
            except (KeyError, OSError, ObsidianIOError):
                continue
            note_dir = note_path.rsplit("/", 1)[0] if "/" in note_path else ""
            filename = note_path.rsplit("/", 1)[-1].removesuffix(".md")
            note_type = fm.get("note_type")
            drift = self._scan_drift(fm, body, note_dir, filename, note_type)
            if not drift:
                continue
            entry: dict[str, Any] = {"path": note_path, "note_type": note_type, "drift": drift}
            if resolve:
                healed = self._heal(note_path, fm, body, note_dir, filename, note_type)
                entry["corrected"] = healed["corrected"]
                entry["corrections"] = healed["corrections"]
                entry["remaining"] = healed["remaining"]
                if healed["corrected"]:
                    corrected += 1
            notes.append(entry)
        return {
            "ok": True,
            "scope": scope or "<all>",
            "scanned": len(paths),
            "drifted": len(notes),
            "corrected": corrected if resolve else None,
            "resolve": resolve,
            "notes": notes,
        }

    def _tags_of(self, fm: dict[str, Any]) -> list[str]:
        val = fm.get("tags")
        if isinstance(val, list):
            return [str(t) for t in val]
        return [] if val in (None, "") else [str(val)]

    def _scan_drift(
        self,
        fm: dict[str, Any],
        body: str,
        directory: str,
        filename: str,
        note_type: str | None,
    ) -> list[dict[str, Any]]:
        """Collect every drift item for one note: untyped, deprecated/dead keys,
        all linter errors, and routing misplacement.
        """
        drift: list[dict[str, Any]] = []
        if note_type is None:
            drift.append({"category": "untyped", "detail": "no note_type"})
        for old, new in self._schema.deprecated_renames:
            if old in fm:
                drift.append({"category": "deprecated_key", "field": old, "detail": f"{old} -> {new}"})
        for dead in self._schema.dead_keys:
            if dead in fm:
                drift.append({"category": "dead_key", "field": dead, "detail": f"drop {dead}"})
        candidate = LintCandidate(
            frontmatter=fm, body=body, directory=directory, filename=filename,
            note_type=note_type, tags=self._tags_of(fm), actor=Actor.AGENT,
            mode=WriteMode.CREATE, touches_body=True, touched_fields=None,
        )
        for finding in self._linter.lint(candidate).errors:
            drift.append({"category": finding.code.value, "field": finding.field, "detail": finding.message})
        try:
            correct = self._schema.resolve_directory(
                note_type=note_type, pillar=fm.get("pillar"), attrs=fm
            )
            if correct != directory:
                drift.append(
                    {"category": "routing", "detail": f"in {directory!r}, should be {correct!r}", "target": correct}
                )
        except RouteError:
            pass  # no resolvable route — not a routing-drift signal
        return drift

    def _heal(
        self,
        path: str,
        fm: dict[str, Any],
        body: str,
        directory: str,
        filename: str,
        note_type: str | None,
    ) -> dict[str, Any]:
        """Apply deterministic corrections, then write only if the result is clean."""
        new_fm = dict(fm)
        corrections: list[str] = []
        before = set(new_fm)
        self._migrate_keys(new_fm)
        for old, new in self._schema.deprecated_renames:
            if old in before and old not in new_fm:
                corrections.append(f"renamed {old} -> {new}")
        for dead in self._schema.dead_keys:
            if dead in before and dead not in new_fm:
                corrections.append(f"dropped {dead}")
        if "status" in new_fm:
            repaired = self._schema.normalize_status(new_fm["status"])
            if repaired != new_fm["status"]:
                corrections.append(f"status {new_fm['status']!r} -> {repaired!r}")
                new_fm["status"] = repaired

        try:
            target_dir = self._schema.resolve_directory(
                note_type=note_type, pillar=new_fm.get("pillar"), attrs=new_fm
            )
        except RouteError:
            target_dir = directory
        moving = target_dir != directory
        new_path = f"{target_dir}/{filename}.md"
        collision = moving and self._note_exists(new_path)
        write_dir = directory if collision else target_dir

        candidate = LintCandidate(
            frontmatter=new_fm, body=body, directory=write_dir, filename=filename,
            note_type=note_type, tags=self._tags_of(new_fm), actor=Actor.AGENT,
            mode=WriteMode.CREATE, touches_body=True, touched_fields=None,
        )
        result = self._linter.lint(candidate)
        remaining = [f.message for f in result.errors]
        if note_type is None:
            remaining.append("untyped: set note_type manually")
        if collision:
            remaining.append(f"routing target {new_path!r} already exists")

        if not result.ok:
            return {"corrected": False, "corrections": corrections, "remaining": remaining}

        rendered = _render_note(new_fm, body)
        if moving and not collision:
            self._write(new_path, rendered, created=True)
            self._io.delete_note(path)
            corrections.append(f"moved {directory!r} -> {target_dir!r}")
            return {"corrected": True, "corrections": corrections, "remaining": remaining}
        if corrections:
            self._write(path, rendered, created=False)
            return {"corrected": True, "corrections": corrections, "remaining": remaining}
        return {"corrected": False, "corrections": [], "remaining": remaining}

    # --- Dry-run validation (Feature: lint() tool) -------------------------
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
        directory = self._schema.resolve_directory(
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
        return self._linter.lint(candidate).to_dict()

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
        schema = self._schema
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
            fm["note_type"] = _title_case_note_type(note_type)  # FR: Title-Case note_type
        if pillar is not None:
            fm["pillar"] = pillar
        if len(tags) > 0:
            fm["tags"] = tags
        if extra_fields is not None:
            for key, value in extra_fields.items():
                if key in reserved:
                    continue  # governance fields are Gate-stamped, never caller-set
                fm[key] = value

        self._migrate_keys(fm)  # rename deprecated keys + drop dead keys (before defaults)

        # identifier defaults to a kebab slug of the label; caller override wins.
        if fm.get("identifier") in (None, ""):
            fm["identifier"] = _slugify(title)
        # status defaults to the schema default on create when a vocabulary exists,
        # then normalizes (repairs) so the linter validates the stored value.
        if "status" not in fm and len(schema.status_values) > 0:
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

    # --- Deprecated key migration (Feature: Deprecated Key Migration) ------
    def _migrate_keys(self, fm: dict[str, Any]) -> None:
        """Rename deprecated keys (preserving value) and drop dead keys, in place.

        Renames never clobber an existing canonical key; dead keys are removed
        unconditionally. Driven by the schema so the maps stay config, not code.
        """
        for old, new in self._schema.deprecated_renames:
            if old in fm:
                if new not in fm:
                    fm[new] = fm[old]
                del fm[old]
        for dead in self._schema.dead_keys:
            fm.pop(dead, None)

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
