---
name: speckit-tasks
description: Break down implementation plans into actionable task lists.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:cross-platform-governance
user-invocable: true
disable-model-invocation: false
---

# Speckit Tasks Skill

Before continuing, apply the Cross-Platform Governance preset:

- add explicit tasks for both `*.sh` and `*.ps1` variants in the same
  change
- add tasks for the Unix man-page and the bilingual PowerShell help
  block
- add a task to expose the PowerShell variant as a Cmdlet with an
  approved `Verb-Noun` name
- add a parity-verification task using the script-parity checklist

Before continuing, apply the Security Governance preset:

- convert MSL applicability and justification needs into explicit tasks
- convert security obligations into explicit tasks
- include evidence-production tasks under `docs/security/`
- avoid leaving secure-development work as undocumented assumptions


## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before tasks generation)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_tasks` key
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

In the Forge pipeline, `/speckit-tasks` deepens Stories into Tasks on the board via board-mcp instead of generating a local `tasks.md` file. By this point in the workflow, projecter has already reconciled the RFC into board issues (Epic/Feature/Story). This step adds the Task layer.

**Prerequisites:**
- The RFC has been reconciled to the board (projecter ran as `after_plan` hook)
- board-mcp is available
- Stories exist on the board under the current feature's Epic

## Execution Steps

### 1. Resolve the feature

Locate the active feature:
- Read `.specify/feature.json` for the `feature_directory`
- Read `{feature_directory}/spec.md` for the RFC
- If `$ARGUMENTS` contains an issue number (e.g., `#874`), scope to that single Story
- If `$ARGUMENTS` contains "all", deepen all Stories under the Epic

### 2. Identify Stories to deepen

Query board-mcp for the Epic's sub-issues:
- Find the Epic by matching the RFC title or frontmatter `bucket` to board items
- List all Stories under that Epic (Type=Story, state=OPEN)
- Exclude Stories that already have sub-issue Tasks (already deepened)
- Present the Story list and ask which to deepen — default is all undeepened Stories

### 3. Generate and create Tasks per Story

For each Story to deepen, read its acceptance criteria from the board item body or the RFC's corresponding Slice. Decompose into Tasks:

- Each Task is an **atomic unit one agent executes in one sitting**
- Tasks carry acceptance criteria derived from the Story's acceptance
- Typical decomposition: 2-5 Tasks per Story
- Task titles should be action-oriented ("Implement X", "Write tests for Y", "Wire Z config")

**No review gate.** Task decomposition is AI-driven post-planning — create directly via board-mcp `deepen` without presenting for approval. The planning phase (specify → plan → projecter) is where human review happens; by this point the architecture is locked.

For each Story, call board-mcp `deepen`:
- `issue_number`: the Story's GitHub issue number
- `tasks`: list of `{title, body}` dicts
- `repo`: the Story's repo

`deepen` creates sub-issues, adds them to the project, and sets their Type to Task.

### 5. Report

After all Stories are deepened, report:
- Total Tasks created (by Story)
- Any Stories skipped or errored
- Suggested next step: `/speckit-implement` to dispatch to the fleet

### 6. Extension hooks (after tasks)

After the Stories are deepened, check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_tasks` key
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

## Fallback: Local tasks.md

If board-mcp is unreachable or the RFC hasn't been reconciled to the board yet:
1. Warn that the board is unavailable
2. Offer to generate a local `tasks.md` using the stock spec-kit tasks flow
3. If accepted, read `.specify/templates/tasks-template.md` and generate tasks locally at `{feature_directory}/tasks.md`

## Context

$ARGUMENTS
