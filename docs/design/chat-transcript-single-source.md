---
title: 会话内容唯一来源 — 结束聊天显示的打地鼠
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: DESIGN v2 — 已按独立审查（e7e27e355733defb）修订；P0 进行中
related: streaming-spinner-ssot.md (2026-06-30，同类的 spinner 修复), liveReload.property.test.ts (5109df87)
---

# 会话内容唯一来源 — 结束聊天显示的打地鼠

## 0. 一句话

一个会话的内容现在有**两种存法**（实时事件、库里的记录）和**十几条写法**（前端多条路径各写各的，
每条带自己的合并规则）。今天修掉的聊天问题全是这些写法之间的分歧。修法：**库是唯一记录，每次
写都在库里分配一个会话内递增的版本号 `rev`；事件带上它产生的那条记录的 `rev`；屏幕 = 快照
（到 rev R）+ 之后的事件；发送用一张请求表做幂等；会话状态只有一个快照来源。** 先写测试（P0），
再改。

## 1. 问题清单（2026-10-22 一天碰到的，全部查证过）

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 1 | CLI 自开回合停在审批上，卡片不显示，session 等了 18 分钟 | RC1 | 已补丁 1166f6d2 |
| 2 | 提问卡片和答案刷新后消失（R2） | RC1 | 未修 |
| 3 | 并行后台 agent：实时 N 个气泡，刷新后 1 个 | RC1 | 已补丁 cc2ceef6 |
| 4 | 续接回合单独成气泡，对账后内容显示两遍（R1） | RC1 | 已补丁 23697bf8 |
| 5 | 共用气泡里跨回合的相同文本被去重删掉（R3，#3 的补丁引入） | RC1 | 已补丁 23697bf8 |
| 6 | 已失效的审批卡片仍显示可点按钮 | RC1 | 已补丁 1166f6d2 |
| 7 | 消息排在审批后面，界面不说原因 | RC3 | 已补丁 a30d89f6 |
| 8 | SESSION_BUSY 碰上静默重试，同一条消息存两次（10-15 起 19 对） | RC2 | 已补丁 cc2ceef6 |
| 9 | 恢复后重发、断线重连、排队送出、首次发送断线后重试，是否重复未查 | RC2 | 未查 |
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

"已补丁"= 在现有机制上加了一条特殊规则，根因还在。#1–#9 是本设计要从根上消掉的。

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

v2：**版本号在库里分配，和写入同一个事务。**

- 每个会话一个计数 `session_revs(session_id, rev)`。messages 的每次插入、每次更新（审批决定、
  提问答案、后台 agent 结果）都在同一个事务里 `rev += 1`，并把新 rev 写进这条记录的 `rev` 列。
- 事件发布时带上它产生或改动的那条记录的 `rev`（`_persist_stream_event` 返回 rev，发布时附上）。
  不产生记录的事件（text_delta、thinking_delta）带"当前已提交的最大 rev"，只当临时显示。
- 历史接口在**一个读事务里**取记录和 `max(rev)`，返回 `X-Transcript-Rev: R`。快照里有且只有
  `rev ≤ R` 的改动。
- 前端：快照到 R，之后只应用 `rev > R` 的事件。因为写库总在发布之前，`rev ≤ R` 的事件一定已经
  在快照里，不会重复；`rev > R` 的事件一定还没在快照里，不会漏。
- 不经过发布的写入（Stop 回执、事后更新）也加 rev，并补发一个 `transcript_rev` 事件
  （只带 rev 和记录 id），前端收到就知道快照落后了，去拉增量。
- **增量接口** `GET /sessions/{id}/messages?since_rev=R`：返回 `rev > R` 的记录（插入和更新都在
  内）。重连、epoch 变化、缓冲溢出、`resync`，统一用它补，不再整页重拉。
- **分页**：翻页请求带上首屏的 `R`，只返回 `rev ≤ R` 的旧记录；分页永远不前移 R。

#### 3.2 规范化事件格式（Python / TS 共用的输入）

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
- **POST 和订阅流的分工**（替换 v1 的"POST 永不渲染"）：
  - POST 第一件事返回**受理回执** `{type:"accepted", clientId, sessionId, turnId | null, queued}`。
    回合开始前的结果（排队、SESSION_BUSY、校验错误）只在 POST 上，前端只用来更新会话状态和界面提示。
  - 回合开始后，内容只由订阅流渲染。新 tab 拿到回执里的 sessionId 后立即订阅，订阅流的 JOIN 会
    从 turn_start 回放当前回合（已有）。
  - 订阅流断开时不由 POST 兜底渲染，而是：断开期间转圈照旧，重连后 `since_rev` 补增量，再接上
    实时事件。断开太久（重连失败）时退回按 rev 轮询增量。
- **乐观状态只有一种**：发送时插入用户气泡（id = client_id），快照或 `turn_start` 里出现这个
  client_id 后由投影接管。

#### 3.5 删除清单（P4 完成时这些都不应存在）

`_mergePreservingInteractive`、`_INTERACTIVE_BLOCK_TYPES`、`isStalePlaceholder` 与数字 id 占位、
`hostedTurn` / `hostForInjectedTurn` / `turnStart`、轮询补卡片的分支与 `shouldResurface*`、
`drainSyntheticId`、`sessionTurnRouter` 的 POST 渲染和 shadow 部分、`_confirmed` 标记、
流式路径上的 `collapseAdjacentDuplicateBlocks`、前端生成的错误消息行。

### S2 发送请求表（解 RC2）

v1 只在 messages 上按 (session_id, client_id) 查，挡不住"首次发送断线后重试新建 session"。v2：

- **表 `send_requests`**：`client_id`（主键，全局唯一）、`session_id`、`status`
  （`reserved | persisted | queued | running | done | failed`）、`turn_id`、`pending_seq`、
  `user_message_id`、`created_at`、`updated_at`。
- **先占位再做事**：`run_conversation` 入口第一件事 `INSERT ... ON CONFLICT(client_id) DO NOTHING`。
  - 插入成功：正常流程，每推进一步更新 status（建 session → persisted → queued / running → done）。
  - 已存在：不建 session、不写用户行、不排队，直接返回已有结果的受理回执
    `{accepted, clientId, sessionId, turnId, queued, duplicate:true}`；正在运行的，前端按回执订阅即可。
- **client_id 来源**：桌面端由前端生成（已有）；排队送出沿用原消息的 client_id；**频道用平台消息
  id**（Slack `channel:ts`，其他频道各自的消息 id），gateway 传入，不再由 router 每次生成。
- **前端**：一个发送器，所有重发都调它、都用同一个 client_id；后端幂等，重试本身安全。
- **已有重复记录的清理**：按"相同 session、相同 client_id、两条 user 行"列清单，核对各自的
  pending 状态和后面的助手输出，给你看过再在一个事务里删。不按"内容相同 + 时间接近"判断。
- messages 表加独立的 `client_id` 列（迁移时从 metadata 回填，坏掉的 JSON 跳过并记数），
  不在 `json_extract` 上建索引。

### S3 会话状态只有一个来源（解 RC3）

不新造状态源，**把现有 streaming-state 的快照变成唯一来源**：

- 订阅流在 `events_hello` 和 `resync` 里带上每个会话的完整状态快照（就是现在 streaming-state 返回
  的那份），状态变化时发 `session_state` 增量。
- 前端的转圈、"在等你批准…"提示、卡片能不能点，只看这份状态。
- `/sessions/streaming-state` 保留，只用于订阅流断开时；它和推送的快照由同一个函数生成。

### S4 防护做成测试（解 RC4）；S5 日志巡检（解 RC5）

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
   已完成），断言库里用户行恰好一条。今天这条测试会失败，P1 让它通过。
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
| P1 | S2：`send_requests` 表、受理回执、频道传平台消息 id、前端统一发送器、重复记录清单 | P0 | 中 | 中（发送入口） |
| P2 | S1 后端：rev 与 `session_revs`、规范化事件、提问卡片存库（R2）、Stop / 压缩 / 事后更新带 rev、`since_rev` 增量、分页带 R、按回合边界合并 | P0 | 大 | 中（写库路径、迁移） |
| P3 | S3：状态快照随订阅流推送，前端改看它 | P1 | 中 | 中 |
| P4 | S1 前端：投影器、快照 + rev 之后的事件、三类分开存、POST 只回回执；按 3.5 逐项删除 | P2、P3 | 大 | 高：分多个提交，每个提交 `KNOWN` 不变长 |
| P6 | S4 防护、S5 巡检 | 无，可并行 | 小 | 低 |

P4 完成前，聊天渲染不加新功能；发现新的显示问题，先写成测试场景，再决定放进哪个阶段。

## 6. 不做什么

- 不把 `session_events` 改成持久化：它仍是内存里的实时通道，库是唯一记录，rev 衔接两者。
- 不改多 tab 隔离、会话身份、Stop 语义等已有不变量（steering 文件里的规则照旧）。
- 不在这次范围：pipeline v1 的问题、模型行为（#12 这类）。

## 7. 待定

1. 超长回合超过 hub 的活跃回合上限（20,000 条）时，JOIN 回放不完整；有了 `since_rev`，已落库的
   部分可以补，未落库的 delta 丢了只影响临时显示。P2 里确认没有别的缺口。
2. Hive / 远程部署下订阅流的断线频率和 `since_rev` 的负担，需要在真实网络上测。
3. 频道各平台消息 id 的格式和唯一性，P1 里逐个确认。

## 8. 修订记录

- v1（2026-10-22 66d9868a）：初稿。
- v2（2026-10-22）：按独立审查修订。事件水位 → 库内事务分配的 rev；"POST 永不渲染" → 受理回执 +
  回合开始后只由订阅流渲染 + `since_rev` 补增量；messages 查重 → `send_requests` 请求表（覆盖首次
  建 session 和频道）；补规范化事件格式与一致性用例；写入路径补全并分成三类；分页沿用首屏 R；S3 改为
  现有 streaming-state 快照随订阅流推送；测试提前为 P0。修正 v1 的事实错误：频道和排队送出有服务端
  生成的 client_id（只是每次不同）；现在是订阅流为主、POST 兜底，不是两边都渲染；历史接口的合并
  只按相邻角色，不识别回合；"内容相同 + 2 秒内"的清理办法不安全。

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

**根因清单**（`KNOWN` 里按此分组；修好一个就删一组，测试守住它）

| 根因 | 现象 | 场景 | 由谁解决 |
|---|---|---|---|
| R1 | 发送 tab 的续接回合由它自己的 POST 渲染进数字占位，单独成气泡（只在流式期间，对账后消失） | 19/40 | S1 / P4（只由订阅流渲染） |
| R2 | 提问卡片和答案不存库，刷新后消失 | 7/40 | P2 |
| R4 | 用户消息不是会话事件：另一个 tab 对账前没有用户气泡 | 40/40 | S1 |
| R5 | 一个 tab 的审批决定不发布：另一个 tab 对账前显示未决定 | 17/40 | S1 |
| R6 | 一个回合里重复的相同文本 / 思考，实时只显示一次（当成重复发送），刷新后两次 | 5/40 | S1（投影只按回合内的块） |
| R7 | SESSION_BUSY 处理器删掉排队的用户气泡（按旧约定"后端删了这条"写的，后端现在保留为待发送），排队消息在对账前消失，送出的回合单独成气泡 | 12/40 | P1（受理回执）+ S1 |
| R8 | Stop 回执只写库不发布：只有按 Stop 的 tab 看得到 | 10/40 | S1（带 rev 并发 `transcript_rev`） |
| R9 | 上下文压缩行只在读历史时拼进去：实时从不显示分隔线（12/40）；拼接放在合并后的消息之后，压缩之后的 CLI 回合刷新后排在分隔线之前，对账对不上，内容显示两遍（2/40） | 14/40 | S1（压缩行是带 rev 的记录） |

R3（共用气泡里跨回合去重）已在 23697bf8 修掉。

**过程中发现并修掉的**：审批决定在写库前刷新显示成"已失效"（95ff04da）；P0 测试的运行顺序依赖
（e050c23a）；生成器的记录 id 在场景之间重复，覆盖了库里的旧记录，曾被误看成"历史接口丢消息"
（生成器问题，不是产品问题）。

**仍未覆盖**：订阅流断线与 resync、轮询打开、daemon 重启、首次建 session 的绑定、多个频道。
