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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

# The exceptions and dataclasses live in schema_types (vault-mcp#5294). They are
# re-exported here — and named in __all__ below — so every existing
# `from vault_mcp.schema import Route` keeps resolving and no call site moved.
from vault_mcp.schema_types import (
    FORMAT_VALIDATORS,
    PillarDefault,
    Route,
    RouteError,
    SchemaConfigError,
    SchemaError,
    SchemaValidationError,
    TypeConfig,
    WriteProtectionRule,
)

__all__ = [
    "PillarDefault",
    "Route",
    "RouteError",
    "SchemaConfigError",
    "SchemaError",
    "SchemaValidationError",
    "TypeConfig",
    "VaultSchema",
    "WriteProtectionRule",
    "load_schema",
]


if TYPE_CHECKING:
    from collections.abc import Mapping

SCHEMA_ENV_VAR = "VAULT_MCP_SCHEMA"

# Allowed write-protection rule kinds (Convention Gate enforces the semantics).
PROTECTION_RULES: frozenset[str] = frozenset(
    {
        "body-immutable",
        "fully-immutable",
        "compute-only",
        "voice-only",
        "exempt",
    }
)

# Per-@type write-mode: who may create a note of this type through the Gate.
#   agent            — caller-creatable via the Gate (normal authoring)
#   materialize-only — Gate rejects agent-create; the materialize verb renders it
#   pure-DB          — never a vault file; the Gate rejects any vault-write
WRITE_MODES: frozenset[str] = frozenset(
    {"agent", "materialize-only", "pure-DB"}
)


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
    author_type_values: tuple[str, ...] = ()
    author_type_repairs: tuple[tuple[str, str], ...] = ()
    deprecated_renames: tuple[tuple[str, str], ...] = ()
    dead_keys: frozenset[str] = frozenset()
    reserved_tags: frozenset[str] = frozenset()
    forbidden_dirs: frozenset[str] = frozenset()
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
        """Repair a status value: unwrap singleton lists, blank -> default, apply rename repairs (does not check validity)."""
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
        if not self.status_values:
            return True  # no vocabulary configured -> nothing to enforce
        return value in self.status_values

    # --- author_type vocabulary (Feature: Value Vocabularies) --------------
    def normalize_author_type(self, value: object) -> str:
        """Repair an author_type value: unwrap singleton lists, apply rename repairs.

        Mirrors ``normalize_status`` but injects no default — a blank value is
        returned blank so the caller can re-derive it from author_level. Does
        not check validity (an unmapped value is returned unchanged).
        """
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else ""
        text = "" if value is None else str(value).strip()
        if text == "":
            return text
        for frm, to in self.author_type_repairs:
            if text == frm:
                return to
        return text

    def is_valid_author_type(self, value: str) -> bool:
        """Report whether ``value`` is in the configured author_type vocabulary."""
        if not self.author_type_values:
            return True  # no vocabulary configured -> nothing to enforce
        return value in self.author_type_values

    # --- value formats (Feature: Value Vocabularies) -----------------------
    def is_valid_format(self, format_name: str, value: object) -> bool:
        """Validate ``value`` against a named format (e.g. ``geo``).

        Unknown format names pass (forward-compatible): a schema may name a
        format this build doesn't validate yet rather than hard-failing.
        """
        validator = FORMAT_VALIDATORS.get(format_name)
        if validator is None:
            return True
        return bool(validator(value))

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
        if not hits:
            raise RouteError(
                f"no route matches note_type={note_type!r} pillar={pillar!r}"
            )
        best = max(r.specificity() for r in hits)
        top = [r for r in hits if r.specificity() == best]
        unique_dirs = {r.directory for r in top}
        if len(unique_dirs) == 1:
            return top[0].directory
        dirs = ", ".join(sorted(unique_dirs))
        raise RouteError(
            f"ambiguous routing for note_type={note_type!r} pillar={pillar!r}: {dirs}"
        )

    # --- Introspection (Feature: Schema Introspection) ---------------------
    def _managed_fields(self) -> set[str]:
        """Frontmatter keys the Gate stamps — never the caller's to set."""
        managed = {
            self.label_field,
            self.created_field,
            "note_type",
            "author_type",
            "author_level",
            "ai_model",
            "identifier",
            "nn_color",
            "nn_icon",
            "provenance",
        }
        if self.updated_field is not None:
            managed.add(self.updated_field)
        return managed

    def list_types(self) -> list[dict[str, Any]]:
        """Every registered @type with its write-mode and required-field summary."""
        return [
            {
                "name": tc.type_name,
                "write_mode": tc.write_mode,
                "required": list(tc.required_fields),
                "body_empty": tc.body_empty,
                "atom_slug": tc.atom_slug,
            }
            for tc in self.types
        ]

    def list_tags(self) -> dict[str, list[str]]:
        """Return the closed tag glossary grouped by prefix (text before the first '/')."""
        grouped: dict[str, list[str]] = {}
        for tag in sorted(self.tags):
            prefix = tag.split("/", 1)[0] if "/" in tag else "_root"
            grouped.setdefault(prefix, []).append(tag)
        return grouped

    def list_keys(self) -> list[str]:
        """Return the global union of frontmatter property keys across all @types (sorted)."""
        keys: set[str] = set(self.required_frontmatter)
        for tc in self.types:
            keys.update(tc.required_fields)
            keys.update(tc.freeform_fields)
            keys.update(field for field, _ in tc.value_constraints)
            keys.update(field for field, _ in tc.formats)
        if self.status_values:
            keys.add("status")
        return sorted(keys)

    def describe_type(self, note_type: str) -> dict[str, Any] | None:
        """Return the per-type spec sheet, or None when ``note_type`` is unknown.

        Covers caller-settable fields, value constraints, formats, freeform
        fields, and routing with discriminators plus body guidance.
        """
        tc = self.type_config(note_type)
        if tc is None:
            return None
        managed = self._managed_fields()
        caller_settable = [
            f
            for f in (*tc.required_fields, *tc.freeform_fields)
            if f not in managed
        ]
        routing = [
            {
                "directory": r.directory,
                "pillar": r.pillar,
                "discriminator": (
                    {
                        "field": r.discriminator[0],
                        "values": list(r.discriminator[1]),
                    }
                    if r.discriminator is not None
                    else None
                ),
            }
            for r in self.routes
            # routes key on the raw note_type ('note'); type configs are
            # Title-Cased ('Note') — match case-insensitively across the split.
            if r.note_type is not None
            and r.note_type.lower() == note_type.lower()
        ]
        return {
            "note_type": note_type,
            "write_mode": tc.write_mode,
            "required": list(tc.required_fields),
            "caller_settable": caller_settable,
            "constraints": {
                field: list(vals) for field, vals in tc.value_constraints
            },
            "formats": dict(tc.formats),
            "freeform": list(tc.freeform_fields),
            "status_values": list(self.status_values),
            "body_empty": tc.body_empty,
            "atom_slug": tc.atom_slug,
            "routing": routing,
            "body_guidance": tc.body_guidance,
        }


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
            discriminator = (
                disc_raw["field"],
                tuple(disc_raw.get("values", []) or []),
            )
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

    provenance = tuple(
        (raw.get("provenance", {}) or {}).get("levels", []) or []
    )

    types: list[TypeConfig] = []
    for name, raw_cfg in (raw.get("types", {}) or {}).items():
        cfg = raw_cfg or {}
        constraints = tuple(
            (fname, tuple(vals or []))
            for fname, vals in (cfg.get("constraints", {}) or {}).items()
        )
        formats = tuple(
            (fname, fmt)
            for fname, fmt in (cfg.get("formats", {}) or {}).items()
        )
        types.append(
            TypeConfig(
                type_name=name,
                required_fields=tuple(cfg.get("required", []) or []),
                freeform_fields=tuple(cfg.get("freeform", []) or []),
                write_mode=cfg.get("write_mode", "agent"),
                body_empty=bool(cfg.get("body_empty", False)),
                atom_slug=bool(cfg.get("atom_slug", False)),
                value_constraints=constraints,
                formats=formats,
                body_guidance=cfg.get("body_guidance"),
            )
        )

    pillar_defaults = tuple(
        PillarDefault(
            pillar=name,
            nn_color=(cfg or {}).get("nn_color"),
            nn_icon=(cfg or {}).get("nn_icon"),
        )
        for name, cfg in (raw.get("pillar_defaults", {}) or {}).items()
    )

    vocab = raw.get("value_vocabularies", {}) or {}
    status_cfg = vocab.get("status", {}) or {}
    status_values = tuple(status_cfg.get("values", []) or [])
    status_default = status_cfg.get("default", "Pending")
    status_repairs = tuple(
        (frm, to) for frm, to in (status_cfg.get("repairs", {}) or {}).items()
    )
    author_type_cfg = vocab.get("author_type", {}) or {}
    author_type_values = tuple(author_type_cfg.get("values", []) or [])
    author_type_repairs = tuple(
        (frm, to)
        for frm, to in (author_type_cfg.get("repairs", {}) or {}).items()
    )

    dep = raw.get("deprecated_keys", {}) or {}
    deprecated_renames = tuple(
        (o, n) for o, n in (dep.get("rename", {}) or {}).items()
    )
    dead_keys = frozenset(dep.get("dead", []) or [])
    reserved_tags = frozenset(raw.get("reserved_tags", []) or [])
    forbidden_dirs = frozenset(raw.get("forbidden_dirs", []) or [])

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
        author_type_values=author_type_values,
        author_type_repairs=author_type_repairs,
        deprecated_renames=deprecated_renames,
        dead_keys=dead_keys,
        reserved_tags=reserved_tags,
        forbidden_dirs=forbidden_dirs,
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
        raise SchemaConfigError(
            f"cannot read schema config at {resolved}: {exc}"
        ) from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaValidationError(
            f"schema at {resolved} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise SchemaValidationError(
            f"schema at {resolved} must be a mapping at the top level"
        )

    schema = _build(raw, resolved)
    _validate(raw, schema)
    return schema
