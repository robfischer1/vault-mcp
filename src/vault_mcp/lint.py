"""Convention Linter — the standalone, collect-all validation core.

Extracted from the Convention Gate's inline-and-raise validation so the *same*
rules drive three callers: the write path (``write_note``), the dry-run
``lint()`` tool, and the ``audit`` healer. The linter never writes and never
raises on a validation fault — it returns a :class:`LintResult` carrying every
:class:`Finding` it sees, so a caller learns *all* the problems in one pass
instead of one-rejection-at-a-time.

Two design contracts make the extraction back-compatible:

* **Codes map 1:1 onto the legacy ``GateError`` subclasses.** A write path can
  reconstruct the historical exception + message from the first error finding,
  so the existing Gate tests keep passing unchanged.
* **Delta-aware severity.** On an *update*, a fault in a frontmatter field the
  write did not touch is downgraded ``ERROR -> WARNING`` (it is pre-existing
  drift, not something this write introduced). This is what un-sticks an edit
  to a note that already carries an invalid value — the motivating bug.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .provenance import Actor, WriteMode

if TYPE_CHECKING:
    from .schema import VaultSchema, WriteProtectionRule


class Severity(StrEnum):
    """Whether a finding blocks a write or is merely advisory."""

    ERROR = "error"
    WARNING = "warning"


class Code(StrEnum):
    """Finding codes — one per legacy ``GateError`` subclass (back-compat map)."""

    TAG = "tag"
    FIELD = "field"
    PROTECTION = "protection"
    BODY = "body"
    LINK = "link"
    WRITE_MODE = "write_mode"
    FILENAME = "filename"


@dataclass(frozen=True)
class Finding:
    """One validation result. ``field``/``value`` locate the fault for tooling.

    A finding with ``field is None`` is *structural* (write-mode, protection,
    filename) and is never delta-downgraded — it bars the write regardless of
    which fields an update touched.
    """

    code: Code
    message: str
    field: str | None = None
    value: Any = None
    suggestion: str | None = None
    severity: Severity = Severity.ERROR

    def to_dict(self) -> dict[str, Any]:
        """Return this finding as a JSON-serializable dict."""
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "field": self.field,
            "value": None if self.value is None else str(self.value),
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class LintResult:
    """The collected findings for one candidate note."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        """Return the error-severity findings."""
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        """Return the warning-severity findings."""
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when nothing blocks the write (no error-severity findings)."""
        return not self.errors

    def first_error(self) -> Finding | None:
        """Return the first blocking finding in check order, or None."""
        for f in self.findings:
            if f.severity is Severity.ERROR:
                return f
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return this lint result as a JSON-serializable dict."""
        return {
            "ok": self.ok,
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
        }


@dataclass
class LintCandidate:
    """Everything the linter needs to evaluate one prospective note.

    The Gate builds this *after* stamping frontmatter, resolving the directory,
    normalizing status, and escaping the body — the linter validates a finished
    candidate, it does not construct one.
    """

    frontmatter: dict[str, Any]
    body: str
    directory: str
    filename: str
    note_type: str | None
    tags: list[str]
    actor: Actor
    mode: WriteMode
    touches_body: bool = True
    # On update, the frontmatter keys this write changes (plus 'tags'/'body'
    # sentinels). None => create: everything is "touched", so every fault is an
    # ERROR. A fault on a field NOT in this set is pre-existing drift -> WARNING.
    touched_fields: set[str] | None = None

    @property
    def is_update(self) -> bool:
        """Return True when this candidate is an update (vs a create)."""
        return self.touched_fields is not None


# A predicate the Gate supplies so the linter can check link resolution without
# owning IO — returns True if a note path exists/reads.
NoteExists = Callable[[str], bool]

# Body-scan regexes: strip code spans/fences before scanning for bare
# angle-bracket placeholders (which break Obsidian rendering — FR-33).
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9]*>")


def _strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans so body scans ignore code."""
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))


class Linter:
    """Schema-aware, collect-all validator. No IO except the link predicate."""

    def __init__(
        self, schema: VaultSchema, note_exists: NoteExists | None = None
    ) -> None:
        """Build a linter over a schema with an optional note-exists predicate."""
        self._schema = schema
        self._note_exists = note_exists or (lambda _t: True)

    # --- Public entry point ------------------------------------------------
    def lint(self, candidate: LintCandidate) -> LintResult:
        """Run every check, collecting all findings, then delta-adjust severity."""
        findings: list[Finding] = []
        # Order mirrors the legacy Gate so first_error() reconstructs the same
        # exception type that the inline-raise pipeline used to throw.
        self._check_write_mode(candidate, findings)
        self._check_tags(candidate, findings)
        self._check_protection(candidate, findings)
        self._check_directory(candidate, findings)
        self._check_required_frontmatter(candidate, findings)
        self._check_status(candidate, findings)
        self._check_type_rules(candidate, findings)
        if candidate.touches_body:
            self._check_body(candidate, findings)
        self._check_links(candidate, findings)
        self._check_filename(candidate, findings)
        return LintResult(
            findings=[self._adjust(candidate, f) for f in findings]
        )

    # --- Delta-aware severity ---------------------------------------------
    def _adjust(self, candidate: LintCandidate, finding: Finding) -> Finding:
        """Downgrade a field-scoped error to a warning when an update did not touch that field (pre-existing drift must not block an unrelated edit)."""
        if not candidate.is_update:
            return finding
        if finding.severity is not Severity.ERROR:
            return finding
        if finding.field is None:  # structural — never downgraded
            return finding
        touched = candidate.touched_fields or set()
        if finding.field in touched:
            return finding
        # field carried by this candidate but not changed by the write
        from dataclasses import replace

        return replace(finding, severity=Severity.WARNING)

    # --- Individual checks (each appends findings; none raise) -------------
    def _check_write_mode(self, c: LintCandidate, out: list[Finding]) -> None:
        tc = (
            self._schema.type_config(c.note_type)
            if c.note_type is not None
            else None
        )
        if tc is None:
            return
        if tc.write_mode == "pure-DB":
            out.append(
                Finding(
                    Code.WRITE_MODE,
                    f"{c.note_type} is pure-DB; use atom emit or phdb directly, "
                    f"not a vault write",
                )
            )
        elif (
            tc.write_mode == "materialize-only"
            and c.mode is not WriteMode.COMPUTE
        ):
            out.append(
                Finding(
                    Code.WRITE_MODE,
                    f"{c.note_type} is materialize-only; use the materialize verb, "
                    f"not agent-create",
                )
            )

    def _check_tags(self, c: LintCandidate, out: list[Finding]) -> None:
        unknown = [t for t in c.tags if not self._schema.is_valid_tag(t)]
        if not unknown:
            return
        hints = []
        for tag in unknown:
            near = self._schema.nearest_tags(tag)
            hints.append(
                f"{tag!r} (did you mean: {', '.join(near) or 'no close match'})"
            )
        out.append(
            Finding(
                Code.TAG,
                "unknown tag(s): " + "; ".join(hints),
                field="tags",
                value=unknown,
                suggestion="; ".join(hints),
            )
        )

    def _protection_for(self, directory: str) -> WriteProtectionRule | None:
        for rule in self._schema.write_protection:
            if directory == rule.directory or directory.startswith(
                rule.directory + "/"
            ):
                return rule
        return None

    def _check_protection(self, c: LintCandidate, out: list[Finding]) -> None:
        rule = self._protection_for(c.directory)
        if rule is None:
            return
        barred = (
            rule.rule == "fully-immutable"
            or (rule.rule == "body-immutable" and c.touches_body)
            or (rule.rule == "compute-only" and c.mode is not WriteMode.COMPUTE)
            or (rule.rule == "voice-only" and c.actor is not Actor.HUMAN)
        )
        if barred:
            out.append(Finding(Code.PROTECTION, rule.error))

    def _check_directory(self, c: LintCandidate, out: list[Finding]) -> None:
        top = c.directory.split("/", 1)[0]
        for forbidden in self._schema.forbidden_dirs:
            if forbidden in (c.directory, top) or c.directory.startswith(
                forbidden + "/"
            ):
                out.append(
                    Finding(
                        Code.PROTECTION,
                        f"writes are not allowed in non-pillar directory {c.directory!r}",
                    )
                )
                return

    def _check_required_frontmatter(
        self, c: LintCandidate, out: list[Finding]
    ) -> None:
        missing = [
            r
            for r in self._schema.required_frontmatter
            if c.frontmatter.get(r) in (None, "")
        ]
        if missing:
            out.append(
                Finding(
                    Code.FIELD,
                    f"missing required frontmatter field(s): {missing}",
                    field=missing[0],
                    value=None,
                )
            )

    def _check_status(self, c: LintCandidate, out: list[Finding]) -> None:
        if "status" not in c.frontmatter:
            return
        schema = self._schema
        repaired = schema.normalize_status(c.frontmatter["status"])
        if schema.is_valid_status(repaired):
            return
        near = difflib.get_close_matches(repaired, schema.status_values, n=1)
        suggestion = f" (did you mean {near[0]!r}?)" if near else ""
        out.append(
            Finding(
                Code.FIELD,
                f"status {repaired!r} is not one of {sorted(schema.status_values)}{suggestion}",
                field="status",
                value=repaired,
                suggestion=near[0] if near else None,
            )
        )

    def _check_type_rules(self, c: LintCandidate, out: list[Finding]) -> None:
        tc = (
            self._schema.type_config(c.note_type)
            if c.note_type is not None
            else None
        )
        if tc is None:
            return
        fm = c.frontmatter
        for req in tc.required_fields:
            if fm.get(req) in (None, ""):
                out.append(
                    Finding(
                        Code.FIELD,
                        f"{c.note_type}: missing required field {req!r}",
                        field=req,
                        value=None,
                    )
                )
        for field_name, allowed in tc.value_constraints:
            value = fm.get(field_name)
            if value is not None and str(value) not in allowed:
                out.append(
                    Finding(
                        Code.FIELD,
                        f"{c.note_type}.{field_name}: {value!r} is not one of {list(allowed)}",
                        field=field_name,
                        value=value,
                    )
                )
        for field_name, fmt in tc.formats:
            value = fm.get(field_name)
            if value is not None and not self._schema.is_valid_format(
                fmt, value
            ):
                out.append(
                    Finding(
                        Code.FIELD,
                        f"{c.note_type}.{field_name}: {value!r} is not a valid {fmt}",
                        field=field_name,
                        value=value,
                    )
                )

    def _check_body(self, c: LintCandidate, out: list[Finding]) -> None:
        tc = (
            self._schema.type_config(c.note_type)
            if c.note_type is not None
            else None
        )
        if tc is not None and tc.body_empty and c.body.strip() != "":
            out.append(
                Finding(
                    Code.BODY,
                    f"{c.note_type} is a DB-canonical redirect stub; its body must be empty",
                    field="body",
                )
            )
        if c.directory.startswith(
            "System/Templates"
        ) and c.body.lstrip().startswith("---"):
            out.append(
                Finding(
                    Code.BODY,
                    "System/Templates files must use a Templater fence, "
                    "not a literal '---' block",
                    field="body",
                )
            )
        match = _ANGLE_PLACEHOLDER_RE.search(_strip_code(c.body))
        if match is not None:
            token = match.group(0)
            name = token[1:-1]
            out.append(
                Finding(
                    Code.BODY,
                    f"bare placeholder {token!r} breaks Obsidian rendering — "
                    f"use {{{name}}} instead",
                    field="body",
                    value=token,
                )
            )

    def _link_resolves(self, value: object) -> bool:
        target = str(value).strip().strip("[]").strip()
        if target == "":
            return False
        if not target.endswith(".md"):
            target = target + ".md"
        return self._note_exists(target)

    def _check_links(self, c: LintCandidate, out: list[Finding]) -> None:
        for key in ("prev", "next"):
            value = c.frontmatter.get(key)
            if value is not None and not self._link_resolves(value):
                out.append(
                    Finding(
                        Code.LINK,
                        f"{key} -> {value!r} does not resolve to an existing note",
                        field=key,
                        value=value,
                    )
                )

    def _check_filename(self, c: LintCandidate, out: list[Finding]) -> None:
        for segment in c.directory.split("/"):
            if re.match(r"^\d+[\s_-]", segment):
                out.append(
                    Finding(
                        Code.FILENAME,
                        f"numeric folder prefix is not allowed: {segment!r}",
                    )
                )
                return
        if re.search(r"\s--\s", c.filename):
            out.append(
                Finding(
                    Code.FILENAME,
                    f"' -- ' prefix is not allowed (retired 'Pillar -- ' convention): "
                    f"{c.filename!r}",
                )
            )
