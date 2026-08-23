"""The vault-schema type model — its exceptions and its dataclasses.

Split out of schema.py under vault-mcp#5294 (617 LOC, over the 600 block).
`vault_mcp.schema` re-exports every name, so no import site moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
        """Return True if this type config matches the given note_type, pillar, and attrs."""
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
            1
            for c in (self.note_type, self.pillar, self.discriminator)
            if c is not None
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
    body_empty: bool = False
    atom_slug: bool = False
    value_constraints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    formats: tuple[tuple[str, str], ...] = ()
    body_guidance: str | None = None

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
