# Autonomous Pipeline Report: Chat turn display v7.11: work groups, one strip per tool call, zero per-item chrome

**Run ID:** run_bf20d0a7 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 10:45 UTC | **Duration:** 89.8 min (in progress) | **Tokens:** 1,791,167 (measured per response, cache read 107,884,208)
**Gate 1:** WARN | **Files:** 17 | **Tests:** 1530 run, 0 failed, 2 pre-existing

## TL;DR
**解决了什么问题**
- 原来：A finished turn showed a per-thought 'Thought' label and 'Show all' button, single-step groups that opened to the same line, raw shell commands as row titles, a 'Started without restating the ask' line on most turns, and process text at the same size as the answer.
- 现在：A thought and the tool calls it led to fold into one WORKED line (files beside it); only the running group is an open WORKING card; every tool call is one strip of kind, target, short result and status; thoughts carry no chrome and open on click; the intent (Approach) is the first substantial thought within two tool calls; process sits in one dim lane, the answer outside it at full size.

**怎么做的**
- A pure groupWork pass over the existing projection, so deriveTurnView stays the one source
- WorkGroup + ToolStrip replace ProcessBlock (deleted)
- labelBash never falls back to the whole command
- design-judgment Surface 6 records zero-per-item-chrome and one-strip-per-call

**遗留问题**（6 条，见 §10）

## 1. Requirement
前端 chat 回合展示按 mock v7.11 落地（只做前端 + design-judgment 文档，后端数据另起 run）。规格 = Knowledge/Designs/2026-10-18-chat-stream-v7.11-mock.html（真实 session 重放，顶部清单是完整规则）。(1) 工作分组：一条思考+它引出的工具调用为一组；在写给用户的说明、阶段/gate 边界、interrupt 处分开；冒号结尾的预告句作为下一组标题；工具后的复盘思考留在原组。完成的组折叠成一行 WORKED/THOUGHT（小号大写标签+标题+改了/碰了的文件 FileChip），只有正在跑的组是展开的 WORKING 卡片（唯一 live 区域）；完成的阶段点开时组以标题+内容平铺，最多两层。(2) 每个工具调用=一条 strip：类型·对象·用时·状态，无展开层，只放有用信息（≤40 字结果、sub-agent 结论），失败错误一行在下；标题不出原始命令；单步不再重复一行。用时：运行中用现有 elapsed，完成后有数据才显示。(3) ThoughtBlock：删每条的 Thought 标签和 Show all/less；点正文展开/收起（键盘可用）；进行中 3 行、完成后 1 行；intent 6 行、标签 Approach，识别延长到前 2 次工具调用内第一段 >=160 字思考。(4) 删除 Started without restating the ask。(5) 过程通道：左线后、12.5px、暗一档；答案 15px 全对比度。(6) 文件链接不嵌在 button 里。(7) 阶段行无模型小结时用效果补。保留 Understanding 卡、阶段行+next、Gate 1 行、待你确认、本轮产出、下一步 chips、Copy 只复制答案。(8) 规则写进 backend/skills/s_frontend-design/data/design-judgment.md Surface 6。不在本 run：工具完整输入/起止时间持久化、Gate 0/2 事件、当前阶段读 stage_timing。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> v7.11 可以，起前端 pipeline

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG 2026-10-18: 细节的WORKED 我展开再看 做完是折叠的, 只有正在WORKING 的是展开的 | confirmed | ● | Given a finished turn with thoughts and tool calls, when it renders, then each group is one folded WORKED/THOUGHT line and clicking it shows that group's thoughts and strips<br>Given a streaming turn, when a new group starts, then the previous group folds and only the newest is the open WORKING card |
| FR-2 | user-story | user-stated: XG 2026-10-18: Read/Search/Write/Edit/Query 级别就是一个strip 条 ... 在用什么tool 已经花了多少时间 状态 不就够了吗 | confirmed | ● | Given a finished Bash call that ran a test file, when it renders, then the row reads Test + the test file + its pass count + duration + a check mark, never the raw command |
| FR-3 | user-story | user-stated: XG 2026-10-18: 每一条都加这种屁话 是污染chat 会话的 | confirmed | ● | Given a turn with 10 thoughts, when it renders, then the words Thought, Show all and Show less do not appear and clicking a clamped thought expands it in place |
| FR-4 | user-story | user-stated: XG 2026-10-18: 刚开始的intent thinking 你不能都给我折叠进去 | confirmed | ● | Given a turn whose first thought is under 160 chars followed by one tool call then a long thought, when it renders, then the long thought is the Approach |
| FR-5 | user-story | user-stated: XG 2026-10-18 screenshots; it fires on ~88% of turns so it carries no information | confirmed | ● | Given any turn, when it renders, then the line is absent |
| FR-6 | user-story | user-stated: XG 2026-10-18: 跟正常response 根本分不清 | confirmed | ● | Given a finished turn, when it renders, then process text is 12.5px behind a left rule and the answer is 15px full contrast |
| FR-7 | task | ddd-derived: ProcessBlock.tsx Row=button wraps FoldedSummary FileChip (gate0 skeptic confirmed); invalid nested interactive content | confirmed | ● | Given a folded group line with file chips, when the DOM renders, then no a/button element is a descendant of a button |
| FR-8 | user-story | user-stated: XG 2026-10-18: 你绝对不能犯 | confirmed | ● | Given design-judgment.md, when read, then Surface 6 states zero per-item chrome and one strip per tool call |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 9.00 | |
| Feasibility | 7.00 | |

**Acceptance Criteria:**
1. AC1: A finished work group (a thought plus the tool calls it leads to) renders as ONE folded header line: an uppercase WORKED (or THOUGHT when it has no tool call) tag, a title (the colon-ended lead-in note, else the first sentence of its lead thought) and the files it changed/touched as FileChips; only the group still running renders open as the WORKING card
2. AC2: Groups split only at a note the model wrote to the user, a stage/gate boundary or an interrupt; a note ending in ':' is the next group's title, not a split; a thought after tool calls with no further tool call before the next note stays in its group
3. AC3: Every tool call renders as one strip row (kind, target, duration when known, status icon) with no detail level; a short readable result (<=40 chars, e.g. test counts) or a sub-agent verdict may sit on the row, a failure shows its error on one line below; no row title is a raw shell command; a single-step group never lists the same step twice
4. AC4: ThoughtBlock renders no per-item 'Thought' label and no 'Show all'/'Show less' button; the clamped text itself toggles (click and Enter/Space); 3 lines while streaming, 1 line once the turn ended; the intent is labelled Approach, 6 lines, and is the first >=160-char thought within the first two tool calls (else the thoughts before the first tool call)
5. AC5: The 'Started without restating the ask' line is gone
6. AC6: Process content (thoughts, notes, strips) sits behind one left rule at 12.5px and one shade dimmer; the answer stays 15px full contrast, not indented
7. AC7: No clickable FileChip/link is nested inside a <button>; the fold toggle and the file links are separate controls
8. AC8: A finished stage row with no model-written summary shows an effect summary (files changed, test count, committed); an opened finished stage lays its groups out as title + content, so disclosure stays at two levels
9. AC9: Existing surfaces keep working: Understanding card, stage rows with next X -> Y, Gate 1 row, For-you-to-check card, Produced tiles, next-step chips, Copy copies only the answer
10. AC10: design-judgment.md Surface 6 states the zero-per-item-chrome rule (labels/controls only on group headers and turn landmarks) and the one-strip-per-tool-call rule


## 3. Design & Approach
**Research Findings (THINK):**
- deriveTurnView (turnView.ts:266-358) already yields every member a work group needs, in stream order: 'thought' units with a stable ordinal, 'process' units of ADJACENT tool calls with stable ids seg-<n>, and 'note' / 'landmark' / 'stage' / 'interrupt' / 'outcome' boundaries. Grouping can therefore be a pure pass over units, like the existing sectionUnits / headedProcesses passes, without touching the done rule or the reconcile-sensitive ids.
- The note-as-title condense (085cbc98, AssistantMessageView.tsx headedProcesses/titleOf) is a partial version of the colon-title rule; keeping both would render a note twice. It is replaced, not stacked.
- FileChip (FileChip.tsx:75) is itself a <button>; ProcessBlock's folded Row is a <button> (ProcessBlock.tsx:406) that wraps FoldedSummary's FileChips (:289): nested interactive content today.
- labelBash (turnView.ts:564-592) falls back to the whole command (up to 60 chars) for anything it does not recognise, and SETUP_RE (:482) strips cd/export/source but not bare assignments like R=run_x; — that is why rows read 'Ran R=run_df73d970; python3 -c ...'.
- ToolResultContent (types/index.ts:230) persists only status + a one-line outcome; there is no start/end time, so finished durations cannot be shown until the backend stores them. usePendingToolElapsed covers the running step.
- ThoughtBlock (ThoughtBlock.tsx:54/:80) owns the per-thought label row and Show all/less button; the i18n parity test locks every chat.turn key, so removed keys must leave en.json and zh.json together.

**Alternatives Evaluated:**
- QUALITY — effort: L
- SIMPLICITY — effort: M
- SPEED — effort: S

**Chosen Approach:** Keep deriveTurnView unchanged. Add a pure groupWork() pass in turnView.ts that turns a unit list (top level or a stage's children) into work groups + pass-through units, and move the intent rule there. Render groups with a new WorkGroup component (header row: a toggle button for tag+title, file chips as SIBLINGS of the button; body: ThoughtBlock + ToolStrip rows; live variant = the WORKING card; section variant inside an opened finished stage). ToolStrip replaces ProcessBlock's ToolStepRow/FoldedSummary/LiveTitle. Harden labelBash against raw commands. Strip ThoughtBlock to text-as-toggle. Remove noAlignment and the headedProcesses condense. Wrap process content in a lane. Update i18n, tests and design-judgment Surface 6.

**Success Criteria:**
- all exit criteria tests pass
- mode:verify green (tsc + affected vitest)
- a real finished turn renders as Approach + WORKED lines + RESULT

**Always:**
- keep deriveTurnView's done rule, process/thought ids and outcome/copy semantics unchanged; the ONLY deriveTurnView change is the intent rule (C3)
- change en.json and zh.json together

**Never:**
- render a raw shell command as a row title
- nest a button/link inside a button
- add a per-item label or control text


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile full: ~6 frontend files + tests + one doc, new group | mechanical | multi-file logic change, not a known-pattern trivi |
| evaluate | Scope frontend only; backend data (full input, timings, gate | taste | XG asked for the frontend pipeline; the frontend d |
| think | Group in a render-layer pass, not inside deriveTurnView | judgment | keeps the done rule and reconcile-stable ids untou |
| think | Replace the 085cbc98 note-as-title condense with the colon-t | mechanical | stacking both renders a note twice |
| plan | Delete ProcessBlock rather than keep it beside WorkGroup | mechanical | two renderers for tool calls would drift; WorkGrou |
| plan | Finished durations hidden until the backend persists start/e | taste | showing nothing beats a fake number; the backend r |
| plan | Gate 1 WARN folded into the plan: one intent source (deriveT | mechanical | each action is a direct correction with one right  |
| build | A group of one item (one tool call or one thought) renders a | judgment | a header that opens to the same line again is the  |
| build | Groups inside an opened pipeline stage are laid out open | judgment | the stage row is the fold; a second fold inside it |
| build | An Agent strip's verdict is the live summary while streaming | judgment | sub-agent progress is only attached while streamin |
| build | Group tags stay English in zh (like stage names) | judgment | they are landmark vocabulary, not prose |
| build | Tool-call duration for finished strips is not shown | judgment | start/end times are not persisted; the backend run |
| review | Accepted the spec gate's EXTRA items without removing them | judgment | each one narrows a fallback toward the no-raw-comm |
| review | Did not treat the verify deadline kills as failures | judgment | same files pass in 7.9s in isolation; the kill hap |
| test | Ran regression with --deadline 240 instead of the default 95 | judgment | two runs at the default were cut by the global dea |
| deliver | Rebuilt the index hunk-level and re-based on the parallel se | judgment | committing them would have shipped another session |
| deliver | Moved a stale .git/index.lock aside (renamed, not deleted) | judgment | held 6 minutes with no git process alive; renaming |
| deliver | Lane as a per-item class rather than a wrapper | judgment | an item that changes lane membership must not chan |

**Summary:** 4 mechanical, 2 taste, 12 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**14 found · 14 fixed · 0 open**
- [HIGH] turnView.ts groupWork: a multi-line ':' note became a group title and only its f
- [HIGH] turnView.ts liveLeadIn: a streaming answer ending in ':' ('Here are three option
- [HIGH] turnView.ts result.subagent / stop-status rule: not this run's code — a parallel
- [HIGH] Same as above (types/index.ts subagent field) — resolved by the same index rebui
- [MEDIUM] Same foreign stop-status hunk (every result-less call 'unknown' on legacy histor

### 7.6 Completion Audit
**Requirements met:** 10/10
**All green:** yes


## 8. Files Changed
- `desktop/src/pages/chat/turnView.ts`
- `desktop/src/pages/chat/turnView.test.ts`
- `desktop/src/pages/chat/turnView.groupWork.test.ts`
- `desktop/src/pages/chat/components/WorkGroup.tsx`
- `desktop/src/pages/chat/components/ThoughtBlock.tsx`
- `desktop/src/pages/chat/components/AssistantMessageView.tsx`
- `desktop/src/pages/chat/components/PipelineStageRow.tsx`
- `desktop/src/pages/chat/components/TabView.tsx`
- `desktop/src/pages/chat/components/ProcessBlock.tsx`
- `desktop/src/pages/chat/components/ContentBlockRenderer.tsx`
- `desktop/src/pages/chat/tf.ts`
- `desktop/src/hooks/usePendingToolElapsed.ts`
- `desktop/src/i18n/locales/en.json`
- `desktop/src/i18n/locales/zh.json`
- `desktop/src/i18n/__tests__/locale-keys.test.ts`
- `backend/skills/s_frontend-design/data/design-judgment.md`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.condense.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.pipelineStage.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.renderFidelity.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.thoughts.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.turnCards.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.turnView.test.tsx`
- `desktop/src/pages/chat/components/__tests__/TabView.pillAction.test.tsx`
- `desktop/src/pages/chat/components/__tests__/WorkGroup.test.tsx`
- `desktop/src/__tests__/sse-to-dom.integration.test.tsx`
- `desktop/src/pages/chat/scrollFocus.ts`
- `desktop/src/pages/chat/__tests__/scrollFocus.test.ts`

## 9. Lessons
- [pitfall] A whole-file git add on a file another session is editing at the same time stages that session's half-done hunks. In a shared repo, stage hunk-level and diff the staged file against your own patch before the review runs; reviewers will otherwise spend their findings on code that is not yours.
- [pitfall] An index snapshotted before another session's commit silently reverts that commit if you commit it as-is (its new lines show as deletions in your staged diff). After any foreign commit lands mid-run, rebuild your index from the new HEAD and re-check that the staged diff holds only your hunks.
- [principle] A wrapper element around a dynamic run of items makes every item that changes membership remount under the reader. Express membership as a class on the item itself, so its key and its parent never change.
- [decision] A ':'-ended note becomes a group title only when it is one short line and a tool call already follows it. A title shows one line, so a longer note would hide the rest of what the agent said, and a streaming answer that happens to end in ':' would flip into a card and back.


## 10. Known Gaps & Attention Flags
- Visual check in the packaged app is still open (RP60): jsdom cannot prove the lane, strip widths or fold timing — look at one chat turn and one pipeline turn after the next build
- Tool-call durations for finished strips need the backend to persist start/end times (separate backend run)
- 风险：MED scaling: groups are rebuilt every streaming frame — mitigated: stepLabel is cached per block (labelCache WeakMap), so the per-frame cost is O(steps) lookups, the same order as ProcessBlock's per-unit summary
- 风险：LOW: stripKind depends on the backend summarizer's 'Running:' prefix — cosmetic if it changes (falls back to Run)
- 风险：LOW: TabView imports stripKindWord from the WorkGroup module
- 风险：LOW (refuted): 'no spinner while the last item is a thought/note' — a non-intent thought is always inside the live group, a streaming intent carries its own spinner, and a note cannot be the streaming tail (it is the outcome)


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 10:45 UTC*
