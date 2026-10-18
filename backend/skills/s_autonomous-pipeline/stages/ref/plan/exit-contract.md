# PLAN ref — exit contract (`exit_criteria[]`)

Read when: writing a probe, mutation or manual criterion, picking a work-type default, or
re-planning after a later stage completed. The shape and the "met → stop verifying" rule are
on the card (step 10); the requirement gate is in the card's Gates.

The contract is written BEFORE build: it fixes the end point of verification. `stage-complete`
at build, test and deliver judges every criterion against runner evidence and returns
`exit_criteria_met: true` once all are met; `next_action` then says not to add more
verification. On strict profiles DELIVER's `push_ready` is false while any criterion is unmet.

## Shape

```json
{"exit_criteria": [
  {"id": "X1", "kind": "test", "ref": "backend/tests/test_x.py::test_red_team_case_1", "expect": "pass"},
  {"id": "X2", "kind": "test", "ref": "backend/tests/test_security_hooks.py", "expect": "pass"},
  {"id": "X3", "kind": "probe", "ref": "bash-fuzz", "expect": "no_new_findings"}]}
```

Structural checks (`validate_exit_criteria`, error code `E_EXIT_CRITERIA`):
- `id`: non-empty, unique.
- `kind` ∈ `test` | `probe` | `mutation` | `manual`.
- `ref`: non-empty string; any `<...>` placeholder is rejected (name the real target).
  `manual` may omit `ref`. A `probe` ref must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$`.
  A `test` ref is `mode:<m>` or ONE file / nodeid (`file.py`, `file.py::test`, `x.test.tsx`).
  A directory (trailing `/`, no file extension, or an existing directory) or a glob
  (`*`, `?`) is rejected on every profile: the judge matches one result file, so it
  could never be met. List each test file, or use `mode:regression` for whole-scope coverage.
- `expect`: non-empty; use `pass` (test), `no_new_findings` (probe), `all_killed`
  (mutation), `done` (manual).
- Missing or empty list → BLOCK on strict profiles (everything except trivial/docs/research;
  `direct` is strict). Relaxed profiles may omit it.

## How each kind is judged

| kind | met when | evidence written by |
|------|----------|---------------------|
| `test` (nodeid or file) | newest runner report mentioning it has ≥1 pass and no failure, and the report postdates the last edit of the touched files | `pipeline_test.py --mode tdd/verify/regression/recheck` |
| `test` with `ref: "mode:<m>"` | `latest-<m>.json` fully green and no newer report has a regression-classified failure | same |
| `probe` | `evidence/probes/<ref>.json` has `converged: true` | `pipeline_test.py --mode probe --name <ref> -- <command>` |
| `mutation` | `evidence/mutations/latest.json` has `all_killed: true` | `pipeline_test.py --mode mutation --mutations <spec.json>` |
| `manual` | the stage record's `exit_evidence["<id>"]` is a non-empty string | you, in the build/test/deliver record |

Manual evidence example (DELIVER record): `"exit_evidence": {"X9": "manual check: opened the
page and the badge renders"}`.

## Work-type defaults (`exit_criteria_default` from stage-start)

The defaults are templates with `<...>` refs you must replace.

| work_type | default |
|-----------|---------|
| bugfix | X1 test: one regression test per counter-example · X2 test: each touched test file · X3 probe: one round, `no_new_findings` |
| refactor | X1 test: each touched test file · X2 test `mode:verify` · X3 mutation `all_killed` |
| docs / research | X1 manual `done`: what a reader checks to confirm the deliverable |
| anything else | one `test` criterion per AC (up to 10) + a final `mode:verify` test |

For a gate or parser fix the usual contract is: every red-team counter-example as a regression
test, the gate's test file green, one probe round with no new findings.

## Probe and mutation rules

- A probe is converged after one round with 0 new findings. Re-running it with neither the
  target nor the probe changed returns the cached result at zero cost ("target and probe
  unchanged"). If that happens after you "extended" the probe, the extension did not take
  effect.
- Extending only the probe (target unchanged) earns one more round; if that round is also
  clean, the probe is finally converged. Further extension needs a new finding first.
- In a pipeline run, a converged probe in the contract is judged by its converged result;
  stage-complete does not accept new rounds of it as evidence.
- Mutation mode runs every mutation in one call in an isolated worktree (HEAD + this session's
  changed files + the mutation) with `--maxfail=0`. Never hand-roll mutation scripts in the
  shared tree.

## Moving the goalposts

The contract only grows because of a new finding: a new counter-example adds a new
regression-test criterion with a recorded reason. A PLAN re-completed after a later stage
already completed may only ADD criteria: every old id must survive unchanged (same kind, ref,
expect), and every added criterion needs a non-empty `reason`. To change a criterion, keep it
and add a new id. Re-enter PLAN with `stage-start --stage plan` before re-completing it:
stage-complete refuses a completed stage that was not re-entered (`E_STAGE_NOT_OPEN`), so the
re-record is measured over its own window, not across the stages that ran since.

## `direct` profile

`direct` = plan → build → deliver, no evaluate/think docs. Required for a commit that changes
more than 20 source lines in a session with no run of its own (`direct_commit_gate` prints the
`run-create --profile direct` command). The model writes its own contract here in PLAN; no
user question is needed.
