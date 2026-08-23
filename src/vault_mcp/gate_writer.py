"""The Gate's WRITE collaborator — create, update, delete and move a note.

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

from vault_mcp.cli_client import ObsidianIOError
from vault_mcp.gate import (
    FieldError,
    ProtectionError,
    WriteResult,
    _render_note,
    _split_note,
    _today,
)
from vault_mcp.lint import LintCandidate, LintResult
from vault_mcp.provenance import (
    Actor,
    AuthorType,
    Provenance,
    ProvenanceError,
    WriteMode,
    author_type_for,
    parse,
    parse_author_type,
    stamp,
    transition,
    transition_author_type,
)

# Own logger, not the gate's. Beyond correct provenance in the log record,
# ruff's BLE001 permits a blind except whose body calls log.exception — but only
# when it can SEE that `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vault_mcp.gate import ConventionGate


class GateWriter:
    """The Gate's WRITE collaborator — create, update, delete and move a note."""

    def __init__(self, gate: ConventionGate) -> None:
        """Bind to the gate whose state this collaborator operates on."""
        self._gate = gate

    def _write(self, path: str, content: str, *, created: bool) -> None:
        """Issue the one disk write — the single chokepoint every write path converges on.

        create_note, update_note, write_note, and the materialize/compute path
        all converge here, so there is exactly one place a note reaches disk.
        """
        if created:
            self._gate._io.create_note(path, content)
        else:
            self._gate._io.write_note(path, content)

    def _note_exists(self, target: str) -> bool:
        """Link-resolution predicate for the linter, backed by the injected IO."""
        try:
            self._gate._io.read_note(target)
        except KeyError, OSError, ObsidianIOError:
            return False
        return True

    def _atom_filename(
        self, created: str, note_type: str, directory: str
    ) -> str:
        """Dated atom slug YYYY-MM-DD-{type}.{seq}; seq probes for the next free name.

        ObsidianIOError is load-bearing in the except, not defensive breadth:
        it is what BOTH real NoteIO implementations raise for a missing note,
        and "missing" is the answer this probe is looking for. Without it every
        atom write failed in production while the suite stayed green, because
        the only NoteIOs the tests ever used were dict fakes raising KeyError
        (vault-mcp#5258). The two sibling call sites already caught all three.
        """
        base = f"{created}-{note_type}"
        seq = 0
        while True:
            candidate = f"{base}.{seq}"
            try:
                self._gate._io.read_note(f"{directory}/{candidate}.md")
            except KeyError, OSError, ObsidianIOError:
                return candidate
            seq += 1

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
            directory = self._gate._schema.resolve_directory(
                note_type=note_type, pillar=pillar, attrs=extra_fields or {}
            )

        author_level = stamp(actor, mode)
        declared = (
            parse_author_type(author_type) if author_type is not None else None
        )
        resolved_author_type = author_type_for(author_level, declared)
        model = ai_model if actor is Actor.AGENT else None
        frontmatter = self._gate._frontmatter._build_frontmatter(
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
        body = self._gate._frontmatter._maybe_escape_body(directory, body)

        tc = (
            self._gate._schema.type_config(note_type)
            if note_type is not None
            else None
        )
        if tc is not None and tc.atom_slug and note_type is not None:
            filename = self._atom_filename(
                created if created is not None else _today(),
                note_type,
                directory,
            )
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
        if (
            candidate.mode is WriteMode.COMPUTE
            and not self._gate._lint_on_compute
        ):
            lint_result = (
                LintResult()
            )  # trusted DB source: lint disabled by flag
        else:
            lint_result = self._gate._linter.lint(candidate)
            self._gate._frontmatter._raise_first_error(lint_result)

        path = f"{directory}/{filename}.md"
        self._write(path, _render_note(frontmatter, body), created=True)
        result = WriteResult(
            path=path,
            frontmatter=frontmatter,
            provenance=author_level,
            author_type=resolved_author_type,
            ai_model=model,
            warnings=self._gate._frontmatter._tag_warnings(tags, actor)
            + self._gate._frontmatter._link_warnings(
                directory, title, frontmatter
            )
            + [f.message for f in lint_result.warnings],
        )
        self._gate._frontmatter._emit_diff(
            path, "create", sorted(frontmatter.keys()), author_level
        )
        return result

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
        self._gate.check_protection(
            directory, actor, mode, touches_body=touches_body
        )

        current_fm, current_body = _split_note(self._gate._io.read_note(path))

        new_fm: dict[str, Any] = dict(current_fm)
        changed: list[str] = []
        if fields is not None:
            new_fm.update(fields)
            changed.extend(fields.keys())
        if tags is not None:
            new_fm["tags"] = tags
            changed.append("tags")

        self._gate._frontmatter._migrate_keys(
            new_fm
        )  # clean deprecated/dead keys on every write

        # Read current author_level, falling back to the legacy single-axis
        # `provenance:` key so notes that predate the 3-property model keep working.
        raw_level = current_fm.get("author_level")
        if not (isinstance(raw_level, str) and raw_level):
            raw_level = current_fm.get("provenance")
        current_level = (
            parse(raw_level)
            if isinstance(raw_level, str) and raw_level
            else Provenance.HUMAN
        )
        # Resolve the current author_type, self-healing a stale value rather
        # than hard-blocking the update (obsidian-vault#1021). A pre-tightening
        # value (e.g. `ai-generated`) is first run through the schema repair
        # map; anything still off-category is re-derived from author_level. The
        # repair surfaces as a warning, never a rejection — so an unrelated edit
        # is no longer wedged by a note's legacy author_type.
        raw_at = current_fm.get("author_type")
        provenance_warnings: list[str] = []
        if isinstance(raw_at, str) and raw_at:
            normalized_at = self._gate._schema.normalize_author_type(raw_at)
            try:
                current_at = parse_author_type(normalized_at)
            except ProvenanceError:
                current_at = author_type_for(current_level)
            if current_at.value != raw_at:
                provenance_warnings.append(
                    f"author_type {raw_at!r} repaired to {current_at.value!r} "
                    f"(stale pre-enum value)"
                )
        else:
            current_at = author_type_for(current_level)

        # Provenance-based body protection (FR-29): an AI agent may not rewrite the
        # body of human/external-authored content (metadata edits stay OK). Exception:
        # Outputs/ Articles (@type=Article) remain AI-body-mutable.
        if (
            touches_body
            and actor is Actor.AGENT
            and current_at
            in (
                AuthorType.HUMAN,
                AuthorType.EXTERNAL,
            )
        ):
            is_outputs_article = directory.startswith("Outputs") and (
                current_fm.get("@type") == "Article"
                or current_fm.get("note_type") == "Article"
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
        # Compare against the on-disk values so a self-heal repair (which changes
        # the stored string even when the resolved enum is otherwise unchanged)
        # is recorded as a real field change.
        if new_fm.get("author_level") != current_fm.get("author_level"):
            changed.append("author_level")
        if new_fm.get("author_type") != current_fm.get("author_type"):
            changed.append("author_type")
        if ai_model is not None and actor is Actor.AGENT:
            new_fm["ai_model"] = ai_model
            changed.append("ai_model")

        if self._gate._schema.updated_field is not None:
            new_fm[self._gate._schema.updated_field] = _today()
            if self._gate._schema.updated_field not in changed:
                changed.append(self._gate._schema.updated_field)

        if "status" in new_fm:
            new_fm["status"] = self._gate._schema.normalize_status(
                new_fm["status"]
            )

        new_body = body if body is not None else current_body
        if touches_body:
            new_body = self._gate._frontmatter._maybe_escape_body(
                directory, new_body
            )
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
        lint_result = self._gate._linter.lint(candidate)
        self._gate._frontmatter._raise_first_error(lint_result)

        self._write(path, _render_note(new_fm, new_body), created=False)
        result = WriteResult(
            path=path,
            frontmatter=new_fm,
            provenance=new_level,
            author_type=new_at,
            ai_model=new_fm.get("ai_model"),
            created=False,
            warnings=provenance_warnings
            + self._gate._frontmatter._tag_warnings(
                tags if tags is not None else [], actor
            )
            + self._gate._frontmatter._link_warnings(directory, title, new_fm)
            + [f.message for f in lint_result.warnings],
        )
        self._gate._frontmatter._emit_diff(path, "update", changed, new_level)
        return result

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
            raise FieldError(
                f"mode must be 'upsert', 'create', or 'update', not {mode!r}"
            )

        tc = (
            self._gate._schema.type_config(note_type)
            if note_type is not None
            else None
        )
        if tc is not None and tc.atom_slug:
            # Dated atom slugs are append-only — always a fresh create.
            if mode == "update":
                raise FieldError(
                    "atom-slug types are create-only; mode='update' is invalid"
                )
            return self.create_note(
                title=title,
                note_type=note_type,
                pillar=pillar,
                body=body or "",
                tags=tags,
                actor=actor,
                created=created,
                directory=directory,
                extra_fields=fields,
                author_type=author_type,
                ai_model=ai_model,
            )

        target_dir = directory
        if target_dir is None:
            target_dir = self._gate._schema.resolve_directory(
                note_type=note_type, pillar=pillar, attrs=fields or {}
            )
        path = f"{target_dir}/{title}.md"
        exists = self._note_exists(path)

        if exists and mode == "create":
            raise FieldError(
                f"{path} already exists (mode='create' forbids overwrite)"
            )
        if not exists and mode == "update":
            raise FieldError(
                f"{path} does not exist (mode='update' requires it)"
            )

        if exists:
            return self.update_note(
                path,
                fields=fields,
                body=body,
                tags=tags,
                actor=actor,
                ai_model=ai_model,
            )
        return self.create_note(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body or "",
            tags=tags,
            actor=actor,
            created=created,
            directory=directory,
            extra_fields=fields,
            author_type=author_type,
            ai_model=ai_model,
        )

    def delete(
        self, path: str, *, actor: Actor = Actor.AGENT
    ) -> dict[str, Any]:
        """Move a note to Obsidian's ``.trash/`` (reversible), if permitted.

        Runs the same write-protection rules as a write — an agent cannot trash a
        voice-only, compute-only, fully-immutable, or body-immutable note — then
        delegates to the IO's trash move. No lint (there is no payload).
        """
        if not self._note_exists(path):
            raise FieldError(f"{path} does not exist")
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        self._gate.check_protection(
            directory, actor, WriteMode.CREATE, touches_body=True
        )
        self._gate._io.delete_note(path)
        self._gate._frontmatter._emit_diff(
            path, "delete", [], stamp(actor, WriteMode.METADATA)
        )
        return {"ok": True, "path": path, "deleted": True}

    def move_note(
        self,
        src: str,
        dst: str,
        *,
        actor: Actor = Actor.AGENT,
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
        self._gate.check_protection(
            src_dir, actor, WriteMode.CREATE, touches_body=True
        )
        self._gate.check_protection(
            dst_dir, actor, WriteMode.CREATE, touches_body=False
        )

        content = self._gate._io.read_note(src)
        self._write(dst, content, created=True)
        self._gate._io.delete_note(src)
        self._gate._frontmatter._emit_diff(
            src, "delete", [], stamp(actor, WriteMode.METADATA)
        )
        self._gate._frontmatter._emit_diff(
            dst, "create", [], stamp(actor, WriteMode.METADATA)
        )

        return {"ok": True, "src": src, "dst": dst, "moved": True}
