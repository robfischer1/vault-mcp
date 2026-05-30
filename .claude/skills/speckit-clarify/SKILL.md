---
name: speckit-clarify
description: Structured clarification workflow for underspecified requirements.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:forge-pipeline
user-invocable: true
disable-model-invocation: false
---

# Speckit Clarify Skill

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before clarification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_clarify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- When constructing slash commands from hook command names, replace dots (`.`) with hyphens (`-`).
- For each executable hook, output based on its `optional` flag per standard hook format.
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Outline

Goal: Detect and reduce ambiguity in the active feature RFC, with questions anchored to the board hierarchy and the constitution.

Note: This runs BEFORE `/speckit-plan`. If the user explicitly skips clarification, warn that downstream rework risk increases.

### 1. Load context

Run `.specify/scripts/powershell/check-prerequisites.ps1 -Json -PathsOnly` from repo root once. Parse `FEATURE_DIR` and `FEATURE_SPEC`. Abort if JSON parsing fails.

### 2. Taxonomy scan

Load the spec (RFC). Scan using this Forge-adapted taxonomy. Mark each: Clear / Partial / Missing.

**Component Architecture (highest priority):**
- Component boundaries well-defined (each maps to exactly one Epic)
- Capability scope within each Component (each maps to one Feature)
- Slice vertical-cut completeness (each independently verifiable)
- No Tasks in the design (fleet deepens at activation)

**Dependencies and Ordering:**
- Explicit inter-Component dependencies stated
- Circular dependency check
- External prerequisites identified

**Impact and Blast Radius:**
- Impact score (0-10) per Component with rationale
- High-impact items (>=7) flagged for human review gate

**Decision Log Completeness:**
- Every open question has a Resolution + Rationale
- Alternatives considered documented
- No TODO / TBD markers in decisions

**Goal and Non-goals:**
- Goal is falsifiable ("done looks like X")
- Non-goals are explicit (boundary against scope creep)

**Acceptance Criteria:**
- Per-Slice acceptance where known
- Criteria are checkable (not vague adjectives)

**Constitution Alignment:**
- Spec does not violate any MUST principle in `.specify/memory/constitution.md`
- Board-as-truth principle respected (no standalone plan docs planned)

**Integration and External Dependencies:**
- External services/APIs and failure modes
- Cross-initiative dependencies noted

### 3. Generate question queue

Maximum 5 questions. Prioritize by (Impact * Uncertainty). Each question must be either:
- Multiple-choice (2-5 options), OR
- Short-phrase answer (<=5 words)

**Every question MUST include a recommendation.** Analyze all options and determine the most suitable based on the spec context, constitution principles, and common patterns.

**Reasonable defaults -- do NOT spend a question on these** unless the feature specifically depends on a non-standard answer:
- Data retention (use industry-standard practices for the domain)
- Performance targets (standard expectations unless the feature is performance-critical)
- Error handling (user-friendly messages with appropriate fallbacks)
- Authentication method (standard session-based or OAuth2 for web; API keys for services)
- Integration patterns (project-appropriate: REST/GraphQL for web, function calls for libraries, CLI args for tools)

### 4. Sequential questioning loop (interactive picker)

Present EXACTLY ONE question at a time using the `AskUserQuestion` tool as a native structured picker -- not a prose table.

For multiple-choice questions:
- **Analyze all options first** and determine the most suitable one (best practices for the project type, constitution principles, common patterns, alignment with the RFC's stated goals/constraints).
- Call `AskUserQuestion`:
  - `question`: the clarification text, prefixed `"Recommended: Option [X] -- <1-2 sentence reasoning>\n\n<question text>"`.
  - `options[]`: `{label, description}` objects with the **recommended option first**, its `description` prefixed `Recommended -- <reasoning>.`
  - Append a final `{label: "Short", description: "Provide my own short answer (<=5 words)"}` escape hatch.
  - `multiSelect`: `false`.
- If the user picks "Short", ask a follow-up free-text question constrained to <=5 words.

For short-answer questions (no meaningful discrete options):
- Determine your **suggested answer** from best practices and RFC context.
- Call `AskUserQuestion`:
  - `question`: `"Suggested: <answer> -- <brief reasoning>\n\n<question text>\nFormat: Short answer (<=5 words)."`
  - `options[]`: `[{label: "Accept suggestion", description: "Use the suggested answer above"}, {label: "Custom", description: "Provide my own short answer (<=5 words)"}]`.
  - `multiSelect`: `false`.
- If the user picks "Custom", ask a follow-up free-text question constrained to <=5 words.

After each answer: if the user accepted the recommendation/suggestion option, use your stated recommendation as the answer; otherwise validate it maps to one option or fits the <=5-word constraint. Record in working memory; never reveal queued questions in advance.

Stop when: all critical ambiguities resolved, user signals done, or 5 questions asked.

### 5. Integrate answers

After each accepted answer:
- Ensure a `## Clarifications` section exists (create after Summary if missing)
- Append `- Q: {question} -> A: {answer}` under `### Session YYYY-MM-DD`
- Apply the clarification to the appropriate RFC section
- Save the spec file after each integration

### 6. Validate and report

- Count questions asked/answered
- List sections touched
- Coverage summary table (taxonomy categories with status)
- If Outstanding or Deferred remain, recommend whether to proceed to plan or re-clarify

## Post-Execution Checks

Check for `hooks.after_clarify` in `.specify/extensions.yml` per standard hook format.

## Context

$ARGUMENTS
