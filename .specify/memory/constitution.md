<!--
Sync Impact Report — v1.0.0 (initial draft, corrected)
- Version: (none) → 1.0.0
- Source: Forge planning governance (gap-protocol, binding/deferral, conformance —
  turns 1-3 of the spec-kit-adoption session). Code-stage rules are NOT sourced here;
  they live in the repo's own `.claude/rules/` (path-scoped, on-demand).
- Correction vs first draft: removed the embedded Type-Soundness / Simplicity
  articles and the `Code-Standards.md` reference. Those restated the code-scoped
  `.claude/rules` — which are the operative source and load on-demand. `Code-Standards.md`
  is the human-readable compilation of them, not agent-loaded and not present in a
  stamped repo, so the constitution must not depend on it.
- Principles: I Spec-Is-Law · II Deferral-Terminates · III Contracts-Named ·
  IV Conformance-Checkable · V Verify-Before-Done
- Templates aligned: plan-template.md (I-IV) ✅ · tasks-template.md (IV + I/II) ✅ ·
  spec-template.md (I) ✅
- Deferred: RATIFICATION_DATE = today (first adoption). Revisit if backdating to the
  first spec-kit pilot (2026-05-15) is preferred.
-->
# Forge Pipeline Constitution

The architectural DNA the `analyze` gate enforces at plan-stage — how a spec becomes
a binding, executor-ready plan. Code-stage engineering rules are governed separately
and load on-demand from this repo's own `.claude/rules/` (see Engineering Standards);
they are not restated here.

## Core Principles

### I. The Spec Is Law (No Convention)
Every point a plan touches is exactly one of `decided` (the spec licenses it — the
executor MUST follow), `[OPEN]` (the spec is silent and it matters — SURFACE it,
never resolve by inventing), or `non-goal` (out of frame). There is no fourth
state. "Use judgment" / "how the repo does it" is a violation. **There is no
convention, only the spec.** Rationale: frontloading relocates confabulation from
silent mid-execution to a loud, inspectable plan-time gate.

### II. Deferral Terminates in the Plan
A decision deferred to the planner is decided concretely and written binding,
provenance-tagged (Rob / Default / Claude) — never passed down to the executor as
implicit discretion. A reasonable default the author picked is BINDING, not a hint
the executor may re-open. Rationale: laissez-faire compounds downward; the plan is
where the deferral chain dies.

### III. Contracts Are Named, Not Pointed-At
Every interface a unit **exposes** AND **consumes** is specified by shape —
signature, DDL, payload, port — not merely a pointer to where it lives. Both sides
of every seam are pinned. Rationale: the consumed/required side is the dependency
edge; an executor given only the provided side re-derives it or drifts.

### IV. Conformance Is Checkable
Every unit's Acceptance is a falsifiable conformance target. The implementation is
diffed against Acceptance + Touches + State before the unit counts done. Rationale:
bindingness without an enforcement diff is hope; the gate is what makes "decided"
real.

### V. Verify Before Done
"Done" means the work was read back / run / row-counted AND what was observed is
reported. "I changed it" without a check is insufficient. Code that compiles is not
code that works. Every bug and error is the author's work — no deferral to the
framework or a prior session.

## Engineering Standards (code-stage)

Code-quality rules are **not** restated here. They load **on-demand via this repo's
own `.claude/rules/`** — path-scoped `.md` rules (e.g. `python-style`, `typescript`,
`docker`, `frontend`, `file-length`, `worktree-check`) that an agent loads when it
touches a matching file — and are enforced by per-repo linters + CI on a **strict
ratchet** (a touched file's debt blocks; pre-existing debt is grandfathered until
that file is next edited).

The stamped repo carries its own `.claude/rules/`; `Code-Standards.md` is the
human-readable compilation of those rules (a digest — not agent-loaded, and not
present in this repo). Target shape for new services: the container-ready Forge MCP
service (`service-repo-template`).

## Development Workflow

- The board is the source of truth for work items; plans project onto it.
- git-guarded repos: branch per work item → PR → disposition; the default branch is
  not a work surface.
- Each scoped commit carries its proof-of-work; a plan passes its Definition of
  Ready and the `analyze` gate before `implement`.

## Governance

This constitution supersedes ad-hoc practice for the planning pipeline. The
`analyze` gate validates a plan against Articles I-IV (plan-stage); the repo's
`.claude/rules/` + CI enforce code-stage quality; Article V is the author's standing
duty. Amendments require a documented rationale and a semantic version bump (MAJOR:
principle removal/redefinition; MINOR: new principle/section; PATCH: clarification).

**Version**: 1.0.0 | **Ratified**: 2026-06-16 | **Last Amended**: 2026-06-16
