# Autonomous Pipeline Report: Chat turn first-level structure: direct runs are not pipelines; no Working card before the restatement

**Run ID:** run_6c5ef452 | **Project:** SwarmAI | **Profile:** direct
**Date:** 2026-10-19 12:13 UTC | **Duration:** 32.2 min (in progress) | **Tokens:** 678,316 (measured per response, cache read 28,564,884)
**Gate 1:** N/A | **Files:** 8 | **Tests:** not recorded

## TL;DR
**解决了什么问题**
- 原来：A turn opened with a 'Working · Thinking' card (and a second spinner beside the bee) before anything was said, a thought written before the restatement stood in a block above it, and a turn that made a direct run was drawn as a PIPELINE checklist (PLAN / BUILD / GATE 2 / DELIVER) though no pipeline ran.
- 现在：A turn opens with only the bee; the restatement is the first thing on screen; the opening thought is the first item of the first WORKING block after it; a direct-run turn reads restatement, WORKED, result.

**怎么做的**
- deriveTurnView skips the stage and run blocks of a run whose latest profile is direct
- no empty live unit or Working card before any content
- groupWork (top level) puts leading thoughts into the first work block across the restatement and questions, and hides them until the turn settles when there is none

**遗留问题**（7 条，见 §10）

## 1. Requirement
Chat turn first-level structure (XG 2026-10-19, design-judgment Surface 6 #7). (1) A direct-profile run is not a pipeline: its pipeline_stage / pipeline_run blocks do not make the turn a pipeline turn, so no PIPELINE checklist block for it; the turn shows as a chat turn (restatement, WORKED, result). (2) A turn starts with no WORKING card: before any work the streaming bee is the one spinner; thoughts written before the restatement never form a first-level block above it, they join the first work block after the restatement (live and settled alike).

## 2. Evaluation
Recommendation: **?** | Scope: ?

## 3. Design & Approach
**Chosen Approach:** Frontend only. turnView.deriveTurnView skips pipeline_stage/pipeline_run blocks of runs whose latest profile is direct and no longer pushes an empty live process for empty content; groupWork merges the lead thoughts into the first block after the restatement while streaming too, and with hideLead drops an unmergeable leading thoughts-only block until the turn settles; groupWork no longer adds an empty WORKING card when nothing has arrived. AssistantMessageView passes hideLead (streaming or settling) at the top level and keeps lead thoughts at the top of a pipeline turn until settle. design-judgment.md Surface 6 #7 updated.

**Success Criteria:**
- verify mode green except failures classified pre-existing

**Always:**
- the stage placeholder after a stage row stays (pipeline live line)

**Never:**
- edit streaming_orchestrator.py or tool_summarizer.py


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| plan | Filter direct runs in the frontend projection rather than st | judgment | stored history already holds those blocks, so only |
| plan | Hide the leading thoughts-only block until the turn settles  | judgment | Surface 6: the restatement leads the turn and the  |
| build | Pipeline turns keep lead thoughts at the top (unshown) until | judgment | the replay test showed the run's live row being re |
| deliver | Keep the direct run's summary and attention cards | judgment | the user objected to the PIPELINE checklist block; |
| deliver | Accept one folded Thought line appearing above a no-work ans | judgment | keeping the restatement still in task turns (the c |

**Summary:** 0 mechanical, 0 taste, 5 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**5 found · 1 fixed · 4 open**
- [MEDIUM] think → restate → ask → work hid the lead thought all stream and showed it above
- [MEDIUM] desktop/src/pages/chat/components/AssistantMessageView.tsx: a turn with no work 
- [LOW] desktop/src/pages/chat/turnView.ts: a direct run's pipeline_summary card (verdic
- [LOW] desktop/src/pages/chat/turnView.ts groupWork: a no-work turn that asks a questio
- [MEDIUM] desktop/src/pages/chat/turnView.ts: the direct-run filter drops only pipeline_st

### 7.6 Completion Audit
**Requirements met:** 5/5
**All green:** yes


## 8. Files Changed
- `backend/skills/s_frontend-design/data/design-judgment.md`
- `desktop/src/pages/chat/components/AssistantMessageView.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.replay.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.thoughts.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.turnView.test.tsx`
- `desktop/src/pages/chat/turnView.groupWork.test.ts`
- `desktop/src/pages/chat/turnView.test.ts`
- `desktop/src/pages/chat/turnView.ts`

## 9. Lessons
- [pitfall] A rule that hides an item while a view streams must place it with the same rule once settled; a merge keyed on one neighbour (the restatement directly before a work block) breaks when any other item sits between them, and the hidden item then appears somewhere else at the settle, above what the reader was reading.
- [pitfall] Classifying a turn as a pipeline from the presence of run blocks treated every run as a pipeline; the decision has to read the run profile (latest block wins), because direct runs share the same block types.


## 10. Known Gaps & Attention Flags
- 未修复的 MEDIUM 发现：desktop/src/pages/chat/components/AssistantMessageView.tsx: a turn with no work (think, then answer) shows only the bee while streaming and adds one folded Thought line above the answer at the settle. Kept by design and documented in Surface 6 #7: the first text cannot be told apart from a restatement until a tool call comes, and keeping the restatement still is the common case.
- 未修复的 LOW 发现：desktop/src/pages/chat/turnView.ts: a direct run's pipeline_summary card (verdict + documents) still renders after the result. Kept: it is the run's delivery record, not the PIPELINE checklist block; the needs-you attention card must keep rendering too.
- 未修复的 LOW 发现：desktop/src/pages/chat/turnView.ts groupWork: a no-work turn that asks a question (think → ask → answer) shows its settled Thought line above the question, not just above the answer. Rare; left for a follow-up (place unjoined leads before the first item that is neither the restatement nor an interrupt).
- 未修复的 MEDIUM 发现：desktop/src/pages/chat/turnView.ts: the direct-run filter drops only pipeline_stage and pipeline_run; a direct run's pipeline_summary (and a DELIVER pipeline_attention) still render as cards after the result. Kept on purpose: the needs-you card must never be hidden and the summary is the run's delivery record, not the PIPELINE checklist block. Follow-up: state this in design-judgment.md Surface 6 #7 and add a test.
- 风险：MED: a direct run still shows its pipeline summary card (and a needs-you card if DELIVER flags one) after the result; kept on purpose (delivery record / needs-you must not be hidden), to be written into Surface 6 #7 in a follow-up
- 风险：LOW: a run moved from direct to a pipeline profile mid-stream switches the message to pipeline layout at that moment (rare)
- 风险：LOW: lead thoughts stay unshown for a whole pipeline turn until it settles; the bee is the indicator


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-19 12:13 UTC*
