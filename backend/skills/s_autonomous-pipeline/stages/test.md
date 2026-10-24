# TEST

Run the final verification on the reviewed tree yourself: the change's tests and the wider checks the repo expects before a commit (its README, AGENTS.md or CI tell you which). Do not change code in TEST: if something needs fixing, say so; the change goes back through review.

End TEST with the validation report on stdin, in the same shape as BUILD: `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -`.

TS-1: every repo has a TEST report on its current code tree with no failed command. A failed command keeps the run in TEST; never report a failure as passing. If main moves in DELIVER, test again on the synced tree and send the report with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`.
