---
name: speckit-plan
description: Generate technical implementation plans from feature specifications.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:cross-platform-governance
user-invocable: true
disable-model-invocation: false
---

# Speckit Plan Skill

Before continuing, apply the Cross-Platform Governance preset:

- plan paired Bash + PowerShell script work as a single unit
- plan the man-page, the bilingual PowerShell help block, and the
  `Verb-Noun` Cmdlet alongside the script
- plan manual verification on at least one target OS per variant
- plan implementation discipline checks (Bash quoting, `set -euo
  pipefail`, `Set-StrictMode -Version Latest`, `-NoProfile`) and the
  parity-checklist artefact

Before continuing, apply the Security Governance preset:

- plan explicit MSL applicability or non-MSL justification work when relevant
- plan explicit secure-development verification work
- plan dependency and supply-chain evidence updates where relevant
- surface security review checkpoints instead of leaving them implicit


## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before design)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_plan` key
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

In the Forge pipeline, `/speckit-plan` maps to the **Design** posture. This is a deliberate override: spec-kit's stock `plan` command produces research.md, data-model.md, and contracts/. The Forge pipeline replaces that with convergent RFC authoring that ends at Definition of Ready, then hands to `/projecter` for board transcription.

**If running inside the Obsidian vault (Claude Code at `Forge/Obsidian/`):** Invoke `/design` directly -- it has the full Design posture. This override exists so that projects using spec-kit natively get the Forge behavior when the forge-pipeline preset is installed.

**If running inside a spec-kit project:** Follow the Design posture below.

## Design Posture

Goal: Sculpt a pitch (from `/speckit-specify` or provided directly) into an RFC. Convergent -- refine, decide, structure.

### Step 1: Concept extraction

Parse the pitch into structured elements before carving architecture:
- **Actors** -- who uses this? (human roles, agent types, external systems)
- **Actions** -- what do they do? (verbs become capabilities)
- **Data entities** -- what objects/records are created, read, updated?
- **Constraints** -- what's fixed? (technical, governance, timeline)

Every extracted element must map to a Component, Capability, or Slice -- or be explicitly listed in Non-goals.

### Step 2: RFC authoring

Fill the RFC template (resolved from the forge-pipeline preset's `spec-template`):
- **Summary, Goal, Non-goals**
- **Component architecture** -- carve Component (Epic) / Capability (Feature) / Slice (Story). No Tasks. Acceptance per Slice where known.
- **Dependencies, Impact (0-10), Decision Log (ADR-style), Open follow-ons**

Acceptance criteria per Slice must be:
- Measurable (specific metrics)
- Technology-agnostic (no frameworks/languages/databases)
- User-focused (outcomes, not internals)
- Verifiable (testable without implementation details)

### Step 3: Clarify pass

Structured ambiguity reduction -- max 5 recommendation-first questions:
- Taxonomy scan: Component boundaries, Capability scope, Slice vertical-cut, Dependencies, Impact rationale, Decision Log completeness, Goal falsifiability, Acceptance checkability.
- Every question includes a recommendation with reasoning.
- Answers fold directly into the RFC.
- Skip if the user signals the RFC is sharp enough.

**Do NOT ask about:** data retention, performance targets (unless perf-critical), error handling patterns, auth method, integration patterns. These have reasonable defaults.

### Step 4: Quality checklist

Before the Definition of Ready gate, validate content quality:
- [ ] No implementation details in acceptance criteria or Goal
- [ ] Focused on user value, not system internals
- [ ] No vague adjectives without metrics ("robust," "intuitive," "fast")
- [ ] All Slices have acceptance or explicit "TBD -- fleet derives" marker
- [ ] No unresolved placeholders outside Decision Log

### Step 5: Definition of Ready (gate)

- [ ] Quality checklist passes
- [ ] Every Decision Log entry has Resolution + Rationale (no OPEN/TBD)
- [ ] Architecture complete: every Component has Capabilities, every Capability has Slices
- [ ] Acceptance derivable per Slice
- [ ] Dependencies stated (inter-Component, cross-initiative)
- [ ] Impact estimated (0-10 per Component with rationale)

### Step 6: Analyze gate

Lightweight traceability check before handoff:
- **Constitution alignment** -- no MUST violations in `.specify/memory/constitution.md`
- **Hierarchy integrity** -- well-formed tree, no Tasks in design
- **Dependency graph** -- no circular deps, all explicit
- **Board readiness** -- frontmatter complete, Slice titles fit GitHub Issue titles
- **Concept coverage** -- every actor/action/entity/constraint accounted for
- **Content quality** -- no implementation details in acceptance, no vague adjectives

CRITICAL findings block handoff. MEDIUM/LOW are advisory.

### Output

Save the RFC:
- In the vault: to `Outputs/Pitches/` (doubles as the pitch + RFC)
- In a spec-kit project: to `specs/{NNN-feature}/spec.md`

Report: Definition of Ready status, analyze gate results, suggested next step (projecter or `/speckit-tasks`).

### Step 7: Extension hooks (after design)

After reporting, check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_plan` key
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

This is the hook that offers `/speckit-projecter-reconcile` after the RFC is produced (projecter's `after_plan` hook, `optional: true`).

## Context

$ARGUMENTS
