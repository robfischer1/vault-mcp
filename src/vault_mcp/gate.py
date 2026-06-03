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

import difflib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import yaml

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


class BodyError(GateError):
    """The note body violates a content rule (placeholder / stub / template)."""


class LinkError(GateError):
    """A frontmatter link (prev/next) does not resolve to an existing note."""


class WriteModeError(GateError):
    """The note's @type write-mode forbids this write path (materialize-only / pure-DB)."""


class FilenameError(GateError):
    """The target filename or path uses a forbidden convention."""


class NoteIO(Protocol):
    """The vault IO surface the Gate depends on (Obsidian CLI implements it)."""

    def create_note(self, path: str, content: str) -> None: ...
    def read_note(self, path: str) -> str: ...
    def write_note(self, path: str, content: str) -> None: ...


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
    ``TVSeries`` preserved)."""
    return value[:1].upper() + value[1:] if value else value


# Keys beginning with '@' are YAML-reserved; PyYAML single-quotes them. FR-7 wants
# them double-quoted in emitted frontmatter.
_AT_KEY_RE = re.compile(r"(?m)^(\s*)'(@[^']+)':")

# Body-validation regexes: strip code spans/fences before scanning for bare
# angle-bracket placeholders (which break Obsidian rendering — FR-33).
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9]*>")


def _strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans so body scans ignore code."""
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))


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

    # --- Write-mode enforcement (Feature: Write-Mode Enforcement) ----------
    def _check_write_mode(self, note_type: str | None, mode: WriteMode) -> None:
        """Enforce the @type write-mode (FR — materialize-only / pure-DB).

        pure-DB types are never vault files; materialize-only types may only be
        written by the materialize/compute path (mode=COMPUTE), not agent-create.
        """
        tc = self._schema.type_config(note_type) if note_type is not None else None
        if tc is None:
            return
        if tc.write_mode == "pure-DB":
            raise WriteModeError(
                f"{note_type} is pure-DB; use atom emit or phdb directly, not a vault write"
            )
        if tc.write_mode == "materialize-only" and mode is not WriteMode.COMPUTE:
            raise WriteModeError(
                f"{note_type} is materialize-only; use the materialize verb, not agent-create"
            )

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

        self._check_write_mode(note_type, mode)
        self._validate_tags(tags)

        if directory is None:
            directory = self._schema.resolve_directory(
                note_type=note_type, pillar=pillar, attrs=extra_fields or {}
            )
        self.check_protection(directory, actor, mode, touches_body=True)

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
        self._enforce_type_rules(note_type, frontmatter)
        body = self._maybe_escape_body(directory, body)
        self._validate_body(note_type, directory, body)
        self._validate_links(frontmatter)

        tc = self._schema.type_config(note_type) if note_type is not None else None
        if tc is not None and tc.atom_slug and note_type is not None:
            filename = self._atom_filename(created if created is not None else _today(), note_type, directory)
        else:
            filename = title
        self._check_filename(directory, filename)
        path = f"{directory}/{filename}.md"
        self._io.create_note(path, _render_note(frontmatter, body))
        result = WriteResult(
            path=path,
            frontmatter=frontmatter,
            provenance=author_level,
            author_type=resolved_author_type,
            ai_model=model,
            warnings=self._tag_warnings(tags, actor)
            + self._link_warnings(directory, title, frontmatter),
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

    def _check_filename(self, directory: str, filename: str) -> None:
        """Reject numeric folder prefixes and ``Pillar -- `` filename prefixes (FR-25)."""
        for segment in directory.split("/"):
            if re.match(r"^\d+[\s_-]", segment):
                raise FilenameError(f"numeric folder prefix is not allowed: {segment!r}")
        if re.search(r"\s--\s", filename):
            raise FilenameError(
                f"' -- ' prefix is not allowed (retired 'Pillar -- ' convention): {filename!r}"
            )

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

    # --- Link validation (Feature: Link Validation) ------------------------
    def _link_resolves(self, value: object) -> bool:
        """True if a prev/next link value resolves to an existing note via the IO."""
        target = str(value).strip().strip("[]").strip()
        if target == "":
            return False
        if not target.endswith(".md"):
            target = target + ".md"
        try:
            self._io.read_note(target)
        except (KeyError, OSError):  # not found in the fake store / on disk
            return False
        return True

    def _validate_links(self, fm: dict[str, Any]) -> None:
        """Reject prev/next frontmatter that doesn't resolve to a file (FR-35)."""
        for key in ("prev", "next"):
            value = fm.get(key)
            if value is not None and not self._link_resolves(value):
                raise LinkError(f"{key} -> {value!r} does not resolve to an existing note")

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

        new_body = body if body is not None else current_body
        if touches_body:
            new_body = self._maybe_escape_body(directory, new_body)
            self._validate_body(current_fm.get("note_type"), directory, new_body)
            changed.append("body")

        self._validate_links(new_fm)
        title = path.rsplit("/", 1)[-1].removesuffix(".md")
        self._io.write_note(path, _render_note(new_fm, new_body))
        result = WriteResult(
            path=path,
            frontmatter=new_fm,
            provenance=new_level,
            author_type=new_at,
            ai_model=new_fm.get("ai_model"),
            created=False,
            warnings=self._tag_warnings(tags if tags is not None else [], actor)
            + self._link_warnings(directory, title, new_fm),
        )
        self._emit_diff(path, "update", changed, new_level)
        return result

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
        # status defaults to the schema default on create when a vocabulary exists.
        if "status" not in fm and len(schema.status_values) > 0:
            fm["status"] = schema.status_default

        # Pillar visual defaults (nn_color / nn_icon) — stamped only when the
        # caller did not supply them, so an explicit value always wins.
        pd = schema.pillar_default(pillar)
        if pd is not None:
            if pd.nn_color is not None and "nn_color" not in fm:
                fm["nn_color"] = pd.nn_color
            if pd.nn_icon is not None and "nn_icon" not in fm:
                fm["nn_icon"] = pd.nn_icon

        missing = [req for req in self._schema.required_frontmatter if fm.get(req) in (None, "")]
        if len(missing) > 0:
            raise FieldError(f"missing required frontmatter field(s): {missing}")
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

    # --- Body validation (Feature: Body Validation) ------------------------
    def _validate_body(self, note_type: str | None, directory: str, body: str) -> None:
        """Reject body-content rule violations (FR-30/31/33).

        Stub types must be body-empty; System/Templates files must use a
        Templater fence (not a literal ``---``); bare ``<Name>`` placeholders
        outside code break Obsidian rendering and are rejected.
        """
        tc = self._schema.type_config(note_type) if note_type is not None else None
        if tc is not None and tc.body_empty and body.strip() != "":
            raise BodyError(
                f"{note_type} is a DB-canonical redirect stub; its body must be empty"
            )
        if directory.startswith("System/Templates") and body.lstrip().startswith("---"):
            raise BodyError(
                "System/Templates files must use a Templater fence, not a literal '---' block"
            )
        match = _ANGLE_PLACEHOLDER_RE.search(_strip_code(body))
        if match is not None:
            token = match.group(0)
            name = token[1:-1]
            raise BodyError(
                f"bare placeholder {token!r} breaks Obsidian rendering — use {{{name}}} instead"
            )

    # --- Per-@type value enforcement (Feature: Type Registry) --------------
    def _enforce_type_rules(self, note_type: str | None, fm: dict[str, Any]) -> None:
        """Enforce per-@type required fields, value constraints, and formats.

        Also repairs + validates the global ``status`` value. Raises
        ``FieldError`` citing the specific type and field on any violation;
        ``fm`` is mutated in place to carry the repaired ``status``. Unknown
        types carry no config and pass untouched.
        """
        schema = self._schema
        if "status" in fm:
            repaired = schema.normalize_status(fm["status"])
            fm["status"] = repaired
            if not schema.is_valid_status(repaired):
                near = difflib.get_close_matches(repaired, schema.status_values, n=1)
                suggestion = f" (did you mean {near[0]!r}?)" if len(near) > 0 else ""
                raise FieldError(
                    f"status {repaired!r} is not one of "
                    f"{sorted(schema.status_values)}{suggestion}"
                )

        tc = schema.type_config(note_type) if note_type is not None else None
        if tc is None:
            return
        for req in tc.required_fields:
            if fm.get(req) in (None, ""):
                raise FieldError(f"{note_type}: missing required field {req!r}")
        for field_name, allowed in tc.value_constraints:
            value = fm.get(field_name)
            if value is not None and str(value) not in allowed:
                raise FieldError(
                    f"{note_type}.{field_name}: {value!r} is not one of {list(allowed)}"
                )
        for field_name, fmt in tc.formats:
            value = fm.get(field_name)
            if value is not None and not schema.is_valid_format(fmt, value):
                raise FieldError(f"{note_type}.{field_name}: {value!r} is not a valid {fmt}")

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
