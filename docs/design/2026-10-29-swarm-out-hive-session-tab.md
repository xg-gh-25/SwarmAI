---
title: "Swarm Out：Hive 会话 tab——在 App 里直接打开、实时看、照常交互一个派出去的 run"
date: 2026-10-29
created: 2026-10-29
updated: 2026-10-29
status: draft r3（r1 经 correctness + security 审查，r2 经 concurrency 审查；两轮 finding 按原因并入本版，见 §8。审查到此为止，余下由测试先红后绿验证）
project: SwarmAI
supersedes: docs/design/2026-10-27-swarm-out.md §5.8（远程 session 浮层）
tags: [swarm-out, hive, chat-tab, relay]
---

# Swarm Out：Hive 会话 tab

## 1. 要什么（XG 2026-10-29）

- 点 Swarm Out 里任意一个 run，在 App 里打开它在 Hive 上的那个会话：实时看 streaming，**照常交互**
  （回答问题、审批、补话、停止），和本机 chat tab 一样，不另做一套交互。
- 开成 **tab**，放在 Swarm Out tab **后面**，不和本机 chat tab 混在一起。
- **只有一个 Hive tab**：点不同的 run，都在这一个 tab 里切换（注意力）。
- **不影响现在 chat tab 的逻辑。**

替代 2026-10-27 设计 §5.8 的浮层：浮层要在 ChatPage 之外再搭一套 tab 状态和 streaming 生命周期；
开成 tab 复用 ChatPage。§5.8 假设的 `/api/conductor/runs/<id>/input` 不存在：今天的派发就是在 Hive
上开一个普通 chat 会话（`core/swarm_out.py:311` `_start_session` 调 Hive 的 `/api/chat/stream`）。

## 2. 现状（按代码核对）

- **Hive 就是一个 SwarmAI**，chat 接口与本机相同（`routers/chat.py`：`/stream` 389、
  `/answer-question` 607、`/events` 705、`/sessions/{id}/messages` 1082、`/stop/{id}` 1261、
  `/cmd-permission-continue` 1496）。
- **实时**：`/events?cursors={sid:-1}` 加入当前 turn 并从 `turn_start` 补发，断线按游标续上
  （`core/session_events.py` 模块头）。它推送**所有**会话的事件（`_Subscriber`，:143，不过滤）。
- **本机 hub 可以发布任意 sid**：`SessionEventHub.publish(session_id, event, turn_id=, turn_client_id=)`
  （:176）自己盖 `eventSeq`，按 `turn_start / turn_end` 维护活动 turn 的补发；`session_state` 快照来自一个
  可替换的 provider（`set_state_provider`，:86）。
- **Host 已能带凭据调 Hive**：`core/swarm_out.py:154` `_client(inst)`（Basic auth，域名经 `DOMAIN_RE`）。
  派发日志每行有 `dispatch_id / hive_id / session_id`（:180）；sid 是 Hive 报回来的（:340）。
- **Hive 数据进 Host 今天只有一个入口**：`_clean_ask`（:361）逐字段重建、带类型和长度上限。前端把某些
  事件当作"本机后端说的"：`ui_command.allowAbs`（`utils/uiCommands.ts:158-165`）、`file_changed`
  （lifecycle :3052-3110，会让 Canvas 自动打开文件）。
- **特殊 tab 的先例**：Swarm Out 是保留 id 的 chat tab（`pages/chat/swarmOut.ts`），`normalTabs()` 把它
  排除在 tab 条、上限（`useUnifiedTabState.ts:638`、`ChatPage.tsx:970`）、落地目标
  （`landPromptInTab.ts:128`）、Cmd+1-9（`ChatPage.tsx:1809`）之外。
- **chat 的请求都发往一个地址**：`getApiBaseUrl()`（`services/tauri.ts:58`）。

## 3. 关键决定：实时事件由本机后端中继，前端仍然只有一条连接

r1 让前端为 Hive 再开一条事件连接。审查表明这会改动 lifecycle 里四处 `isConnected()`
（:2602、:4371、:4501、:4525）、连接断开时对所有 tab 的 `onSubDisconnected` 扇出（:4884）、
状态轮询（:1270），而且 Hive 那条连接一抖，本机 tab 也会被切到降级渲染——正好违反"不影响本机 tab"。

所以改为：**本机后端订阅 Hive 的 `/events`，只取这个 sid 的信封，清洗后发布进本机自己的 hub**
（`hub().publish(sid, ev, turn_id=…, turn_client_id=…)`）。前端照旧只订阅本机的 `/api/chat/events`，
Hive 会话在它眼里就是又一个 sid：

- `sessionEvents`、`isConnected()`、连接扇出、`sessionTurnRouter` 全部**不改**。
- 会话状态：本机 `/sessions/streaming-state` 和 hub 的 `session_state` provider 在中继活着时加上这个
  sid 的条目（中继缓存的 Hive 状态）；中继不在时与今天相同。前端的状态轮询**不改**。
- Hive 上 `/events` 推所有会话：只有本机后端这一条上游连接，前端拿到的只是一个 sid。

HTTP 类请求（发送、回答、审批、停止、读消息……）是无状态的，仍在前端 service 层按 sid 换路径前缀
（§5），走同一个代理（§6）。

**中继要守的规则**（r2 审查后补全；每条对应 §10 的一个测试）：

1. **一个会话只有一套序号。** 前端的 turn 路由对 POST 流和订阅共用一个 `lastSeq`
   （`sessionTurnRouter.ts:191,204,254`）。经代理的三条 POST 流（`/stream`、`/answer-question`、
   `/cmd-permission-continue`）带的是 Hive 的 `eventSeq`，中继发布进本机 hub 的是本机盖的 seq。所以代理
   **从 POST 流里删掉 `eventSeq`**，也不转发其中的 `events_hello / resync`：POST 流只负责把发送绑到
   `turnId`（`isSeq` 不成立，不计数），渲染全部来自订阅。
2. **不重复发布。** 中继记 `(hive_epoch, hive_high_seq)`，同一 epoch 里 seq ≤ high 的上游信封丢弃；
   第一次连接用 JOIN（-1），之后重连只用自己记的游标。
3. **Hive 的 `resync / events_hello / session_state(snapshot)` 不原样转发**：它们的 seq 和
   `activeTurnId` 描述的是 Hive 的日志（`sessionEvents.ts:127` 会据此设游标，`sessionTurnRouter.ts:224`
   会据此设 `lastSeq`）。
4. **停中继、切 sid、Hive 换 epoch 时清掉本机这个 sid 的活动 turn。** hub 的 `_Log.active` 只在
   `turn_end` 时清，中继停在 turn 中间会留下一个永远不结束的 turn（再打开时 JOIN 补发残缺的 turn，
   `open_turn_id` 也一直非空）。hub 加 `reset_relayed(sid)`：清 `buf / active / claim / phase`，保留 `seq`。
   停中继是 `task.cancel()` → `await task` → `reset_relayed`。Hive 换 epoch 时先 `reset_relayed`，再用
   **本机的值**发布一个 `resync`（`eventSeq` = 本机 head，`activeTurnId` = null），tab 经代理从 DB 重载，
   然后接受新 epoch 的补发。不调 `drop_session`（它会用本机时钟 `set_activity`）。
5. **中继登记期间，这个 sid 永远有一条状态。** 本机 reconcile 轮询把"`streaming-state` 里没有这个 sid"
   当成"unit 没了"，直接置 idle、清队列（lifecycle :1291-1306）。所以中继从 `POST …/relay` 开始到停止，
   `streaming-state` 和 hub provider 都返回这个 sid 的条目：最后已知的 Hive 状态加 `relay_connected`。
   开中继时在 POST 返回前先读一次 Hive 的 `/sessions/streaming-state`（只取这个 sid）作初值；之后按
   reconcile 的节奏、以及每次重连时再读，Hive 那边没有这个 sid 就是 idle（Hive 的 `drop_session` 不发事件，
   只靠推送的状态会一直停在 streaming）。
6. **中继的 sid 不进本机时钟。** `set_activity / refresh_activity / drop_session` 不处理中继的 sid
   （它的 `activity_seq` 来自 Hive 的时钟，混进本机时钟后，时钟偏差会让 tab 把后来的 Hive 状态当旧的扔掉）。
7. **开 / 停串行、按窗口计数。** 每个 sid 一条上游任务；开 / 停在一把 `asyncio.Lock` 里做；每个任务带
   generation，发布前核对。按窗口（客户端 id）计引用，`DELETE` 只减计数，同时最多 4 个 sid（两个窗口各开
   一个 Hive tab 时互不影响）。
8. **白名单包括 turn 之外的事件**：`transcript_row`（`core/transcript_feed.py:50`，`turn_id=""`）、
   `background_agents`，否则 CLI 起的 turn 和 turn 外写的行要等重载才出现。
9. **上游只要这个会话**：Hive 的 `/events` 今天推所有会话。给 Hive 加一个可选的 `only=1`，只推 `cursors`
   里列出的会话（Hive 网页同样受益）；Hive 版本不支持时，中继照常过滤，只是多耗流量。

## 4. Hive tab：身份、位置、打开与切换、生命周期

- **身份**：tab id 是 `hive-session:<sid>`（每个 Hive 会话一个新 id），`isHiveTab(id)` 按前缀判断，写在
  `pages/chat/swarmOut.ts`。"只有一个"靠打开新的之前先关掉旧的来保证，不靠复用同一个 id——复用同一个 id 时，
  旧 tab 迟到的回调（POST 的 error / complete、重试计时器，lifecycle 里约 30 处 `tabMapRef.current.get(tabId)`）
  会落到新 tab 上。`normalTabs()` 同时排除 Swarm Out 和 Hive tab，于是 tab 条、本机上限、落地目标、
  Cmd+1-9 都不碰它。**"不能关""标题固定"仍只对 Swarm Out**（`useUnifiedTabState.ts:662/834`、
  `ChatPage.tsx:1226` 不改成泛化判断），Hive tab 可以关。
- **位置**：ChatHeader 里 Swarm Out 后面、同一分割线组。hive 图标，标题 `<Hive 名> · <run 标题>`；
  `updateTabTitle`（:721）同样保护它，不被会话标题覆盖。没有 "+"。
- **入口**：run 卡片标题区可点（运行、等你、结束的都行，只要有 `session_id`；`session_unknown` 不行），
  历史行也可点。
- **打开 / 切换 = 关掉旧的 Hive tab、开一个新的**：`openHiveSession(sid, title)`：
  1. 已经是这个 sid：只选中。
  2. 旧 Hive tab 的输入框有草稿：不换，toast "先发出或清空草稿"。
  3. 否则用 Hive tab 的关闭分支（下面）关掉旧的（abort 它的请求、销毁它的 store、清 `pendingStreamTabs`、
     停它的中继），再以 `hive-session:<新 sid>` 建一个全新的 tab（新的 turn state、空 store），通知后端为
     新 sid 开中继，选中、加载消息。
  不在原 tab 上重装的原因：在原 tab 上重装（`handleResumeSession` 的做法）会留下旧 turn 的绑定——`syncTurnStateSession`
  在旧 turn 未完成时不换 sid（`sessionTurnRouter.ts:123-131`），新会话的事件全被判 foreign；
  `initTabState` 也不清 `abortController / pendingQuestion / activity / _turnHandlers` 等
  （`useUnifiedTabState.ts:783-795`）。`handleResumeSession` 只复用空闲的活动 tab（`ChatPage.tsx:1466`），
  不是这里的先例。
- **切走不停 run**：Hive 上照跑。旧 sid 的迟到事件：旧 tab 已不存在；中继停时 `reset_relayed` 清了它在
  本机 hub 里的活动 turn（§3 规则 4），再切回来时从 DB 加载、从新的 JOIN 接上，不会补发残缺的 turn。
- **关闭**：Hive tab 走单独的分支：不弹"关闭正在 streaming 的 tab"确认（关掉不停 run），不调
  `releaseSession`（那是本机 CLI 的释放，`ChatPage.tsx:1201`），通知后端停中继。
- **不占本机槽位**：本机不起 CLI，`normalTabs` 已排除。
- **持久化与恢复**：`open_tabs.json` 存 `{id:'hive-session:<sid>', sessionId}`（`PersistedTab` 已有这两个
  字段）。`restoreFromFile` 像 `savedSwarmOut` 一样**显式**恢复它（`useUnifiedTabState.ts:895-913`
  先 `map.clear()` 再只恢复 `normalTabs`，不显式恢复就丢）。恢复后通知后端为这个 sid 起中继；后端说不是
  本机派出的会话（404）就删掉这个 tab。
- **本机后端重启**（`swarm:backend-recovered`，`ChatPage.tsx:756-800`）：对 Hive tab **不做**
  `resendTabOnRecovery`（消息可能已经经代理到了 Hive，再发会重复 turn）；**重新 `POST …/relay`**（幂等；新的
  daemon 没有中继，前端会收到本机 hub 正确的 `resync` 但之后没有事件）；merge 照常（读消息走代理）。

## 5. 前端传输层：只给 HTTP 请求换前缀

`services/remoteSession.ts`：

```ts
setRemote(sid | null)   // Hive tab 绑定 / 换掉 / 关闭时；同一时刻至多一个
isRemote(sid): boolean
remotePath(sid, path)   // '/chat/stream' → '/swarm-out/remote/<sid>/chat/stream'
```

未登记的 sid 走原路径，字节级不变（§7 I1 固定）。按方法决定：

| 转发 | 方法 |
|---|---|
| 是 | `streamChat`、`streamAnswerQuestion`、`streamCmdPermissionContinue`（SSE）、`cancelPending`、`getSession`、`getSessionMessagesPage`、`getTranscriptSince`、`getSessionMessagesReconcileTail` / `getReconcileTailWithRev`、`stopSession`、`stopTask`、`getContextUsage`、`compactSession`、`refreshSession`；`tscc.ts` 的 per-session 读；`canvasSession` 的 `artifacts`、`changes` 两个只读列表 |
| 是，但改调用方传 sid | `getTurnDiff`、`canvasSession.treeDiff`：今天不带 sid（`ChangesCard.tsx:98`、`CanvasChanges.tsx:267`），加一个可选 `sessionId` 参数，git_root 是 Hive 上的路径，转发给 Hive 才对 |
| 否，remote 时返回 null | `canvasSession.workingRepo`（否则本机终端会被指到 Hive 的路径，`stores/terminalLocation.ts:160`） |

## 6. 后端：代理 + 中继 `/api/swarm-out/remote/{sid}/…`

### 6.1 谁能用、用哪个会话

- 整个 router 挂 `_require_desktop` 依赖（不是每个 handler 各自调用，免得新路由漏掉）。
- 写请求要求 `Content-Type: application/json` 和 `X-Swarm-User-Action` 头（强制预检，挡掉网页的简单
  跨站 POST；今天 `cmd-permission-continue` 不看 Content-Type）。
- **sid 必须是本机派发日志里的 `session_id`**，Hive 由那一行的 `hive_id` 决定；同一个 sid 出现在多个
  `hive_id`、或与本机某个会话 id 相同 → 拒绝。sid 和路径里的其他 id（`task_id`、`client_id`、
  `request_id`）一律 `^[A-Za-z0-9_-]{1,128}$`，拼上游路径时 `quote(safe="")`（`KEY_RE` 允许 `.` 和 `..`，
  不够）。

### 6.2 请求：白名单重建，不转发客户端的东西

- 只放行 §5 表里"转发"的方法+路径模板，其余 404。
- **body 按接口白名单重建**，不是删字段：`/stream` 只留 `{message, content, session_id, client_id}`，
  `session_id` **必填**且等于 sid（`null` 会在 Hive 上开一个不在日志里的新会话），`agent_id` 固定
  `default`，`content` 只允许 `text / image / document(base64)` 块；`answer-question`、
  `cmd-permission-continue` 同样各自白名单。今后 `ChatRequest` 加了本机上下文字段，也不会被转发。
- 上游请求用 `_client` 从头构造，**不带**客户端的任何头（Cookie、Authorization、Origin）。
- **两个 diff 接口（`chat/turn-diff`、`canvas/changes/tree-diff`）不按会话限定 `root`**：路径里没有 sid，
  任何一个本机派出的会话都能读那台 Hive 上**任一个已绑定 repo** 的 diff。这是有意的边界：Host 和 Hive 是
  同一个主人，Hive 侧只读已绑定 repo（`repo_changes.authorized_root`），提交 id 必须是完整 SHA、路径不能含
  `..`（`turn_changes._valid_diff_args`）。将来 Hive 服务多个主人时，要在代理里先用该会话
  `canvas/session/{sid}?parts=changes` 的 git root 核对 `root`。
- `project_agent` 的延续：派发时带的 pin 在后续 turn 上是否保持，第 1 步先在 Hive 上验证；不保持就在
  代理里按派发日志补上，不让前端传。

### 6.3 响应：清洗 Hive 来的一切

Hive（或其上被提示注入的 agent）是不可信来源；它的输出今后要流进本机 chat 的事件处理。

- **事件类型白名单**：只转发聊天渲染需要的类型（assistant / tool 块、`turn_start / turn_end`、
  `turn_phase`、`ask_user_question`、`cmd_permission_request`、`session_activity / session_state`、
  `result`、`error` 等，清单在第 1 步对照前端的 handler 定稿）。**丢弃 `ui_command`、`file_changed`**
  （否则 Hive 能让本机 Canvas 打开本机任意文件，或自动打开 SwarmWS 里的同名文件）。
- **sid 一致**：信封的 sessionId 和负载里的 `session_id / sessionId` 不是这个 sid 的事件一律丢弃，
  四条流（`/events` 中继、`/stream`、`/answer-question`、`/cmd-permission-continue`）都这样。
- **大小**：单行 1 MiB 上限，超了关掉这条流；非流式响应体有上限。
- **头和状态**：3xx 映射成 502（不让 webview 跟随 Hive 的 Location）；响应头只放 `Content-Type、ETag、
  X-Has-More、X-Transcript-Rev`；Hive 的错误正文不透出，只给状态码和通用文案（同
  `swarm_out.py:327-330` 的做法）。
- **中继**（`/events`）：每个 sid 至多一条上游连接；Host 用自己记的游标（一个整数）拼 cursors；
  连接与读超时分开，读超时是心跳间隔（15 秒）的数倍，作为真挂死的保护而不是时长上限；401 / 5xx 退避重连。
  `POST /api/swarm-out/remote/{sid}/relay`（开，带客户端 id）和 `DELETE …/relay`（减计数）。去重、
  epoch、停止、状态条目、计数这些规则见 §3。

### 6.4 审批：tab 里和 dashboard 里同样严格

S3 桥对审批有两个约束：命令超过 `MAX_COMMAND_CHARS`（2000）不能批准、命令在显示后变了不能批准
（`swarm_out_bridge.py:16-19`）。Hive tab 里的审批卡（chat 的同一个组件）对 remote tab：

- 命令超过 `MAX_COMMAND_CHARS` 或含控制字符 / bidi 字符时，**只允许拒绝**。
- Hive 侧补一个检查：`cmd-permission-continue` 只接受属于 `body.session_id` 的 `request_id`
  （`routers/chat.py:1518` 今天按 `request_id` 找 `perm_req`，不看会话）；`answer-question` 同理核对
  `tool_use_id`。这是本机也受益的修正，但这里它是代理"只动这个会话"的前提，所以在本设计第 1 步做。

## 7. Hive tab 里关掉的本机功能

| 功能 | 做法 |
|---|---|
| `ui_command`、`file_changed` | 中继丢弃（§6.3）；前端对 remote tab 再忽略一次（lifecycle :3052、:3120 处按 `isRemote(tab.sessionId)` 跳过） |
| 消息里的本机路径链接 | Hive tab 的 TabView 外面包一个 `RemoteContentContext`，`MarkdownRenderer` 读到它时，工作区路径和绝对路径链接显示为文本（悬停"在 Hive 上"），外链照旧 |
| Canvas 的产出列表（artifacts） | 第 2 步起 `artifacts()` 对 remote 会话读的是 Hive 的行，`absolutePath` 是 Hive 上的路径；在本机打开会走本机 `/workspace/file`（本机没有这个会话，被拒，或同名相对路径打开本机的另一个文件）。remote 会话的这些行显示为文本，不打开本机文件 |
| 终端上下文、UI 感知 `editor_context` | 前端不附带；代理的 body 白名单也不放 |
| 从工作区拖文件（`path_hint`）、ToDo 拖入 | 拒绝并提示；上传的附件（base64）照常 |
| `/plugin` 命令、技能开关、记忆保存 | 不显示 |
| stall 判活 | 本机 `/health` 对 Hive 永远是活的，前端的 stall 计时不会因 Hive 卡住而触发；由中继的读超时发现，中继断开后 tab 显示"连不上 Hive" |

## 8. r1 审查结果怎么处理的

| 原因 | finding | 本版 |
|---|---|---|
| 前端为 Hive 开第二条事件连接 | corr #2 四处 `isConnected()`、#3 连接扇出误伤本机 tab、#5 `buildUrl` 拼不出代理路径、#6 状态合并的失败模式、sec `/events` 推所有会话 | §3：后端中继，前端连接不变 |
| Hive 内容被当成本机后端的话 | sec HIGH `ui_command.allowAbs` / `file_changed` / 路径链接 / 换 sid，corr #8 | §6.3 类型白名单 + sid 一致；§7 前端再挡 |
| 复用的 tab 在原地重装 | corr CRITICAL #1 切换时旧 turn 绑定残留，#4 恢复丢 tab，#9 关闭分支，#10 恢复后重发，#11 标题 | §4 换掉整个 tab、显式恢复、关闭分支、跳过重发、标题保护 |
| 代理的输入没收紧 | sec body 黑名单、头和 3xx、CSRF、路径参数、sid 查找、router 级依赖；corr #7 diff 方法不带 sid、`workingRepo`；#12 project_agent | §5 表、§6.1、§6.2 |
| 两条审批路线一严一松 | sec MEDIUM #3，Hive 侧 request_id 不核会话 | §6.4 |
| 行号 | corr #13 | §2 已改；心跳常量是 `EVENTS_HEARTBEAT_INTERVAL` |

r2 审查（concurrency，只审中继和换 tab）：

| 原因 | finding | 本版 |
|---|---|---|
| 两套序号进了同一个计数器 | CRITICAL：POST 流带 Hive seq、中继带本机 seq，`lastSeq` 共用 | §3 规则 1 |
| 中继的生命周期没定义清楚 | HIGH 停在 turn 中间留下不结束的 turn；HIGH 重连重复、换 epoch 不结束；MEDIUM 并发开停与两个窗口；MEDIUM 本机重启后不重开中继 | §3 规则 2、3、4、7；§4 本机重启 |
| 本机把"没有这个 sid"当成"结束了" | HIGH 轮询置 idle、清队列；MEDIUM 只靠推送的状态会过期；MEDIUM 本机时钟混入 | §3 规则 5、6 |
| 同一个 tab id 复用 | MEDIUM 旧 tab 迟到的回调落到新 tab | §4 每个会话一个 tab id、先关后开 |
| 白名单和上游流量 | LOW `transcript_row`、`background_agents`；LOW 上游推所有会话 | §3 规则 8、9 |

## 9. 风险

- **ChatPage / lifecycle 仍有改动**，实话是这些处：`openHiveSession`（先关后开）、关闭分支、恢复分支、
  `backend-recovered` 跳过重发、`updateTabTitle` 保护、lifecycle 里 `ui_command / file_changed` 两处
  remote 跳过、`ChangesCard / CanvasChanges` 传 sid。全部以 `isHiveTab` / `isRemote` 为条件；
  没有 Hive tab 时这些分支不执行，由 I1 固定。事件连接、路由、状态轮询不改。
- **中继是这次最复杂的部分**：九条规则（§3），每条一个先红后绿的测试；第 1 步代码再请 concurrency +
  security 审一次（审代码，不再审设计）。
- **CloudFront 上的长连接**：Hive 网页已走同样的 SSE；中继的读超时按心跳设。

## 10. 验证计划（在这里定，做完逐行报告）

| # | 最后必须成立 | 怎么验证 |
|---|---|---|
| I1 | 没有 Hive tab 时，chat 每个请求的 URL / body、事件连接、状态轮询与今天相同 | 单测各 service 方法未登记 sid 时的 URL/body；`streaming-state` 与 hub provider 在无中继时输出不变；现有 `useChatStreamingLifecycle`、`sessionTurnRouter`、`useUnifiedTabState`、`ChatHeader`、`landPromptInTab` 测试全绿 |
| I2 | 同时只有一个 Hive tab；点另一个 run 关掉旧的、开新的（新 id）；旧 turn 正在跑时切换，新会话照常渲染，旧 tab 的迟到回调和事件不影响新 tab | 单测 `openHiveSession`（含"旧 turn 未完成 + 迟到回调 + 迟到事件"） |
| I3 | Hive tab 不进 tab 条、不占上限、不是落地目标、不被 `removeInvalidTabs` 清掉、关闭不确认不 release、重启后恢复、恢复时不重发 | 单测对应路径 |
| I4 | 中继：只发布这个 sid；白名单外的类型、负载 sid 不符的丢；超长行关流；同 epoch 重放不重复；停在 turn 中间后本机 hub 没有残留活动 turn；换 epoch 先清再发本机 resync；登记期间 `streaming-state` 总有这个 sid；中继 sid 不进本机时钟；并发开停只留一条上游；两个窗口互不停对方 | 后端单测（假 Hive SSE） |
| I4b | 前端：Hive seq 远大于本机 seq、POST 的 `turn_start` 先到时，turn 照常渲染并结束（POST 流无 eventSeq） | 前端单测（router + 一段模拟流） |
| I5 | 代理：只认本机派出的会话、白名单路径、body 白名单（含 `session_id:null` 拒绝）、无客户端头上游、3xx→502、响应头白名单、无 JSON 头 / 无 user-action 头的写请求拒绝、路径参数正则 | 后端单测，每条做 mutation |
| I6 | 审批：超长 / 含控制字符的命令在 tab 里只能拒绝；Hive 拒绝不属于该会话的 request_id | 前端单测 + 后端单测 |
| I7 | 真机：turn 跑到一半打开接上实时；忙时发消息排队；tab 里回答问题 Hive 继续；停止生效；切 run；关 tab 后 Hive 照跑；本机重启后 tab 恢复 | 手动 E2E，一台真 Hive |

**每步审查**：第 1 步 security + concurrency；第 2 步 correctness；第 3 步 correctness + ux。
每个修复做 mutation 检查。

## 11. 分步

1. 后端：代理（§6.1-6.2）、中继（§6.3）、`streaming-state` / provider 加中继条目、Hive 侧审批与
   回答的会话核对（§6.4）。验 I4、I5、I6 后端部分。
2. 前端传输层（§5）：`remoteSession.ts`、各 service 的路径选择、diff 方法传 sid。验 I1。
3. Hive tab（§4、§7）：`hive-session:<sid>` id、header、`openHiveSession`（先关后开）、关闭与恢复分支、
   `RemoteContentContext`、审批卡的 remote 限制、卡片和历史行入口。验 I2、I3、I6 前端部分。
4. 真 Hive 上的 E2E（I7）。
