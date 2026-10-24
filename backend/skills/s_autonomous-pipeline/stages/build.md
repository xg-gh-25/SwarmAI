# BUILD

Implement DESIGN_SPEC tasks in dependency order (direct: the requirement given at create; it has no DESIGN_SPEC).

Test the way you would without the pipeline, in the run's worktree: write or change the test that shows the change, see it fail before the fix, run the change's tests with the repo's own commands. Fix a whole failure cluster in one pass.

End BUILD with your validation report on stdin: `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -` with `{"reports": [{"repo", "commands": [{"command", "cwd", "result": "pass|fail|not_run", "reason"}], "tests_changed", "rationale"}]}`, one report per repo. `fail` and `not_run` need a reason. The pipeline does not rerun anything: review judges whether the report is enough.

Required check: BX-3 (a change exists, and every repo has a BUILD report on its current code tree).
