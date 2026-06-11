"""Provenance System — the single-axis provenance spectrum for vault-mcp v2.

Replaces the coarse ``author_type`` field with a closed, ordered spectrum that
runs from wholly-human to deterministically-computed::

    human <- human-edited <- human-revised <- [collaboration] -> ai-assisted
        -> ai-compiled -> ai-metadata -> ai-computed

The Convention Gate stamps a value from this spectrum on every write, derived
from the write's context rather than trusted from the caller. Once direct
edits are blocked, this stamp becomes ground truth.
"""

from __future__ import annotations

from enum import Enum, StrEnum


class ProvenanceError(Exception):
    """A provenance value or declared level set is outside the closed spectrum."""


class Provenance(StrEnum):
    """The seven canonical provenance levels, human end first."""

    HUMAN = "human"
    HUMAN_EDITED = "human-edited"
    HUMAN_REVISED = "human-revised"
    AI_ASSISTED = "ai-assisted"
    AI_COMPILED = "ai-compiled"
    AI_METADATA = "ai-metadata"
    AI_COMPUTED = "ai-computed"


# Ordered low (most human) to high (most machine).
SPECTRUM: tuple[Provenance, ...] = (
    Provenance.HUMAN,
    Provenance.HUMAN_EDITED,
    Provenance.HUMAN_REVISED,
    Provenance.AI_ASSISTED,
    Provenance.AI_COMPILED,
    Provenance.AI_METADATA,
    Provenance.AI_COMPUTED,
)

_HUMAN_LEVELS: frozenset[Provenance] = frozenset(
    {Provenance.HUMAN, Provenance.HUMAN_EDITED, Provenance.HUMAN_REVISED}
)
_AI_LEVELS: frozenset[Provenance] = frozenset(
    {
        Provenance.AI_ASSISTED,
        Provenance.AI_COMPILED,
        Provenance.AI_METADATA,
        Provenance.AI_COMPUTED,
    }
)


class AuthorType(StrEnum):
    """The provenance *category* (V2D 3-property model): who authored a note.

    Coarser than the ``author_level`` gradient — ``human`` (Rob), ``ai`` (any
    AI-touched content), ``external`` (3rd-party origin, caller-declared, e.g.
    SoftwareApplication or imported Web Content).
    """

    HUMAN = "human"
    AI = "ai"
    EXTERNAL = "external"


def author_type_for(
    level: Provenance, declared: AuthorType | None = None
) -> AuthorType:
    """Derive the author_type category from an author_level.

    A caller-declared ``external`` always wins (3rd-party origin can't be
    inferred from the level). Otherwise pristine ``human`` maps to ``human``;
    every AI-touched level maps to ``ai``.
    """
    if declared is AuthorType.EXTERNAL:
        return AuthorType.EXTERNAL
    if level is Provenance.HUMAN:
        return AuthorType.HUMAN
    return AuthorType.AI


def parse_author_type(value: str) -> AuthorType:
    """Parse a string into an AuthorType, rejecting anything off-category."""
    try:
        return AuthorType(value)
    except ValueError as exc:
        raise ProvenanceError(
            f"{value!r} is not a valid author_type; "
            f"expected one of {[a.value for a in AuthorType]}"
        ) from exc


class Actor(Enum):
    """Who is performing a write."""

    HUMAN = "human"
    AGENT = "agent"


class WriteMode(Enum):
    """The kind of write being performed."""

    CREATE = "create"  # authoring note content
    METADATA = "metadata"  # touching frontmatter / tags only
    COMPUTE = "compute"  # deterministic compute, no session context


def parse(value: str) -> Provenance:
    """Parse a string into a Provenance level, rejecting anything off-spectrum."""
    try:
        return Provenance(value)
    except ValueError as exc:
        raise ProvenanceError(
            f"{value!r} is not a valid provenance level; "
            f"expected one of {[p.value for p in SPECTRUM]}"
        ) from exc


def stamp(actor: Actor, mode: WriteMode) -> Provenance:
    """Derive the provenance level for a *new* write from its context."""
    if mode is WriteMode.COMPUTE:
        return Provenance.AI_COMPUTED
    if actor is Actor.HUMAN:
        return Provenance.HUMAN
    if mode is WriteMode.METADATA:
        return Provenance.AI_METADATA
    return Provenance.AI_ASSISTED


def transition(current: Provenance, editor: Actor) -> Provenance:
    """Advance provenance when an existing note is modified.

    A human editing AI-produced content marks it ``human-edited``; an agent
    touching human content makes it ``ai-assisted``. The transition never
    silently downgrades AI-touched content back toward ``human``.
    """
    if editor is Actor.HUMAN:
        if current in _AI_LEVELS:
            return Provenance.HUMAN_EDITED
        return current
    # editor is an agent
    if current in _HUMAN_LEVELS:
        return Provenance.AI_ASSISTED
    return current


def transition_author_type(current: AuthorType, editor: Actor) -> AuthorType:
    """Advance author_type when an existing note is modified (monotonic).

    Mirrors the level no-downgrade rule: a human authoring atop human content
    stays ``human``; an agent touching human content makes it ``ai``; ``ai``
    and ``external`` never silently revert to ``human``.
    """
    if current is AuthorType.HUMAN and editor is Actor.AGENT:
        return AuthorType.AI
    return current


def validate_schema_levels(levels: tuple[str, ...]) -> None:
    """Verify a schema's declared provenance levels match the canonical spectrum.

    Ties the Provenance System to the Schema Engine: the schema config may
    declare the levels, but this module owns the closed truth.
    """
    declared = tuple(levels)
    canonical = tuple(p.value for p in SPECTRUM)
    if declared != canonical:
        raise ProvenanceError(
            f"schema provenance levels {list(declared)} do not match the "
            f"canonical spectrum {list(canonical)}"
        )
