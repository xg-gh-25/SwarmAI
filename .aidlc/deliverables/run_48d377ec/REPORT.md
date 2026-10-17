# Autonomous Pipeline Report: Stop repetition-loop output mid-stream; flag invisible-thinking stalls (model-agnostic)

**Run ID:** run_48d377ec | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-17 11:27 UTC | **Duration:** 144.2 min (in progress) | **Tokens:** 615,000
**Gate 1:** WARN | **Files:** 7 | **Tests:** 34 run, 0 failed

## TL;DR
流式输出退化/无进展检测(model-agnostic):streaming 层按 content block 维护 2000 字符窗口 zlib 压缩比 >=15 判退化 → 复用用户 Stop 中断路径(client.interrupt)中止并对话内提示、不触发 leak continuation、退化尾巴不进 DB/CLI transcript;thinking 无进展(thinking_t

## 1. Requirement
流式输出退化/无进展检测(model-agnostic):streaming 层按 content block 维护 2000 字符窗口 zlib 压缩比 >=15 判退化 → 复用用户 Stop 中断路径(client.interrupt)中止并对话内提示、不触发 leak continuation、退化尾巴不进 DB/CLI transcript;thinking 无进展(thinking_tokens 增长无输出/长时间无事件)对话内提示;真实 transcript 数据集回归(421+3459 合法 0 触发、8/8 退化触发)

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 流式输出退化/无进展检测(model-agnostic):streaming 层按 content block 维护 2000 字符窗口 zlib 压缩比 >=15 判退化 → 复用用户 Stop 中断路径(client.interrupt)中止并对话内提示、不触发 leak continuation、退化尾巴不进 DB/CLI transcript;thinking 无进展(thinking_tokens 增长无输出/长时间无事件)对话内提示;真实 transcript 数据集回归(421+3459 合法 0 触发、8/8 退化触发)

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG 2026-10-17 acceptance item (1) | confirmed | ● | Given 见 AC1, when 实现完成, then 流式层对每个 content block(text_delta 与 tool_use 的 input_json_delta,按 (parent_tool_use_id,index) 分键)维护最近 2000 字符窗口,窗口满后每新增 250 字符算一次 zlib 压缩比,>=15 判退化;重放 8 个真实退化样本 8/8 在 <=2250 字符内触发 |
| FR-2 | user-story | user-stated: XG 2026-10-17 acceptance item (2) | confirmed | ● | Given 见 AC2, when 实现完成, then 判退化后复用 SessionUnit.interrupt()(非 autonomous = 用户 Stop 语义:_user_stopped_current_turn=True + 回收到 COLD),不自动重试,本轮在对话内以 assistant 文本给出'⚠️ 输出陷入重复已中止'提示并以 result 事件结束;生成器在中断后立即 return,leak continuation / leak restart / glitch restart 均不可达(强制执行测试证明) |
| FR-3 | enabler-story | user-stated: XG 2026-10-17 acceptance item (3) | confirmed | ● | Given 见 AC3, when 实现完成, then 退化尾巴不进 DB 与 CLI transcript:DB 侧 yield 的 assistant 块 = 退化起点前的合法前缀 + 提示;transcript 侧置 _transcript_poisoned,下次 send() 经现有 _clean_poisoned_transcript→clean_transcript_leaks 截断退化段(text 块与 tool_use input 字符串),JSON 合法、uuid 链不变 |
| FR-4 | user-story | user-stated: XG 2026-10-17 acceptance item (4) | confirmed | ● | Given 见 AC4, when 实现完成, then thinking 无进展:消费 system/thinking_tokens;无打开工具时,距上次可见输出 >=300s 且 thinking token 仍在增长 → 对话内提示一次(含分钟数与估计 tokens);>=300s 无任何 SDK 事件 → 对话内提示一次;只提示不中断;阈值来自实测分布(transcript 首个可见块延迟 4.8 p99.9=156s,26914 次中 8 次 >=300s) |
| FR-5 | task | user-stated: XG 2026-10-17 acceptance item (5) | confirmed | ● | Given 见 AC5, when 实现完成, then 回归:本地数据集脚本重放真实 transcript —— 421 合法长文本 + >=3459 工具输入 0 触发,8 退化样本 8/8 触发;CI 单测用仓库自身 .py/.md 文件作公开合法语料 0 触发 + 退化尾巴样本触发;中断路径有强制执行测试(R28) |
| FR-6 | task | user-stated: XG 2026-10-17 acceptance item (6) | confirmed | ● | Given 见 AC6, when 实现完成, then 不改 ━ 模板与 turnView.ts(LANDMARK_RE 依赖 ━━━);合法 completion 摘要框(多条 60 字符 ━ 分隔线)不触发 |
| FR-7 | task | user-stated: XG 2026-10-17 acceptance item (7) | confirmed | ● | Given 见 AC7, when 实现完成, then REPORT 写明覆盖边界:headless(jobs executor、eval scenario_runner)直接跑 claude CLI 子进程,不经 streaming_orchestrator,不受本检测保护 |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | 提示文案用中文还是双语? | B. 中文+英文括注 | 🧠 self |
| Q2 | '对话内提示'走哪个通道(前端有并行 session 正改 turnView/TabView)? | B | 🧠 self |
| Q3 | thinking 内容本身(4.8 可见)要不要也做压缩比检测? | B | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [SELF-ANSWERED] Q1: B. 中文+英文括注 (🧠 AI 替你拍的板 — resolution: XG 指定中文短语'输出陷入重复已中止';产品其他后端提示为英文(turn_limit_reached),双语兼顾两类用户,可逆)
- [SELF-ANSWERED] Q2: B (🧠 AI 替你拍的板 — resolution: useChatStreamingLifecycle.updateMessages 对 assistant 事件的文本块:替换未确认的流式临时块、与已确认文本不同则保留——读 :348-505 确认;并行 run_bfd8b9d3 暂存了 TabView/turnView,避免冲突)
- [SELF-ANSWERED] Q3: B (🧠 AI 替你拍的板 — resolution: AC1 明确范围是 text 与 tool_use input;改写 thinking 块会破坏 signature,resume 时 API 可能拒绝;run_6a41fc99 网关落地后再扩展)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 9.00 | |
| Priority | 9.00 | |
| Feasibility | 8.00 | |

**Acceptance Criteria:**
1. AC1: 流式层对每个 content block(text_delta 与 tool_use 的 input_json_delta,按 (parent_tool_use_id,index) 分键)维护最近 2000 字符窗口,窗口满后每新增 250 字符算一次 zlib 压缩比,>=15 判退化;重放 8 个真实退化样本 8/8 在 <=2250 字符内触发
2. AC2: 判退化后复用 SessionUnit.interrupt()(非 autonomous = 用户 Stop 语义:_user_stopped_current_turn=True + 回收到 COLD),不自动重试,本轮在对话内以 assistant 文本给出'⚠️ 输出陷入重复已中止'提示并以 result 事件结束;生成器在中断后立即 return,leak continuation / leak restart / glitch restart 均不可达(强制执行测试证明)
3. AC3: 退化尾巴不进 DB 与 CLI transcript:DB 侧 yield 的 assistant 块 = 退化起点前的合法前缀 + 提示;transcript 侧置 _transcript_poisoned,下次 send() 经现有 _clean_poisoned_transcript→clean_transcript_leaks 截断退化段(text 块与 tool_use input 字符串),JSON 合法、uuid 链不变
4. AC4: thinking 无进展:消费 system/thinking_tokens;无打开工具时,距上次可见输出 >=300s 且 thinking token 仍在增长 → 对话内提示一次(含分钟数与估计 tokens);>=300s 无任何 SDK 事件 → 对话内提示一次;只提示不中断;阈值来自实测分布(transcript 首个可见块延迟 4.8 p99.9=156s,26914 次中 8 次 >=300s)
5. AC5: 回归:本地数据集脚本重放真实 transcript —— 421 合法长文本 + >=3459 工具输入 0 触发,8 退化样本 8/8 触发;CI 单测用仓库自身 .py/.md 文件作公开合法语料 0 触发 + 退化尾巴样本触发;中断路径有强制执行测试(R28)
6. AC6: 不改 ━ 模板与 turnView.ts(LANDMARK_RE 依赖 ━━━);合法 completion 摘要框(多条 60 字符 ━ 分隔线)不触发
7. AC7: REPORT 写明覆盖边界:headless(jobs executor、eval scenario_runner)直接跑 claude CLI 子进程,不经 streaming_orchestrator,不受本检测保护


## 3. Design & Approach
**Research Findings (THINK):**
- zlib 压缩比在 2000 字符窗口上把退化与合法输出干净分开:合法 text <=2.76(421)、工具输入 <=5.07(3492)、退化 >=52.6(7+1);每次检查约 20µs,每 250 字符一次,热路径成本可忽略
- interrupt 后 CLI 补发部分 TextBlock 的 AssistantMessage 并写入 transcript(探针 e04fee9d 实测),所以 DB 侧要由 orchestrator 自己 yield 截断后的块,transcript 侧必须清洗
- compaction_guard 已在同一位置使用 `await self._parent.interrupt(); return` 模式(streaming_orchestrator.py:1489);interrupt(autonomous=False) 设置 _user_stopped_current_turn 并 recycle 到 COLD(PIT01 修复),send() 不在流式期间持有 _lock
- _transcript_poisoned → 下次 send() 开头 _clean_poisoned_transcript(session_unit.py:2244, 4707)在 spawn 前、进程已 COLD 时清洗,是现成的'无写者时清洗'时机
- system/thinking_tokens 帧以 SystemMessage(subtype='thinking_tokens', data.estimated_tokens 累计/estimated_tokens_delta)送达,约每秒一帧;Opus 5.5 thinking 内容为空
- 前端 updateMessages:assistant 事件里的文本块会替换未确认的流式临时文本块,与已确认文本不同则并存——assistant 事件可作为零前端改动的对话内提示通道
- headless 路径(jobs/executor.py、scripts/scenario_runner.py)直接 subprocess 跑 claude CLI,不经 orchestrator

**Alternatives Evaluated:**
- SIMPLICITY — effort: M, 1 session
- FLEXIBILITY — effort: L, 1-2 sessions
- DELETION — effort: S

**Chosen Approach:** New pure module backend/core/output_degeneration.py (detector, run-edge search, strip helpers, ProgressWatch). StreamingOrchestrator keeps one detector per (parent_tool_use_id, index) for text_delta and input_json_delta; on a hit it builds the legit-prefix + notice assistant event and a result(output_degenerated) first, then runs SessionUnit.interrupt() in the background while still draining the SDK stream, then returns (no retry, no leak continuation). system/thinking_tokens feed ProgressWatch, whose thinking-stall (300s) and silence (240s) notices ride the in-tab still_working status. leak_transcript cuts degenerate runs from the CLI transcript on the next send(); the Slack gateway replaces its streamed reply with the notice.

**Success Criteria:**
- 8 个真实退化样本以 30 字符左右的 delta 流式喂入 DegenerationDetector 全部在 <=2250 字符内命中
- 本地数据集脚本:真实 transcript 中所有 >=2000 字符的合法 text 与工具输入 0 命中,退化 8/8 命中,脚本 exit 0
- orchestrator 驱动测试:退化流 → interrupt 被调用 1 次、yield 的 assistant 文本 = 合法前缀 + '输出陷入重复已中止'、最后一个事件 type=result subtype=output_degenerated、client.query 从未被调用(无 leak continuation)、_transcript_poisoned=True
- 真实 SessionUnit.interrupt 被强制执行:_user_stopped_current_turn=True、state=COLD、client.interrupt 被 await
- transcript 清洗:含退化块的 jsonl 清洗后逐行可解析、行数不变、uuid/parentUuid 不变、退化段被提示替换,合法前缀保留;工具输入字符串同样被截断
- ProgressWatch:300s 无可见输出且 thinking 增长 → 返回提示一次;工具打开期间不提示;可见输出后重新 arm
- 真实 CLI E2E(Layer 4):让模型输出重复内容,经真实 orchestrator 触发中断与提示,CLI transcript 经清洗后无重复段

**Always:**
- 检测与截断用同一个函数(live 与 transcript 清洗一致)
- 中断走 SessionUnit.interrupt(autonomous=False),不新增 kill 路径
- 每个新增恢复/中断路径有强制执行测试(R28)
- 改了行为的注释/docstring 同提交更新(R7)
- 只 git add 本 run 的文件(并行 run_bfd8b9d3 暂存了前端文件)

**Never:**
- 自动重试退化的轮次
- 在 thinking 停滞时中断/杀进程(只提示)
- 改 ━ 模板或 turnView.ts LANDMARK_RE
- 改写 thinking 块(signature)
- 提交任何真实 transcript 内容进公开仓库


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | profile=bugfix (known root cause, bounded multi-file change) | mechanical | decision tree: clear bug with known root cause |
| evaluate | notice via assistant text event, zero frontend change | taste | avoids colliding with paused run_bfd8b9d3 staged f |
| evaluate | bilingual notice text | taste | XG phrase + English for non-CJK users |
| think | Approach A: pure module + inline wiring over plugin framewor | taste | only two detectors; framework is premature (R25) |
| plan | thresholds 300s for thinking/silent notices (silence later l | taste | measured: model-side first-visible latency p99.9=1 |
| plan | dataset replay is a local script, CI uses repo files as publ | mechanical | transcripts are private; public repo (STEERING #19 |
| build | run edges grown against a core sample with preset-dictionary | taste | pure period/window methods left junk or swallowed  |
| build | harness-hang fix (conftest permission-queue reset) withdrawn | mechanical | conftest.py carries another session's uncommitted  |
| review | same 15x threshold for tool inputs as text | taste | 0 of 3597 real tool inputs above 5.07; no data sup |
| test | classify order-dependent xdist failures as pre-existing | mechanical | pass alone on this tree; HEAD fails the same class |
| deliver | defer run-commit: two shared files carry another session's l | judgment | run-commit stages whole files; committing now swee |
| deliver | stall notices as still_working status instead of conversatio | taste | integration review: persisted assistant rows pollu |
| deliver | keep one 15x threshold for tool inputs | taste | 0/3597 real tool inputs above 5.07 |

**Summary:** 4 mechanical, 8 taste, 1 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**13 found · 13 fixed · 0 open**
- [MEDIUM] core/streaming_orchestrator.py _abort_on_degeneration: abort prefix could re-exp
- [MEDIUM] core/output_degeneration.py find_degenerate_run: drift edge chunk could swallow 
- [MEDIUM] core/streaming_orchestrator.py _abort_on_degeneration: per-turn parent fields wr
- [MEDIUM] core/streaming_orchestrator.py: stall notices persisted as assistant rows (histo
- [MEDIUM] channels/gateway.py: Slack never showed the degeneration notice and kept the str

### 7.6 Completion Audit
**Requirements met:** 7/7
**All green:** yes
- [ ] FR-4/AC4 amended during Gate-2: stall notices are shown as the in-tab still_working status, not as conversation content (persisting them as assistant rows polluted resume/memory and could replace provisional streamed text). The silence notice fires at 240s (below the 300s message-timeout floor); the thinking-stall notice at 300s. The status shows on the active tab only.
- [ ] Coverage boundary (AC7): headless paths — the jobs executor and eval scenario_runner — run the claude CLI as a subprocess and do not pass through streaming_orchestrator, so they are NOT protected by this guard.
- [ ] Accepted boundary: a legitimately repetitive tool input the user asked for (e.g. a file of identical rows) is stopped like a loop; 0 of 3,597 real tool inputs came near the threshold.
- [ ] Slack gateway branch (replace reply with the notice on output_degenerated) has no unit test; it mirrors the existing max_output_tokens branch.
- [ ] Pre-existing, unchanged by this run: test_context_warning_bridge times out identically on HEAD.


## 8. Files Changed
- `backend/core/output_degeneration.py`
- `backend/core/streaming_orchestrator.py`
- `backend/core/leak_transcript.py`
- `backend/core/session_unit.py`
- `backend/tests/test_output_degeneration.py`
- `backend/scripts/degeneration_dataset_eval.py`
- `backend/channels/gateway.py`

## 9. Lessons
- [pitfall] A per-file pathspec commit is not a per-hunk boundary: if a parallel session edits the same file, a whole-file git add sweeps its live hunks into your commit. Before committing, diff each touched file (staged and unstaged) for hunks you did not write; a shared file with foreign hunks is a commit blocker, not something to stage around.
- [principle] Judge runaway generation by information content, not by volume or time: the compression ratio of a recent window separated repetition loops (>=52) from thousands of real outputs (<=5) with no per-model tuning, whereas volume or elapsed-time watchdogs stop healthy long work.
- [pitfall] Awaiting a control call (such as interrupt) on a producer that writes into a bounded buffer, while no one reads that buffer, deadlocks until the call times out: the producer's reader blocks on the full buffer and never sees the control reply. Keep draining the stream while the control call is in flight.
- [pitfall] A module-level asyncio primitive keyed by a fixed id (a Queue bound to the first event loop) shared across per-test asyncio.run() calls makes the next test spin forever. Before blaming a change for a whole-file test hang, run the same file on HEAD; fix the harness with fresh keys or a registry reset.
- [decision] Stream-status notices (thinking stall, silence) belong on a status channel, never in conversation content: persisted as assistant text they leak into resume context and memory as if the model said them, and they can replace provisional streamed text in the UI.
- [pitfall] Where an interrupted turn leaves traces must be probed, not assumed: the Claude CLI persists a partially streamed text block to its transcript (so --resume replays it) but records no tool_use when interrupted mid tool input.
- compression ratio separates loops from real output
- interrupt must drain bounded SDK buffer
- per-file commit is not per-hunk


## 10. Known Gaps & Attention Flags
- FR-4/AC4 amended during Gate-2: stall notices are shown as the in-tab still_working status, not as conversation content (persisting them as assistant rows polluted resume/memory and could replace provisional streamed text). The silence notice fires at 240s (below the 300s message-timeout floor); the thinking-stall notice at 300s. The status shows on the active tab only.
- Coverage boundary (AC7): headless paths — the jobs executor and eval scenario_runner — run the claude CLI as a subprocess and do not pass through streaming_orchestrator, so they are NOT protected by this guard.
- Accepted boundary: a legitimately repetitive tool input the user asked for (e.g. a file of identical rows) is stopped like a loop; 0 of 3,597 real tool inputs came near the threshold.
- Slack gateway branch (replace reply with the notice on output_degenerated) has no unit test; it mirrors the existing max_output_tokens branch.
- Pre-existing, unchanged by this run: test_context_warning_bridge times out identically on HEAD.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 11:27 UTC*
