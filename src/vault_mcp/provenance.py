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
