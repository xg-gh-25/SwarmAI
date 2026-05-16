# TEST Stage

## Base Methodology

> **Reference:** `backend/skills/s_qa/INSTRUCTIONS.md`
>
> Follow the scoped test methodology defined there: detect test framework, run scoped tests, fix failures with atomic commits.

## Pipeline-Specific Behavior

### Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Tests pass, no need for scoped re-run" | Run changed + related test files. Pass in isolation ≠ pass together. LL13: mock-based tests all passed but real DB had zero matching rows — function returned empty string in production. | LL13 |
| "This fix is simple, skip the WTF score" | Score every fix. "Simple" fixes that touch 4 files are not simple. C009: 5 iterations on a "simple" hook because each fix revealed new scope. | C009 |
| "I'll adjust the test expectation to match the new behavior" | Fix the CODE, not the test. Changing test expectations = changing the spec = go back to PLAN. Tests define CORRECT behavior; code must conform to them. | TDD principle |
| "Pre-existing failure, not our problem" | Log it in IMPROVEMENT.md "Known Issues." Never silently pass over a red test — it erodes the signal. Today's "pre-existing" is tomorrow's "we thought it was fine." | Pipeline design |
| "19 fixes done, just one more to clean up" | 20 is the hard cap. Checkpoint. Report. Quality > completion. The 21st fix historically introduces more bugs than it solves (WTF score data). | WTF Gate |
| "Tests are flaky, re-run until green" | Flaky = non-deterministic = real bug (race condition, shared state, time dependency). Fix the flake, don't re-roll the dice. A test that passes 9/10 times FAILS. | STEERING.md |

### WTF Gate

Calculate WTF score via script:

```bash
python backend/skills/s_autonomous-pipeline/scripts/wtf_gate.py --files-touched N --fix-count M
```

WTF score formula:
```
wtf_score = 0
+2 if fix touches > 3 files
+3 if fix modifies unrelated module
+2 if fix changes API contract
+1 if fix_count > 10
+3 if previous fix broke something
--> halt if wtf_score >= 5 (judgment decision --> L2 BLOCK)
```

### Max Fixes

Max 20 fixes per session. After 20, checkpoint and report regardless of remaining failures.

### Test Execution

1. Detect test framework (or read from TECH.md)
2. Run tests scoped to changed files
3. For each failure: attempt fix + atomic commit
4. Run WTF gate after each fix
5. Run changed + related test files after all fixes (NOT full suite)

### Exit Evidence Checklist

Confirm each before publishing:
- [ ] Test output pasted (actual framework output, not summary)
- [ ] WTF score calculated and shown (even if 0)
- [ ] Each fix has atomic commit listed
- [ ] Remaining unfixed issues documented with diagnosis
- [ ] No test modifications (only code fixes)

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type test_report --producer s_autonomous-pipeline \
  --summary "Tests: <passed>/<total> pass, <fixed> bugs fixed" --stage test \
  --data '{"passed":true,"failed":M,"fixed":K,"skipped":J,"tests_new":N,"tests_total":T,"regressions":0}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver
```
