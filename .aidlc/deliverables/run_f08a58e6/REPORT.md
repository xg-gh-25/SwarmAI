# # Fix: a pipeline run whose PLAN is refused three times skips REVIEW and TEST, … · Audit through DELIVER

Delivered: # Fix: a pipeline run whose PLAN is refused three times skips REVIEW and TEST, then cannot land \*\*Evidence (2026-11-16, on this Hive).\*\* Pipeline run \`run\_cf620062\` (a rerun of dispatch so\_85198d91815. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 59m 46s wall · 59m 46s active · 120.3% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 4/5 findings closed (8 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 1 fail-to-pass · CF-91fba314: no mutation check (a text-only change: the e2e test reads the PLAN\_REFUSED constant, so no edit of the text can turn it red) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 541k comparable · 421k input · 120k output · 33.3M cache reads, not counted · 33.8M billed · host-session responses in the run window · 55.4% budget |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+4 found on demand) · searched the DDD 3×

## Decisions

- Q1 · Should a run whose PLAN keeps failing stop at PLAN or go on with review? → Go on: PLAN degraded, then BUILD, REVIEW, TEST and the normal landing. The design says a retry count never asks a person, and DELIVER and landing stay lenient; REVIEW and TEST still decide (REQUIREMENTS, agent)
- D1 · Stop at PLAN or go on with review? → Go on: PLAN degraded, BUILD next, then REVIEW, TEST and the normal landing guards. Why: DELIVER and landing stay lenient; REVIEW and TEST decide the change, and the audit says the PLAN was refused (DESIGN\_SPEC, agent)
- D2 · Should a failure word in the outcome count for DS-6? → No: the condition (left of the first -&gt;) must name it, as the PLAN card says; the message now says so. Why: Keeps the check's meaning (a separate failure branch) and gives the first refused flow an exact fix (DESIGN\_SPEC, agent)
- D3 · Fix golden\_case\_validator's similar word-form gap here? → No: its own small change after this run lands. Why: Different purpose and module; not widening this diff (DESIGN\_SPEC, agent)
- D4 · How to keep old event logs folding? → The rule is an optional stage\_attempt field written only for PLAN; absent, the fold behaves as before. Why: The fold replays every run's whole log; an unconditional change would break completed runs such as run\_cf620062 (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Stop the run at PLAN (pause for a person) after the third refusal. Not taken: A retry count never asks a person in this design, and the agent cannot fix a matcher bug by asking; the run would still need the person to commit outside the pipeline.
- A3 · Change the fold for every log so a third PLAN failure always continues. Not taken: The lifecycle is an event-sourced fold: old completed runs whose PLAN was exhausted (their next events are DELIVER attempts) would no longer fold.
- A4 · Make DS-6 an audit-only check so it never refuses PLAN. Not taken: Fixes only this check; any other PLAN check refused three times would still skip REVIEW and TEST.

Requirements analysis:

- missing\_edge (U3): The lifecycle is an event-sourced fold: changing what PLAN exhaustion does for every log would make old completed runs such as run\_cf620062 fail to fold. → rewritten
- missing\_edge (R2.2): A degraded PLAN stores no design\_spec.json; BUILD, REVIEW and TEST checks must work without it (TS-4 already treats a missing design as no test refs). → rewritten
- vague (R1.3): 'Says exactly what to change' is made checkable as: the message names the separator, the edge form, the accepted words and gives an example. → rewritten
- missing\_edge (R1.2): Fixture: the three flows run\_cf620062 submitted, from its session transcript: (1) 'git show a1d2b5689:backend/tests/test\_cjk\_term\_map.py -&gt; file restored; comment reworded; test\_recall\_filter run; grep finds term-map wording -&gt; error, fix before commit' (2) 'restore test file from a1d2b5689 -&gt; reword comment -&gt; run test\_recall\_filter; failing test or a grep match for term-map wording -&gt; error, fixed before commit; clean -&gt; commit' (3) 'restore test file from a1d2b5689 -&gt; reword comment -&gt; run test\_recall\_filter; test fails or grep finds term-map wording -&gt; error reported, fixed before commit; clean -&gt; commit'. Flows 2 and 3 name the failure in the condition and must pass; flow 1 names it only in the outcome and must get the message of R1.3. → rewritten
- missing\_edge (R1.1): scripts/golden\_case\_validator.py \_RUBRIC\_FAIL has the same word-form gap (fail(s\|ed)?) for a different purpose (a rubric names its fail case); it is fixed as its own small change after this run lands, not shared with the pipeline schema. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 17/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0398c4c477ff: 3 pass, 0 fail, 0 not run
- test · swarmai @ 36292e58ca3a: 9 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- GIT\_CONFIG\_GLOBAL=&lt;user.name/email file&gt; .venv/bin/python -m pytest tests/test\_run\_protocol\_&lt;each of 20 files&gt;.py -q --timeout=270 --maxfail=1000 (one file per run) → pass
- GIT\_CONFIG\_GLOBAL=&lt;user.name/email file&gt; .venv/bin/python -m pytest tests/test\_pipeline\_v1\_&lt;each of 64 files&gt;.py -q --timeout=270 --maxfail=1000 (one file per run; every pipeline v1 file but the five  → pass
- GIT\_CONFIG\_GLOBAL=&lt;user.name/email file&gt; .venv/bin/python -m pytest tests/test\_pipeline\_v1\_base\_moved.py tests/test\_pipeline\_v1\_deliver\_reopen.py tests/test\_pipeline\_v1\_e2e\_sync.py tests/test\_pipeline → pre\_existing (12 tests fail, the same 12 by name on the run's base tree c8c2898 on this Hive: 11 where main moves during landing (LG-1 'the test report is for tree X, the code is now Y' / next loops), and test\_an\_a)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pass
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · GIT\_CONFIG\_GLOBAL=&lt;user.name/email file&gt; .venv/bin/python -m pytest tests/test\_pipeline\_v1\_base\_moved.py tests/test\_pipeline\_v1\_deliver\_reopen.py tests/test\_pipeline\_v1\_e2e\_sync.py tests/test\_pipeline: 12 tests fail, the same 12 by name on the run's base tree c8c2898 on this Hive: 11 where main moves during landing (LG-1 'the test report is for tree X, the code is now Y' / next loops), and test\_an\_adopted\_prewarm\_session\_creates\_a\_run\_it\_owns (expects SWARM\_APP\_SESSION\_ID in the create output). No

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run on the branch through task\_checks after landing
- swarmai · npm run lint:contract: no desktop file changed; run on the branch through task\_checks after landing
- swarmai · npm run build: no desktop file changed; run on the branch through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run on the branch through task\_checks after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_pipeline\_v1\_plan\_refused.py
