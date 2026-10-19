# BUILD ref — AC Coverage Matrix and the exit artifact

Read before writing `/tmp/<R>/build.json`, when `stage-complete` rejects it, or on the
legacy publish/advance path.

## Self-Verification: AC Coverage Matrix (MANDATORY before publish) `[GATE·validator]` <!-- R-build-016 -->

After all TDD cycles complete, before `stage-complete`: walk every PLAN Acceptance
Criterion and produce the coverage matrix. You verify completeness, not the reviewer.

1. Read the PLAN artifact's `acceptance_criteria` (stage-start returns them).
2. For each AC identify `impl` (file + function/class implementing it, e.g.
   `auth.py::login()`), `test` (test file + test function verifying it, e.g.
   `test_auth.py::test_login_email`) and `verified` (did the test pass? must be `true`).
3. Cannot identify impl or test for an AC → you are NOT DONE. Go back, implement/test it.
4. Put the matrix in the artifact as `ac_coverage`:

```json
"ac_coverage": [
  {"ac": "AC1: User can login with email", "plan_ac_ref": "AC1", "impl": "auth.py::login()", "test": "test_auth.py::test_login_email", "verified": true},
  {"ac": "AC2: Invalid email returns 400", "plan_ac_ref": "AC2", "impl": "auth.py::login()", "test": "test_auth.py::test_login_invalid_email", "verified": true}
]
```

Rules:
- **Every** PLAN AC must appear. Missing = you didn't finish.
- `impl` is specific (`file::symbol`), never vague ("somewhere in auth module"). Python
  `auth.py::login()` | TypeScript `Auth.tsx::useAuth()` | Rust `auth.rs::login`.
- `test` is specific (`test_file::test_function`), never "tests exist".
- `verified` must be `true`. A failing test is fixed before completing the stage.
- `plan_ac_ref` (optional, recommended): the AC id for unambiguous cross-reference with
  PLAN; without it the validator text-matches.
- **Validator enforcement:** Check 8f BLOCKS if `ac_coverage` is missing, incomplete, or
  doesn't cover all PLAN ACs. You cannot advance to REVIEW without it.
- TEST's Layer 1 runs the `test` entries, so they must be real refs, never prose: a
  backend nodeid (`backend/tests/test_x.py::test_y`), a desktop vitest FILE
  (`desktop/src/.../X.test.tsx`, no `::name`), or `probe:<name>` for a probe run with
  `--mode probe --name <name>` (no `--files`). Every word of the field is read as a ref,
  so an explanation in it makes the AC fail. Format: `../test/layers.md` § Layer 1.

## The build.json data file

Start from the stage-start example (or `python backend/scripts/artifact_cli.py schema --stage build`).
Required: `files_changed`, `tdd` (depth: `green_pass`, `smoke_tests`), `ac_coverage`.
Recommended: `commits`, `diff_summary`.

```json
{"artifact": {
   "files_changed": ["backend/core/foo.py", "backend/tests/test_foo.py"],
   "diff_summary": "...",
   "commits": [],
   "tdd": {"acceptance_criteria_count": 3, "tests_generated": 5, "red_count": 5,
           "green_count": 5, "red_failures": 5, "green_pass": true, "regressions": 0,
           "smoke_tests": 3, "smoke_crashes_caught": 0, "user_path_traces": 3,
           "user_path_bugs_found": 0, "probes": 0, "probe_bugs_found": 0},
   "ac_coverage": [ ... ]},
 "record": {"decisions": [{"description": "...", "classification": "taste", "reasoning": "..."}],
            "gate1_verdict": "PASS", "gate1_blocking": true, "gate1_override": false,
            "gate1_retries": 0, "gate1_checks": {...}, "replanned_acs": [],
            "exit_evidence": {"verify": "VERIFY attempt 1/2 green"}}}
```

- `files_changed`: list every file this run changed, including files written by a Bash
  command or script (the edit hook records only Edit/Write tool edits). `stage-complete`
  unions the list with run.json `files_touched`, keeps each entry that is a file on disk
  or a tracked deleted file (not a directory), and writes the result back to
  `files_touched`, which is what the DELIVER diff, the Gate-2 snapshot and `run-commit`
  use. List only files you edited. Never paste `git status` output: in a shared
  checkout it holds other sessions' files, and `run-commit` would commit them. The
  stage-complete output shows the entries it added under
  `derived.files_changed.added_from_declared` and the ones it refused under
  `dropped_from_declared`. A file a script writes after BUILD (a
  REVIEW, TEST or Gate-2 fix) is recorded with `run-update --files-touched <path>`.
- `commits` stays empty: BUILD stages, `run-commit` at DELIVER is the sole commit point.
- `regressions` = failures left after the 2 VERIFY re-runs (`ref/build/verify.md`).
- Check 8 (semantic depth) WARNs when `smoke_tests` is 0 with more than one file changed.
- `gate1_*` and `replanned_acs` go in `record` because `stage-complete` replaces the
  build stage record (only `artifact_id`, `adversarial_review`, `lessons` carry forward).
- A PLAN exit criterion of kind `manual` is met only by a non-empty
  `record.exit_evidence["<criterion id>"]` string; `test`/`probe`/`mutation` criteria are
  judged from runner evidence.
- Derived by the tool, never written by you: `token_cost`, `consumed_artifacts`,
  `stage_doc_consumed`, `artifact_id`, `exit_criteria_met`.

No preflight: stage-complete validates everything before it writes anything, so run it directly and fix from its error. `--check` (same command + `--check`) only asks mid-stage whether the exit criteria are met.
Errors name the field path and the expected value; fix from the error, don't read
`artifact_cli.py` source. When the result says `exit_criteria_met: true`, stop adding
verification and complete the stage.

## Legacy path (sibling skills / older runs)

`publish --type changeset --producer s_autonomous-pipeline --stage build --summary "<N>
files changed, TDD: <red>/<green>/<verify>" --data '<artifact JSON>'` followed by
`advance --state review` still works. New runs use `stage-complete`, which validates,
publishes, records and advances in one call.
