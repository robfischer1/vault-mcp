"""The Gate's AUDIT collaborator — scan the vault for drift and heal it.

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
from vault_mcp.gate import _render_note, _split_note
from vault_mcp.lint import LintCandidate
from vault_mcp.provenance import Actor, WriteMode
from vault_mcp.schema import RouteError

# Own logger, not the gate's. Beyond correct provenance in the log record,
# ruff's BLE001 permits a blind except whose body calls log.exception — but only
# when it can SEE that `log` is a logger, which an imported one hides.
log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vault_mcp.gate import ConventionGate


class GateAuditor:
    """The Gate's AUDIT collaborator — scan the vault for drift and heal it."""

    def __init__(self, gate: ConventionGate) -> None:
        """Bind to the gate whose state this collaborator operates on."""
        self._gate = gate

    def _tags_of(self, fm: dict[str, Any]) -> list[str]:
        val = fm.get("tags")
        if isinstance(val, list):
            return [str(t) for t in val]
        return [] if val in (None, "") else [str(val)]

    def audit(
        self,
        directory: str = "",
        *,
        resolve: bool = False,
        all_dirs: bool = False,
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
        paths = self._gate._io.list_notes(scope, recursive=True)
        notes: list[dict[str, Any]] = []
        corrected = 0
        for note_path in paths:
            try:
                fm, body = _split_note(self._gate._io.read_note(note_path))
            except KeyError, OSError, ObsidianIOError:
                continue
            note_dir = note_path.rsplit("/", 1)[0] if "/" in note_path else ""
            filename = note_path.rsplit("/", 1)[-1].removesuffix(".md")
            note_type = fm.get("note_type")
            drift = self._scan_drift(fm, body, note_dir, filename, note_type)
            if not drift:
                continue
            entry: dict[str, Any] = {
                "path": note_path,
                "note_type": note_type,
                "drift": drift,
            }
            if resolve:
                healed = self._heal(
                    note_path, fm, body, note_dir, filename, note_type
                )
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

    def _scan_drift(
        self,
        fm: dict[str, Any],
        body: str,
        directory: str,
        filename: str,
        note_type: str | None,
    ) -> list[dict[str, Any]]:
        """Collect every drift item for one note: untyped, deprecated/dead keys, all linter errors, routing misplacement."""
        drift: list[dict[str, Any]] = []
        if note_type is None:
            drift.append({"category": "untyped", "detail": "no note_type"})
        for old, new in self._gate._schema.deprecated_renames:
            if old in fm:
                drift.append(
                    {
                        "category": "deprecated_key",
                        "field": old,
                        "detail": f"{old} -> {new}",
                    }
                )
        for dead in self._gate._schema.dead_keys:
            if dead in fm:
                drift.append(
                    {
                        "category": "dead_key",
                        "field": dead,
                        "detail": f"drop {dead}",
                    }
                )
        candidate = LintCandidate(
            frontmatter=fm,
            body=body,
            directory=directory,
            filename=filename,
            note_type=note_type,
            tags=self._tags_of(fm),
            actor=Actor.AGENT,
            mode=WriteMode.CREATE,
            touches_body=True,
            touched_fields=None,
        )
        for finding in self._gate._linter.lint(candidate).errors:
            drift.append(
                {
                    "category": finding.code.value,
                    "field": finding.field,
                    "detail": finding.message,
                }
            )
        try:
            correct = self._gate._schema.resolve_directory(
                note_type=note_type, pillar=fm.get("pillar"), attrs=fm
            )
            if correct != directory:
                drift.append(
                    {
                        "category": "routing",
                        "detail": f"in {directory!r}, should be {correct!r}",
                        "target": correct,
                    }
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
        self._gate._frontmatter._migrate_keys(new_fm)
        for old, new in self._gate._schema.deprecated_renames:
            if old in before and old not in new_fm:
                corrections.append(f"renamed {old} -> {new}")
        for dead in self._gate._schema.dead_keys:
            if dead in before and dead not in new_fm:
                corrections.append(f"dropped {dead}")
        if "status" in new_fm:
            repaired = self._gate._schema.normalize_status(new_fm["status"])
            if repaired != new_fm["status"]:
                corrections.append(
                    f"status {new_fm['status']!r} -> {repaired!r}"
                )
                new_fm["status"] = repaired

        try:
            target_dir = self._gate._schema.resolve_directory(
                note_type=note_type, pillar=new_fm.get("pillar"), attrs=new_fm
            )
        except RouteError:
            target_dir = directory
        moving = target_dir != directory
        new_path = f"{target_dir}/{filename}.md"
        collision = moving and self._gate._writer._note_exists(new_path)
        write_dir = directory if collision else target_dir

        candidate = LintCandidate(
            frontmatter=new_fm,
            body=body,
            directory=write_dir,
            filename=filename,
            note_type=note_type,
            tags=self._tags_of(new_fm),
            actor=Actor.AGENT,
            mode=WriteMode.CREATE,
            touches_body=True,
            touched_fields=None,
        )

        result = self._gate._linter.lint(candidate)
        remaining = [f.message for f in result.errors]
        if note_type is None:
            remaining.append("untyped: set note_type manually")
        if collision:
            remaining.append(f"routing target {new_path!r} already exists")

        if not result.ok:
            return {
                "corrected": False,
                "corrections": corrections,
                "remaining": remaining,
            }

        rendered = _render_note(new_fm, body)
        if moving and not collision:
            self._gate._writer._write(new_path, rendered, created=True)
            self._gate._io.delete_note(path)
            corrections.append(f"moved {directory!r} -> {target_dir!r}")
            return {
                "corrected": True,
                "corrections": corrections,
                "remaining": remaining,
            }
        if corrections:
            self._gate._writer._write(path, rendered, created=False)
            return {
                "corrected": True,
                "corrections": corrections,
                "remaining": remaining,
            }
        return {"corrected": False, "corrections": [], "remaining": remaining}
