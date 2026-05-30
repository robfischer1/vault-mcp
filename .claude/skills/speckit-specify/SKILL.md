---
name: speckit-specify
description: 'Forge override: redirects /speckit-specify to the Discovery posture.
  In Forge projects, specify IS discovery -- divergent ideation that produces a pitch,
  not a structured spec file.'
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: forge-pipeline:commands/speckit.specify.md
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Forge Pipeline Override

In the Forge pipeline, `/speckit-specify` maps to the **Discovery** posture. This is a deliberate override: spec-kit's stock `specify` command produces a structured spec file in `specs/`. The Forge pipeline replaces that with freeform, snowball-by-default ideation that produces a **pitch** saved to `Outputs/Pitches/`.

**If running inside the Obsidian vault (Claude Code at `Forge/Obsidian/`):** Invoke `/discover` directly -- it has the full Discovery posture. This override exists so that projects using spec-kit natively get the Forge behavior when the forge-pipeline preset is installed.

**If running inside a spec-kit project (e.g., `forge-agents/`, `personal-history-db/`):** Follow the Discovery posture below.

## Discovery Posture

Goal: Expand the idea without converging. Output is a lay-readable pitch.

### Principles
- **Snowball by default** -- build on the idea, surface adjacent considerations and "what am I missing." Generative first.
- **Don't converge early** -- option-menus are convergent; lean "yes, and." Menus belong in the design phase.
- **Don't judge** -- no adversarial critique here; half-formed ideas need room. Critique belongs in design.
- **Constraints, not criticism** -- reignite a flat snowball with a creative constraint ("no new infra," "ship in a day"), never by poking holes.
- **Flag resonance, propose next steps, let the user yes-and them.**

### Lightweight clarify
When a branch of the snowball stabilizes enough to have a shape, surface 1-2 recommendation-first questions to sharpen it. Not a taxonomy scan -- just "this looks like it could go two ways; I'd lean X because Y, but which way?"

### Stall detection
When the user gives a short, confirming response (no new idea/question, not expanding), offer: "Ready to move to design, or want a prompt?"
- **Design** -- chain to `/speckit-plan` (which runs the Design posture)
- **Prompt** -- a provocation (constraint, fresh angle, obscure connection) to restart the snowball

### Output
At initiative-scale (3+ independent epic-shaped branches) with the shape stabilizing, draft the **pitch** -- lay-readable, no jargon. Save to `Outputs/Pitches/` if in the vault, or to `specs/{NNN-feature}/pitch.md` if in a spec-kit project. Then chain to `/speckit-plan`.

## Context

$ARGUMENTS