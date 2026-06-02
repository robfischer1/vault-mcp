"""Schema Engine — the deterministic governance foundation for vault-mcp v2.

Loads a machine-readable governance schema from an external config file
(resolved via the ``VAULT_MCP_SCHEMA`` environment variable), validates it,
and answers the questions the Convention Gate asks on every write: is this
tag in the closed glossary, and what directory does this note route to.

The schema is deliberately external (Constitution I): no vault-specific
content lives in this module. Any Obsidian-compatible vault supplies its own
``vault-mcp.schema.yml``.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA_ENV_VAR = "VAULT_MCP_SCHEMA"

# Allowed write-protection rule kinds (Convention Gate enforces the semantics).
PROTECTION_RULES: frozenset[str] = frozenset(
    {"body-immutable", "fully-immutable", "compute-only", "voice-only"}
)

# Per-@type write-mode: who may create a note of this type through the Gate.
#   agent            — caller-creatable via the Gate (normal authoring)
#   materialize-only — Gate rejects agent-create; the materialize verb renders it
#   pure-DB          — never a vault file; the Gate rejects any vault-write
WRITE_MODES: frozenset[str] = frozenset({"agent", "materialize-only", "pure-DB"})


def _is_geo(value: object) -> bool:
    """Validate a ``"lat,long"`` geo string (two comma-separated floats)."""
    parts = str(value).split(",")
    if len(parts) != 2:
        return False
    try:
        float(parts[0].strip())
        float(parts[1].strip())
    except ValueError:
        return False
    return True


# Named value formats the Gate can enforce (constraint kind: format, not enum).
FORMAT_VALIDATORS: dict[str, Callable[[object], bool]] = {"geo": _is_geo}


class SchemaError(Exception):
    """Base class for all schema-engine failures."""


class SchemaConfigError(SchemaError):
    """The schema config could not be located or read."""


class SchemaValidationError(SchemaError):
    """The schema document loaded but is internally inconsistent."""


class RouteError(SchemaError):
    """No (or ambiguous) routing rule for the given note attributes."""


@dataclass(frozen=True)
class Route:
    """A routing rule: match optional note_type / pillar / discriminator.

    ``discriminator`` is an optional ``(field, allowed_values)`` pair matched
    against the note's attributes (e.g. Product ``category`` → Supplements vs
    Things). A route carrying a discriminator is *more specific* than one
    without; ``resolve_directory`` lets the most specific match win.
    """

    directory: str
    note_type: str | None = None
    pillar: str | None = None
    discriminator: tuple[str, tuple[str, ...]] | None = None

    def matches(
        self,
        note_type: str | None,
        pillar: str | None,
        attrs: Mapping[str, Any] | None = None,
    ) -> bool:
        if self.note_type is not None and self.note_type != note_type:
            return False
        if self.pillar is not None and self.pillar != pillar:
            return False
        if self.discriminator is not None:
            field_name, allowed = self.discriminator
            value = (attrs or {}).get(field_name)
            if value is None or str(value) not in allowed:
                return False
        return True

    def specificity(self) -> int:
        """How many constraints this route pins — higher wins on overlap."""
        return sum(
            1 for c in (self.note_type, self.pillar, self.discriminator) if c is not None
        )


@dataclass(frozen=True)
class WriteProtectionRule:
    """A per-directory write-protection rule with its caller-facing message."""

    directory: str
    rule: str
    error: str


@dataclass(frozen=True)
class PillarDefault:
    """Per-pillar visual-identity defaults the Gate stamps when omitted."""

    pillar: str
    nn_color: str | None = None
    nn_icon: str | None = None


@dataclass(frozen=True)
class TypeConfig:
    """Per-@type governance configuration resolved from the schema's ``types``.

    The Gate stamps admin fields and routes; the caller supplies factual data.
    This config tells the Gate which fields a type *requires*, which freeform
    fields it *permits*, the controlled vocabularies it enforces on *values*,
    and the type's *write-mode* (who may create it through the Gate).

    ``value_constraints`` is a tuple of ``(field, allowed_values)`` pairs rather
    than a dict so the dataclass stays hashable and frozen like its siblings.
    """

    type_name: str
    required_fields: tuple[str, ...] = ()
    freeform_fields: tuple[str, ...] = ()
    write_mode: str = "agent"
    value_constraints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    formats: tuple[tuple[str, str], ...] = ()

    def allowed_values(self, field_name: str) -> tuple[str, ...] | None:
        """Return the closed value set for ``field_name``, or None if unconstrained."""
        for fname, allowed in self.value_constraints:
            if fname == field_name:
                return allowed
        return None

    def format_for(self, field_name: str) -> str | None:
        """Return the named format ``field_name`` must satisfy, or None."""
        for fname, fmt in self.formats:
            if fname == field_name:
                return fmt
        return None


@dataclass(frozen=True)
class VaultSchema:
    """The loaded, validated governance schema."""

    pillars: tuple[str, ...]
    tags: frozenset[str]
    routes: tuple[Route, ...]
    required_frontmatter: tuple[str, ...]
    write_protection: tuple[WriteProtectionRule, ...]
    provenance_levels: tuple[str, ...]
    types: tuple[TypeConfig, ...] = ()
    pillar_defaults: tuple[PillarDefault, ...] = ()
    status_values: tuple[str, ...] = ()
    status_default: str = "Pending"
    status_repairs: tuple[tuple[str, str], ...] = ()
    label_field: str = "title"
    created_field: str = "created"
    updated_field: str | None = None
    source_path: Path | None = field(default=None, compare=False)

    # --- Per-@type config lookup (Feature: Type Registry) ------------------
    def type_config(self, type_name: str) -> TypeConfig | None:
        """Return the TypeConfig for ``type_name``, or None if the type is unknown."""
        for tc in self.types:
            if tc.type_name == type_name:
                return tc
        return None

    # --- Pillar visual defaults (Feature: Pillar Defaults) -----------------
    def pillar_default(self, pillar: str | None) -> PillarDefault | None:
        """Return the visual defaults for ``pillar``, or None if none configured."""
        if pillar is None:
            return None
        for pd in self.pillar_defaults:
            if pd.pillar == pillar:
                return pd
        return None

    # --- status vocabulary (Feature: Value Vocabularies) -------------------
    def normalize_status(self, value: object) -> str:
        """Repair a status value: unwrap singleton lists, blank -> default,
        apply the schema's rename repairs. Does not check validity."""
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else ""
        text = "" if value is None else str(value).strip()
        if text == "":
            return self.status_default
        for frm, to in self.status_repairs:
            if text == frm:
                return to
        return text

    def is_valid_status(self, value: str) -> bool:
        """Report whether ``value`` is in the configured status vocabulary."""
        if len(self.status_values) == 0:
            return True  # no vocabulary configured -> nothing to enforce
        return value in self.status_values

    # --- value formats (Feature: Value Vocabularies) -----------------------
    def is_valid_format(self, format_name: str, value: object) -> bool:
        """Validate ``value`` against a named format (e.g. ``geo``).

        Unknown format names pass (forward-compatible): a schema may name a
        format this build doesn't validate yet rather than hard-failing.
        """
        validator = FORMAT_VALIDATORS.get(format_name)
        if validator is None:
            return True
        return validator(value)

    # --- Tag glossary lookup (Feature: Tag glossary lookup) ----------------
    def is_valid_tag(self, tag: str) -> bool:
        """Report whether a tag is in the closed glossary."""
        return tag in self.tags

    def nearest_tags(self, tag: str, n: int = 3) -> list[str]:
        """Return the closest known tags to an unknown one, best first."""
        return difflib.get_close_matches(tag, self.tags, n=n, cutoff=0.6)

    # --- Pillar routing (Feature: Pillar routing table) --------------------
    def resolve_directory(
        self,
        note_type: str | None = None,
        pillar: str | None = None,
        attrs: Mapping[str, Any] | None = None,
    ) -> str:
        """Resolve the single canonical target directory for a note.

        When several routes match, the *most specific* wins (a discriminator
        match beats a bare type/pillar route). Raises ``RouteError`` when no
        route matches or two equally specific routes both match.
        """
        hits = [r for r in self.routes if r.matches(note_type, pillar, attrs)]
        if len(hits) == 0:
            raise RouteError(f"no route matches note_type={note_type!r} pillar={pillar!r}")
        best = max(r.specificity() for r in hits)
        top = [r for r in hits if r.specificity() == best]
        if len(top) == 1:
            return top[0].directory
        dirs = ", ".join(r.directory for r in top)
        raise RouteError(
            f"ambiguous routing for note_type={note_type!r} pillar={pillar!r}: {dirs}"
        )


def _resolve_path(path: str | Path | None) -> Path:
    """Resolve the schema path from an explicit arg or the env var.

    The env var is read at call time (never bound as a default argument), so
    tests and runtime overrides via the environment take effect.
    """
    if path is not None:
        return Path(path)
    env = os.environ.get(SCHEMA_ENV_VAR)
    if env is None or env == "":
        raise SchemaConfigError(
            f"no schema path given and {SCHEMA_ENV_VAR} is not set; "
            f"point {SCHEMA_ENV_VAR} at a vault-mcp.schema.yml"
        )
    return Path(env)


def _build(raw: dict[str, Any], source: Path) -> VaultSchema:
    """Construct a VaultSchema from parsed YAML (no cross-field validation)."""
    pillars = tuple(raw.get("pillars", []) or [])
    tag_list = list(raw.get("tags", []) or [])

    routes: list[Route] = []
    for entry in raw.get("routes", []) or []:
        disc_raw = entry.get("discriminator")
        discriminator: tuple[str, tuple[str, ...]] | None = None
        if disc_raw is not None:
            discriminator = (disc_raw["field"], tuple(disc_raw.get("values", []) or []))
        routes.append(
            Route(
                directory=entry["directory"],
                note_type=entry.get("note_type"),
                pillar=entry.get("pillar"),
                discriminator=discriminator,
            )
        )

    protection: list[WriteProtectionRule] = []
    for entry in raw.get("write_protection", []) or []:
        protection.append(
            WriteProtectionRule(
                directory=entry["directory"],
                rule=entry["rule"],
                error=entry["error"],
            )
        )

    provenance = tuple((raw.get("provenance", {}) or {}).get("levels", []) or [])

    types: list[TypeConfig] = []
    for name, cfg in (raw.get("types", {}) or {}).items():
        cfg = cfg or {}
        constraints = tuple(
            (fname, tuple(vals or []))
            for fname, vals in (cfg.get("constraints", {}) or {}).items()
        )
        formats = tuple((fname, fmt) for fname, fmt in (cfg.get("formats", {}) or {}).items())
        types.append(
            TypeConfig(
                type_name=name,
                required_fields=tuple(cfg.get("required", []) or []),
                freeform_fields=tuple(cfg.get("freeform", []) or []),
                write_mode=cfg.get("write_mode", "agent"),
                value_constraints=constraints,
                formats=formats,
            )
        )

    pillar_defaults = tuple(
        PillarDefault(pillar=name, nn_color=(cfg or {}).get("nn_color"), nn_icon=(cfg or {}).get("nn_icon"))
        for name, cfg in (raw.get("pillar_defaults", {}) or {}).items()
    )

    vocab = raw.get("value_vocabularies", {}) or {}
    status_cfg = vocab.get("status", {}) or {}
    status_values = tuple(status_cfg.get("values", []) or [])
    status_default = status_cfg.get("default", "Pending")
    status_repairs = tuple((frm, to) for frm, to in (status_cfg.get("repairs", {}) or {}).items())

    fm_cfg = raw.get("frontmatter", {}) or {}

    return VaultSchema(
        pillars=pillars,
        tags=frozenset(tag_list),
        routes=tuple(routes),
        required_frontmatter=tuple(raw.get("required_frontmatter", []) or []),
        write_protection=tuple(protection),
        provenance_levels=provenance,
        types=tuple(types),
        pillar_defaults=pillar_defaults,
        status_values=status_values,
        status_default=status_default,
        status_repairs=status_repairs,
        label_field=fm_cfg.get("label_field", "title"),
        created_field=fm_cfg.get("created_field", "created"),
        updated_field=fm_cfg.get("updated_field"),
        source_path=source,
    )


def _validate(raw: dict[str, Any], schema: VaultSchema) -> None:
    """Check internal consistency; raise SchemaValidationError on the first fault."""
    # Duplicate tags — frozenset hides them, so inspect the raw list.
    seen: set[str] = set()
    for tag in raw.get("tags", []) or []:
        if tag in seen:
            raise SchemaValidationError(f"duplicate tag in glossary: {tag!r}")
        seen.add(tag)

    pillar_set = set(schema.pillars)
    for route in schema.routes:
        if route.pillar is not None and route.pillar not in pillar_set:
            raise SchemaValidationError(
                f"route for directory {route.directory!r} references "
                f"unknown pillar {route.pillar!r}"
            )

    for rule in schema.write_protection:
        if rule.rule not in PROTECTION_RULES:
            raise SchemaValidationError(
                f"write_protection for {rule.directory!r} has unknown rule "
                f"{rule.rule!r}; expected one of {sorted(PROTECTION_RULES)}"
            )

    for tc in schema.types:
        if tc.write_mode not in WRITE_MODES:
            raise SchemaValidationError(
                f"type {tc.type_name!r} has unknown write_mode {tc.write_mode!r}; "
                f"expected one of {sorted(WRITE_MODES)}"
            )


def load_schema(path: str | Path | None = None) -> VaultSchema:
    """Load, validate, and return the governance schema.

    ``path`` takes precedence over the ``VAULT_MCP_SCHEMA`` environment
    variable. Raises ``SchemaConfigError`` if the path can't be resolved or
    read, and ``SchemaValidationError`` if the document is inconsistent.
    """
    resolved = _resolve_path(path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaConfigError(f"cannot read schema config at {resolved}: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaValidationError(f"schema at {resolved} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SchemaValidationError(f"schema at {resolved} must be a mapping at the top level")

    schema = _build(raw, resolved)
    _validate(raw, schema)
    return schema
