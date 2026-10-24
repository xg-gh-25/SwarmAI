# GOAL_CYCLE

Execute one bounded goal cycle against DESIGN_SPEC. Use the same TDD and proof commands as BUILD.

Required checks: DS-2b, DS-9b, BX-1. The stage attempt cap is authoritative; exhausting it ends safely rather than asking the user.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
