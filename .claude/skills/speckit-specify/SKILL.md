---
name: speckit-specify
description: Create or update feature specifications from natural language descriptions.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:cross-platform-governance
user-invocable: true
disable-model-invocation: false
---

# Speckit Specify Skill

Before continuing, apply the Cross-Platform Governance preset:

- identify whether this feature adds, changes, or removes a
  script-shaped tool
- record that both Bash (`*.sh`) and PowerShell (`*.ps1`) variants are
  in scope, plus a Unix man-page and a bilingual PowerShell help block
- record the planned `Verb-Noun` Cmdlet name (approved verbs only)
- record dry-run / `-WhatIf` parity expectations

Before continuing, apply the Security Governance preset:

- determine whether the primary implementation language is memory-safe
- document a short justification if the language is not memory-safe
- determine whether `NIST SSDF`, `CWE Top 25`, `OWASP ASVS`, `SBOM`, `VEX`,
  and `SLSA` are relevant
- document `N/A` decisions with rationale
- identify which security evidence artefacts should be created or updated under
  `docs/security/`


## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before discovery)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_specify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- When constructing slash commands from hook command names, replace dots (`.`) with hyphens (`-`).
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}

    Wait for the result of the hook command before proceeding.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

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

### Extension hooks (after discovery)

After producing the pitch, check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_specify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- When constructing slash commands from hook command names, replace dots (`.`) with hyphens (`-`).
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Context

$ARGUMENTS
