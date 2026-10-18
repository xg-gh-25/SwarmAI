# Autonomous Pipeline Report: Pipeline run budget: rounds counted, test reruns bounded, runner evidence trustworthy

**Run ID:** run_1907ca1d | **Project:** SwarmAI | **Profile:** goal
**Date:** 2026-10-18 04:44 UTC | **Duration:** 663.5 min (in progress) | **Tokens:** 5,426,239 (measured, $132.68, cache read 91,166,470; 不含当前一轮 / excludes the turn in flight; 含建 run 前的同一轮 / includes the pre-run part of the creating turn)
**Gate 1:** N/A | **Files:** not recorded | **Tests:** not recorded

## TL;DR
Pipeline run budget, enforced at command boundaries instead of prose: (1) dispatch — stage-start's list is the only licence; a spawn outside it is denied unless registered with a reason; per expert on

## 1. Requirement
Pipeline run budget, enforced at command boundaries instead of prose: (1) dispatch — stage-start's list is the only licence; a spawn outside it is denied unless registered with a reason; per expert one initial review + at most one fix-recheck; one owner per lens. (2) tests — one verify and one regression per stage on an unchanged tree; reruns of an identical tree are refused in favour of recheck; tests scoped to the run's own files; AC tests first; a per-test timeout does not take its shard down. (3) visibility — each stage-complete reports sub-agents, test runs and time vs budget. Merges run_0932fd0c (dispatch scope norms) and run_8a0d0bdd (runner scope) so the combined diff gets one Gate-2. DoD measured on a real run by the profiler.

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 9.00 | |
| Priority | 9.00 | |
| Feasibility | 8.00 | |

**Acceptance Criteria:**
1. AC1 (H4a): recheck keeps desktop:: and import:: failures from the previous evidence and reruns their jobs; when the previous evidence has failures and recheck plans zero targets, the result is a failure, never exit 0.
2. AC2 (H4b): recheck reads only the latest verify or regression evidence, never tdd.
3. AC3 (H4c): in regression, AC-referenced tests run in the first shard; an AC nodeid inside a file already selected whole is split out with a deselect so it does not run twice; a whole-file AC ref is not split.
4. AC4 (H4d): after a shard exits on a per-test timeout, its not-run tests are requeued once into a new shard (excluding the timed-out test, serial suites stay serial); results are deduplicated by nodeid with the later result winning; a second timeout in the same file stops the requeue.
5. AC5 (H3): a verify/regression whose cache_key equals the previous run of that mode and whose previous result was uncacheable runs as a recheck automatically; a recheck after a regression overlays its results onto latest-regression.json by nodeid and records rechecked_from.
6. AC6 (H1): stage-complete counts completed sub-agents from SubagentStop markers per run x goal cycle x expert_id; more than 2 for one key fails validation unless the artifact gives over_budget_reasons for it; spawn_prompt.py gate prompts start with an EXPERT-ID line.
7. AC7 (H5): stage-complete output carries a budget section (per expert_id counts, unattributed count, over-budget reasons, test runs per mode, auto-rechecks, stage time) and REPORT.md sums it for the run.
8. AC8 (carried): the dispatch-scope work (one owner per lens, source-only thresholds, REVIEW list, dispatch_plan, foreground spawns, profiler sub-agent section) and runner-scope work (files_touched-only change set, shard priority order) keep passing their tests.


## 3. Design & Approach
**Research Findings (THINK):**
- recheck false green is a live bug independent of the design
- TEST consumes only latest-regression.json, so recheck must overlay it
- SubagentStop markers already give completed spawns per run with expert_id
- no PreToolUse gate exists on the Agent tool; adding one is unverified and bypassable

**Alternatives Evaluated:**
- QUALITY — effort: M
- SIMPLICITY — effort: M
- DELETION — effort: S

**Chosen Approach:** Implement design r3 in order. Cycle 1: H4a+H4b (recheck false green, evidence source) in pipeline_test.py with tests in test_pipeline_test.py. Cycle 2: H4c+H4d (AC first, timeout requeue + nodeid dedupe). Cycle 3: H3 (cache_key auto-recheck + overlay onto latest-regression). Cycle 4: H1/H2/H5 (marker count at stage-complete, over_budget_reasons, EXPERT-ID in gate prompts, budget section, REPORT sum). One Gate-2 over the combined diff at DELIVER.

**Success Criteria:**
- a regression on this run's own scope finishes with AC tests actually run
- recheck never exits 0 with zero targets while prior failures exist
- the profiler on this run shows no expert_id above 2 without a reason

**Always:**
- scoped tdd per change, batch edits before running
- every new behaviour test proven RED under mutation

**Never:**
- wall-clock kill of a sub-agent or test
- cost/token caps
- touching other sessions' uncommitted files


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| goal_cycle | Merge the goal Final Quality Gate into the DELIVER Gate-2 ba | judgment | both review the same total diff; two rounds on one |
| goal_cycle | pytestmark timeout(120) for test_pipeline_test.py | judgment | end-to-end runner tests are slow (3-19 s alone, >3 |
| deliver | one Gate-2 batch + one re-check round for the experts that r | judgment | H2: a second round only for fixes of HIGH findings |

**Summary:** 0 mechanical, 0 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**20 found · 20 fixed · 0 open**
- [HIGH] pipeline_test.py _shards/_pytest_job: pytest --deselect matches by prefix, so an
- [HIGH] pipeline_test.py recheck: a failed vitest test (desktop/src/x.test.ts::name) was
- [HIGH] pipeline_test.py _auto_recheck: based on the newest evidence of any mode, could 
- [HIGH] pipeline_test.py: shard:: runner errors could not be re-targeted, every later re
- [HIGH] pipeline_test.py overlay hid the recheck's budget skips (stale passes). Fixed: n

### 7.6 Completion Audit
**Requirements met:** 8/8
**All green:** yes


## 8. Files Changed
- `backend/scripts/artifact_cli.py`
- `backend/scripts/pipeline_run_profile.py`
- `backend/tests/test_stage_boundary_commands.py`
- `backend/tests/test_pipeline_run_profile.py`
- `backend/tests/test_pipeline_spawn_foreground.py`
- `backend/skills/s_autonomous-pipeline/stages/review.md`
- `backend/skills/s_autonomous-pipeline/stages/deliver.md`
- `backend/skills/s_autonomous-pipeline/stages/evaluate.md`
- `backend/skills/s_autonomous-pipeline/stages/build.md`
- `backend/skills/s_autonomous-pipeline/stages/goal_cycle.md`
- `backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md`
- `backend/experts/code/spec-compliance.md`
- `backend/scripts/pipeline_test.py`
- `backend/tests/test_pipeline_test.py`
- `docs/design/pipeline-run-budget.md`
- `backend/core/runtime_hooks.py`
- `backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py`
- `backend/skills/s_autonomous-pipeline/data/gate-prompts/gate1-skeptic.md`
- `backend/skills/s_autonomous-pipeline/data/gate-prompts/gate2-adversarial.md`
- `backend/tests/test_runtime_hooks.py`
- `backend/tests/test_spawn_prompt_emitter.py`
- `backend/tests/test_spawn_prompt_meta_review.py`
- `backend/tests/test_pipeline_run.py`
- `backend/skills/s_autonomous-pipeline/data/gate-prompts/gate0-skeptic.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/gate1.md`
- `.gitignore`
- `backend/tests/test_skill_ref_docs_tracked.py`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/artifact.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/pre-checks.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/smoke.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/tdd.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/build/verify.md`

## 9. Lessons
- [pitfall] A broad ignore rule written for one ecosystem (the Python build/ pattern) silently kept a same-named docs folder out of git; every on-disk check passed on the author's machine while a fresh clone lost six stage docs. Guard referenced docs with git check-ignore --no-index, which answers the same locally and in CI.
- [principle] A budget that only reports after the fact constrains nothing. Enforce it at the boundary that already observes the event (completed sub-agent markers counted at stage-complete, a reason required past two rounds per expert), not at a spawn hook that is easy to spoof and also counts rejected spawns.
- [pitfall] Runner evidence is only as trustworthy as its not-run accounting: a recheck that dropped non-backend failures reported green on zero tests, and one timeout aborted every later test in its shard. Fix the evidence before capping reruns, or the cap freezes bad evidence in place.
- [guideline] Hand reviewers the evidence instead of asking them to gather it: one diff file plus a pre-filled gate prompt and a two-line spawn cut reviewer tool calls and roughly halved Gate-2 wall-clock on the same diff.
- [pitfall] Changing the test runner inside the run that is judged by that runner makes every edit re-run the runner's own slow end-to-end suite and turns evidence collection into repeated coin-flip reruns. Land the runner change first, or split one slow file's tests across workers.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 04:44 UTC*
