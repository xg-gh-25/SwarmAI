# Autonomous Pipeline Report: Pipeline test runner: L1 runs desktop vitest and probe AC refs

**Run ID:** run_a626ee7f | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 05:38 UTC | **Duration:** 717.1 min (in progress) | **Tokens:** 457,151 (measured, $25.73, cache read 32,412,348; 不含当前一轮 / excludes the turn in flight; 含建 run 前的同一轮 / includes the pre-run part of the creating turn)
**Gate 1:** N/A | **Files:** 2 | **Tests:** 46 run

## TL;DR
**解决了什么问题**
- 未记录

**怎么做的**
- 未记录

**遗留问题**（1 条，见 §10）

## 1. Requirement
Pipeline test runner L1 (ac_driven) must accept frontend AC test refs: BUILD ac_coverage[].test entries naming desktop vitest files (desktop/src/**/*.test.ts(x), optionally ::test name) are resolved and run through vitest in regression mode and counted in layers.ac_driven, so a frontend-only run can satisfy the TEST gate (ac_driven.pass >= ac_coverage count). Today _resolve_ref only accepts .py refs, so a pure-frontend run's TEST is structurally unpassable. Unblocks run_cb0e98ad.

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 4.00 | |
| Priority | 5.00 | |
| Feasibility | 4.00 | |

**Acceptance Criteria:**
1. AC1: In --mode regression, an ac_coverage[].test ref naming a desktop vitest file (desktop/src/...test.ts(x), src/... or a bare filename unique under desktop/src, optionally ::<full test name>) resolves; the file is executed by vitest in that same call, and the entry counts as pass only when every named test passed (file-level ref: at least one passed, none failed).
2. AC2: A ref `probe:<name>` passes only when the run's evidence/probes/<name>.json is converged, has 0 findings, and its converged_target equals the target digest of the run's current changed files; a stale or failing probe counts as fail.
3. AC3: Unresolvable refs (prose words, missing files, ambiguous bare names) still fail their entry and are listed in warnings (fail-closed unchanged); the frontend result cache key includes the AC-named test files so a cached vitest result can never satisfy an AC file it did not run.
4. AC4: Existing .py ref resolution, L2/L3, tdd/verify/recheck behaviour and backend/tests/test_pipeline_test.py stay green.
5. AC5: A deliberately failing frontend AC test makes ac_driven.fail > 0 (proved by a test with a real failing vitest file through the runner's frontend path, or the runner's existing fake-npx harness).


## 3. Design & Approach
**Research Findings (THINK):**
- tdd already runs explicit desktop test files through `vitest related <test file>` and gets real per-assertion results (observed: run_cb0e98ad's 10 tdd batches ran 745 assertions), so adding the AC-named test files to the regression vitest job's file list executes them with no new job type.
- In _plan, plan['desktop'] is assigned AFTER the ac_entries loop (pipeline_test.py ~:1697), so AC files must be appended after that line or they are overwritten; _frontend_key hashes exactly plan['desktop'], so appending there also puts them into the frontend cache key (AC3) for free.
- vitest nodeids are `<desktop/... file>::<fullName>` (_parse_frontend), so _ref_matches works unchanged for file-level refs (nodeid None -> file equality) and exact ::fullName refs.
- Probe evidence cannot answer 'is it passing now': st.findings accumulates every finding ever seen and rounds store only new_findings/findings_total, and the digested file set is not recorded. Two fields are needed on the probe write side: per-round current_findings and state.converged_files (the files the converged target digest covers).
- backend/scripts/pipeline_test.py carries ~80 uncommitted lines from run_1907ca1d (changed_files, shard ordering); none touch _resolve_ref/_ac_layer/_plan's ac loop/run_probe's state update, so hunks do not overlap but commit must be patch-level.

**Alternatives Evaluated:**
- SIMPLICITY — effort: S
- QUALITY — effort: M
- SPEED — effort: S

**Chosen Approach:** pipeline_test.py, all new code self-contained against HEAD (no use of run_1907ca1d's uncommitted 4-arg changed_files): (1) new _resolve_desktop_ref(repo, ref) -> (file, nodeid|None) | None for .test/.spec .ts/.tsx/.js/.jsx under desktop/ (desktop/..., src/..., unique bare name); _resolve_ref untouched. (2) _plan regression ac loop: probe: refs skipped (no warning), desktop refs via _resolve_desktop_ref collected and appended to plan['desktop'] after it is assigned, everything else via _resolve_ref as today. (3) _cache_key extras gain each referenced probe evidence file's digest. (4) _ac_layer(ctx, plan, results, ev_root): desktop refs matched via _resolve_desktop_ref, probe refs via _probe_ref_ok(ctx, ev_root, name, plan['changed']) which requires _ID_RE.fullmatch(name), converged, latest non-cached round current_findings == 0, converged_target == _digest_files(ctx.repo, plan['changed']). (5) run_probe rounds.append gains current_findings. Tests inserted mid-file after test_probe_convergence_rules (not at EOF, where the foreign hunk lives). Staging: pre-BUILD snapshots in /tmp/run_a626ee7f/pre/; my patch = diff(pre->post) per file; `git apply --cached` onto HEAD; verify with `git patch-id --stable` that diff(staged->working) == diff(HEAD->pre) per file; prove the staged blob by `git worktree add` at HEAD + applying my patch and running the runner's tdd mode on backend/tests/test_pipeline_test.py inside that worktree; then one chained command: assert `git diff --cached --name-only` is exactly the 2 files && git commit && git show --stat. Pass-3 adjustments: desktop refs file-level only (::name rejected with a clear warning); _assemble gains ev_root (definition + its single caller in _run_test_mode); the worktree proof sets SWARM_TEST_PYTHON to the main repo's backend/.venv python; the stale-probe warning says any change to this run's changed files after the probe (including another session's edit) requires a probe re-run.

**Success Criteria:**
- new tests RED before the change, GREEN after
- run_cb0e98ad regression can reach ac_driven.pass == 5 once its refs are re-recorded and its probe re-run

**Always:**
- fail closed on any unresolved or stale ref
- patch-level staging for pipeline_test.py
- probe freshness keyed on the run's own changed-file digest, never a probe-chosen file set

**Never:**
- git add/restore/checkout the whole pipeline_test.py
- relax the validator
- edit run_1907ca1d's hunks


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Include probe:<name> refs in scope, not only vitest files | judgment | geometry ACs (alignment in real WebKit) are provab |
| evaluate | Fail-closed staleness for probes via converged_target == cur | judgment | a probe converged on an older tree must not vouch  |
| think | Record converged_files + current_findings on the probe write | judgment | without them a probe that once converged would vou |
| plan | Reuse the regression vitest job instead of a second vitest i | judgment | tdd proves `vitest related <test file>` runs that  |
| plan | Dropped converged_files; probe freshness = converged_target  | judgment | matches EVALUATE's definition, closes the narrow - |
| plan | Probe evidence digests join the whole-result cache key | judgment | Gate-1: a cached ac_driven verdict must not outliv |
| plan | Separate desktop resolver instead of widening _resolve_ref | judgment | Gate-1 pass 2: _resolve_ref has 3 callers; widenin |
| plan | Test the commit blob (HEAD + this run's diff) in a temp work | judgment | the working tree also contains another session's e |
| plan | Folded Gate-1 pass-3 findings into PLAN without a fourth Gat | judgment | pass 3 left three mechanical items (file-level des |
| build | Resumed BUILD on HEAD e09bd0ce after run_1907ca1d committed; | mechanical | conflict cause gone |
| build | Smoke = real regression of run_cb0e98ad | judgment | the run this fix exists to unblock is the most fai |
| review | Cache key uses the probe verdict rows, not the evidence file | judgment | the verdict is what the result depends on; the key |
| review | Suggestion-level refactors deferred | judgment | no behaviour defect; this run unblocks two stuck r |
| deliver | One Gate-2 batch + one correctness fix-recheck; security LOW | judgment | run budget: one initial review + at most one fix-r |

**Summary:** 1 mechanical, 0 taste, 13 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**2 found · 2 fixed · 0 open**
- [MEDIUM] backend/scripts/pipeline_test.py _plan recheck guard: a recheck whose only previ
- [LOW] backend/scripts/pipeline_test.py _probe_row: a probe whose latest executed round

### 7.6 Completion Audit
**Requirements met:** 6/6
**All green:** yes


## 8. Files Changed
- `backend/scripts/pipeline_test.py`
- `backend/tests/test_pipeline_test.py`
- `backend/skills/s_autonomous-pipeline/stages/ref/test/layers.md`

## 9. Lessons
- [pitfall] A test-result cache keyed only on the test file's own content serves stale passes (and stale failures) after the code under test changes; key it on the source the test imports, or on HEAD plus the whole uncommitted subtree it can reach, and never cache a failing result.
- [pitfall] A test written to prove a cache is invalidated is vacuous if its scenario is already uncacheable for an unrelated reason; first assert the identical second run IS served from cache, then change the input and assert it is not.
- [guideline] When a quality gate cannot recognise a legitimate evidence type (a frontend test, a probe), fix the gate in its own run instead of forcing past it; the stuck run that exposed the gap is the most faithful end-to-end smoke test for the fix.
- [principle] Any evidence that becomes gate-bearing must be re-judged on every path that rewrites the gate's input: an overlay that keeps unrevisited rows silently carries a stale pass forward, so re-evaluate the verdict wherever results are merged, not only where they are first produced.
- [correction] Waiting for a parallel session to commit by repeatedly polling its files produced no progress for hours; after about half an hour with no writes, check the owner's run state and escalate to the user instead of polling.


## 10. Known Gaps & Attention Flags
- 风险：validator kind:probe exit criterion still checks converged only (follow-up)


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 05:38 UTC*
