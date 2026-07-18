"""Scope policy for the vault carve (C6) — which files dissolve, which stay.

CONFIG, not hardcoded. The exact pillar cut is an **open Rob-decision**: the
master-plan flags "confirm the exact pillar cut" and "Obsidian vestigial vs
retired". So the policy is a *data structure* — a dissolvable-pillar allowlist
plus an always-protect set — carried on a frozen :class:`ScopePolicy` that a
caller (or env) can override, with a flagged conservative default.

**Fail-safe by construction.** A carve DELETES the vault original after writing
it to Calliope, so an unclassified file must never be swept. The classifier's
default disposition is therefore STAY: a file dissolves only when its top-level
pillar is explicitly on the dissolve allowlist *and* not on the protect set
(protect always wins). Everything else — an unlisted pillar, a non-``.md`` file,
a bare root file — stays.

The open decisions ride on the policy as :data:`OPEN_DECISIONS` so the sweep's
pre-flight can surface them loudly rather than letting a default masquerade as a
settled choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# The exact dissolvable cut is UNCONFIRMED — surfaced by the sweep pre-flight.
OPEN_DECISIONS: tuple[str, ...] = (
    "SCOPE CUT UNCONFIRMED [Rob]: default dissolve set is the master-plan's "
    "named pillars {Journal, Brain Soup, Records, Resources, Entities}. This "
    "vault has no 'Resources/' dir — its analogue is 'References/'; confirm "
    "whether References dissolves. Undecided for Inbox / Atlas / Garden / "
    "Artifacts / Archives / Outputs / Responsibilities — all default to STAY.",
    "OBSIDIAN DISPOSITION UNDECIDED [Rob]: vestigial render over Calliope vs "
    "fully retired. Informational — does not change the sweep (the .md is "
    "deleted either way); recorded so the end-state is a choice, not a drift.",
)


@dataclass(frozen=True)
class CarveDecision:
    """The disposition of one file under a :class:`ScopePolicy`."""

    path: str
    pillar: str
    dissolve: bool
    reason: str


@dataclass(frozen=True)
class ScopePolicy:
    """Which vault pillars dissolve into Calliope and which stay put.

    ``dissolve_pillars`` is the allowlist of top-level directories whose notes
    are eligible to dissolve; ``protect_pillars`` always stay and beat the
    allowlist on any overlap. ``obsidian_disposition`` is informational (the
    open vestigial-vs-retired decision) and does not affect classification.
    """

    dissolve_pillars: frozenset[str]
    protect_pillars: frozenset[str]
    obsidian_disposition: str = "undecided"
    open_decisions: tuple[str, ...] = field(default=OPEN_DECISIONS)

    def classify(self, vault_rel_path: str) -> CarveDecision:
        """Decide whether ``vault_rel_path`` dissolves. Default is STAY."""
        norm = vault_rel_path.replace("\\", "/").lstrip("/")
        parts = norm.split("/")
        pillar = parts[0] if parts and parts[0] else ""

        if not norm.endswith(".md"):
            return CarveDecision(norm, pillar, False, "not-markdown")
        if len(parts) < 2:
            # A bare root-level file has no pillar — never sweep it.
            return CarveDecision(norm, pillar, False, "root-file-no-pillar")
        if pillar in self.protect_pillars:
            return CarveDecision(norm, pillar, False, "protected-pillar")
        if pillar in self.dissolve_pillars:
            return CarveDecision(norm, pillar, True, "dissolvable-pillar")
        return CarveDecision(norm, pillar, False, "unlisted-pillar-default-stay")

    def with_overrides(
        self,
        *,
        dissolve_pillars: frozenset[str] | None = None,
        protect_pillars: frozenset[str] | None = None,
        obsidian_disposition: str | None = None,
    ) -> ScopePolicy:
        """Return a copy with the given fields replaced (config override)."""
        return replace(
            self,
            dissolve_pillars=(
                self.dissolve_pillars
                if dissolve_pillars is None
                else dissolve_pillars
            ),
            protect_pillars=(
                self.protect_pillars
                if protect_pillars is None
                else protect_pillars
            ),
            obsidian_disposition=(
                self.obsidian_disposition
                if obsidian_disposition is None
                else obsidian_disposition
            ),
        )


# The FLAGGED default — the master-plan's named cut. Governance stays; the named
# prose/records/entities pillars dissolve. Everything unlisted defaults to STAY.
DEFAULT_SCOPE_POLICY = ScopePolicy(
    dissolve_pillars=frozenset(
        {"Journal", "Brain Soup", "Records", "Resources", "Entities"}
    ),
    protect_pillars=frozenset({"System"}),
)
