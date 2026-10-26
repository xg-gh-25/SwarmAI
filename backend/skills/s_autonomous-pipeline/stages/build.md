# BUILD

Input: the DESIGN_SPEC tasks and exit criteria (direct: the requirement given at create; it has no DESIGN_SPEC).

Write and test the change your own way, in the run's worktree, with the repo's own commands. The pipeline does not say how. If a dependency is installed in editable form from the main checkout (an editable Python install, an npm/yarn link), code run in the worktree can load the main checkout's copy: start the tests from the worktree's own source directory or install the environment in the worktree, and give that directory as `cwd`.

End BUILD with your validation report on stdin: `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -` with `{"reports": [{"repo", "commands": [{"command", "cwd", "result": "pass|fail|not_run", "reason"}], "tests_changed", "rationale", "risk"}]}`, one report per repo. `fail` and `not_run` need a reason. Add `"risk": ["security"]` when the change touches authentication, permissions, input validation, credentials, network, file paths or shell commands: it dispatches the security review (nothing else infers it). The pipeline does not rerun anything: review judges whether the report is enough.

Required check: BX-3 (a change exists, and every repo has a BUILD report on its current code tree).
