# TEST

Run the final verification on the reviewed tree yourself: the change's tests and the wider checks the repo expects before a commit (its README, AGENTS.md or CI tell you which). Run it from the worktree's own source directory (an editable install from the main checkout can load main's code instead). Do not change code in TEST. If a fix is needed, make it and end TEST as usual: the run goes back to REVIEW for an incremental review of that change, then returns to TEST (at most twice).

When `next` lists `brain_checks` (the Brain's own checks for the kind of work the run recorded at create, `--agent-mode`), run the ones this change touches and give each command that runs one `"validation": "<its id>"`. The REPORT lists each check as its result or not run; that never stops TEST or the landing.

End TEST with the validation report on stdin, in the same shape as BUILD: `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -`.

TS-1: every repo has a TEST report on its current code tree with no failed command. TS-4: every DESIGN_SPEC test reference names a file of that tree (a planned one counts only once it exists). A failed command keeps the run in TEST; never report a failure as passing. If main moves in DELIVER, test again on the synced tree and send the report with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`.

A valid example of this stage's document, with placeholders for your own values: `"$SWARM_BACKEND_BIN" pipeline-v1 template validation_report`.
