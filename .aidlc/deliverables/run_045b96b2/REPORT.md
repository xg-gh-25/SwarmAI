# Autonomous Pipeline Report: Pipeline profiler sees new-style runs; stage tokens measured per response

**Run ID:** run_045b96b2 | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 09:56 UTC | **Duration:** 75.8 min (in progress) | **Tokens:** 1,835,067 (measured per response, cache read 61,550,067)
**Gate 1:** WARN | **Files:** 8 | **Tests:** 1514 run, 0 failed, 2 pre-existing

## TL;DR
**解决了什么问题**
- 原来：The run profiler found no transcript for runs driven by stage-start/stage-complete or that held the run id in a shell variable, so 3 of the 6 newest runs profiled as all zeros, and runner test executions counted as bookkeeping. Stage token cost came from per-turn rows, so a stage ending mid-turn read 0: 8 of 156 stage records in 24 hours were measured.
- 现在：All 6 previously blind runs resolve a transcript and report tool calls (63-143) and runner test executions (5-34). Stage token cost is measured per model response from the CLI transcript, falling back to per-turn rows only when no transcript is usable; this run's build, review and test stages are recorded as measured.

**怎么做的**
- New core/transcript_usage.py: one locator and per-response usage reader shared by the profiler, stage tokens and the delivery summary
- stage-complete (and stage-start on first entry) records CLAUDE_CODE_SESSION_ID in run.json transcripts[], validated and capped at 32
- Profiler recognises the stage verbs, shell-variable run ids and pipeline_test.py runs, and counts executions from run_index.json plus unindexed evidence files

**遗留问题**（7 条，见 §10）

## 1. Requirement
Make the run speed profiler and token telemetry measure post-refactor pipeline runs correctly, so the §5 acceptance metrics in docs/design/pipeline-speed-systematic-fix.md can be checked. Measured defects: (a) backend/scripts/pipeline_run_profile.py _WRITE_VERB_RE only matches publish|run-update|advance|run-commit|run-report|run-checkpoint, not stage-start/stage-complete, so 3 of 6 post-refactor runs (run_5d22bb1e, run_1f81be36, run_3c35f9cb) resolve to zero transcripts and all-zero metrics; (b) tests run through backend/scripts/pipeline_test.py are classified as pipeline-bookkeeping, so tests.calls=0 for new runs (run_39068a4a, run_930b3a62) while evidence/tests shows 8-30 executions; (c) only 8 of 156 stage records created in the last 24h have token_cost_source=measured — find why measured token usage does not reach stage records. Acceptance: profiler finds transcripts for all 6 post-refactor runs with nonzero tool calls; test counts/minutes for new runs match evidence/tests; stages of a fresh run get measured token_cost, or a verified root cause if that is structurally impossible. Then profile the post-refactor runs and report against §5. Out of scope: parallel-session commit isolation, test cache hit rate.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 先把 3， 4，6 做了， 1，2 往后放

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: 先把 3 做了 (item 3a of the 2026-10-18 efficiency review) | confirmed | ● | Given run_3c35f9cb, whose transcript uses R=run_3c35f9cb; ... stage-complete --run-id $R, when pipeline_run_profile.py --run-id run_3c35f9cb runs, then it lists the owning transcript and reports tool_calls > 0 |
| FR-2 | user-story | user-stated: item 3b of the review | confirmed | ● | Given run_39068a4a with 30 evidence/tests execution files, when the profiler runs, then tests.calls equals the evidence execution count and exec_min is the sum of duration_s |
| FR-3 | user-story | user-stated: item 3c of the review | confirmed | ● | Given a stage that starts and ends inside one turn, when stage-complete runs, then token_cost_source is measured and token_cost > 0 |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 7.00 | |
| Priority | 8.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: pipeline_run_profile.py resolves at least one owning transcript and reports tool_calls > 0 for each of run_5d22bb1e, run_1f81be36, run_3c35f9cb, run_39068a4a, run_930b3a62, run_aad8145a (runs that use stage-start/stage-complete and R=<id>; --run-id $R)
2. AC2: for a post-refactor run, the profiler's test calls and test minutes come from that run's evidence/tests/*.json (one per execution, duration_s), so run_39068a4a reports the same execution count as its evidence files instead of 0
3. AC3: a stage completed through stage-complete gets token_cost_source=measured from the per-response usage in the SDK transcript (CLAUDE_CODE_SESSION_ID) inside the stage window, even when no turn ends inside the window
4. AC4: the REPORT header Tokens value uses the same per-response measurement, and no longer prints a bare 0 labelled estimate when the run's turn is still in flight
5. AC5: with no transcript available (no env id, file missing), stage token_cost stays unavailable and nothing raises


## 3. Design & Approach
**Research Findings (THINK):**
- Ownership: pipeline_run_profile.command_writes_run (:213-224) needs a write verb from _WRITE_VERB_RE (:87-89, no stage-start/stage-complete/run-summary) AND a literal --run-id <id>; post-refactor runs use stage-start/stage-complete and R=<id>; --run-id $R, so run_5d22bb1e/run_1f81be36/run_3c35f9cb resolve to no transcript
- Tests: profile_run counts tests only where _TEST_RE (:56) matches pytest/vitest/tsc; pipeline_test.py never matches, so tests.calls=0; each runner execution already writes evidence/tests/<mode>-<ts>.json with duration_s (latest-*.json and tdd_index.json are pointers/indexes, overlay files are rewrites, not runs)
- Tokens: token_usage rows are written once per turn end with the CLI's total_cost_usd (streaming_orchestrator.py:240); per-stage (_measure_stage_tokens artifact_cli.py:8093) and run-level (run_token_usage, used by the REPORT header at artifact_cli.py:6043-6055) both depend on a row landing inside the window, so a one-turn run gets 'unavailable' stages and 'Tokens: 0 (估算)' in 2 of 3 recent REPORTs
- The SDK transcript ~/.claude/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl carries usage on every assistant record (one usage per message id, identical across its records: 137 multi-record responses, 0 differing) with a UTC timestamp; sub-agent responses are in <stem>/subagents/agent-*.jsonl; a prefiltered scan of a 4.8 MB transcript took 0.02 s
- CLAUDE_CODE_SESSION_ID is in the environment of every Bash the CLI runs (observed in this session); the transcript has no cost field, so a transcript measurement carries tokens only, never a guessed price

**Alternatives Evaluated:**
- QUALITY — effort: M (1 session)
- SIMPLICITY — effort: S (half session)
- DELETION — effort: M

**Chosen Approach:** Add core/transcript_usage.py as the one reader (locating the main transcript via leak_transcript.find_transcript, and the sub-agent locator moved there from the profiler) of per-response usage from CLI transcripts (main file plus its sub-agent files, deduped by message id, filtered to a window). artifact_cli records CLAUDE_CODE_SESSION_ID into run.json transcripts[] at run-create, stage-start and stage-complete, measures stage token_cost from the transcript first and token_usage second, and the REPORT header and chat summary share one merged run-usage function (transcript tokens, settled dollar cost from token_usage rows when present). pipeline_run_profile gains the new write verbs, resolves R=<id> shell assignments, unions recorded transcript ids with the scan, and counts runner test executions from evidence/tests/run_index.json.

**Success Criteria:**
- the 6 named runs profile with tool_calls > 0
- a fresh stage-complete in this run records token_cost_source=measured
- targeted test files pass via pipeline_test.py

**Always:**
- transcript ids validated before they touch a path
- telemetry failures never fail a stage or a completion

**Never:**
- hard-coded token prices
- touching parallel-session commit isolation or test caching


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile bugfix: three defects with reproduced root causes | mechanical | profile rule: clear bug, known root cause |
| evaluate | Reuse the profiler's existing per-response usage reader for  | taste | gate0-skeptic found pipeline_run_profile already c |
| think | Transcript measurement reports tokens only; cost_usd stays N | taste | prices drift and the transcript has no cost; a gue |
| think | Record transcript ids in run.json at stage-start/stage-compl | taste | identity captured where it is produced instead of  |
| think | Sub-agent responses inside the window count toward stage and | taste | the CLI's own turn total includes them; leaving th |
| plan | Put the usage reader in core/ (new module) rather than in th | taste | core/run_delivery_summary must call it and core sh |
| plan | Required experts: security (env value becomes a path) and co | mechanical | plan card step 8 domain mapping |
| plan | X5 manual: profile the 6 named runs live and report against  | mechanical | AC1 is about real transcripts, which no unit test  |
| plan | Gate 1 BLOCK resolved by Fix Plan: count tests from run_inde | mechanical | each finding verified against pipeline_test.py:60/ |
| plan | The CLI session id is stored only in run.json transcripts[], | mechanical | runtime_hooks.py:776 documents the two ids as diff |
| build | run-create does not record the transcript id; the first stag | taste | the creating turn and EVALUATE run in one CLI sess |
| build | Runner commands are matched as a python invocation of pipeli | mechanical | real-data replay: grep/sed of the file inflated ru |
| build | Overlay evidence from before the run index is recognised by  | mechanical | run_39068a4a: verify-...053131 carries rechecked_f |
| build | The stage test fixture clears CLAUDE_CODE_SESSION_ID | mechanical | without it the tests recorded and measured the liv |
| review | Accept unbounded line length in transcript reads | taste | the files are the CLI's own local transcripts and  |
| review | Refute the ownership-bypass finding instead of re-checking o | taste | only this run's stage commands record ids, so the  |
| test | Two regression failures logged as pre-existing, not debugged | mechanical | runner classified both pre-existing against the HE |
| deliver | Measure stage tokens from the CLI transcript's per-response  | judgment | token_usage gets one row per turn end, and a pipel |
| deliver | Deleted stage-start's re-entry write instead of hardening it | judgment | stage-complete already saves the id in its own wri |
| deliver | Left the full transcript-directory scan in place | judgment | it predates this change and older runs have no rec |

**Summary:** 9 mechanical, 8 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**4 found · 4 fixed · 0 open**
- [MEDIUM] backend/core/run_delivery_summary.py _transcript_run_usage(): the window ended a
- [MEDIUM] backend/scripts/pipeline_run_profile.py _is_overlay(): checked whether rechecked
- [LOW] backend/scripts/artifact_cli.py cmd_stage_start: re-entering a stage did an unlo
- [LOW] stages/ref/instructions/checkpoint-and-budget.md doc sync: said stage-start alwa

### 7.6 Completion Audit
**Requirements met:** 5/5
**All green:** yes


## 8. Files Changed
- `backend/core/transcript_usage.py`
- `backend/scripts/pipeline_run_profile.py`
- `backend/scripts/artifact_cli.py`
- `backend/core/run_delivery_summary.py`
- `backend/tests/test_transcript_usage.py`
- `backend/tests/test_pipeline_run_profile.py`
- `backend/tests/test_stage_boundary_commands.py`
- `backend/tests/test_run_delivery_summary.py`
- `backend/skills/s_autonomous-pipeline/stages/ref/instructions/checkpoint-and-budget.md`

## 9. Lessons
- [pitfall] A token meter built on per-turn rows reads 0 for any window that closes inside a turn, and a pipeline stage almost always does. Measure from the per-response source (the CLI transcript) and keep the per-turn rows only as a fallback.
- [pitfall] When a measurement window belongs to something that has not finished, end it at that thing's last-updated time, never at now(). An open end pulls in later unrelated work from the same session and still looks authoritative.
- [pitfall] A 'this record is an overlay' check keyed on whether a field exists misfires when the writer always emits the field, set to null for normal records. Test the value, and add a fixture with the field set to null.
- [decision] When a second unlocked writer exists only to save data that an existing write already carries, delete it instead of adding locking or a re-read. One fewer writer removes the race completely.


## 10. Known Gaps & Attention Flags
- Not deployed: the daemon runs a packaged binary; a rebuild is needed before the completion-time profile uses this code (artifact_cli/profiler run from source via the CLI already use it)
- 2 tests in test_artifact_cli_run_ownership.py already failed before this change (recorded in TEST); not addressed here
- Meta-review LOW: skip the full ~/.claude/projects scan when recorded transcript ids resolve; left as a follow-up
- No backfill: runs finished before this change keep turn-row token data
- 风险：LOW scaling: find_run_transcripts still scans all of ~/.claude/projects on each completion; recorded ids are added on top instead of replacing the scan (already the case before this change)
- 风险：LOW format: transcript tokens and the settled dollar figure cover different windows; the label says so
- 风险：LOW deployment: without CLAUDE_CODE_SESSION_ID (headless jobs) stage tokens fall back to per-turn rows; token_cost_source shows which source was used


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 09:56 UTC*
