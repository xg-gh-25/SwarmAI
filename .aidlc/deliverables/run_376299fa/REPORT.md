# Autonomous Pipeline Report: stage-complete refuses a closed stage that was not re-entered; re-records cover the stage's own windows

**Run ID:** run_376299fa | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 12:24 UTC | **Duration:** 27.9 min (in progress) | **Tokens:** 625,354 (measured per response, cache read 28,798,092)
**Gate 1:** WARN | **Files:** 4 | **Tests:** 1461 run, 0 failed, 6 pre-existing

## TL;DR
**解决了什么问题**
- 原来：stage-complete on a stage that was already completed, without stage-start first, measured token cost from the stage's original start across every later stage and overwrote the original completed_at (a re-recorded BUILD read 816K measured vs 363K). The expert/test budget also counted from the first start, so later stages were charged to a re-record.
- 现在：stage-complete refuses a closed stage that was not re-entered (E_STAGE_NOT_OPEN, names the stage-start command, writes nothing). A re-record through stage-start keeps one record whose cost is the earlier windows plus the new one, and budget counts only the stage's own windows. Stages never entered through stage-start behave as before.

**怎么做的**
- Open-stage check at the top of cmd_stage_complete on the run as loaded
- stage-start records the closed window in prior_windows before re-opening
- _add_prior_window_cost and window-scoped _stage_budget
- plan.md replan line names stage-start

**遗留问题**（15 条，见 §10）

## 1. Requirement
bugfix: stage-complete on a stage that is already completed, with no stage-start re-entry first, measures token_cost over the window from the stage's ORIGINAL stage_timing.started_at to now (artifact_cli.py cmd_stage_complete -> _measure_stage_tokens(timing.started_at, completed_at)), so the measured value silently spans every later stage (observed run_a87bfe3f: BUILD re-recorded read 816K vs 363K original, window included REVIEW and TEST). Fix so a re-recorded stage never measures over later stages: either refuse stage-complete on a completed stage unless stage-start re-entered it, or start the window at the previous completed_at. Measured wins over record-provided token_cost (C5, _apply_token_cost) stays.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起 pipeline：修 stage-complete 重入窗口

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | bugfix | user-stated: XG: 起 pipeline：修 stage-complete 重入窗口 | confirmed | ● | Given a run whose build stage_timing has started_at and completed_at, when stage-complete --stage build runs without a stage-start first, then exit 1, error names stage-start --stage build, run.json byte-identical |
| FR-2 | bugfix | derived: cmd_stage_start pops completed_at and sets a new started_at on re-entry (artifact_cli.py:9438-9444) | confirmed | ● | Given a completed build stage, when stage-start --stage build then stage-complete --stage build, then the measured window is [new started_at, now], first_started_at keeps the original |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 6.00 | |
| Priority | 7.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: stage-complete (and --check) on a stage whose stage_timing has BOTH started_at and completed_at (completed, not re-opened by stage-start) exits non-zero with an error naming the stage and the exact stage-start command, and writes nothing (run.json and manifest unchanged)
2. AC2: stage-start then stage-complete on an already-completed stage works: the measured window starts at the new started_at, and first_started_at keeps the original start
3. AC3: unchanged behaviour for the normal path (stage-start, then stage-complete once) and for a stage with no started_at (never entered through stage-start, e.g. the existing MED-3 replan tests); C5 measured-wins is unchanged
4. AC4: the MED-3 replan guidance (stages/plan.md) says to run stage-start --stage plan before re-completing PLAN, and the refusal's error text says the same


## 3. Design & Approach
**Research Findings (THINK):**
- cmd_stage_complete (artifact_cli.py 8688-8730) sets completed_at=now and measures tokens over [stage_timing.started_at, completed_at] (8487-8490) with no open-stage check
- cmd_stage_start (9438-9444) is the only re-open: new started_at, completed_at popped, first_started_at keeps the original
- The MED-3 replan tests (test_stage_boundary_commands.py 960-1000) re-complete PLAN with NO started_at at all, so they measure nothing and are not the bug shape; plan.md:28 documents replan without naming stage-start
- --check never writes, so a stage stays open across --check then the real stage-complete

**Alternatives Evaluated:**
- SIMPLICITY — effort: S (1 session)
- FLEXIBILITY — effort: S
- DELETION — effort: S

**Chosen Approach:** Add an open-stage check at the top of cmd_stage_complete, before any derivation: if stage_timing[stage] has started_at and completed_at, fail via _stage_boundary_fail with E_STAGE_NOT_OPEN and the stage-start command. Update plan.md:28. Tests in a new file so they do not collide with another session's uncommitted hunks in test_stage_boundary_commands.py.

**Success Criteria:**
- new tests pass
- test_stage_boundary_commands.py passes

**Always:**
- write nothing on refusal

**Never:**
- change _apply_token_cost


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Refuse stage-complete on a completed, un-reopened stage rath | judgment | the gap after the old completion is exactly where  |
| think | Refuse only when both started_at and completed_at exist | judgment | a stage never entered through stage-start measures |
| plan | New test file instead of extending test_stage_boundary_comma | taste | another session has uncommitted hunks in that file |
| build | Guard reads run_state as loaded, before completed_at is set  | mechanical | Gate-1: on cand_run every stage would look closed |
| test | 6 failures classified pre-existing, not fixed here | mechanical | runner HEAD baseline; all in IMPROVEMENT.md Known  |
| reflect | No ADR: the guard and window bookkeeping are reversible | judgment | ADR gate needs hard-to-reverse + surprising + trad |
| deliver | Combine the previous record's cost with the new window inste | taste | each window was measured correctly when it closed; |

**Summary:** 2 mechanical, 2 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**13 found · 6 fixed · 7 open**
- [MED] backend/scripts/artifact_cli.py a re-record via stage-start measured only the ne
- [MED] backend/scripts/artifact_cli.py _stage_budget counted spawns/tests from first_st
- [MED] backend/scripts/artifact_cli.py the review diff mixed in another session's uncom
- [MED] recheck: no test for 2+ re-entries. Added test_two_rerecords_sum_every_window_on
- [LOW] recheck: token_usage merge truncated floats and summed bools. Fixed with _sum_us

### 7.6 Completion Audit
**Requirements met:** 4/4
**All green:** yes


## 8. Files Changed
- `backend/scripts/artifact_cli.py`
- `backend/scripts/pipeline_validator.py`
- `backend/skills/s_autonomous-pipeline/stages/plan.md`
- `backend/tests/test_stage_complete_reentry.py`

## 9. Lessons
- [pitfall] A measurement window that only one entry point can open must be enforced at the exit too: if the closing command accepts an already-closed window, it silently re-measures from the original start across everything that ran since, and the number still reads as measured.
- [pitfall] Forcing every re-record through a new window fixes over-counting only by trading it for under-counting unless the earlier windows' cost is carried into the one record per stage; check both directions when you narrow a measurement window.
- [decision] When two values answer the same question ('this stage's own windows'), record the windows once (prior_windows) and derive both budget and cost from them, rather than letting each consumer keep its own notion of the window.
- enforce a window at the exit, not only the entry
- narrowing a window trades over-count for under-count unless prior windows carry
- No ADR: the guard and window bookkeeping are reversible


## 10. Known Gaps & Attention Flags
- 未修复的 LOW 发现：backend/scripts/artifact_cli.py a stage re-opened by an older binary (first_started_at, no prior_windows) loses that first window in budget and cost; transitional, accepted.
- 未修复的 LOW 发现：backend/scripts/artifact_cli.py _fence single-pass tag strip (other session's gate2-recheck code); reported to that work, not changed here.
- 未修复的 LOW 发现：backend/scripts/artifact_cli.py _gate2_fix_diff non-deterministic sort (other session's code); not changed here.
- 未修复的 LOW 发现：backend/scripts/artifact_cli.py round-1 snapshot keyed on started_at interacts with the guard (other session's code; it is being re-keyed in the working tree).
- 未修复的 LOW 发现：E_STAGE_NOT_OPEN documented only in plan.md; the error text names the fix; follow-up note in cli-reference.md.
- 未修复的 LOW 发现：pipeline_run_profile._stage_of and pipeline_analytics._duration_of ignore prior_windows; follow-up.
- 未修复的 LOW 发现：token cost uses the previous record while budget recomputes windows; acceptable, unify later.
- Committed from the index only: another session has uncommitted gate2-recheck hunks in artifact_cli.py; run-commit (whole-file) must wait until those land
- run_a87bfe3f's BUILD stage_timing still spans REVIEW/TEST from the incident; left as history
- Follow-ups: profiler/analytics read prior_windows; cli-reference note for E_STAGE_NOT_OPEN; _fence/_gate2_fix_diff findings belong to the gate2-recheck work
- 6 pre-existing test failures (run_ownership x2, review_completeness x3, no_unguarded_project_mkdir x1), all in Known Issues
- 风险：LOW cross-fix: estimate/unmeasured mixing -> fixed
- 风险：LOW first-run: in-flight re-opened stages lack prior_windows -> accepted
- 风险：LOW deployment: E_STAGE_NOT_OPEN only in plan.md -> error text names the fix
- 风险：LOW scaling: profiler/analytics ignore prior_windows -> follow-up


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 12:24 UTC*
