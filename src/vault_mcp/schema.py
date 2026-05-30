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
from typing import Any

import yaml

SCHEMA_ENV_VAR = "VAULT_MCP_SCHEMA"

# Allowed write-protection rule kinds (Convention Gate enforces the semantics).
PROTECTION_RULES: frozenset[str] = frozenset(
    {"body-immutable", "fully-immutable", "compute-only", "voice-only"}
)


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
    """A routing rule: match optional note_type / pillar -> target directory."""

    directory: str
    note_type: str | None = None
    pillar: str | None = None

    def matches(self, note_type: str | None, pillar: str | None) -> bool:
        if self.note_type is not None and self.note_type != note_type:
            return False
        return not (self.pillar is not None and self.pillar != pillar)


@dataclass(frozen=True)
class WriteProtectionRule:
    """A per-directory write-protection rule with its caller-facing message."""

    directory: str
    rule: str
    error: str


@dataclass(frozen=True)
class VaultSchema:
    """The loaded, validated governance schema."""

    pillars: tuple[str, ...]
    tags: frozenset[str]
    routes: tuple[Route, ...]
    required_frontmatter: tuple[str, ...]
    write_protection: tuple[WriteProtectionRule, ...]
    provenance_levels: tuple[str, ...]
    label_field: str = "title"
    created_field: str = "created"
    updated_field: str | None = None
    source_path: Path | None = field(default=None, compare=False)

    # --- Tag glossary lookup (Feature: Tag glossary lookup) ----------------
    def is_valid_tag(self, tag: str) -> bool:
        """Report whether a tag is in the closed glossary."""
        return tag in self.tags

    def nearest_tags(self, tag: str, n: int = 3) -> list[str]:
        """Return the closest known tags to an unknown one, best first."""
        return difflib.get_close_matches(tag, self.tags, n=n, cutoff=0.6)

    # --- Pillar routing (Feature: Pillar routing table) --------------------
    def resolve_directory(self, note_type: str | None = None, pillar: str | None = None) -> str:
        """Resolve the single canonical target directory for a note.

        Raises ``RouteError`` when no route matches or more than one does.
        """
        hits = [r for r in self.routes if r.matches(note_type, pillar)]
        if len(hits) == 1:
            return hits[0].directory
        if len(hits) == 0:
            raise RouteError(f"no route matches note_type={note_type!r} pillar={pillar!r}")
        dirs = ", ".join(r.directory for r in hits)
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
        routes.append(
            Route(
                directory=entry["directory"],
                note_type=entry.get("note_type"),
                pillar=entry.get("pillar"),
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

    fm_cfg = raw.get("frontmatter", {}) or {}

    return VaultSchema(
        pillars=pillars,
        tags=frozenset(tag_list),
        routes=tuple(routes),
        required_frontmatter=tuple(raw.get("required_frontmatter", []) or []),
        write_protection=tuple(protection),
        provenance_levels=provenance,
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
