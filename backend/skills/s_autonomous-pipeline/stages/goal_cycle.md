# GOAL_CYCLE

Execute one bounded goal cycle against DESIGN_SPEC. Test it the same way as BUILD and end the cycle with a validation report.

Required check: BX-3. The stage attempt cap is authoritative; exhausting it ends safely rather than asking the user.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -` (the report on stdin) and continue.
