# Autonomous Pipeline Report: Chat stream: process fold + single live step + clickable next steps

**Run ID:** run_bfd8b9d3 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-17 10:12 UTC | **Duration:** 107.4 min (in progress) | **Tokens:** 462,000
**Gate 1:** WARN | **Files:** 8 | **Tests:** 0/0

## TL;DR
chat 流『过程折叠 + 当前步单行 + 可点击下一步』:单一展示模型 deriveTurnView(过程/当前步/结论/interrupts,done 单一定义,不依赖 tool_result 持久化)→ 过程收成一行、流式中只露当前一步、展开三层(摘要→步骤→原始命令);前端 label 适配层(去 cd 前缀+过去时,后端 summary 不动);当前活动只显示一处;回复末尾 next-s

## 1. Requirement
chat 流『过程折叠 + 当前步单行 + 可点击下一步』:单一展示模型 deriveTurnView(过程/当前步/结论/interrupts,done 单一定义,不依赖 tool_result 持久化)→ 过程收成一行、流式中只露当前一步、展开三层(摘要→步骤→原始命令);前端 label 适配层(去 cd 前缀+过去时,后端 summary 不动);当前活动只显示一处;回复末尾 next-steps 代码块渲染为可点击 chip(点文字填入/↑发送);正文栏 min(940px,100%)、回复无底色;strangler 删 ToolGroup/ActivityFeed/groupConsecutiveTools。shape-reference: Knowledge/Designs/2026-10-17-streaming-process-fold-mock.html;验收:38 步纯工具调研流式可见行≤2,DB 回放每 turn 可见行 p90 164→个位数,重载后同样折叠,失败步骤与 interrupts 永不隐藏。纯前端,零后端/streaming 执行逻辑变更,需 Layer-4 E2E smoke。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> chat 流『过程折叠 + 当前步单行 + 可点击下一步』:单一展示模型 deriveTurnView(过程/当前步/结论/interrupts,done 单一定义,不依赖 tool_result 持久化)→ 过程收成一行、流式中只露当前一步、展开三层(摘要→步骤→原始命令);前端 label 适配层(去 cd 前缀+过去时,后端 summary 不动);当前活动只显示一处;回复末尾 next-steps 代码块渲染为可点击 chip(点文字填入/↑发送);正文栏 min(940px,100%)、回复无底色;strangler 删 ToolGroup/ActivityFeed/groupConsecutiveTools。shape-reference: Knowledge/Designs/2026-10-17-streaming-process-fold-mock.html;验收:38 步纯工具调研流式可见行≤2,DB 回放每 turn 可见行 p90 164→个位数,重载后同样折叠,失败步骤与 interrupts 永不隐藏。纯前端,零后端/streaming 执行逻辑变更,需 Layer-4 E2E smoke。

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | enabler-story | user-stated: XG: 系统化方案不要打补丁;Gate-0 证实两模块 done 判定相反 | confirmed | ● | Given DB 重载的 turn(0 个 tool_result), when 渲染, then 所有非末尾 tool 判为完成,过程折叠成一行 |
| FR-2 | user-story | user-stated: mock 2026-10-17-streaming-process-fold-mock.html,XG 确认 | confirmed | ● | Given 38 步纯 Bash turn 流式中, when 渲染, then 工具部分可见行 ≤2 |
| FR-3 | task | ddd-derived: summary 被 4 处按前缀解析(summarization.py/context_injector/MergedToolBlock/ActivityFeed),改后端会破坏契约 | confirmed | ● | Given summary='Running: cd /a/b; grep -n x f.py', when 渲染完成态步骤, then 显示『运行 grep -n x f.py』类人话,不含 /a/b |
| FR-4 | user-story | user-stated: 截图:同一事实被说 6 遍 | confirmed | ● | Given 流式中且贴底, when 渲染, then 无内联状态行、无胶囊,只有过程行的当前一步 |
| FR-5 | user-story | user-stated: XG: 下一步可直接点击进 input box;STEERING #7 格式条款已加 | confirmed | ● | Given 最终文字以 next-steps 块结尾, when 点第一个 chip 的文字, then 输入框填入该行,未发送 |
| FR-6 | task | user-stated: XG: 两侧留白不要太大;同意回复不加底色 | confirmed | ● | Given 宽窗口, when 渲染, then 正文栏宽度 = min(940px, 容器) |
| FR-7 | task | user-stated: XG: 不要打补丁;R26 | confirmed | ● | Given 交付时, when grep 这三个符号, then 生产代码 0 引用 |
| FR-8 | user-story | assumption: Gate-0 实测:过程文字中 landmark/gate 占 10% 字符,短 narration 3%,其余 86% 是工作笔记;turn 结论由 outcome 承载 | assumed | ● | Given 过程段含一条 '## ★ GATE 1' 文字和三段工作笔记, when turn 结束后渲染, then GATE 文字可见,工作笔记在展开视图里 |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | 过程段 76% 的文字(工作笔记/中途发现)如何处理? | B | 🧠 self |
| Q2 | next-steps 块在 Slack 等 channel 会以原始代码块出现,怎么处理? | A | 🧠 self |
| Q3 | chip 的『直接发送』走什么路径? | A | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [ASSUMPTION] FR-8: 过程段文字策略:landmark/gate verdict 留主流;其余过程文字流式时只显示最新一段(变暗、限高),turn 结束后收进过程展开视图(与步骤按时间交错)
- [SELF-ANSWERED] Q1: B (🧠 AI 替你拍的板 — resolution: Knowledge/Designs/2026-10-17-streaming-audit/interim.py 实测:landmark/gate 10% 字符(765 块),<80 字 narration 3%,80-300 字 27%,≥300 字 59%;抽样均为过程中工作笔记(读到了什么、下一步要做什么),对外结论由最后一个 tool 之后的 outcome 承载。C 保留 p90 59 行文字,达不到 AC3;A 会藏掉 pipeline landmark。B 可逆(纯渲染),按 taste 决策记录。)
- [SELF-ANSWERED] Q2: A (🧠 AI 替你拍的板 — resolution: 本 run 约束为零后端改动;已在 STEERING #7 加 scope 句『desktop chat only — channel 用 plain bullet list』)
- [SELF-ANSWERED] Q3: A (🧠 AI 替你拍的板 — resolution: MessageStore single-writer + R27:发送只能有一条路径;chip 只是另一个触发者)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 8.00 | |
| Feasibility | 7.00 | |

**Acceptance Criteria:**
1. AC1: 一个真实的 38 步纯 Bash turn 在流式中,工具部分可见行 ≤2(过程一行 + 当前一步),不再逐条铺开
2. AC2: 同一 turn 从 DB 重新加载后(无 tool_result)同样折叠成过程一行,步数与流式时一致
3. AC3: 用最近 7 天真实 turn 回放 deriveTurnView,每 turn 默认可见单元 p90 ≤ 9(现状 164)
4. AC4: 失败的步骤与 ask_user_question / cmd_permission_request / escalation 永远渲染在主流,不被折叠藏掉(混合 fixture 断言)
5. AC5: 步骤 label 去掉开头 `cd <path>;` / `cd <path> &&`,只显示动作+对象;完成后过去时;读/写类步骤的文件路径点击仍能打开文件
6. AC6: 最终文字末尾的 ```next-steps 块渲染为 ≤3 个 chip,原始块(含流式中半截 fence)从不显示;点文字填入输入框、点 ↑ 走现有发送路径;无块不渲染
7. AC7: 流式中消息区只有一处『当前在干什么』:内联状态行移除;sticky 胶囊仅在 follow=false(用户翻离底部)时出现
8. AC8: 过程段里的 pipeline landmark / gate verdict 文字留在主流可见
9. AC9: Layer-4 E2E:build:all 后在真实 app 里跑一次流式 turn + 切 tab + 重载,三个场景都折叠正确且滚动贴底


## 3. Design & Approach
**Research Findings (THINK):**
- 根因是展示层缺一个单一投影:groupConsecutiveTools(utils.ts:293) 与 MergedToolBlock(:149-158) 对 done 的判定相反,且 tool_result 不持久化(DB 0 条),所以任何依赖 resultMap 的折叠在历史中永不生效
- 真实分布:每 turn 可见行 p90 164;以『同类连续』为单位只降到 135,以『不分类连续 tool』降到 118,以『过程段』为单位降到 2 —— 折叠单位必须是过程,不是同类 tool
- 过程文字占 76% 字符,其中 landmark/gate 10%(必须留主流),其余是工作笔记
- 现成可复用机制(C042):dispatchInjectChatInput({text,focus,autoSend})(pages/chat/injectChatInput.ts:36)已支持填入/自动发送 → chip 直接复用;followRef(TabView.tsx:333)是唯一贴底权威 → 胶囊显隐复用它
- summary 字符串是 tool_summarizer.py:172-299 的 13 种前缀格式,被 4 处解析;前端适配层只读不写,未识别前缀回退原文

**Alternatives Evaluated:**
- QUALITY — effort: M · 1 session
- SIMPLICITY — effort: S
- DELETION — effort: S

**Chosen Approach:** 单一展示模型:新 pure 模块 turnView.ts 把一条 assistant message 投影成有序 TurnUnit[](landmark 文字 | 过程段 process-segment | outcome 文字 | interrupt 块),tool 完成只有一个定义(有 result / 其后有任意 block / 消息不在流式中)。过程段以 landmark 文字为分隔(无 landmark 时整个过程 = 1 段),每段渲染为 ProcessBlock 一行;流式中只有最后一段露出当前一步与最新一段过程文字。outcome = 最后一个 tool_use 之后的文字,剥离末尾 ```next-steps 块后正常渲染;next-steps 渲染为 NextSteps chips(仅最后一条 assistant、非流式)。TabView 删除内联活动行(保留 reconnecting/resuming),胶囊只在 followRef=false 时出现,消息列 max-w 940px 居中。删除 ToolGroup / ActivityFeed / groupConsecutiveTools。

**Success Criteria:**
- turnView.test.ts 覆盖 AC1/AC2/AC4/AC5/AC6/AC8 的纯函数断言全部通过,且回退任一实现(删 done 的位置判定 / 删 landmark 分段)会让对应测试变红
- AssistantMessageView 组件测试:38 tool fixture 流式 → DOM 中 tool 行 ≤1 + 当前步 1;重载 fixture 无 spinner
- segments 回放脚本改为直接调用编译后的 deriveTurnView(或等价 TS 测试读取真实导出样本)得到 p90 ≤ 9
- vitest 受影响套件全绿,tsc -b 无错
- 真实 app 截图:同一 38 步调研流式中可见过程区 ≤2 行

**Always:**
- deriveTurnView 是唯一的 done 判定来源;MergedToolBlock 的 isPending 由投影传入,不再自行判定
- 流式中内容高度变化后复用 TabView 现有单一贴底路径(followRef),不新增贴底触发器(TECH 前端脆弱区 ③)
- store 仍是唯一渲染权威;投影是纯函数,不缓存、不持有第二份消息状态(脆弱区 ②)
- 删除旧组件前 grep 全部消费方(生产 + 测试),R27
- OT03 内容不丢失守护保留语义:展开过程后每个 text block 的全文都在 DOM 中

**Never:**
- 不改 streaming 执行逻辑、SSE 事件、MessageStore 写路径
- 不把 next-steps 文本当 HTML 渲染;chip 不在用户点击前自动发送
- 不新增第二个 done 判定或第二个贴底权威


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | 过程文字策略 B:landmark/gate 留主流,其余流式只显最新一段、结束后折进过程 | taste | Gate-0 实测 landmark/gate 10% 字符;纯渲染可逆 |
| evaluate | STEERING #7 next-steps 格式限定 desktop,channel 用列表 | mechanical | 零后端约束,Slack 无 chip 渲染器 |
| evaluate | profile=full, work_type=existing-feature, cross_boundary=tru | mechanical | 多文件有界 feature;summary 字符串与 DB 持久化形态两个 seam |
| think | 选 A:单一展示模型 + 新组件,删除 ToolGroup/ActivityFeed/groupConsecutiveT | mechanical | B/C 的关键假设被证伪(AC1-AC3 不满足) |
| think | chip 复用 dispatchInjectChatInput(autoSend),胶囊复用 followRef | mechanical | C042:机制已存在 |
| think | 流式中尾部文字移入过程后以『最新一段』限高 3 行显示 | taste | 限制布局跳动幅度;可调 |
| plan | 过程段以 landmark 文字为分隔(回放 p90=8) | mechanical | segments.py 实测满足 AC3 且保留 pipeline 结构 |
| plan | AC4 细化:失败数显示在折叠摘要行;仅『turn 末尾未恢复的失败』单独显示在折叠外 | taste | 与 XG 确认的 mock 一致;多数失败是 grep 退出码/守卫拦截后已重试,逐条外露会重新制造 |
| plan | OT03 渲染保真测试改为『展开过程后全文在 DOM』 | taste | 保留内容不丢失守护语义,同时允许过程默认折叠 |
| plan | UI 文案走 i18n(默认 en),zh 同键 | mechanical | i18n/index.ts lng=en |
| plan | next-steps chip 只在最后一条 assistant 显示 | taste | 旧消息的下一步已过时 |
| build | MergedToolBlock / ContentBlockRenderer 的 tool_use 分支保留(chat  | taste | R26:旧路径在新路径通过集成验证前保留 |
| build | Thinking 改为过程内的一步(默认收起),不改 ContentBlockRenderer 的 ThinkingBl | mechanical | chat 中 thinking 不再经过 ContentBlockRenderer |
| build | 历史 turn 不显示『已工作 Xm』时长,只显示步数 | taste | 合并后的历史消息只有首行时间戳,时长不可知,不编造 |
| build | Action row 门控保持『任意文字』,Copy 优先复制 outcome、无 outcome 时回退全部文字 | mechanical | 用户路径追踪发现:以 copyText 门控会让中断的 turn 失去 Continue 按钮 |
| test | Layer-4 用真实 data.db 808 个 turn 回放 + backend↔frontend summary | mechanical | cross_boundary=true 的两个 seam |
| test | 两个负载超时(与改动无关模块,单独跑通过)记为既有负载敏感,不在本 run 修 | taste | 不 import 改动模块;单独 59/59 通过 |
| deliver | push-ready for the LOCAL commit; push waits for the user rea | judgment | STEERING #5: qualified = verified in the running a |

**Summary:** 9 mechanical, 8 taste, 1 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**16 findings** (verdict: ?)
- [MED] turnView.ts deriveTurnView: segment ids were block-index based and shifted after
- [MED] turnView.ts done rule: a later tool_use counted as proof the previous call retur
- [MED] turnView.ts: a tool gated by an unanswered permission request showed a green che
- [MED] turnView.ts INVISIBLE_RE missed Unicode TAG characters / C1 / variation selector
- [MED] ProcessBlock: stepLabel recomputed for every step on every streamed token (perfo

### 7.6 Completion Audit
(No completion audit data)


## 8. Files Changed
- `desktop/src/pages/chat/turnView.ts`
- `desktop/src/pages/chat/turnView.test.ts`
- `desktop/src/pages/chat/components/ProcessBlock.tsx`
- `desktop/src/pages/chat/components/NextSteps.tsx`
- `desktop/src/pages/chat/components/AssistantMessageView.tsx`
- `desktop/src/pages/chat/components/TabView.tsx`
- `desktop/src/pages/chat/utils.ts`
- `desktop/src/pages/chat/utils.test.ts`
- `desktop/src/i18n/locales/en.json`
- `desktop/src/i18n/locales/zh.json`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.turnView.test.tsx`
- `desktop/src/pages/chat/components/__tests__/TabView.activityIndicator.test.tsx`
- `desktop/src/pages/chat/components/__tests__/AssistantMessageView.renderFidelity.test.tsx`
- `desktop/src/__tests__/sse-to-dom.integration.test.tsx`
- `desktop/src/pages/chat/components/ToolGroup.tsx`
- `desktop/src/pages/chat/components/ActivityFeed.tsx`
- `desktop/src/pages/chat/tf.ts`
- `backend/tests/test_tool_summary_fixture.py`
- `backend/tests/bvt_manifest.txt`
- `desktop/src/pages/chat/__fixtures__/tool-summaries.json`
- `desktop/src/pages/chat/turnView.contract.test.ts`

## 9. Lessons
- [pitfall] A UI 'done' signal keyed on a result block that the backend never persists works live and is dead in history — derive completion from what IS persisted (position + stream state) and test with the real persisted shape, never a fixture that seeds the missing blocks.
- [principle] For attention-reduction UI, pick the fold unit by replaying the real distribution through each candidate rule: same-category grouping looked right but only moved visible rows p90 164→135, while folding the whole process segment reached single digits.
- [pitfall] Table tests written from imagination missed 362 real shell shapes (2>/dev/null, || exit, || { … }, quoted exports); running the parser over the real persisted corpus as a Layer-4 replay found them in one pass.
- [pitfall] An expansion/selection key derived from a block index shifts when a reconcile drops non-persisted blocks (tool results, thinking); key by an ordinal over structure that survives persistence.
- [pitfall] An interrupt (permission request / question) is appended AFTER the call it gates, so 'a later block exists' is not proof that call ran — treat an unanswered gate as waiting, and a turn that ended on a call as unknown, never done.
- [decision] A one-click 'send this' chip must show exactly what it sends: wrap instead of truncating, cap length, and strip invisible characters by an explicit list (bidi, zero-width, TAG, C0/C1) — not by \p{Cf}/\p{Cn}, which break emoji joiners and vary with the WebKit Unicode version.


## 10. Known Gaps & Attention Flags
- Validator blocked 1 time(s) during execution


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 10:12 UTC*
