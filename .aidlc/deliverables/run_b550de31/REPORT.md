# Autonomous Pipeline Report: Chat turn: the answer-so-far never moves at turn end; a turn without a result still shows something

**Run ID:** run_b550de31 | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 15:51 UTC | **Duration:** 46.6 min (in progress) | **Tokens:** 1,177,787 (measured per response, cache read 41,549,290; $78.60 settled, excludes the turn in flight, includes the pre-run part of the creating turn)
**Gate 1:** WARN | **Files:** 6 | **Tests:** 598 run, 0 failed

## TL;DR
**解决了什么问题**
- 原来：While a turn streamed, the block above the answer-so-far folded and re-opened when the next tool call arrived; and a finished or stopped turn with no result showed only folded 'WORKED · N steps' lines with no text at all.
- 现在：The answer-so-far stays first level, so the final answer never moves when the turn ends, and the block above it stays open while it streams. A turn without a result shows its last note under the folded line, or opens its last block when there is no note.

**怎么做的**
- groupWork holdLiveAboveAnswer: a block with tool calls right before the streaming answer stays live
- WorkGroup tail (under the folded line, in place when opened) and defaultOpen for a turn with no text
- tail / default-open chosen among top-level units only, so a note folded inside a stage never counts
- textOrdinal keys for every text unit; block id = first process, then first text

**遗留问题**（5 条，见 §10）

## 1. Requirement
bugfix: chat turn display follow-up to run_c5eb0858 (Gate-2 findings left open). (1) While a turn streams, text written after the latest tool call renders first-level as the answer-so-far; when the next tool_use arrives it becomes a note and moves into the WORKING block (remount, smaller size, folded WORKED block re-opens) — layout moves under the reader. Make that transition move nothing, with a streaming rerender test. (2) A finished turn that ends on a tool call (or was stopped mid-work) has no outcome, so every note folds into WORKED and the user sees only 'WORKED · N steps' with no text. Keep the turn's last note visible at first level (or the last block open) when there is no RESULT, with a test. Keep: two-level structure, counts-only folded line, real tool calls, no per-item chrome, stable ids across stream-end reconcile. Frontend only (desktop/src/pages/chat).

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 你继续

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: Gate-2 finding left open in run_c5eb0858; XG: 继续 | confirmed | ● | Given a streaming turn [tool, tool_result, text], when the next tool_use arrives, then the text keeps its DOM node and place inside the still-live WORKING block |
| FR-2 | user-story | user-stated: Gate-2 finding left open in run_c5eb0858 | confirmed | ● | Given a finished turn [tool, text, tool], when it renders, then the text is visible outside the folded WORKED block, which still counts 2 steps |
| FR-3 | constraint | user-stated: requirement Keep: list | confirmed | ● | Given the existing chat display tests, when the change is in, then they pass unchanged |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 7.00 | |
| Priority | 7.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: While a turn streams, the text written after the latest tool call is the last item of the live WORKING block; when the next tool_use arrives that text keeps the same DOM node and place, and the block stays live (no fold, no re-open)
2. AC2: A finished turn's text after its last tool call is the RESULT, first level, as today
3. AC3: A finished turn with no RESULT (it ended on a tool call, or was stopped) shows its last note after the first tool call at first level, outside the folded WORKED block; the block's counts are unchanged
4. AC4: The intent restatement, stage sections, block ids across the stream-end reconcile and the existing chat display tests are unchanged


## 3. Design & Approach
**Research Findings (THINK):**
- turnView.ts:329-337: while streaming, any text after the latest tool_use is an outcome; a landmark there ('## ✦ ⑦ TEST') is also an outcome until the next tool_use, so pipeline turns hit the same jump
- groupWork boundary rule: outcome ends a block; the block before it stops being live, then becomes live again when the text turns into a member
- WorkGroup itemsOf keys a note 'cb-<index>' and a landmark 'lm-<index>'; AssistantMessageView keys an outcome 'cb-<index>' first-level. Same key, different parent = remount
- Block id rule = first process id, else first thought ordinal / first item index; a block that starts with a text member changes id when a process joins it
- WorkGroup already renders an unrecovered failure under the folded line (WorkGroup.tsx folded branch): the place a turn-ending note can show without breaking the block
- AGENT R20: tool calls are silent, so text between tool calls is rare; text after the last call is usually the answer

**Alternatives Evaluated:**
- SIMPLICITY — effort: S (1 session)
- QUALITY — effort: S
- DELETION — effort: S

**Chosen Approach:** SIMPLICITY alternative from THINK. groupWork gains opts.trailingInBlock: while streaming, a final outcome after work joins (or opens) the live block as a member. A block's id comes from its first non-thought member (process id, or a text ordinal for a note / landmark / outcome), so a block that starts with text keeps its id when a tool call joins. WorkGroup keys every text member 'cb-<index>' and renders an outcome member at answer size. AssistantMessageView computes tailNote for a finished turn with no RESULT, passes it to the block as `omit`, and renders it first level right after that block.

**Success Criteria:**
- X1-X5 test files pass
- tsc clean in the regression run
- the streaming rerender test keeps the same DOM node

**Always:**
- frontend only, desktop/src/pages/chat
- tests through pipeline_test.py
- hunk-level staging of this run's files only

**Never:**
- touch another session's uncommitted files
- push
- restart the daemon


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Live block owns the trailing text (not 'text keeps its place | taste | a note is second level; keeping text first level m |
| evaluate | Profile bugfix | mechanical | clear defect with a code-traced cause |
| think | Trailing text renders at answer size inside the live block | taste | it is usually the answer (tool calls are silent by |
| think | Last note shows under the folded line, not as a split | judgment | a split changes block counts and order; the failur |
| plan | Exit criteria name this run's test files, not mode:verify | judgment | mode:verify counts failures outside the change (la |
| plan | Tail note rendered after its block, omitted from the block b | taste | first level without duplicating it when the block  |
| plan | Trailing text joins the block only when the turn already has | judgment | a pure answer turn must keep its answer first leve |
| build | Three existing expectations changed (answer-so-far outside t | mechanical | they encoded exactly the behaviour this requiremen |
| build | Inner text keys by textOrdinal instead of content index | judgment | index shifts at the stream-end reconcile and chang |
| review | Keep the txt- id for a thoughts + answer-so-far block | judgment | stable for the common thought -> landmark -> tool  |
| test | AC coverage refs re-recorded file-level | mechanical | the runner matches desktop tests by file, not test |
| deliver | Option B: answer-so-far stays first level; block above held  | judgment | XG chose B at the L2 checkpoint (2026-10-18): the  |
| deliver | ACs restated to option B (AC1', AC3') | judgment | the original AC1 'move nothing' cannot hold for bo |
| deliver | Old expectations changed: history / unrecognised-tool tests  | mechanical | they encoded the behaviour this requirement replac |

**Summary:** 4 mechanical, 3 taste, 7 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**13 found · 10 fixed · 3 open**
- [HIGH] Round 1 (option A): the in-block answer-so-far still shrank to note size at the 
- [MEDIUM] Round 1: every work turn's final answer streamed inside the WORKING card and jum
- [HIGH] Round 1 duplicate of the size shift. Resolved by option B.
- [MEDIUM] Round 1: a turn with no notes still showed only 'WORKED · N steps'. Fixed: defau
- [LOW] Round 1: lifted tail broke stream order when opened; tail jumped out of a linger

### 7.6 Completion Audit
**Requirements met:** 3/3
**All green:** yes


## 8. Files Changed
- `desktop/src/pages/chat/turnView.ts`
- `desktop/src/pages/chat/components/WorkGroup.tsx`
- `desktop/src/pages/chat/components/AssistantMessageView.tsx`
- `desktop/src/pages/chat/turnView.groupWork.test.ts`
- `desktop/src/pages/chat/components/__tests__/WorkGroup.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.turnView.test.tsx`

## 9. Lessons
- [principle] When a display must classify streaming content whose kind is only known later (answer or note), no layout can keep both transitions still; decide which transition may move by frequency, so the common case (the final answer) never moves and the rare case (text between silent tool calls) absorbs it.
- [pitfall] Putting the answer-so-far inside the live work block looked like 'move nothing' in unit tests that only asserted node identity, yet it shrank the text at the next tool call and moved every final answer at turn end. Assert the user-visible property (size, parent, position) a requirement names, not a proxy like node identity.
- [pitfall] A 'last visible item' rule (the turn's last note) must be chosen from what actually renders at that level: a note folded inside a pipeline stage section was picked, so a stopped pipeline turn still showed nothing. Choose from the same unit set the renderer places at top level.
- [guideline] A block id anchored on its first member changes when a member can join in front mid-stream (the intent note once the pipeline_run block lands); anchor on the member that cannot be preceded later (the first tool-call segment), with text and thought ordinals only as fallbacks.
- pick which streaming transition moves by frequency
- assert the user-visible property not node identity


## 10. Known Gaps & Attention Flags
- 未修复的 LOW 发现：Option B residual: a note between tool calls still remounts from first level into the block and shrinks. Accepted by XG (rare: tool calls are silent by rule); test pins it is shown once.
- 未修复的 LOW 发现：The block above a streaming answer now folds after the turn ends (above the finished answer) instead of 2s into it; scroll anchoring (TabView anchorDelta) offsets it for a reader. Not separately tested.
- 未修复的 LOW 发现：Older stopped turns render taller after upgrade (tail line or last block open). Cosmetic.
- LOW 风险：fold of the held-open block now happens after the turn ends
- LOW 风险：older stopped turns render taller


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 15:51 UTC*
