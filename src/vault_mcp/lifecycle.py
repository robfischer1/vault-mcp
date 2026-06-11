"""Lifecycle Verbs — the materialize write path.

The ``materialize`` verb renders a durable note from a structured payload and
writes it through the Convention Gate with ``mode=COMPUTE`` — the only path
permitted to create *materialize-only* ``@type``s (the Gate rejects ordinary
agent-create for them). Rendering is deterministic: the same payload and
template always yield a byte-identical note (no LLM runs here — phdb owns all
model calls, mirroring the Compute Receiver in ``compute.py``).

Where the Compute Receiver renders a body from named-template *data*, the
materialize verb carries the note ``body`` in its payload and renders it
through a ``note.stub`` template so every materialized note shares one
deterministic shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Any

from .provenance import Actor, WriteMode

if TYPE_CHECKING:
    from .gate import ConventionGate, WriteResult

_REQUIRED_KEYS: tuple[str, ...] = ("title", "note_type", "directory")

# The default rendering template. Materialize is body-carrying, so the stub is
# a pass-through; a vault may override ``note.stub`` to standardize the shape.
DEFAULT_TEMPLATE: str = "note.stub"
_BUILTIN_TEMPLATES: dict[str, str] = {DEFAULT_TEMPLATE: "$body"}


class MaterializePayloadError(Exception):
    """A materialize payload does not conform to the published contract."""


@dataclass(frozen=True)
class MaterializePayload:
    """A validated materialize payload."""

    title: str
    note_type: str
    directory: str
    body: str
    frontmatter: dict[str, Any]
    template: str
    data: dict[str, Any]


def parse_payload(raw: object) -> MaterializePayload:
    """Validate a raw payload against the contract; raise on any nonconformance."""
    if not isinstance(raw, dict):
        raise MaterializePayloadError("materialize payload must be a mapping")

    missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
    if len(missing) > 0:
        raise MaterializePayloadError(
            f"materialize payload missing required field(s): {missing}"
        )
    for key in _REQUIRED_KEYS:
        if not isinstance(raw[key], str):
            raise MaterializePayloadError(
                f"materialize payload field {key!r} must be a string"
            )

    body = raw.get("body") or ""
    frontmatter = raw.get("frontmatter") or {}
    data = raw.get("data") or {}
    if not isinstance(body, str):
        raise MaterializePayloadError(
            "materialize payload 'body' must be a string"
        )
    if not isinstance(frontmatter, dict):
        raise MaterializePayloadError(
            "materialize payload 'frontmatter' must be a mapping"
        )
    if not isinstance(data, dict):
        raise MaterializePayloadError(
            "materialize payload 'data' must be a mapping"
        )

    template = raw.get("template") or DEFAULT_TEMPLATE
    if not isinstance(template, str):
        raise MaterializePayloadError(
            "materialize payload 'template' must be a string"
        )

    return MaterializePayload(
        title=raw["title"],
        note_type=raw["note_type"],
        directory=raw["directory"],
        body=body,
        frontmatter=frontmatter,
        template=template,
        data=data,
    )


def _render_body(template_src: str, data: dict[str, Any]) -> str:
    """Render a note body deterministically from payload data (no LLM)."""
    return Template(template_src).safe_substitute(
        {k: str(v) for k, v in data.items()}
    )


class Materializer:
    """Renders materialize payloads into notes via the Convention Gate."""

    def __init__(
        self, gate: ConventionGate, templates: dict[str, str] | None = None
    ) -> None:
        """Build a materializer that renders payloads through the Gate using `templates`."""
        self._gate = gate
        # Caller templates override the built-in pass-through stub.
        self._templates = {**_BUILTIN_TEMPLATES, **(templates or {})}

    def materialize(
        self, raw_payload: object, created: str | None = None
    ) -> WriteResult:
        """Validate, render, and write a payload as a materialized note.

        Writes through the Gate with ``mode=COMPUTE`` so materialize-only
        ``@type``s are accepted; ``directory`` is passed through verbatim (the
        caller owns routing, as materialize-only types often have no schema
        route). The note ``body`` is exposed to the template as ``$body``.
        """
        payload = parse_payload(raw_payload)
        if payload.template not in self._templates:
            raise MaterializePayloadError(
                f"unknown template {payload.template!r}; known: {sorted(self._templates)}"
            )

        body = _render_body(
            self._templates[payload.template],
            {**payload.data, "body": payload.body, "title": payload.title},
        )
        return self._gate.create_note(
            title=payload.title,
            note_type=payload.note_type,
            body=body,
            directory=payload.directory,
            actor=Actor.AGENT,
            mode=WriteMode.COMPUTE,
            extra_fields=payload.frontmatter,
            created=created,
        )
