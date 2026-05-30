---
name: speckit-implement
description: Execute all tasks from the task breakdown to build the feature.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:forge-pipeline
user-invocable: true
disable-model-invocation: false
---

# Speckit Implement Skill

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before implementation)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_implement` key
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

## Mode Selection

Parse `$ARGUMENTS` for the execution mode:

- **`local`** anywhere in arguments → Local execution mode (skip fleet, implement here)
- **Everything else** → Fleet dispatch mode (default)

Examples:
- `/speckit-implement` → fleet dispatch
- `/speckit-implement #856` → fleet dispatch for Story #856
- `/speckit-implement local` → local execution, all Stories
- `/speckit-implement local #856` → local execution for Story #856

---

## Shared Steps (both modes)

### 1. Resolve the Story

Identify the Story to implement:
- If `$ARGUMENTS` contains an issue number (e.g., `#856`), use that
- If in a spec-kit feature directory, resolve the Story from the spec's frontmatter
- If ambiguous, ask which Story to dispatch

### 2. Gather Story payload

Read the Story's details from Board-MCP or the manifest:
- `issue_number`, `title`, `body`, `repo`
- `acceptance` criteria (from the RFC or Story body)
- `parent_issue` (the Feature this Story belongs to)

### 3. Check impact gate

If the Story's parent Feature or Epic has Impact >= 7:
- **Do NOT auto-proceed.** Present the Story details and ask for explicit approval.
- This is the human-review gate from the constitution's Impact-Gated Progression principle.

If Impact < 7, proceed automatically.

---

## Fleet Dispatch Mode (default)

**Prerequisites:**
- The Story must be on the board (has a GitHub issue number)
- Board-MCP must be running (Hephaestus, port 8102)
- Crew service must be running (llm01, port 8200)

### 4. Dispatch to crew service

Send a POST request to the crew service:

```
POST http://{crew_service_host}:8200/kickoff
Content-Type: application/json

{
  "story": {
    "issue_number": {issue_number},
    "title": "{title}",
    "body": "{body}",
    "repo": "{repo}",
    "parent_issue": {parent_issue},
    "acceptance": "{acceptance}"
  }
}
```

The crew service returns a `job_id` immediately.

### 5. Poll for completion

Poll `GET /job/{job_id}` until status is "completed" or "failed":
- Check every 30 seconds
- Report progress to the user on status changes
- Timeout after 30 minutes

### 6. Report results

On completion, report:
- **Tasks created** by the deepener (with issue numbers)
- **Commits** linked by the implementer (with SHAs)
- **PR URL** if one was opened
- **Review findings** from the reviewer
- **Overall status** (success / partial / failure)

On failure, report the error and suggest manual intervention.

### 7. Update board status

If the crew succeeded and the reviewer approved:
- Update the Story status to "In Review" via Board-MCP
- The human review step decides whether to close it

If the reviewer requested changes:
- Leave the Story in "In Progress"
- List the findings for Rob to review

### Fleet unavailable

If the crew service is unreachable (connection refused, timeout on health check):
1. Warn that the fleet is unavailable
2. Offer to switch to local mode
3. If accepted, follow the Local Execution steps below

---

## Local Execution Mode (`local`)

Implement the Story directly in the current Claude Code session.

### 4. Claim the Story

Call board-mcp `claim` with `agent_id: "claude-code"` to set the lease and move status to In Progress.

### 5. Implement

Read the Story's acceptance criteria and implement directly:
- Write code, tests, config — whatever the Story requires
- Follow the repo's AGENTS.md and coding conventions
- Each acceptance criterion must be satisfied and verifiable

### 6. Verify

Run tests, linters, or manual checks as appropriate to verify acceptance criteria are met.

### 7. Report and update board

- Commit changes (via `/speckit-git-commit` if available)
- Update the Story status to "In Review" via board-mcp
- Report: files changed, tests passed, acceptance criteria status

---

## Extension hooks (after implementation)

After implementation completes (either mode), check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_implement` key
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
