---
name: speckit-analyze
description: Perform cross-artifact consistency analysis across spec.md, plan.md,
  and tasks.md.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:forge-pipeline
user-invocable: true
disable-model-invocation: false
---

# Speckit Analyze Skill

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

Check for `hooks.before_analyze` in `.specify/extensions.yml` per standard hook format.

## Goal

Verify that the RFC, plan, and tasks are internally consistent and board-ready. This is the quality gate before implementation. Constitution violations are always CRITICAL.

**STRICTLY READ-ONLY**: Do not modify any files.

## Execution Steps

### 1. Load artifacts

Run `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks` once. Parse paths for SPEC, PLAN, TASKS. Load `.specify/memory/constitution.md`.

### 2. Build traceability models

- **Hierarchy check**: Verify the Component architecture follows the pipeline hierarchy:
  - Each Component maps to exactly one Epic
  - Each Capability maps to exactly one Feature under its Component's Epic
  - Each Slice maps to exactly one Story under its Capability's Feature
  - No Tasks appear in the RFC (fleet deepens at activation)

- **Dependency graph**: Extract inter-Component dependencies. Check for:
  - Circular dependencies (CRITICAL)
  - Missing dependency declarations (Components that reference each other without explicit dep)
  - Dependency ordering consistency between RFC and plan execution sequence

- **Impact scoring**: Verify every Component has an Impact score (0-10). Flag:
  - Missing scores (HIGH)
  - Scores without rationale in the Impact table (MEDIUM)
  - High-impact items (>=7) without explicit human-review acknowledgment (HIGH)

- **Decision Log**: Check every entry has:
  - A Resolution (not TODO/TBD/OPEN)
  - A Rationale
  - Alternatives considered (at least one)
  - Status marker (LOCKED / OPEN / DEFERRED)

- **Task coverage**: Map each task to one or more Slices. Identify:
  - Slices with zero tasks (gap)
  - Tasks with no mapped Slice (orphan)
  - Acceptance criteria in the RFC with no corresponding verification task

### 3. Detection passes

Limit to 50 findings total.

#### A. Constitution alignment (always first)
- Any spec/plan/task element conflicting with a MUST principle
- Missing mandated quality gates from constitution

#### B. Hierarchy integrity
- Components without Capabilities
- Capabilities without Slices
- Slices that are not vertically sliced (dependent on other Slices within same Capability)

#### C. Definition of Ready gate
- [ ] Every decision resolved (no OPEN status in Decision Log)
- [ ] Architecture complete (all Components have Capabilities and Slices)
- [ ] Acceptance derivable per Slice
- [ ] Dependencies stated
- [ ] Impact estimated

#### D. Coverage gaps
- Slices with zero associated tasks
- Tasks with no mapped Slice
- Non-functional requirements (performance, security) not reflected in tasks

#### E. Consistency
- Terminology drift across files
- Entity references in plan absent from spec (or vice versa)
- Task ordering contradictions

#### F. Board readiness
- RFC frontmatter complete (title, parent, repo, bucket)
- Component names are board-safe (concise, no special characters that break GitHub Issues)
- Slice descriptions fit in a GitHub Issue title (under 256 chars)

#### G. Content quality
- No implementation details leaked into acceptance criteria or Goal
- Acceptance criteria are measurable, technology-agnostic, user-focused, verifiable
- No unresolved vague adjectives ("robust", "intuitive", "fast") without metrics
- No unresolved placeholders (TODO, TBD, NEEDS CLARIFICATION) outside Decision Log

### 4. Severity assignment

- **CRITICAL**: Constitution MUST violation, circular dependency, missing core artifact
- **HIGH**: Definition of Ready gate failure, missing Impact score, orphan Slice with no tasks
- **MEDIUM**: Terminology drift, missing non-functional coverage, underspecified acceptance
- **LOW**: Style improvements, minor redundancy

### 5. Output report

Markdown report (no file writes):

## Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|

**Definition of Ready Checklist:**
- [ ] Decisions resolved: {count resolved}/{count total}
- [ ] Architecture complete: {Components}/{Capabilities}/{Slices}
- [ ] Acceptance derivable: {count with acceptance}/{count Slices}
- [ ] Dependencies stated: {yes/no}
- [ ] Impact estimated: {count scored}/{count Components}

**Coverage Summary:**
| Slice | Has Tasks? | Task IDs | Acceptance | Notes |
|-------|-----------|----------|------------|-------|

**Constitution Alignment:** {pass/fail with details}

**Metrics:**
- Total Components / Capabilities / Slices
- Task coverage %
- Critical issues count
- Definition of Ready: {PASS / FAIL (list failures)}

### 6. Next actions

- If CRITICAL: resolve before `/speckit-implement`
- If Definition of Ready FAIL: list specific failures and remediation
- If PASS: "Ready for implementation. Run `/speckit-implement`."

### 7. Offer remediation

Ask: "Would you like me to suggest concrete edits for the top N issues?" (Do NOT apply automatically.)

## Post-Execution Checks

Check for `hooks.after_analyze` in `.specify/extensions.yml` per standard hook format.

## Context

$ARGUMENTS
