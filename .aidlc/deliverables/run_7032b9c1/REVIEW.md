# Review — gates and experts

**Run** `run_7032b9c1` · **profile** direct · **project** SwarmAI

> 起 direct run 提交这个修复

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 1 finding(s), 0 open | Red Team: no |
| Meta-review | RISKS | 3 risk(s) |
| Push-ready | yes |  |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 1 | 0 | 1 | 0 | 0 |
| meta-review | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: correctness expert (EXPERT-ID: correctness) round 1 + round-2 re-check (R1 RESOLVED, NO FINDINGS); meta-review twice; under the Gate-2 dispatch threshold so no other specialists

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | LOW | backend/core/run_docs.py doc_status: the 'not generated' reason ignored whether the stage artifact loads. Fixed: finished stage + readable artifact → 'completed before run documents existed, or the write failed'; unreadable → 'the <stage> artifact could not be read' (same split as completion_check). | fixed · inv I1 · class fix: doc_status now uses the same two facts completion_check uses: finished status (FINISHED_STATUSES) and artifact readability |

### Fixed by class

- **correctness · LOW** — backend/core/run_docs.py doc_status: the 'not generated' reason ignored whether the stage artifact loads. Fixed: finished stage + readable artifact → 'completed before run documents existed, or the write failed'; unreadable → 'the <stage> artifact could not be read' (same split as completion_check).
  - invariant: I1
  - root cause: doc_status and completion_check judged the same case with different inputs
  - class fix: doc_status now uses the same two facts completion_check uses: finished status (FINISHED_STATUSES) and artifact readability
  - break test: backend/tests/test_run_docs.py::test_doc_status_tells_not_generated_from_not_completed

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| LOW | other readers still hard-code their finished tuple (pipeline_validator, run_worktree, ddd_cultivation, clean_foreign_run_stubs) | OPEN · named in the FINISHED_STATUSES comment; follow-up to migrate them |
| LOW | an old 'done' run re-completed with a missing document now blocks instead of warning | resolved · intended: write_all regenerates first, so a loadable artifact without a file is a real write failure |
| LOW | doc_status with run_dir None and a finished stage says the artifact could not be read instead of no run directory | OPEN · callers always pass the run dir; cosmetic, left for the FINISHED_STATUSES follow-up |

## Open items

- Follow-up: move pipeline_validator / run_worktree / ddd_cultivation / clean_foreign_run_stubs onto pipeline_profiles.FINISHED_STATUSES
- [runner] Not tested in this run (verify): 561 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, bac…
