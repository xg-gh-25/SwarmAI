# Autonomous Pipeline Report: Review/commit attribution + small pipeline gaps (items 2+4)

**Run ID:** run_1a309783 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 17:17 UTC | **Duration:** 78.2 min (in progress) | **Tokens:** 1,702,813 (measured per response, cache read 83,510,198; $0.06 settled, excludes the turn in flight, includes the pre-run part of the creating turn)
**Gate 1:** WARN | **Files:** 12 | **Tests:** 1575 run, 0 failed, 2 pre-existing

## TL;DR
**解决了什么问题**
- 原来：Reviewer markers went to whichever run a session created last, run-commit credited sibling sessions' commits that touched the same files, D7 refused experts that ran without saying why, and several small gaps (RP verdict coverage, false stdin denies, no linter on doc-only changes, unmeasured stage tokens, carried exit_evidence passing a legacy push_ready, a failed surface marker counted as written, experts/ missing from the daemon bundle).
- 现在：stage-start links the session to its own running run; run-commit credits only commits naming the run (workspace auto-commits excepted) and records hand-made commits with --sha; registered adversarial experts count by EXPERT-ID and D7 says why one did not; REVIEW warns on RP rows with no verdict; the stdin deny says how to save an edit script; skill/expert changes select the linters; a stage without stage-start measures from the previous stage; the legacy cap judges only this call's evidence; a failed marker re-emits on the next explicit surface; experts/ ships in the daemon.

**怎么做的**
- Fix each gap in the tool that owns it, one test per AC (34 tests, 3 mutation checks)
- Gate-1 five rounds: AC5 narrowing dropped (fail-closed deny kept), commit procedure CAS + --sha
- Commit only this run's hunks from a temporary index; other sessions' edits stay in the worktree

**遗留问题**（4 条，见 §10）

## 1. Requirement
One run, items 2+4 of the post-run audit. (2) Attribution: (a) a session working on several runs credits reviewer markers to whichever run it created last, because the session->run map is only written at run-create; (b) run-commit's already-committed path credits every commit that touched the run's files since created_at, including other sessions' commits; (c) D7 refuses when an expert ran but its marker is not adversarial, without saying why (prompt line-2 wording). (4) Small gaps: validator does not check that every --patterns-for row got a verdict; the pipeline test gate denies a heredoc whose body merely contains the word pytest; regression does not run the skills linter when stage docs change; PLAN token_cost sometimes unmeasured; exit_evidence carry-forward lets a legacy push_ready re-record pass on an earlier attempt's evidence; the orchestrator records a run as emitted even when the marker write failed and write_marker re-reads run.json; profiler/analytics ignore prior_windows; normalize_exit_evidence not applied on run-update --stage-json; cli-reference lacks E_STAGE_NOT_OPEN. Verify each premise before fixing; drop any that do not hold.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 把2，4 做了， 1 其它session 已经在做了

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | bugfix | user-stated: XG: 把2，4 做了 | confirmed | ● | Given a session with two runs, when experts run during run A's stage, then markers carry run A |
| FR-2 | bugfix | user-stated: XG: 把2，4 做了 | confirmed | ● | Given each listed gap, when the run completes, then a test pins the fixed behaviour |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 7.00 | |
| Priority | 7.00 | |
| Feasibility | 8.00 | |

**Acceptance Criteria:**
1. AC1: stage-start links the run's session to the run it starts (same helper as run-create), so a session that created a newer run credits reviewer markers to the run whose stage it entered
2. AC2: run-commit's already-committed path credits only commits whose message names the run id; a sibling commit touching the same files in the window is not credited; the docstring stops claiming it can never credit a sibling
3. AC3: a SubagentStop marker whose prompt EXPERT-ID names a registered expert with spawn_as: adversarial is adversarial regardless of line-2 wording; D7's refusal lists experts that ran this run but left no adversarial marker, and why
4. AC4: REVIEW validation warns, naming the ids, when runtime_patterns.patterns omits an RP that --patterns-for selects for the run's changed files
5. AC5: the pipeline test gate no longer denies a python-stdin heredoc that only mentions the test runner in text it writes; it still denies stdin code that runs it (runner .main(), -m <runner>, a runner argv element)
6. AC6: regression adds the skills/expert linters and rule-preservation suites when the run changed files under backend/skills/ or backend/experts/
7. AC7: stage-complete on a stage with no started_at measures from the previous stage's completed_at instead of recording tokens as unavailable
8. AC8: the legacy push_ready cap judges exit criteria on the incoming record's own exit_evidence, not one carried from an earlier attempt; run-update --stage-json normalises exit_evidence lists like stage-complete
9. AC9: the orchestrator records a run's commits as emitted only when the marker was written (or the batch had no source rows), and write_marker takes the run id from the snapshot instead of re-reading run.json
10. AC10: cli-reference.md documents E_STAGE_NOT_OPEN and --outputs-surfaced on run-update


## 3. Design & Approach
**Research Findings (THINK):**
- Only run-create links session->run (artifact_cli.py 2337); stage-start is the natural second writer since every stage is entered there
- _lookup_already_committed filters by file + since + HEAD only; every run-commit message contains 'run <id>' (4714), and BUILD forbids manual commits, so a message filter is safe
- _is_adversarial_intent ignores EXPERT-ID; every registered code expert is spawn_as: adversarial, so the registry is the authority
- The python-stdin test-gate check runs _mentions_test_runner over the whole command (security_hooks 3906)
- pipeline_test regression selects L2 by importers; doc-only changes select no linter
- A stage without started_at measures nothing; the previous stage's completed_at is the true start in a linear pipeline
- run-update carries exit_evidence before _cap_legacy_push_ready judges, so a re-record is judged on the earlier attempt's evidence

**Alternatives Evaluated:**
- QUALITY — effort: M (1 session)
- SIMPLICITY — effort: S
- DELETION — effort: S

**Chosen Approach:** Local fix per gap in its owning module; new tests in a new file test_attribution_and_gaps.py so they do not collide with another session's uncommitted test edits.

**Success Criteria:**
- new tests pass

**Always:**
- commit only this run's hunks

**Never:**
- touch the other session's hunks
- git add -p on the shared index
- weaken the stdin gate's deny for code that can run the runner


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | One run for items 2 and 4; profiler prior_windows dropped | judgment | XG asked not to fragment; prior_windows only affec |
| think | Keep FIX B but filter by run id | judgment | tonight's restore of a reverted commit needed the  |
| plan | New test file for this run | taste | another session has uncommitted edits in test_runt |
| plan | Gate-1 BLOCK round 1 -> Fix Plan: AC5 fail-closed, commit vi | judgment | all three BLOCK/WARN points verified true against  |
| plan | Gate-1 BLOCK round 2 -> Fix Plan: run-commit --sha, CAS comm | judgment | verified: run-commit always git-adds before the al |
| plan | Gate-1 round 3 BLOCK on AC5 -> dropped the stdin-check narro | judgment | each round found a new bypass class of a hand-writ |
| plan | Gate-1 round 4: step-4 re-check compares against H, aborts l | judgment | comparing to HEAD after update-ref always differs  |
| build | AC5 narrowing dropped after three Gate-1 rounds found new by | judgment | a hand-written analyser of Python kept leaking fai |
| build | Existing FIX-B test commit message now carries 'Run: run_ab' | judgment | AC2 requires the run id; the test asserted the old |
| build | Mutation checks: removing the --grep run-id bound fails 3 te | judgment | the new tests have teeth |
| build | Gate-1 round 5 WARN carried to commit: assert HEAD == C befo | judgment | skeptic round 5; only applies when foreign hunks r |
| review | Review diff restricted to this run's hunks (security_hooks s | judgment | otherwise reviewers spend findings on foreign code |
| review | AC2 reworded: --sha does not git add files_touched; the revi | judgment | B is the audit projection that must land with the  |
| review | Workspace repo keeps the three old bounds for FIX-B creditin | judgment | auto-commit hook messages never name a run; requir |
| test | Two pre-existing failures logged in IMPROVEMENT.md Known Iss | judgment | fail at HEAD baseline; publish profile resolution  |
| deliver | Gate-2 dispatched on this run's hunks only; HEAD + patch com | judgment | another session has uncommitted edits in five of t |
| deliver | Meta-review deployment risk verified and fixed in the build  | judgment | _internal/ of the installed daemon has context/ski |
| deliver | Concurrency MED partly accepted: spawn-time run binding not  | judgment | pipeline spawns are foreground and the other sessi |

**Summary:** 0 mechanical, 1 taste, 17 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**12 found · 12 fixed · 0 open**
- [MEDIUM] backend/scripts/artifact_cli.py cmd_stage_start: linked the session to any run i
- [LOW] backend/core/security_hooks.py _test_gate_hint: text revealed that script files 
- [MEDIUM] artifact_cli.py cmd_stage_start: map written before stage-start's refusal points
- [LOW] streaming_orchestrator.py: failed marker discarded the run from the shared seen-
- [LOW] runtime_hooks.py _registered_adversarial_experts: cache key glob vs scan rglob, 

### 7.6 Completion Audit
**Requirements met:** 2/2
**All green:** yes


## 8. Files Changed
- `backend/tests/test_attribution_and_gaps.py`
- `backend/scripts/artifact_cli.py`
- `backend/core/runtime_hooks.py`
- `backend/core/security_hooks.py`
- `backend/scripts/pipeline_test.py`
- `backend/scripts/pipeline_validator.py`
- `backend/core/streaming_orchestrator.py`
- `backend/core/ui_actions.py`
- `backend/core/surface_marker.py`
- `backend/skills/s_autonomous-pipeline/stages/ref/instructions/cli-reference.md`
- `backend/tests/test_artifact_cli_run_commit.py`
- `desktop/scripts/build-backend.sh`

## 9. Lessons
- [pitfall] Narrowing a fail-closed gate with a hand-written static analyser of a language leaks a new bypass class every review round (aliases, getattr, module attributes that hand out os, sys.modules). When the false deny costs one step, keep the gate closed and fix its message instead.
- [pitfall] Requiring a new field on records you credit (here: the run id in a commit message) breaks every path whose records come from an automated writer that never sets it (a workspace auto-commit sweep). Enumerate all writers of the records before making the field required.
- [pitfall] A daemon-side reader of a source directory works in dev and silently reads an empty directory in the packaged build when the directory is missing from the bundle's data list; log a warning on an empty read and add the directory to the packaging list in the same change.
- [principle] To force one path to retry, do not remove the key from a guard set that other paths also read: that reopens all of them. Keep a separate retry state that only the retrying path reads.
- [process] With parallel sessions editing the same files, review and commit only this run's hunks: classify hunks by the lines this run wrote, build the commit in a temporary index (commit-tree, then update-ref with the old HEAD as compare-and-swap), and re-apply any foreign staged diff onto the new HEAD so the shared index never stages the reverse of the commit.


## 10. Known Gaps & Attention Flags
- 风险：MED deployment: experts/ not bundled in the daemon (verified) -> fixed: build-backend.sh datas + WARNING on an empty registry; needs a daemon rebuild to take effect
- 风险：LOW scaling: pipeline_agent_audit markers never pruned; the plain-marker scan runs only on a D7 refusal -> accepted
- 风险：LOW format: run-id substring match credits a commit that only mentions the run -> accepted (fixed-width ids; the commit must also touch the run's files after it started)
- 风险：LOW first-run: hand commits without the run id -> refusal text says to add 'Run: <id>' and use --sha


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 17:17 UTC*
