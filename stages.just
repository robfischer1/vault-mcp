# THE TWO STAGES (CA master-plan F15), BOTH RETIRED. THE COMMIT STAGE RUNS
# NOTHING, since 2026-11-11 (Rob: "Check was the equivalent of the pre-commit
# stage, which we retired. Sessions should just push"): hooks/pre-commit only
# runs fixers and always exits 0, and the `check` recipe is gone with `foundry-toolbox ask check` (2026-11-17:
# "It's one workflow, not two": commit, push, and the door's lanes run in the
# branch refPod, fail-fast and light steps first; a session's ci_park hook parks
# on the verdict).
#
# THE PUSH STAGE RUNS NOTHING, since 2026-10-02
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

# The module that carries the stages. CA_MODULE overrides it for a session
# gating a foundry-tools change against itself (`CA_MODULE=/path/to/checkout`).
# It stays declared because a star's non-dev recipes (govulncheck, opengrep) read it.
ca_module := env("CA_MODULE", "git.notusmi.com/rob/foundry-tools@main")

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
