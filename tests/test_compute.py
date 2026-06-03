"""Unit tests for vault_mcp.compute — the Compute Receiver.

Covers Compute Receiver (#74): payload contract validation, deterministic
template rendering (no LLM), and the compute-only write path that is the sole
permitted writer into compute-only directories.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.compute import ComputePayloadError, ComputeReceiver  # noqa: E402
from vault_mcp.gate import ConventionGate, ProtectionError  # noqa: E402
from vault_mcp.provenance import Actor, Provenance, WriteMode  # noqa: E402
from vault_mcp.schema import load_schema  # noqa: E402

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"

TEMPLATES = {
    "atlas-rollup": "# $title\n\nGenerated $created with $count items.\n",
}


class FakeVault:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def create_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content


def _receiver() -> tuple[ComputeReceiver, ConventionGate, FakeVault]:
    vault = FakeVault()
    gate = ConventionGate(load_schema(VALID), vault)
    return ComputeReceiver(gate, TEMPLATES), gate, vault


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "template": "atlas-rollup",
        "title": "Atlas Rollup",
        "directory": "Atlas",
        "data": {"count": 7},
    }
    base.update(over)
    return base


class TestPayloadContract:
    def test_valid_payload_writes(self):
        rcv, _, vault = _receiver()
        result = rcv.receive(_payload(), created="2026-05-30")
        assert result.path == "Atlas/Atlas Rollup.md"
        assert "Atlas/Atlas Rollup.md" in vault.store

    def test_missing_required_key_rejected(self):
        rcv, _, _ = _receiver()
        bad = _payload()
        del bad["template"]
        with pytest.raises(ComputePayloadError) as exc:
            rcv.receive(bad)
        assert "template" in str(exc.value)

    def test_unknown_template_rejected(self):
        rcv, _, _ = _receiver()
        with pytest.raises(ComputePayloadError) as exc:
            rcv.receive(_payload(template="nope"))
        assert "nope" in str(exc.value)


class TestRendering:
    def test_renders_deterministically(self):
        rcv1, _, v1 = _receiver()
        rcv2, _, v2 = _receiver()
        rcv1.receive(_payload(), created="2026-05-30")
        rcv2.receive(_payload(), created="2026-05-30")
        assert v1.store["Atlas/Atlas Rollup.md"] == v2.store["Atlas/Atlas Rollup.md"]

    def test_body_substitutes_template_fields(self):
        rcv, _, vault = _receiver()
        rcv.receive(_payload(), created="2026-05-30")
        content = vault.store["Atlas/Atlas Rollup.md"]
        assert "with 7 items" in content

    def test_stamped_ai_computed(self):
        rcv, _, _ = _receiver()
        result = rcv.receive(_payload(), created="2026-05-30")
        assert result.provenance is Provenance.AI_COMPUTED
        assert result.frontmatter["author_level"] == "ai-computed"
        assert result.frontmatter["author_type"] == "ai"


class TestComputeOnlyPath:
    def test_compute_writes_into_compute_only_dir(self):
        rcv, _, vault = _receiver()
        rcv.receive(_payload(), created="2026-05-30")
        assert "Atlas/Atlas Rollup.md" in vault.store

    def test_session_create_into_compute_only_rejected(self):
        _, gate, _ = _receiver()
        with pytest.raises(ProtectionError):
            gate.create_note(
                title="Hand Written",
                directory="Atlas",
                mode=WriteMode.CREATE,
                actor=Actor.AGENT,
                created="2026-05-30",
            )
