# THE TWO STAGES (CA master-plan F15). hooks/pre-commit runs `just check`, the
# commit stage: ONE LINE, `foundry-toolbox ask check`, which pushes the staged
# tree to the door's refs/checks/<c> and asks lane check on the cluster
# (SYS-008; the build, the push and the long-poll live in foundry-toolbox).
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
#
# THE DOOR IS A DEPENDENCY, AND A SILENT ONE NEVER BLOCKS. The check runs on the
# cluster, where the git origin already lives (Rob, 2026-09-17: "Shit, our git
# origin is already in the cluster"). A push that fails or a door that does not
# answer prints `state=none` and the commit proceeds; only findings refuse it
# (Rob, 2026-10-02). NO SKIP LIST AND NO --no-verify: this is the check the door
# runs, so a commit that findings refuse here cannot pass there either.
#
# THE COMMIT STAGE is the basic checks every tree gets, beside the language
# checks for what this tree contains — format, lint, unit tests — planned before
# anything runs and settled on the worst of them. The slow checks and mutation
# that the push stage used to run here are the branch's lanes now, run by the
# door against the merge base with the default branch.

# The module that carries the stages. CA_MODULE overrides it for a session
# gating a foundry-tools change against itself (`CA_MODULE=/path/to/checkout`).
# `check` no longer reads it (SYS-008); dev.just's `_dev` still does, until
# that recipe asks the door too (build order step 7).
ca_module := env("CA_MODULE", "git.notusmi.com/rob/foundry-tools@main")

# The commit stage — what hooks/pre-commit runs.
check:
    foundry-toolbox ask check

# A WORKSTATION'S ENGINE CALL TAKES A LEASE FIRST (2026-10-11). The engines are
# the CI lanes' too, and daedalus admits lanes on each engine's running units;
# a call made from here used to load the node without counting in them, so
# daedalus read the load as dearer lanes and sized CI down (budget.go: carry =
# running × target / load), and the call left no trace to price it by. Now
# `dev-lease acquire` (foundry-toolbox, attesting as host/<host>/dev-lease)
# asks daedalus for a lease, which counts on the engine it names; the call
# runs with that lease's TRACEPARENT and, when daedalus knows the engine's
# address, against that engine; the lease is renewed while the call runs and
# given back when it ends, and one that is never given back expires.
#
# A LEASE NEVER BLOCKS WORK. No binary, a refusal, a timeout (3 s) or output
# that is not the lease's: one stderr line and the call runs as before,
# unmetered. DEV_LEASE=off skips it without the line. The command is a child,
# not exec'd, so the EXIT trap gives the lease back; its exit code passes
# through unchanged. The binary's lines are checked against NAME='value'
# before they are evaluated.
[private]
[positional-arguments]
_leased *CMD:
    #!/usr/bin/env bash
    set -uo pipefail
    bin="${DEV_LEASE_BIN:-/usr/local/bin/dev-lease}"
    unmetered() { echo "dev-lease: unmetered ($1), using ${_EXPERIMENTAL_DAGGER_RUNNER_HOST:-the default engine}" >&2; }
    if [[ "${DEV_LEASE:-}" == off ]]; then
        :
    elif [[ ! -x "$bin" ]]; then
        unmetered "no $bin"
    else
        holder="$(hostname)/${CLAUDE_CODE_SESSION_ID:-pid$$}"
        if ! out="$("$bin" acquire --holder "$holder")"; then
            unmetered "the lease was refused"
        elif ! grep -qvxE "(DEV_LEASE_[A-Z]+|TRACEPARENT)='[^']*'" <<<"$out" && grep -qE "^DEV_LEASE_ID='[^']+'\$" <<<"$out"; then
            eval "$out"
            export TRACEPARENT
            export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT="${OTEL_EXPORTER_OTLP_METRICS_ENDPOINT:-http://hemera.notusmi.com:4318/v1/metrics}"
            # The leased engine only if it answers (1 s): a pod mid-restart,
            # or a label not yet stamped on it, leaves its Service with no
            # endpoint. The lease still counts where daedalus placed it.
            if [[ "${DEV_LEASE_ADDRESS:-}" =~ ^tcp://([^:/]+):([0-9]+)$ ]]; then
                if command -v timeout >/dev/null 2>&1 &&
                    timeout 1 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" 2>/dev/null; then
                    export _EXPERIMENTAL_DAGGER_RUNNER_HOST="$DEV_LEASE_ADDRESS"
                else
                    echo "dev-lease: $DEV_LEASE_ADDRESS did not answer, using ${_EXPERIMENTAL_DAGGER_RUNNER_HOST:-the default engine}" >&2
                fi
            fi
            idfile="$(mktemp)"
            echo "$DEV_LEASE_ID" >"$idfile"
            # A renewal of a lease daedalus lost grants a new one; the file
            # follows it, so the release gives back the one that counts. The
            # renewer holds none of the call's output: a pipe reading `just`
            # would otherwise wait on its sleep.
            ( while sleep 300; do
                  r="$("$bin" renew --id "$(cat "$idfile")" --holder "$holder" 2>/dev/null)" || continue
                  [[ "$r" =~ DEV_LEASE_ID=\'([^\']+)\' ]] && echo "${BASH_REMATCH[1]}" >"$idfile"
              done ) </dev/null >/dev/null 2>&1 &
            renewer=$!
            trap 'pkill -P "$renewer" 2>/dev/null; kill "$renewer" 2>/dev/null; "$bin" release --id "$(cat "$idfile")" >/dev/null || true; rm -f "$idfile"' EXIT
        else
            unmetered "the lease answered something else"
        fi
    fi
    trap 'exit 130' INT
    trap 'exit 143' TERM
    "$@"
    exit $?

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
