---
title: 会话内容唯一来源 — 结束聊天显示的打地鼠
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-23
project: SwarmAI
status: IMPLEMENTED — P0–P4、P6 已落地（2026-10-23）；第 3.7 节记录实现与 v2 草案的差别；只剩 §7.2（需真实 Hive 网络）
related: streaming-spinner-ssot.md (2026-06-30，同类的 spinner 修复), liveReload.property.test.ts (5109df87)
---

# 会话内容唯一来源 — 结束聊天显示的打地鼠

## 0. 一句话

一个会话的内容现在有**两种存法**（实时事件、库里的记录）和**十几条写法**（前端多条路径各写各的，
每条带自己的合并规则）。今天修掉的聊天问题全是这些写法之间的分歧。修法：**库是唯一记录，每次
写都由触发器在库里分配一个会话内递增的版本号 `rev`；回合之外的写入作为带 rev 的记录变更推送，
回合自己的内容仍走回合事件（按 eventSeq 去重，不带 rev）；屏幕 = 快照 + 之后的变更；历史的 ETag
就是 rev；发送用一张请求表做幂等；会话状态只有一个生成函数。** 先写测试（P0），再改。

## 1. 问题清单（2026-10-22 一天碰到的，全部查证过）

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 1 | CLI 自开回合停在审批上，卡片不显示，session 等了 18 分钟 | RC1 | 已补丁 1166f6d2 |
| 2 | 提问卡片和答案刷新后消失（R2） | RC1 | 已修（提问存库） |
| 3 | 并行后台 agent：实时 N 个气泡，刷新后 1 个 | RC1 | 已补丁 cc2ceef6 |
| 4 | 续接回合单独成气泡，对账后内容显示两遍（R1） | RC1 | 已补丁 23697bf8 |
| 5 | 共用气泡里跨回合的相同文本被去重删掉（R3，#3 的补丁引入） | RC1 | 已补丁 23697bf8 |
| 6 | 已失效的审批卡片仍显示可点按钮 | RC1 | 已补丁 1166f6d2 |
| 7 | 消息排在审批后面，界面不说原因 | RC3 | 已补丁 a30d89f6 |
| 8 | SESSION_BUSY 碰上静默重试，同一条消息存两次（10-15 起 19 对） | RC2 | 已补丁 cc2ceef6 |
| 9 | 恢复后重发、断线重连、排队送出、首次发送断线后重试，是否重复 | RC2 | 已修（S2：请求表 + 一条消息一个 client_id） |
| 10 | CLI 自开的空回合当成 API 故障，进程被结束 | RC3 | 已修 1d060eee |
| 11 | 空响应日志的耗时是排队时间 | — | 已修 a30d89f6 |
| 12 | Stop 后 CLI 的占位话被模型当成自己的回复 | — | 已修 a30d89f6 |
| 13 | git `check-ignore --stdin` 管道死锁，4 个调用点 | RC4 | 已修 da200214 |
| 14 | code-intel 全量重建超时（即 #13） | RC4 | 已修 da200214 |
| 15 | worktree gate 超时放行 | RC4 | 已缓解 da200214 |
| 16 | bash 语法检查超时放行 | RC4 | 已缓解 da200214 |
| 17 | CORS 拒绝 If-None-Match，带缓存请求失败 134 次 | RC5 | 已修 da200214 |
| 18 | 参数校验日志不写字段 | RC5 | 已修 da200214 |
| 19 | main 上红测试无人发现 | RC5 | 已修 401627ec |
| 20 | arXiv 配成 http，被 SSRF 防护拦 | RC5 | 已修（配置） |
| 21 | SwarmWS `.git/index.lock` 反复残留 | RC5 | 未查到原因 |

"已补丁"= 当时在现有机制上加了一条特殊规则。#1–#9 的根因已由 S1–S3 消掉（2026-10-23），回放测试
（80 个场景，`KNOWN` 为空）是证据。

## 2. 根因

### RC1 会话内容没有唯一来源（#1–#6）

**后端：同一个回合写出两种东西，规则分散。** 实时事件由 `_published_turn` 发布，库里的记录由
`_persist_stream_event` 写，两者在这些地方不一样（2026-10-22 main）：

| 内容 | 实时事件 | 库里的记录 |
|---|---|---|
| tool_result | 完整内容 | 只有状态、outcome、父子关系（`_compact_tool_results`） |
| text | 流式 delta + 最终块 | 清洗过的最终块，清洗结果写回事件（bug 2d） |
| turn_deliverables / turn_changes | 写库那一步额外生成的 assistant 事件 | 同 |
| ask_user_question | 有 | **不存**（R2） |
| cmd_permission_request | 有 | 存；决定事后改旧记录；`expired` 读历史时才算出 |
| error | 有 | 不存，只触发未写结果的落库 |
| Stop 回执 turn_stop | **不发布** | 直接写库（`_persist_stop_receipt`） |
| 上下文压缩 | — | 不在 messages 表；读历史时拼成 `compact-<id>` 系统行 |
| 后台 agent 结果 | `background_agents` 事件 | 事后改旧记录（`update_agent_result`） |

**前端：写入路径不止对话本身。** MessageStore 被这些写：POST 流（订阅流断开时）、订阅流、
回合结束对账、15 秒轮询补卡片、`resyncTabFromDb` / `mergeTabFromDb` / `initialize`、乐观插入
（用户气泡、续接回合的数字占位）、排队气泡、Stop（同时写 React / tab / store）、本地插件消息、
前端生成的系统行和错误行、sessionStorage 恢复、`open_tabs.json` 恢复与后台预载。合并规则另有
`_applyMerge`、`isStalePlaceholder`、`_mergePreservingInteractive`、`_confirmed`、相邻重复折叠，
以及今天加的 `hostedTurn` / `turnStart`。

这些写入混着三类东西，应该分开：

1. **对话记录**：用户消息、助手的块、交互卡片（提问、审批）及其答案和决定、回合回执
   （turn_stop、turn_notice、background_finished、交付物）。持久，所有 tab 一致。
2. **会话状态**：在输出 / 在等什么 / 排着几条 / 后台 agent。由后端决定，不进对话记录。
3. **界面提示**：tab notice、本地报错、"正在重连"。只属于这个 tab，不进对话记录。

现在 2、3 类有不少被当成消息写进 MessageStore（前端错误行、轮询补出的卡片），这是分歧的另一个
来源。

### RC2 发送不是幂等的（#8、#9）

用户行在执行前无条件写入（`run_conversation`），之后才可能转成 pending。前端至少有 4 条会再发
的路径：连接阶段静默重试、断线重连（最多 9 次）、恢复后自动重发、SESSION_BUSY 后重新排队。
**新 tab 第一次发送在拿到 sessionId 前断线，重试会新建 session**，按 (session_id, client_id)
去重挡不住。频道入口不传 client_id，`run_conversation` 每次生成新的，频道重试同样不幂等。

### RC3 会话状态有两个来源（#7、#10）

`/sessions/streaming-state` 已返回 state、waiting_input、pending question/permission、
pending_count、drain seq、后台 agent；前端每 15 秒拉一次并补卡片。订阅流里又有回合事件。两者
没有约定谁覆盖谁，前端各取一部分拼。

### RC4 基础设施没有统一防护（#13–#16）；RC5 报错没人看（#17–#21）

同一种写法、同一种失败处理散落各处；日志里一直有，没有汇总和提醒。

## 3. 方案

### S1 库是唯一记录，屏幕 = 快照 + 之后的事件（解 RC1）

#### 3.1 版本号 rev（替换 v1 的事件水位）

v1 用内存事件序号 `epoch:seq` 当水位，审查指出它衔接不上：写库在前、发布分配序号在后，读历史和
读序号之间有竞态；hub 在内存里，重启归零、缓冲 4000 条；Stop 回执和审批决定根本不经过发布。

v2：**版本号在库里分配，和写入同一个事务。** 落地形状（细节见 3.7）：

- 每个会话一个计数 `session_revs(session_id, rev, deleted_rev)`，每行的 rev 在 `transcript_revs`。
  messages 和 compactions 的插入、更新（审批决定、提问答案、后台 agent 结果、压缩摘要）、删除，
  都由触发器在同一个事务里 `rev += 1`。
- **回合事件不带 rev**（和草案不同）：回合自己的行由 TurnWriter 先写库再发布，事件按 eventSeq 去重，
  断线后由 hub 回放当前回合或 `resync` 重读。回合之外的写入（用户消息、决定、答案、Stop 回执、后台
  agent 结果、压缩、删除）作为 `transcript_row` / `transcript_delete` 推送，带这次写入后的 rev 和整行。
- 历史接口**先读 rev、再读记录**（不是一个读事务）：快照里一定有 `rev ≤ R` 的改动，可能还多一点。
  多出来的那条之后若再经变更流到达也不会重复：每条消息带 `row_revs`（它含哪些行、各自的 rev），
  变更的 rev 不高于其中记录的就跳过（3.9）。返回 `X-Transcript-Rev: R`。
- **ETag 就是 rev**：`"<session>:r<rev>:<等待中的审批>:<页形状>"`（3.9）。
- **tab 记下的 rev 只来自读库**（resync 的重读、`since_rev`），不来自变更事件（3.9）。
- **增量接口** `GET /sessions/{id}/transcript?since_rev=R`：返回 `rev > R` 的记录和 `reset`（R 之后删过
  行）。resync 先问它，记录没变就不重读。
- **分页**：~~翻页请求带上首屏的 `R`~~。核对后不需要（2026-10-23）：较早的一页读的是记录的现在，
  变更流只改 tab 已有的记录（`transcriptFeed` 对没加载的行什么也不做），所以晚加载的一页自带之后
  的改动，不重复也不漏。`tests/test_history_paging_union.py` 在真实库上验证：按回合翻页拼起来 =
  一次全量；翻页之间改了未加载页上的审批，加载时就是新状态；首屏之后新增的回合不进旧页。

#### 3.2 规范化事件格式（Python / TS 共用的输入）

**2026-10-23 落地的形状**：`docs/design/transcript-event.schema.json` 是订阅流信封的契约：每个信封的基本
字段，以及前端依赖字段的那些类型（回合事件、提问、审批、resync、记录变更、会话状态、后台 agent）。
`tests/test_transcript_event_schema.py` 用真实后端在 liveReload fixture 里产出的每一个信封校验它，并检查
schema 里的每个类型后端确实在发、前端确实在读。"Python 写库结果和 TS 投影结果逐字相等"的一致性用例就是
liveReload 属性测试本身（80 个场景，实时 = 刷新，`KNOWN` 为空）。没有做 `liveOutput` 拆分：写库内容和
发布内容现在由同一个 TurnWriter 产出，属性测试已经要求两边显示相同，再拆一个非持久字段只多一条路径。

原草案：

先定义一份 **canonical transcript event**，两边都只处理它：

- 后端在写库这一步把原始事件规范化（清洗文本、压缩 tool_result、生成交付物块），**写库内容和
  发布内容都是规范化后的同一份**。实时需要的完整 tool_result 输出单独作为非持久字段
  `liveOutput` 附在事件上，投影时忽略。
- schema 文件 `docs/design/transcript-event.schema.json`，列出每种块的字段和哪些参与显示。
- **一致性用例**：后端生成一组「规范化事件 → 期望消息」的标准用例（扩展现有
  `test_live_reload_scenarios.py` 的 fixture），Python 写库结果和 TS 投影结果都要和它逐字相等。

#### 3.3 投影规则（只定义一次）

1. 用户消息是一条 user 消息，用 client_id 关联。
2. 一个回合的规范化块按 rev 顺序折进当前助手消息；`tool_result` 按 toolUseId 挂在对应 tool_use 后；
   交互块（提问、审批）是一个块，它的答案 / 决定 / 失效是对这个块的更新。
3. **连续的助手回合是一条消息**：回合开始时上一条是助手消息就接着它，前面是用户消息才新起一条。
   这条规则按 turnId 定义；历史接口现在的 `_merge_consecutive_assistant_messages` 只按相邻角色合并，
   结果在今天的场景里一致，P2 里改成按回合边界显式实现。
4. 去重只在一个回合内。
5. 上下文压缩行、Stop 回执、turn_notice 都是对话记录的块，有 rev，按 3.1 处理。

#### 3.4 前端

- **三类分开存**：对话记录只在 MessageStore（快照 + rev 之后的事件，由投影算出）；会话状态只在
  session state（S3）；界面提示只在 tabNoticeStore。前端不再把错误行、补出的卡片写成消息。
- **POST 和订阅流的分工**（落地形状，见 2.2 和 3.7）：
  - 占到位并写下用户行后，POST 第一件事返回**受理回执**
    `{type:"accepted", clientId, sessionId, userMessageId, duplicate, queued}`（不带 turnId）。
    回合开始前的结果（排队、SESSION_BUSY、校验错误）只在 POST 上。
  - 回合开始后，POST 和订阅流送来的事件进同一个处理函数、渲染进同一个气泡，按 eventSeq 去重。
    POST 的渲染保留作订阅流断开时的兜底（3.5）；订阅流重连后由 hub 回放或 `resync` + `since_rev` 补齐。
- **乐观状态**：发送时插入用户气泡（id = client_id），变更流或快照里出现这个 client_id 后不再追加。

#### 3.5 删除清单（2026-10-23 逐项核对的结果）

已删：`pages/chat/stopReceipt.ts`（Stop 回执走记录变更流）；`updateMessages` 的"重发"规则 b 和
"已确认同文就丢流式副本"（R6）；续接回合的数字 id 占位（ChatPage `continuationHostId`：续接直接渲染
进放着提问/审批的那个气泡），以及开始回合时按"纯数字 id"删占位的分支。

仍在用，不删（每项的理由）：
- `hostedTurn` / `hostForInjectedTurn` / `turnStart`：这就是"续接、CLI 自开回合并进前一个气泡"的实现。
- `sessionTurnRouter` 的 POST 渲染与 shadow：订阅流断开时的兜底（断线场景靠它）。
- `_mergePreservingInteractive`：回合结束对账读到的库可能早于答案/决定写入，本地刚做的答案要带过去。
- `drainSyntheticId`：排队消息送出时标 sent 走 `session_pending` 的原生 SQL，不经过变更流，排队气泡
  仍由它退场。
- `shouldResurface*`：现在由推送的 `session_state` 立即触发，轮询兜底，同一套判定。
- `_confirmed`、`collapseAdjacentDuplicateBlocks`：流式副本与确认块的配对，已收窄到只处理流式副本。
- `isStalePlaceholder`：插件命令等其他路径仍用数字 id。

#### 3.7 实际落地的形状（2026-10-23）

和 3.1–3.4 的草案相比，落地时做了这些取舍（理由都是"不造第二个来源"）：

- **rev 由触发器维护**（schema v15）：`session_revs(session_id, rev, deleted_rev)` 和
  `transcript_revs(row_id, session_id, kind, rev)`。messages 的插入、改 content/sent/metadata、删除，
  compactions 的插入、改 summary，都在同一个事务里由触发器加 rev——任何写入路径都绕不过。
  不在 messages 上加列：插入触发器里再 UPDATE 会把 FTS 触发器多跑一遍。实测每次写 +0.04 ms。
- **两类内容两条路**：回合自己的内容仍走回合事件（TurnWriter 写库时关掉通知）；回合之外写进记录的
  一切——用户消息、审批决定、提问答案、Stop 回执和停下回合的产物、后台 agent 结果、上下文压缩、
  删除——由数据库层在提交后通知 `core/transcript_feed`，作为 `transcript_row` / `transcript_delete`
  发布到会话事件里（不属于任何回合，带 rev）。前端 `hooks/transcriptFeed.planTranscriptChange`
  把它变成对 store 的操作：有 id 的块（审批、提问、工具结果）只改状态，其余按回合归属并进气泡。
  所以 Stop 回执不再走 `/chat/stop` 的返回值（`stopReceipt.ts` 删除），所有 tab 一条路。
- **快照的 rev**：历史接口返回 `X-Transcript-Rev`；`GET /sessions/{id}/transcript?since_rev=R`
  返回 R 之后的变更和 `reset`。前端 resync 先问它：记录没变就不重载。
- **一个渲染目标**：发送的回合在它第一个 turn_start（POST 上的或订阅流上的）处开始，一律走订阅流的
  开始逻辑（续接、CLI 自开回合并进前一个气泡），POST 和订阅流送来的事件进同一个处理函数（R1）。
  POST 自己的数字占位不再承载内容。
- **不再按文字去重已确认的块**（R6）：每个信封只到一次（eventSeq），记录里一个块写了两次就是两次。
  只去掉"流式副本挨着它重复的块"（`collapseAdjacentDuplicateBlocks` 只在一方未确认时生效）。
  查过线上库：没有一次"累积重发"（0/570 对），379 次完全重复全是 API 报错的重试。
- **提问存库**（R2）：`core/question_prompt`，和审批块同一套。
- **历史合并**：两条不同回合的行之间有压缩时，气泡在分隔线处断开（R9）。

#### 3.8 硬崩溃（SIGKILL）不丢已显示的内容（2026-10-23）

场景 s70–s79：进程在回合中途被直接杀掉，finally 不执行，杀掉之后的写库和发布全部丢弃。跑出两处：

- 工具结果原来留在内存里，等下一条助手消息或回合结束才写——被杀就丢。现在结果一到就写：追加到这个
  回合最新的一行（放着它对应工具调用的那一行），一条语句，立即持久。
- 排队送出的消息原来要等 `result` 才标为已送达；在那之前历史里看不到这些用户消息，被杀后回答在、
  问题不在，重启后还会再送一次。现在模型开始回答（第一条有内容的助手消息写库）时就标为已送达；
  在那之前失败仍然回滚、稍后重送。

#### 3.9 rev 的两个用法（2026-10-23 对照设计复审后修）

- **历史 ETag = rev**。原来是"行数 + 压缩签名 + 等待中的审批 + 页形状"：原地更新（后台 agent 结果、
  决定、答案）不改行数，前端内存里按会话缓存的旧 body 收到 304，重新打开 tab 时 agent 仍显示 running。
  现在 `"<session>:r<rev>:<等待中的审批>:<页形状>"`；rev 读不出来时不给 ETag。
  `tests/test_history_etag_rev.py`（真实库）：原地更新后旧 ETag 拿到 200 和新内容。
- **tab 的 `transcriptRev` 只来自读库**。原来每条变更事件都把它推高。回合自己的行先写库后发布、不带
  rev，变更事件可能先于一条 rev 更低、tab 还没看到的回合行到达；若此时断线、回合也已结束，下一次
  `since_rev` 从更高的 rev 问起，就跳过了那一行。现在只有 resync 的重读和 `since_rev` 更新它
  （`sessionTurnWiring.test.ts`）。
- **快照里的消息带 `row_revs`**（修掉了上一版记的残留）。原来：一条回合外的写入落在"读 rev"和"读行"
  之间，在快照里已被并进某个气泡（合并后的 id 是第一行的 id）；它的变更事件若晚于这次读库才到 tab，
  `planTranscriptChange` 按行 id 找不到它，没有身份的块（Stop 回执、turn_notice）会再追加一次。
  - 历史接口读每行时在**同一条 SQL** 里读出它的 rev（`list_by_session*(with_rev=True)` 的子查询列
    `_rev`），所以记下的 rev 恰好是返回内容的版本；合并时每条消息带 `row_revs: {行 id: rev}`。
  - 前端：变更的行在某条消息的 `rowRevs` 里、且 rev 不高于记下的，就跳过。比"整份快照一个 rev"
    更准：回合结束对账只读末尾 50 行，窗口外老行的改动 rev 也可能不高于快照 rev，按整份快照跳过会丢掉
    它；按行判断只跳过快照里真的有的行。所有读历史的路径都经过同一个合并函数，不需要每个调用点传 rev。
  - 本地内容胜出的合并（persist-lag 保护：本地更长就保留本地内容）不带 DB 的 `rowRevs`——那份行列表
    不描述本地内容；`mergeOlderMessages` 拼接分页边界时合并两边的 `rowRevs`。
  - 测试：`test_history_etag_rev.py`（真实库：合并气泡列出每行和 rev；不传 `with_rev` 的读取没有
    `_rev`）、`transcriptFeed.rowRevs.test.ts`、`MessageStore.test.ts`；liveReload fixture 现在带
    `row_revs`，80 个场景照样等于刷新。

#### 3.6 一个回合只有一个写入者（2026-10-22 已落地，P2 的写入点）

R10 暴露的不是"某条路径少了 finally"，而是一个回合写什么、何时写完，分散在四条路径里各管各的
（`run_conversation`、两种续接、CLI 自开回合各有一份待写列表，只有一条在结束时补写）。现在：

- **`core/turn_writer.TurnWriter`**：每个回合一个。回合的每个事件发布前都经过 `write()` 写库；
  `close()` 写完回合还攥着的东西（等下一条助手消息一起写的工具结果）。
- **由 `_published_turn` 关闭**：它是每个回合都必经的包装，`writer` 是它的必填参数；在 finally 里、
  发布 `turn_end` 之前 `close()`。所以不管回合怎么结束（结果、Stop、出错、中止、关机时被取消），
  tab 收到 `turn_end` 去读库时，看到过的块都已在库里。
- **回合外写进回合的内容排在后面**：回合运行期间 writer 登记在 `turn_writer` 模块里（`running()`）；Stop 回执写库前先
  `flush()` 这个回合的 writer，库里的顺序和屏幕一致。write / flush / close 共用一把锁（Stop 请求和
  回合并发）。
- **守护测试**（`tests/test_turn_writer.py`）：`core/` 里除 TurnWriter 外没有任何地方直接调
  `_persist_stream_event`；每个 `_published_turn` 调用都传了 writer。新增回合路径绕不过它。
- **rev**：最终由触发器分配（3.7），不在 TurnWriter 里；TurnWriter 写库时关掉记录变更流，回合的行由
  回合事件送达。

### S2 发送请求表（解 RC2）

v1 只在 messages 上按 (session_id, client_id) 查，挡不住"首次发送断线后重试新建 session"。

#### 2.1 表和入口（2026-10-22 已落地：schema v14、`core/send_requests.py`）

- **表 `send_requests`**：`client_id`（主键）、`session_id`、`status`（`reserved | persisted | failed`）、
  `owner`（占位的进程）、`user_message_id`、`created_at`、`updated_at`。
- **只记别处没有的事实**：这次发送被接下了、属于哪个会话、它的用户行是哪条。v2 草案里的
  `queued / running / done`、`pending_seq`、`turn_id` 不放进来——排队与否是用户行自己的
  `sent / pending_seq`，回合在不在跑是会话事件中心的事；放进来就成了第二个来源，要和原处同步。
  回执需要时去原处读。
- **先占位再做事**：`_run_conversation_impl` 在建 unit、领取预热进程、存会话、写用户行**之前**
  `INSERT OR IGNORE` 占位（首次发送的 session id 也在这之前定下）。
  - 占到：照常走；写下用户行后记 `persisted`。没写成用户行（出错，或请求在那之前被取消）就记
    `failed`，同一个 client_id 再来会重新执行。
  - 已存在：不建会话、不写用户行、不排队，只回一个 `accepted` 回执
    `{duplicate:true, clientId, sessionId, status, userMessageId, queued}`，`queued` 读用户行。
    回执只在 POST 上，不发布（它不属于任何回合）。
  - 另一个进程（已经退出的 daemon）占了位却没写用户行：接管。写了用户行的永不接管。
  - 账本读写失败：放行（照旧执行，记 ERROR）。账本坏了不能让聊天停。
- **client_id 来源**：桌面端由前端生成（已有）；排队送出不占位（行已存在）；**频道用平台消息 id**
  （`channel_client_id(类型, 渠道, 会话, 平台消息 id)`，Slack 的 ts 只在频道内唯一，所以带上
  渠道和会话），gateway 传入。gateway 收到重复回执就不回复，并撤回这次投递已发出的"看一下"。
  平台没给消息 id 的，每次投递仍是一次新发送。
- **清理**：和消息同一个 90 天 TTL，由 lifecycle 的定期清理删掉。
- **开销**：每次发送多一次占位插入和一次状态更新，实测 2.4 ms（测试库）。

#### 2.2 受理回执（2026-10-22 已落地）

- 每个占到位的发送，用户行写进库后，POST 上第一条回复就是 `accepted`
  `{duplicate:false, clientId, sessionId, userMessageId, queued:false}`（在 session_start 之前、
  不发布）。重复发送的回执同形，`duplicate:true`。
- 从回执起消息归服务端：之后不管是 SESSION_BUSY、QUEUE_TIMEOUT 还是流断了，前端都不删也不重发
  这条用户气泡。SESSION_BUSY 只去掉这次发送的助手占位（回合是之后的排队送出），等订阅流送来。
  只有服务端没能保存（没有回执、只有 `retryPayload`）时，才按旧逻辑本地重排、去掉气泡。
- 重复发送的回执说 `queued:true` 时，tab 进入"等待送出"状态。
- 这修掉了 R7（发送 tab 的 12/40 → 0）。

#### 2.3 一条消息一个 client_id（2026-10-23 已落地）

- client_id 只在 `services/clientId.newClientId()` 生成，在消息产生时生成一次：直接发送时、排队时。
  排队消息合并新输入时沿用。这条消息的每次发送都用它：断线重发、排队送出、送出失败后恢复再送、
  服务端交还后重发。守护测试扫描源码：别处不得生成，ChatPage 发出的 client_id 只能来自这三处。
- **服务端交还**（行写了但没能标成待发送）：删掉那条从未送出的行、释放账本（`release`），
  `retryPayload` 带上 client_id。客户端用同一个 client_id 重发，只执行一次。
- 更正：此前写"QUEUE_TIMEOUT 的重试按钮会在原消息已保留时生成新 client_id"不对——按钮只在
  服务端没能保留（有 `retryPayload`）时出现；问题是那时行已写下，换新 client_id 重发会多一条。

#### 2.4 不做的

- **已有重复记录的清理**：不做（2026-10-23 XG 决定：不动历史数据，只保证新数据不重复）。
- messages 表加独立的 `client_id` 列：不做（账本已经按 client_id 查）。

### S3 会话状态只有一个来源（解 RC3）

**2026-10-23 已落地（精简版）**：`core/session_state.session_state_entry(unit)` 是唯一的生成函数，
`/sessions/streaming-state` 和推送都用它。unit 每次状态变化，router 把这份状态作为 `session_state`
发布到会话事件里（不属于任何回合；按发起顺序，较早的读晚到就丢弃）。前端收到后，用和轮询同一套
判定逻辑立即执行（合并进上一次全量读取的结果），不再等下一次轮询。轮询保留作订阅流断开时的兜底。
订阅流每次连上（首次、重连、resync 之后）也给它跟踪的每个会话发一份 `session_state` 快照
（`snapshot: true`，不进缓冲区，`eventSeq` 是 tab 此刻已有的位置），同一个生成函数；没有 unit 的会话
不发（轮询结果里也没有它）。前端还没做过一次全量读取时不用单个会话的快照（缺的会话会被当成空闲）。

不新造状态源，**把现有 streaming-state 的快照变成唯一来源**：

- 订阅流在 `events_hello` 和 `resync` 里带上每个会话的完整状态快照（就是现在 streaming-state 返回
  的那份），状态变化时发 `session_state` 增量。
- 前端的转圈、"在等你批准…"提示、卡片能不能点，只看这份状态。
- `/sessions/streaming-state` 保留，只用于订阅流断开时；它和推送的快照由同一个函数生成。

### S4 防护做成测试（解 RC4）；S5 日志巡检（解 RC5）

**2026-10-23 已落地**：

- `tests/test_guard_contracts.py`：后端源码里任何带 `input=` 的调用，如果参数里有 `--stdin` / `--batch`
  就失败（`git hash-object` 先读完再写，按名字放行）；前端 `desktop/src` 发出的每个请求头都要在 CORS
  `allow_headers` 里，读取的每个响应头都要在 `expose_headers` 里。写这条测试时发现 `X-Transcript-Rev`
  没有暴露（webview 读不到），已补上；resync 现在把重读那一刻的 rev 记到 tab 上，下一次 resync
  记录没变就不重读。
- 每日巡检：`core/log_patrol`（纯逻辑）+ 系统任务 `log-patrol`（工作日 UTC 06:00）。读 daemon 日志和
  uvicorn 访问日志自上次以来新增的部分，WARNING/ERROR 按特征归类（id、数字、路径、引号内容遮掉），
  4xx/5xx 按路由归类，gate 超时（放行）按 gate 计数；和前 7 次按每天的量比，新类别、翻倍且 ≥10/天、
  某个 gate 一天超时 ≥20 次，发一条 Slack。第一次只记基线。

原计划：

- 扫描测试：`subprocess.run(... input=` 用于 `--stdin` 类命令即失败，指向 `utils/stdin_file`。
- gate 超时放行统一计数（`hook_timing` 已有），一天超过阈值进巡检报告。
- CORS 契约测试：前端 `services/*.ts` 设置的请求头都必须在 `allow_headers` 里。
- 每日巡检：WARNING/ERROR 和 uvicorn 非 2xx 按特征归类，和前 7 天比，新类别或数量翻倍就通知。

## 4. 验收与 P0 测试

**测试先行（P0）**，测试本身就是这份设计的验收标准。现有属性测试的不足（审查指出）：

- 后端模拟 `run_conversation`，没有真正跑它；不用真实 SQLite；
- 只有 40 个固定场景，不覆盖 SESSION_BUSY / 排队送出 / 合并送出、频道、双 tab、首次绑定 session、
  断线 / epoch / 溢出、Stop / error / notice、上下文压缩、分页、重启；
- 前端关掉了定时器，不测轮询和对账镜像；
- 比较器忽略 tool_result 内容和大部分字段；`KNOWN` 允许差异，所以"绿"不等于相等。

P0 补齐：

1. **后端真实路径**：用真实 SQLite（临时库、真实迁移）跑真实的 `run_conversation`，只把
   `SessionUnit` 换成按脚本吐事件的假 CLI。覆盖：普通回合、SESSION_BUSY 排队与送出、合并送出、
   频道回合、Stop（含停在审批上）、上下文压缩、问题与审批续接、CLI 自开回合。
2. **重复发送**：同一 client_id 发两次（同一 session、首次发送前没有 session、排队中、运行中、
   已完成），断言库里用户行恰好一条。（2026-10-22 时失败，P1 后通过。）
3. **分页**：长会话按回合翻页，所有页拼起来 = 整页历史。
4. **前端**：在现有属性测试上加双 tab（同一会话两个 tab 都要等于刷新）、订阅流断开与重连、
   轮询打开（假定时器推进）；比较器加上 tool_result 内容和交互块的完整字段。
5. 新增性质：**每条用户消息在库里恰好一条**；**交互块不丢**（每个提问 / 审批在实时、对账后、
   刷新后都在，状态一致）。

P0 跑出的所有不一致都列进 `KNOWN`，按根因标注。之后每个阶段只许让它变短；S1 完成 = `KNOWN`
为空。

## 5. 实施顺序

| 阶段 | 内容 | 依赖 | 规模 | 风险 |
|---|---|---|---|---|
| P0 | 第 4 节的测试 | 无 | 中 | 无（只加测试） |
| P1 | S2：`send_requests` 表与占位（已做）、频道传平台消息 id（已做）、受理回执（已做）、一条消息一个 client_id（已做）；历史重复记录不清理（2.4） | P0 | 中 | 中（发送入口） |
| P2 | S1 后端：rev（已做，触发器）、提问存库（已做）、记录变更流（已做）、`since_rev`（已做）、按回合边界合并（已做，压缩处）、历史 ETag 改用 rev（已做，3.9）；分页：不需要带 R，已用测试证明（见 3.1）；规范化事件文件：已做（见 3.2） | P0 | 大 | 中（写库路径、迁移） |
| P3 | S3：状态变化推送 + 连上时的快照（已做，同一个生成函数；轮询保留作兜底） | P1 | 中 | 中 |
| P4 | S1 前端：变更流投影（已做）、一个渲染目标（已做）、不按文字去重（已做）、tab 的 rev 只来自读库（已做，3.9）；删除清单的核对结果见 3.5 | P2、P3 | 大 | 高：分多个提交，每个提交 `KNOWN` 不变长 |
| P6 | S4 防护、S5 巡检（已做） | 无，可并行 | 小 | 低 |

P4 完成前，聊天渲染不加新功能；发现新的显示问题，先写成测试场景，再决定放进哪个阶段。

## 6. 不做什么

- 不把 `session_events` 改成持久化：它仍是内存里的实时通道，库是唯一记录，rev 衔接两者。
- 不改多 tab 隔离、会话身份、Stop 语义等已有不变量（steering 文件里的规则照旧）。
- 不在这次范围：pipeline v1 的问题、模型行为（#12 这类）。

## 7. 待定

1. ~~超长回合超过 hub 的活跃回合上限（20,000 条）时，JOIN 回放不完整~~。2026-10-23：到上限时先丢掉
   已经被 `assistant` 信封包含的流式 delta（`session_events._compact_active`），回放仍从 turn_start 起
   完整；在 liveReload fixture 上把所有这类 delta 删掉，80/80 照样等于刷新。只有一个回合里非 delta 的信封
   就超过上限时才会缺中间一段（记一条 WARNING，巡检会看到；记录在库里）。压缩一次至少要腾出 10%，否则
   等 2,000 条后再试，发布成本实测 2.2 µs/条，和不压缩时相同。
2. Hive / 远程部署下订阅流的断线频率和 `since_rev` 的负担，需要在真实网络上测（本机做不了，留着）。
   `since_rev` 本身只读 `transcript_revs` 里 rev 之后的行，断线后读到的是断开期间的变更，不是整页。
3. ~~频道各平台消息 id 的格式和唯一性~~。2026-10-23 核对：现在只有 Slack 适配器
   （`channels/adapters/slack.py`），它把消息的 `ts` 作为 `external_message_id`；`ts` 只在一个频道内唯一，
   `send_requests.channel_client_id` 用"频道类型 + 频道 + 会话（Slack 频道 id）+ ts"组成 client_id，所以
   唯一。Slack 的重投（Socket Mode 重试、`message` 和 `app_mention` 各来一次）是同一个 `ts`，按设计成为
   同一次发送。适配器对"先来非 @、后来 @"会再交出一次（防止漏掉 @）：第一次没被处理（提及模式下被闸门
   挡下，没进账本）时第二次正常处理；第一次已经被处理（DM、always 模式、已跟进的线程）时第二次在账本里
   是重复，不再回复——这正是以前会回两次的情况。以后加新平台，要在这里写清它的 id 在什么范围内唯一。

## 8. 修订记录

- v1（2026-10-22 66d9868a）：初稿。
- v2（2026-10-22）：按独立审查修订。事件水位 → 库内事务分配的 rev；"POST 永不渲染" → 受理回执 +
  回合开始后只由订阅流渲染 + `since_rev` 补增量；messages 查重 → `send_requests` 请求表（覆盖首次
  建 session 和频道）；补规范化事件格式与一致性用例；写入路径补全并分成三类；分页沿用首屏 R；S3 改为
  现有 streaming-state 快照随订阅流推送；测试提前为 P0。修正 v1 的事实错误：频道和排队送出有服务端
  生成的 client_id（只是每次不同）；现在是订阅流为主、POST 兜底，不是两边都渲染；历史接口的合并
  只按相邻角色，不识别回合；"内容相同 + 2 秒内"的清理办法不安全。
- v3（2026-10-23）：实现完成后对照复审，文档同步到实现（3.1、3.4、3.6、2.4、第 5 节）；修了两处偏差
  带来的 bug 并记在 3.9：历史 ETag 改用 rev；tab 的 rev 只来自读库。

## 9. P0 结果（2026-10-22）

**测试**

- `backend/tests/test_transcript_real_path.py`：真实 SQLite + 真实 `run_conversation`。发送幂等的 4 条
  （同一 client_id 再发、SESSION_BUSY 后重发、建 session 前重试、频道重试）今天都失败，以
  `xfail(strict=True)` 作为 P1 的验收。
- `backend/tests/test_live_reload_scenarios.py`（重写）：40 个场景全部走真实后端和真实库——用户回合走
  `run_conversation`，SESSION_BUSY 排队后由真实 `drain_pending` 合并送出，CLI 自开回合走
  `run_injected_turn`，续接走 `continue_with_answer` / `continue_with_cmd_permission`，Stop 走
  `_persist_stop_receipt`，上下文压缩写真实的 compaction 记录，刷新走真实历史接口。
- `liveReload.property.test.ts`：发送的 tab 按 ChatPage 的方式开 POST（用户气泡 + 占位气泡，续接的数字
  占位，POST 收到同样的信封，回合结束时完成）；SESSION_BUSY 的回复走真实的流处理；Stop 回执按
  `applyStopReceipt` 写入；另一个 tab 只收会话事件。
- 断线与重启（场景 s40–s69，`test_fixture_is_current` 断言每种情况都在）：
  - 订阅断开，同一回合内重连，或回合结束后才重连。重连时，真实的 `SessionEventHub.subscribe`
    按窗口的 cursor 回答：缓冲区里有，就补发缺的事件；缓冲区太短（一半场景用 4 条的缓冲区），
    就发 `resync`，回合进行中再补发整个回合，前端随后读一次库，读到的是那一刻的库；
  - daemon 在回合中途（偏向刚收到工具结果之后）或两个回合之间重启：回合的任务被取消，换新的
    hub（新 epoch）和新 router；发送中的 POST 以网络错误结束，旧 cursor 得到 `resync`。
- 新增一条贯穿全程的性质：任一 tab 曾经显示过的带 id 的块（工具步骤、结果、提问、审批、后台
  通知、Stop 回执），刷新后必须还在（"shown live, gone on reload"）。

**根因清单**（全部已修，`KNOWN` 为空：任何新分歧都让测试失败）

| 根因 | 现象 | 怎么修的 |
|---|---|---|
| R1 | 发送 tab 的续接回合单独成气泡 | 一个渲染目标（3.7） |
| R2 | 提问和答案刷新后消失 | 提问存库（`core/question_prompt`） |
| R4 | 另一个 tab 看不到用户消息 | 记录变更流（`transcript_row`） |
| R5 | 审批决定不同步到其他 tab | 同上 |
| R6 | 回合里写了两遍的块实时只显示一遍 | 不按文字去重已确认的块 |
| R7 | SESSION_BUSY 删掉排队气泡 | 受理回执（S2 2.2） |
| R8 | Stop 回执只有按 Stop 的 tab 看得到 | 记录变更流 |
| R9 | 压缩分隔线实时不显示；压缩后的 CLI 回合排错位置 | 记录变更流 + 历史合并在分隔线处断开 |
| R10 | 回合在工具结果后结束，结果丢了 | 每个回合一个 TurnWriter（3.6） |

R3（共用气泡里跨回合去重）已在 23697bf8 修掉。

**过程中发现并修掉的**：审批决定在写库前刷新显示成"已失效"（95ff04da）；P0 测试的运行顺序依赖
（e050c23a）；生成器的记录 id 在场景之间重复，覆盖了库里的旧记录，曾被误看成"历史接口丢消息"
（生成器问题，不是产品问题）。

断线、resync、重启本身没有带来新的分歧：重连补发按 seq 去重，没有重复；resync 后读库，tab
回到库的状态。它们暴露的是 R10（已由 3.6 修掉）。

**仍未覆盖**：~~轮询打开~~（2026-10-23 已加 POLLED 模式：真实的对账循环在假定时器下运行，80/80；
它能抓到问题——harness 的库视图一度比真实库旧，s18 的回合结束对账就把实时显示的工具步骤冲掉了）、
~~首次建 session 的绑定~~（`test_transcript_real_path` 的"建 session 前重试"已通过）、多个频道（现在只有
Slack 适配器，见 7.3）、~~SIGKILL 式的硬崩溃~~（3.8）。
