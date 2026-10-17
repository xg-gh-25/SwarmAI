# Autonomous Pipeline Report: Backend-emitted pipeline stage rows in the chat stream

**Run ID:** run_d9c6d3c0 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-17 14:53 UTC | **Duration:** 63.6 min (in progress) | **Tokens:** 425,000 (估算 / estimate)
**Gate 1:** N/A | **Files:** 7 | **Tests:** 72 run

## TL;DR
Pipeline stage progress is emitted by the backend, not the model: after any successful artifact_cli.py call carrying --run-id, the streaming orchestrator reads run.json (authority), diffs it against t

## 1. Requirement
Pipeline stage progress is emitted by the backend, not the model: after any successful artifact_cli.py call carrying --run-id, the streaming orchestrator reads run.json (authority), diffs it against the last-seen stage snapshot, and emits a persisted pipeline_stage content block per newly completed stage (circled number, status, gate verdict, one-line metric). The frontend turnView renders it as a never-folded landmark and shows a live 'current stage' header; LANDMARK_RE is widened for model-written gate verdicts (Gate N, case-insensitive) and circled-number lines.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> Pipeline stage progress is emitted by the backend, not the model: after any successful artifact_cli.py call carrying --run-id, the streaming orchestrator reads run.json (authority), diffs it against the last-seen stage snapshot, and emits a persisted pipeline_stage content block per newly completed stage (circled number, status, gate verdict, one-line metric). The frontend turnView renders it as a never-folded landmark and shows a live 'current stage' header; LANDMARK_RE is widened for model-written gate verdicts (Gate N, case-insensitive) and circled-number lines.

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: '起 pipeline：stage 进度改由后端发出' — progress must not depend on model prose | confirmed | ● | Given a pipeline turn with zero model-written landmarks, when a run-update/publish/advance call completes a stage in run.json, then a visible stage row with circled number + stage name appears in that turn |
| FR-2 | user-story | assumption: a 110-minute run must still show its stages after reload; the compact notice precedent is session-only and was judged insufficient for audit history | assumed | ● | Given a session with emitted stage rows, when the user reloads or switches tab, then the same rows render at the same positions, none duplicated |
| FR-3 | user-story | user-stated: XG: '我很难知道pipeline 的进展' | confirmed | ● | Given a streaming pipeline turn in BUILD, when the user looks at the live row, then it reads ⑤ BUILD (in progress), not only the current tool |
| FR-4 | task | ddd-derived: measured misses in session 8a31b1cb/bac8684b; turnView.ts:126 comment warns against loose GATE matches | confirmed | ● | Given text 'Gate 1: **PASS**' between tool calls, when the turn renders, then it is a landmark unit; 'aggregate 1' is not |
| NFR-1 | enabler-story | ddd-derived: run_beff6754 mechanism-over-prose lesson + parallel session introducing stage-start/stage-complete (docs/design/pipeline-speed-systematic-fix.md) | confirmed | ● | Given a run.json read failure or a non-stage artifact_cli call, when its tool_result arrives, then no event, no exception escapes, the turn continues |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | Persist stage rows to DB, or session-only like the compact notice? | A. Persist in assistant content | 🧠 self |
| Q2 | Emit on stage completed only, or also on stage entered? | A. Completed only; live header infers the next stage | 🧠 self |
| Q3 | Keep model-written landmarks? | A. Keep — backend row is the floor, model text adds the qualitative result | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [ASSUMPTION] FR-2: Stage rows are persisted in the assistant content so they survive reload/tab switch, and are de-duplicated per run+stage+status within the session
- [SELF-ANSWERED] Q1: A. Persist in assistant content (🧠 AI 替你拍的板 — resolution: A long run's stage history is the progress record the user audits after reload; session-only notices vanish on reload (useChatStreamingLifecycle.ts:3660 comment). Persisted rows are merged per turn on load (chat.py:985), so position is preserved.)
- [SELF-ANSWERED] Q2: A. Completed only; live header infers the next stage (🧠 AI 替你拍的板 — resolution: run.json reliably records completion today; per-stage start timestamps (stage_timing) are absent in all recent runs and only being added by a parallel session, so 'entered' is derived (next stage in the profile after the last completed).)
- [SELF-ANSWERED] Q3: A. Keep — backend row is the floor, model text adds the qualitative result (🧠 AI 替你拍的板 — resolution: Model landmarks carry the one-line '→ key result' prose run.json does not; removing them loses information. Visual duplication handled in THINK/PLAN.)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 8.00 | |
| Feasibility | 7.00 | |

**Acceptance Criteria:**
1. AC1: In a real pipeline turn where the model writes ZERO landmark text, every stage that run.json records as newly completed appears in the chat stream as a visible (not folded) stage row carrying its circled number and stage name, within the same turn the artifact_cli call returns.
2. AC2: Reloading the session (or switching tabs) shows the same stage rows in the same position; no stage row is emitted twice for the same run+stage+status within a session, including across multiple turns.
3. AC3: A failed artifact_cli call, a call that changes no stage status, or a non-artifact_cli Bash command emits nothing; a run.json read error never breaks the streaming turn.
4. AC4: While the turn streams, the live process row shows which stage is in progress (e.g. '⑤ BUILD') derived from the latest stage event, so a long BUILD reads as BUILD, not as an anonymous run of tool calls.
5. AC5: Model-written gate verdicts 'Gate 1: **PASS** ...' and circled-number progress lines ('进 ⑦ TEST') stay visible mid-turn instead of folding; ordinary prose ('aggregate 1', 'delegate 2') still folds.
6. AC6: The emit works for any artifact_cli subcommand that writes stage state (run-update, publish, advance, and the in-flight stage-complete), because detection keys on run.json change, not on the subcommand name.


## 3. Design & Approach
**Research Findings (THINK):**
- The only stream-side run.json observer is _pending_completion (streaming_orchestrator.py:934-939, consumed in TWO duplicated ToolResult branches :1404-1454 UserMessage and :1845-1880 AssistantMessage); it reads status only on run-update --status completed. A stage observer must be one shared helper called from both branches, not a third copy.
- Every yielded {type:'assistant',content:[...]} event is persisted as its own data.db row (session_router.py:4015 -> _persist_assistant_blocks:2010) and rows are re-merged per turn on load (routers/chat.py:985), so a block yielded right after a tool_result keeps its position across reload.
- Frontend updateMessages keeps unknown block types verbatim (useChatStreamingLifecycle.ts:450 else-branch) but deriveTurnView silently drops them (turnView.ts content.forEach has no branch), and ContentBlock union (types/index.ts:302) has no stage type — so a new block needs one renderer branch, nothing in the store.
- Content readers select by type: memory_extractor.py:115-119 and summarization.py:444/535/698 read text/tool_use only; context_injector._filter_tool_only_messages (:77-91) would KEEP a row holding only a stage block (it is not in _TOOL_ONLY_TYPES) and then contribute empty text — the stage type must be added to that skip set.
- Tool summaries reveal the stage for only 307 of 619 stage-writing artifact_cli calls since 10-10 (data.db probe), and a summary never proves the write succeeded — frontend-only parsing cannot meet AC1.
- Per-stage timestamps are unreliable: stage_timing is absent from every recent run.json (written only by agent-called run-observe; a parallel session is adding it via stage-start). The backend's own observation time is the reliable 'completed at' for elapsed display.

**Alternatives Evaluated:**
- QUALITY — effort: M — 1 run, ~8 files (orchestrator helper + ui_actions parsers + context_injector skip set + types + turnView + renderer + live header + tests)
- SIMPLICITY — effort: S — ~4 files
- SPEED — effort: S — 2 files

**Chosen Approach:** A — the streaming orchestrator observes every successful artifact_cli.py Bash call carrying a run id, reads run.json at the tool_result (authority), diffs its stage records against a session-scoped per-run snapshot (seeded by one pre-read at tool_use time the first time a run is seen in the session), and yields one assistant event carrying a typed {type:'pipeline_stage'} block per stage that newly reached completed/skipped. The event rides the existing assistant persistence path (one data.db row, re-merged in order on load). The frontend projection deriveTurnView gains a 'stage' unit (never folded, splits process segments) and puts the next stage on the live process row; LANDMARK_RE additionally keeps line-leading 'Gate N' verdicts and line-leading circled-number progress lines.

**Success Criteria:**
- Backend integration test: 1 stage transition → exactly 1 pipeline_stage block, positioned after its tool_result and before result
- Unchanged / failed / non-artifact_cli calls → 0 blocks (asserted)
- Frontend: a message containing tool_use, pipeline_stage, tool_use renders 2 process rows with 1 stage row between them
- isLandmarkText true on the 3 measured strings, false on the 3 negatives
- Fixture contract test green on both sides; mutating a backend key name turns a test red

**Always:**
- run.json is the authority — never emit from the command string alone
- the helper is fail-safe: any exception → no event, warning log, stream continues
- one shared helper for both ToolResult branches (no third copy of the completion pattern)
- render model-authored summary as plain text
- re-read turnView.ts/ProcessBlock.tsx immediately before editing (a parallel session committed to them at 21:54)

**Never:**
- write synthetic text into an assistant text block (rejected at THINK: backend speech in the model's persisted voice)
- add a timeout/cap that truncates the turn
- edit or stage the parallel session's dirty files
- change the pipeline skill prose in this run


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile full (multi-file feature across backend stream + fro | mechanical | not trivial/bugfix/docs/research; bounded change,  |
| evaluate | Detection keys on run.json diff after any artifact_cli --run | judgment | a parallel session is replacing run-update/advance |
| think | Chose A: typed persisted pipeline_stage block over synthetic | judgment | B writes backend text into the model-facing persis |
| think | Per-call before/after run.json diff + session-scoped emitted | mechanical | stateless across turns and respawns; race degrades |
| plan | Reuse the existing assistant SSE event + persistence path fo | mechanical | session_router persists every assistant event and  |
| plan | Live stage label carried within one message only | taste | pipelines run in one turn under the continuation c |
| plan | Backend-generated fixture contract for the block shape | mechanical | same lockstep pattern as tool-summaries.json / tes |
| build | Live stage label as its own line above the live process row, | taste | ProcessBlock.tsx has an uncommitted parallel-sessi |
| build | Normalize legacy status complete→completed; map gate1/advers | mechanical | found by real-data replay over 936 run.json files |
| build | Do not add the new test file to bvt_manifest.txt | mechanical | manifest admission rule excludes tmp_path + async  |
| review | Fix 7 REVIEW findings in place; accept rework-loop and inter | taste | display-only, fail-quiet; fixing needs run-state t |
| test | Layer-4 = real orchestrator + real run.json mutation + persi | mechanical | a live-daemon probe needs rebuild+restart (XG appr |
| deliver | Accept the first-call pre-read race as a documented boundary | taste | missed-never-duplicated, display-only, no per-stag |

**Summary:** 7 mechanical, 4 taste, 2 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**6 found · 5 fixed · 1 open**
- [MEDIUM] turnView live stage line named BUILD during Gate 1 and stayed stale after a Gate
- [MEDIUM] missing/half-written baseline (pre-read None) replayed every finished stage. Fix
- [MEDIUM] first watched call per run per session can miss its row if the CLI writes run.js
- [LOW] live-stage Fragment vs bare ProcessBlock changed element type → remount. Fixed: 
- [LOW] run.json reads used the default to_thread pool on the hot path. Fixed: executors

### 7.6 Completion Audit
**Requirements met:** 7/7
**All green:** yes


## 8. Files Changed
- `backend/core/pipeline_stage_events.py`
- `backend/core/streaming_orchestrator.py`
- `backend/core/context_injector.py`
- `backend/tests/test_pipeline_stage_events.py`
- `backend/tests/conftest.py`
- `desktop/src/types/index.ts`
- `desktop/src/pages/chat/turnView.ts`
- `desktop/src/pages/chat/components/PipelineStageRow.tsx`
- `desktop/src/pages/chat/components/AssistantMessageView.tsx`
- `desktop/src/i18n/locales/en.json`
- `desktop/src/i18n/locales/zh.json`
- `desktop/src/pages/chat/__fixtures__/pipeline-stage-blocks.json`
- `desktop/src/pages/chat/pipelineStage.contract.test.ts`
- `desktop/src/pages/chat/turnView.pipelineStage.test.ts`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.pipelineStage.test.tsx`

## 9. Lessons
- [principle] Progress that only the model can report is progress the user will sometimes not see: when an agent always crosses a chokepoint that records state (a CLI writing a run file), have the host observe that chokepoint and render the state itself — the model text becomes optional colour, not the only signal.
- [pitfall] A diff-against-snapshot emitter must treat a MISSING baseline as "seed only": diffing against nothing turns one transient read failure into a burst that replays the whole history as new events.
- [pitfall] A progress display derived from a fixed stage order must model the gates that sit between stages: a gate with no stage record of its own makes the live label name the wrong step for the gate's whole duration.
- [guideline] Before shipping a parser over model-written commands, replay it over the real persisted corpus: here it found a legacy status spelling, gate-named stage records, and in-command shell-variable ids that hand-written cases never covered.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 14:53 UTC*
