# THE BUILD AND TEST LOOP RUNS ON THE CLUSTER, NOT ON THIS MACHINE. This library
# holds ONE private recipe, `_dev`, and it is ONE LINE: `foundry-toolbox ask dev
# <verb> [args...]` (SYS-008). The working tree goes to the door's refs/checks/<c>,
# lane dev runs the verb on the cluster in the same pinned images and cache volumes
# as the branch lanes, so a local answer and the gate's agree and the workstation
# needs neither a Go nor a Rust toolchain; a long-poll returns the verdict and the
# files a run changed are applied back to the worktree. The push, the poll, the
# vendor guards (a real vendor/ directory inside the tree), DEV_FRESH=1 (skip the
# cache) and the exit codes (0 green, 1 findings, 2 anything else) live in
# foundry-toolbox (`foundry-toolbox ask --help`) and are tested there.
#
# RENDERED BY THE SPEC-KIT OVERRIDE TEMPLATE (foundry-stocks, speckit/), tracked,
# re-laid at every session start, beside stages.just. A star's justfile takes it
# with `import? 'dev.just'` and writes public recipes that call `_dev`; it
# defines no `_dev` of its own. `import?`, not `import`, so a tree that has not
# been re-laid yet still parses and `just check` (the commit hook) keeps working;
# the recipes then fail at the call, naming the missing `_dev`. Edit it in
# foundry-stocks, never in a star.
#
# THE CALLING CONVENTION IS UNCHANGED, because every star's recipes spell it the same:
#
#   @{{just_executable()}} --justfile {{justfile()}} --set ca_module {{quote(ca_module)}} _dev <verb> "$@"
#
# (with `[positional-arguments]` on the caller). `_dev` is recursed into rather
# than named as a dependency so each argument keeps its boundary. `--set
# ca_module` is still passed and still accepted: stages.just declares it, and a
# star's non-dev recipes (govulncheck, opengrep) read it. `_dev` itself does not.
#
# EVERY ARGUMENT PASSES THROUGH AS ARGV, commas, quotes and newlines included
# (no CSV): `just test -run TestX ./internal/foo/...` reaches the verb as given.
# The verbs are the door's lane dev: build test vet check clippy fmt tidy vendor
# lock update regen replay lint typecheck. The recipe runs in the importing
# justfile's directory, and the toolbox resolves the worktree from there.

# One dev verb on the cluster: `just _dev <verb> [tool args...]`.
[private]
[positional-arguments]
_dev VERB *ARGS:
    foundry-toolbox ask dev "$@"
