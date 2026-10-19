# TEST ref — classifying failures, the fix loop, the WTF gate

Read when the one full-scope regression (DELIVER) or a tdd run is red, or before scoring a fix.

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`

## Regression definition

The runner classifies every failure against a HEAD baseline (it runs only the failing
nodeids in a cached HEAD worktree; a failure that also fails there is pre-existing at
once, any other failing test's whole file is re-run). You act on its `classification`;
you no longer judge the class by hand.

| Status (`classification`) | Meaning | Action |
|--------|---------|--------|
| **regression** | Test PASSED before this changeset (at HEAD), FAILS now | Fix the code (not the test) |
| **new** (new failure) | Test was ADDED in this changeset and fails | Fix the code (TDD green phase incomplete) |
| **pre-existing** | Test was already failing before this changeset | Log in IMPROVEMENT.md "Known Issues"; do NOT fix in this pipeline run, do not debug it |
| **flaky** | Passes/fails non-deterministically (file re-run passes, passed at HEAD) | Fix the flake (race condition, time dependency, shared state). A test that passes 9/10 = FAILS |
| **unknown** | Not reproducible either way (e.g. never reached a call report at HEAD) | Exit 1, never cached, never pre-existing; investigate from the attached summary/stack |

Rules that hold across all classes:
- Fix the CODE, not the test. Changing a test expectation = changing the spec = go back
  to PLAN. Tests define correct behavior; code conforms to them.
- Never silently pass over a red test. Pre-existing failures are logged, not ignored.
- Never re-run until green. Flaky is a real bug; fix it.

## Fix loop

1. Evaluate the WHOLE red run together: group the failures by cause (one broken
   invariant or shared helper usually explains several), write one fixing spec (cause,
   every site in scope, one solution per cluster), then fix every cluster in one pass;
   list the clusters in `exit_evidence.fixes`. Never fix failure after failure
   (`ref/instructions/fixing-findings.md`).
2. Score the pass with the WTF gate (below). Score ≥ 5 → halt: stop fixing, finish not
   push-ready.
3. Check the pass with the tests it affects only: `RUN --mode tdd --files <the failing
   test files, ≤5>` (or `--mode prove` for break tests), never a full-scope run. Still
   red → the next pass starts again at step 1.
4. After the last fix: ONE more `RUN --mode regression` (the runner allows two full-scope
   rounds per run). Re-running it on unchanged code is refused as a no-op: the result
   stands as reported (it never re-runs a failed test for a lucky pass); `continue: true`
   only continues tests the deadline left unrun. DELIVER's push_ready bound rejects a
   full-scope report older than the last edit.
5. Unfixed issues at the end: document each with its diagnosis in
   `exit_evidence.unfixed`.

## Max fixes

Max 20 fixes per session. After 20, stop: complete TEST with what remains and DELIVER
not push-ready. Quality > completion.

## WTF Gate `[MUST]` <!-- R-test-003 -->

> `wtf_gate.py` is a real script, but the pipeline does NOT block publish on its score:
> the halt-at-≥5 is an agent-obeyed L2 checkpoint (the agent runs the script and acts
> on it), not a validator-enforced publish gate. `[MUST]`. (Also stated on the card, R-test-004.)

Score EVERY fix, including "obvious" ones (an obvious fix that touches 4+ files is not
obvious). Show the score in `exit_evidence.wtf` even when it is 0.

```bash
python backend/skills/s_autonomous-pipeline/scripts/wtf_gate.py --files-touched N --fix-count M \
  [--unrelated-module true] [--api-contract-changed true] [--previous-fix-broke true]
```

WTF score formula (the script computes it; the flags feed the judgment terms):
```
wtf_score = 0
+2 if fix touches > 3 files
+3 if fix modifies unrelated module
+2 if fix changes API contract
+1 if fix_count > 10
+3 if previous fix broke something
--> halt if wtf_score >= 5 (judgment decision --> L2 BLOCK)
```

On `"halt": true`: stop fixing, record the score and breakdown, and finish the run not
push-ready (DELIVER with `push_ready: false`, the halt reason in `attention_flags`).
