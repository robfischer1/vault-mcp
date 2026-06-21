# Code standards — project portfolio

Mechanical code-quality rules shared by every sibling code repo (vault-mcp, phdb, board-mcp, the plugins, fleet-crew, the frontends), enforced by per-repo linters (ruff / eslint / hadolint / jsx-a11y) and CI — NOT by vault-mcp (markdown notes only). Language-, container-, and length-specific rules are path-scoped (`.claude/rules/`: `python-style`, `frontend`, `docker`, `file-length`, poured by furnace); this block carries only the always-on shared judgment.

## Dependencies
Adding a dependency for <20 lines of app code is the wrong trade — write the 20 lines (security-critical crypto / auth excepted).

# Build lifecycle

How a capability matures across the sibling code repos — three tiers, rigor scaling
with rollout rather than build completeness: **Prove → Harden → Replicate**.

- Prove the reusable instance on first use, not the third — recurrence is foreseeable
  and the fleet replicates near-free; hand-rolling a one-off when the shape recurs is
  the antipattern. (Deliberately inverts the Rule of Three.) Test-first/TDD is the
  mechanism of the Prove gate, not a separate tier.
- Harden the proven exemplar before any fan-out — the fleet never touches unhardened
  novel code. Test depth follows tier: Prove = baseline (prove it) · Harden = full
  suite + security/PII/packaging → /publish-ready-auditor · Replicate = smoke per copy.
- Fan-out proof is orchestrator-run, never fleet-self-reported.

# Git discipline

Code-kit block. The mechanical parts are enforced (`git_guard` hook) and reminded (`worktree-check` rule); this is the judgment that isn't.

## Board
- No standalone plan docs — the board is the source of truth (entry: Obsidian's `Planning` pointer). `create_item` for one-off tasks only.
- Close each item the moment its work lands — never batch; `sweep_done` rolls up finished parents.
- `refresh` before reading board state; re-parent via `move_item`, never the GitHub UI.

## Branches & the merge gate
- Branch per work item, named for its board item. Main advances only through the gate (`open_pr` → `disposition_pr`) — never a direct commit.
- Commit to the default branch only when told (`GIT_GUARD_ALLOW_MAIN=1` to authorize).
- `git add` explicit paths only — never `-A`/`.`; commit only your own changes.

## Worktrees (concurrent sessions)
- Each code-repo session works in its own worktree on its own branch — never two sessions in one tree. Worktree is a sibling dir `<repo>-<branch-slug>`.
- Remove your worktree on merge or session close; `git worktree prune` clears stale ones.

## Vault excepted
- The vault is vault-mcp's single checkout — sessions run no git there; `write_note()` commits-on-write + an hourly sweep. The rules above govern code repos only.
