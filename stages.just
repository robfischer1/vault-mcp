# THE TWO STAGES (CA master-plan F15). hooks/pre-commit runs `just check`, the
# commit stage, on the engine. THE PUSH STAGE RUNS NOTHING, since 2026-10-02
# (Rob, "Option 1": the lanes are the gate). A push of a branch makes the door
# ask that head's lanes on the cluster — the gate, and mutation and the build
# where they apply — as the pull it will become (ourea branch_lanes.go), and
# the pull that opens on it reuses them. So hooks/pre-push and `just gate`
# only say where the answer is. The recipe keeps its name because the blades
# and sessions call it; it no longer reaches the engine.
#
# RENDERED BY THE SPEC-KIT OVERRIDE TEMPLATE (foundry-stocks, speckit/), tracked,
# re-laid at every session start: one home for the call across the fleet, so a
# change to what a commit must pass is one template landing. The star's own
# justfile takes it with `import 'stages.just'` and defines no `check` or `gate`
# of its own. Edit it in foundry-stocks, never here.
#
# THE ENGINE IS A DEPENDENCY, DELIBERATELY. The stages run on the cluster's Dagger
# engine, so a commit needs the cluster — which is where the git origin already
# lives (Rob, 2026-09-17: "Shit, our git origin is already in the cluster"). An
# engine that cannot be reached refuses the commit rather than waving it
# through: a check that could not run is not a check that passed. NO SKIP LIST
# AND NO --no-verify: this is the gate the door runs, so a commit that cannot
# pass it here cannot pass it there either.
#
# THE COMMIT STAGE is the basic checks every tree gets, beside the language
# checks for what this tree contains — format, lint, unit tests — planned before
# anything runs and settled on the worst of them. The slow checks and mutation
# that the push stage used to run here are the branch's lanes now, run by the
# door against the merge base with the default branch.

# The module that carries the stages. CA_MODULE overrides it for a session
# gating a foundry-tools change against itself (`CA_MODULE=/path/to/checkout`).
ca_module := env("CA_MODULE", "git.notusmi.com/rob/foundry-tools@main")

# `--source=.` IS THE WHOLE POINT, NOT A NICETY. The module declares its
# Source with `+defaultPath="."`, and for a module loaded from git that
# default is the MODULE'S OWN checkout — GitRef.tree of foundry-tools at the
# ref, shallow — not the tree the hook runs in. MEASURED 2026-09-18 (dagger
# v0.21.9): without it, the push stage in a TypeScript star ran the Go lanes,
# reported "no package.json at the repository root", and the witness could
# not run because the star's merge base was "not in this history" — foundry-
# tools' history. Every star's stage read green against the wrong tree.
# `--source=.` hands the function this checkout, with the module's ignore
# patterns applied and `.git` as the working tree carries it.

# A STAGE THAT OUTLIVES ITS ENGINE FAILS INSTEAD OF HANGING. The dagger client
# waits on the engine, and when the engine pod goes — a roll, an OOM kill, a
# node eviction — the wait has no end: the run neither finishes nor errors,
# and the hook holds the terminal until someone notices. MEASURED across the
# 2026-09-18 wedge (21:28-21:45Z) and the 2026-09-19 OOM kill (20:56:33Z):
# both left client-side stages sitting at "1 steps running" with no engine
# behind them.
#
# The budget is an order above what the stage takes — seconds of narrowed
# atoms — so a stage that reaches it is not slow, it is orphaned.
# INT first so the client can tear its session down and say what it was
# doing; KILL after 30s for the case where it cannot. Exit 124 is timeout's
# own and the message says which budget was spent; every other code is the
# lane's, passed through unchanged — a findings exit must not come back as a
# generic 1 because a guard ate it.

# The commit stage, on the engine — what hooks/pre-commit runs.
check:
    #!/bin/sh
    set -eu
    if ! command -v timeout >/dev/null 2>&1; then
        exec dagger call -m "{{ca_module}}" --source=. check exit
    fi
    set +e
    timeout --signal=INT --kill-after=30s 15m dagger call -m "{{ca_module}}" --source=. check exit
    code=$?
    set -e
    if [ "$code" -eq 124 ]; then
        echo "check: the commit stage ran 15 minutes without finishing — the engine it was talking to is gone or wedged, not slow; look at the engine, then commit again" >&2
    fi
    exit "$code"

# THE PUSH STAGE IS THE BRANCH'S LANES, AND THE LOCK IS GONE (2026-10-02).
#
# This recipe used to run the slow checks and mutation through the dagger client
# straight at the shared engine, under a per-host flock on
# ${TMPDIR:-/tmp}/ca-push-stage.lock with a 45-minute limit. The lock existed
# because a hook's run was in none of the door's accounting: six push stages
# inside a minute OOMKilled dagger-engine-n2bw6 on 2026-09-19. It held, and it
# cost: MEASURED 2026-10-02 16:28Z, five pushes from different sessions on one
# host were queued on it, the oldest for 44 minutes, and an unmetered
# push-stage mutation run killed node dev01 twice that day — while the pull's
# own lanes then ran the same checks again, because nothing accepted the
# hook's result.
#
# WHAT REPLACED IT: the door. A pushed branch's lanes are Jobs the door asks
# and daedalus meters against the engine's budget, visible in the runner's
# dashboards, settled into the CI record, and reused by the pull — one run,
# not two, and no host lock because nothing here reaches the engine. A push
# fails only when the door refuses it; a red lane refuses the landing, which
# is where a refusal can act.
gate:
    @echo "gate: this branch's lanes are its gate: the door runs them on the cluster for the head you pushed (metered by daedalus); git_pr open parks on them, and repo_ci_logs reads them" >&2
