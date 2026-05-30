"""Compute Receiver — turns structured payloads into vault notes.

phdb's periodic compute jobs (Atlas recomputes, summaries, rollups) emit a
structured payload; the Compute Receiver renders it through a named template
and writes the result through the Convention Gate, stamped ``ai-computed``.
Rendering is pure string substitution — **no LLM runs here** (phdb owns all
model calls). Same payload + template always yields a byte-identical note.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import Any

from .gate import ConventionGate, WriteResult
from .provenance import Actor, WriteMode

_REQUIRED_KEYS: tuple[str, ...] = ("template", "title", "directory")


class ComputePayloadError(Exception):
    """A compute payload does not conform to the published contract."""


@dataclass(frozen=True)
class ComputePayload:
    """A validated compute payload."""

    template: str
    title: str
    directory: str
    data: dict[str, Any]
    frontmatter: dict[str, Any]


def parse_payload(raw: object) -> ComputePayload:
    """Validate a raw payload against the contract; raise on any nonconformance."""
    if not isinstance(raw, dict):
        raise ComputePayloadError("compute payload must be a mapping")

    missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
    if len(missing) > 0:
        raise ComputePayloadError(f"compute payload missing required field(s): {missing}")
    for key in _REQUIRED_KEYS:
        if not isinstance(raw[key], str):
            raise ComputePayloadError(f"compute payload field {key!r} must be a string")

    data = raw.get("data") or {}
    frontmatter = raw.get("frontmatter") or {}
    if not isinstance(data, dict):
        raise ComputePayloadError("compute payload 'data' must be a mapping")
    if not isinstance(frontmatter, dict):
        raise ComputePayloadError("compute payload 'frontmatter' must be a mapping")

    return ComputePayload(
        template=raw["template"],
        title=raw["title"],
        directory=raw["directory"],
        data=data,
        frontmatter=frontmatter,
    )


def _render_body(template_src: str, data: dict[str, Any]) -> str:
    """Render a template deterministically from payload data (no LLM)."""
    return Template(template_src).safe_substitute({k: str(v) for k, v in data.items()})


class ComputeReceiver:
    """Renders compute payloads into notes via the Convention Gate."""

    def __init__(self, gate: ConventionGate, templates: dict[str, str]) -> None:
        self._gate = gate
        self._templates = dict(templates)

    def receive(self, raw_payload: object, created: str | None = None) -> WriteResult:
        """Validate, render, and write a compute payload as an ai-computed note."""
        payload = parse_payload(raw_payload)
        if payload.template not in self._templates:
            raise ComputePayloadError(
                f"unknown template {payload.template!r}; known: {sorted(self._templates)}"
            )

        body = _render_body(self._templates[payload.template], payload.data)
        return self._gate.create_note(
            title=payload.title,
            body=body,
            directory=payload.directory,
            actor=Actor.AGENT,
            mode=WriteMode.COMPUTE,
            extra_fields=payload.frontmatter,
            created=created,
        )
