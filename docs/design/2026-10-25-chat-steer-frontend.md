---
title: Chat steer — the frontend (D7)
status: draft v2 (2026-10-25；无上下文审查一轮后按原因修订，见 §13；之后靠测试验证，不再起审查轮次)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
related: 2026-10-24-chat-steer.md（后端 P1，已合入；本文是它的 D7 和 P2）, 2026-10-25-chat-steer-ui-mock.html（四帧 mock）, 2026-10-24-chat-stream-structure.md（R1–R4 仍在）, chat-turn-liveness.md（S3 排队队列）
---

# Chat steer — the frontend (D7)

## 0. 一句话

前端把后端已经决定好的四种状态投影出来：**Waiting / Read during the turn / Read after the reply /
Queued（带原因）**。实现方式是一个 turn 按 steer 切成几段，每段一个 assistant 气泡，steer 气泡夹在
两段之间。这和数据库里的行形状一致（user 行把 assistant 行隔开），所以直播和重载走的是同一条规则。

本文所有关于现有代码的说法都在 2026-10-25 读过，文件路径相对 `desktop/src/`。

## 1. 后端给了什么（P1 已合入，本文不改它的形状）

| 来源 | 字段 | 含义 |
|---|---|---|
| busy 发送的回执 `accepted` | `steer: true` | 消息已写进正在跑的 turn，还没被读（在途） |
| 同上 | `queued: true`，`queuedReason`（只在开关开时有） | 排队了；原因 `question` / `permission` / `busy` |
| turn 事件 `user_steered` | `clientId`、`outcome`（`folded` / `opened`）、`rowKey`（`{clientId}-asst`）、`segments` | CLI 在这里读到了它；之后的内容属于 `rowKey` 这一段 |
| session_state 快照 | `steering: [{client_id, preview}]`（只在有时出现）、`segments`（同） | 还没被读的 steer；当前 turn 的全部段 key，按顺序 |
| transcript feed | user 行 `sent=1`、`created_at`=回显时间、`metadata.steered`=`during`/`after` | 读到之后这条消息成为历史 |
| 历史（重载） | user 行 `metadata.steered`；steer 之后的 assistant 行 `metadata.client_id`=`{steer_cid}-asst` | 按 `created_at` 排，user 行把两段 assistant 行隔开 |

开关关时这些字段都不出现，前端行为必须和现在一样（§9 T-off）。

**P2 要先补的后端（审查找到，P1 的问题，开关关着所以没触发）：** 见 §10 后端三条——steer 行的时间由
writer 决定（B1）、Stop 回执和产物的 key 带 `-asst`（B2）、有排队消息时不 steer（B3）。

## 2. 现状：决定设计形状的几处代码

- **busy 发送。** `pages/chat/turnControls.ts:139-148` 在 store 末尾 `append` 一个 user 气泡，id 就是
  client_id，带 `isQueued: true`，记进 `tab._queuedLocal`；`:180-187` 只读回执的 `queued`，为假时从
  `_queuedLocal` 删掉。回执之外的 POST 事件全部忽略（`:180`）。
- **排队样式。** `pages/chat/components/UserMessageView.tsx:105,111` 半透明 + 左侧虚线，`:154-166` 灰字
  "Queued — will send when ready" 和 ✕；文案键 `chat.queued.label`（`i18n/locales/en.json:236`，zh 有）。
- **排队状态由服务端决定。** `hooks/useChatStreamingLifecycle.ts:1813-1844` `syncQueuedBadges`：一条消息
  排队 = 它的 client_id 在服务端列表上且未被取走，或还在 `_queuedLocal`；列表里有、store 里没有的项
  补一个气泡追加到末尾（`:1831-1835`）。`applyServerQueue`（`:1854-1868`）在快照到达时调它。
- **流式写入的目标气泡是固定的。** `createStreamHandler(assistantMessageId, …)`（`:2488-2489`）里有
  33 处用这个参数，例如文字 `:3123` `textStore.updateLast(textApply, (msg) => msg.id === assistantMessageId)`、
  `assistant` 块 `:3192`。没有"换一个目标气泡"的路。
- **store 记住正在直播的气泡，但只在空闲时用到。** `stores/MessageStore.ts:390-396` `startStreaming(id)`
  记下 `_streamingMessageId`，它只在 `_applyMerge` 里读（`:1036`、`:1113`），而 reconcile 在直播期间只排队
  不执行，所以切段时不用改它。只有一个按位置插入的 API：`insertBefore(anchorId, msg)`（`:243-252`）。
- **reconcile 按 client_id 对应，不看角色。** `MessageStore.ts:995-1006` 把 `local-*` 的 id 和 metadata 里的
  client_id 都放进同一张表，user 气泡和 assistant 行可以对上同一个 key。
- **只有最后一个 assistant 气泡算正在直播。** `pages/chat/components/TabView.tsx:762-764`
  `isLastAssistantForStreaming = isStreaming && msg.role === 'assistant' && idx === lastAssistantIdx`。
- **气泡不再直播后 2 秒就按"turn 已结束"排版。** `pages/chat/components/AssistantMessageView.tsx:239-243`
  `settling = useLinger(!!isStreaming)`、`ended = !settling`；`ended` 后出现 RunResult、OutputCard、
  attention、NextSteps（只给最后一个 assistant）和动作行（`:452-574`）。`pages/chat/turnZones.ts:230-237`
  在 `hasCalls && !hold && !stopped` 时把最后一次调用之后的第一段文字标成 RESULT。
- **每个 assistant 气泡都有头像和抬头。** `AssistantMessageView.tsx:541-556`。
- **气泡的 React key 跟着 client_id。** `TabView.tsx:248-260` `bubbleKeys`：`${role}:${metadata.client_id ?? id}`。
  直播气泡 id 就是 client_id，DB 行在 metadata 里带同一个值，所以 reconcile 换成 DB 行时不重挂。
- **turn 结束的 reconcile。** `useChatStreamingLifecycle.ts:1963-1997` 读最近 50 行，`MessageStore._applyMerge`
  按 `metadata.client_id` 把 DB 行对上本地气泡（`MessageStore.ts:995-1006`、`:1050-1058`），按 DB 顺序；
  本地独有的行放在"它后面那条消息之前，否则最后"（`:1149-1158`）。后端历史按 `created_at` 排，相邻的
  assistant 行合并（`backend/routers/chat.py:950`）。
- **transcript feed 的 user 行。** `hooks/transcriptFeed.ts:99-104`：tab 已经有同 id 或同 client_id 的气泡就
  什么都不做，否则追加到末尾。
- **中途重新订阅的重放。** `useChatStreamingLifecycle.ts:4567-4576` `beginSubscriptionTurn` 清掉
  `metadata.client_id === cid` 的 assistant 行，在末尾 `appendMany` 一个占位。续写 / injected turn 接在
  `hostForInjectedTurn`（`hooks/streaming-guards.ts:309-320`）返回的气泡里：最后一条消息是 assistant 才算。
- **未知事件。** `user_steered` 现在在内层 handler 里没有分支，什么都不渲染（Explore 读过
  `useChatStreamingLifecycle.ts:2826-2851` 和 `sessionTurnRouter.ts:261,273`）。
- **Stop。** Agents 面板的 "Stop all" 发 `STOP_TURN_EVENT`（带 sessionId），`pages/ChatPage.tsx:2457-2467`
  收到后调 `stopTurn`。

## 3. 方案比较

| | 做法 | 问题 |
|---|---|---|
| A | 一个 turn 一个 assistant 气泡，steer 作为一个块画在 assistant 内容里 | 重载时 DB 里它是独立的 user 行，直播和重载形状不同；用户的话出现在助手气泡里，对齐和样式都要另做 |
| B（选） | 每段一个 assistant 气泡，steer 气泡夹在中间 | 要让 handler 能换目标气泡；前一段不能被当成 turn 结束来排版 |
| C | 保持一个气泡，steer 气泡先在底部，turn 结束时挪到读到的位置 | turn 结束时气泡移动，违反 Surface 6 不变量 10 |

B 的形状就是 DB 的形状：读到 steer 之后的行 key 是 `{steer_cid}-asst`，user 行按回显时间排在两段
之间（后端 D4、T6）。所以 B 不需要任何"直播特有"的排列，重载天然一致。

## 4. 状态：一个推导函数

每个 user 气泡的 steer 状态由一处推导，优先级从高到低：

| 状态 | 条件 | 灰字（en / zh） | 样式 | 操作 |
|---|---|---|---|---|
| Read during | `metadata.steered === 'during'` | Read during the turn / 回答过程中读到 | 普通 | — |
| Read after | `metadata.steered === 'after'` | Read after the reply / 回复写完后读到 | 普通 | — |
| Queued | `isQueued`（现有规则，`syncQueuedBadges`） | 按原因，见下 | 现有：半透明 + 虚线 | ✕ |
| Waiting | client_id 在 `tab._serverSteering`，或在 `tab._steerLocal` 里还没被快照判过 | Read after the current step · **Stop & send now** / 当前这一步结束后读到 · **停止并立即发送** | 普通（不能取消，所以不用排队样式） | 链接 = Stop |
| — | 其余 | 无 | 普通 | — |

- **一个字段，直播和重载同源。** "已读"只看 `metadata.steered`：重载时它来自 DB 行；直播时
  `user_steered` 到达的那一刻，在气泡上写同一个字段（它是行上已经有的事实，不是另一份状态）。
- **Waiting 是粘的。** 一旦进入 Waiting，只有三件事让它离开：`user_steered`（或 feed 行带 `steered`）到达、
  快照把它列进 `pending`、turn 结束。快照里它从 `steering` 消失但读取事件还没到的那一刻，不清标签（否则
  Waiting → 空 → Read 会闪）。
- **Waiting 跟排队用同一套"本地直到服务端判过"的规则。** 回执 `steer: true` → `tab._steerLocal.set(cid, Date.now())`，
  从 `_queuedLocal` 删掉（现在 `:186` 对非排队回执就是这么做的）；之后一份读取时间晚于回执的快照说了算：
  在 `steering` 里 → Waiting；转进 `pending` → Queued（steer 没被读就被 Stop 或进程没了，D8）；都不在且没有
  `steered` → 普通。`_serverSteering` 在 `applyServerQueue` 里一起存。`syncQueuedBadges` 扩成同时写
  `isQueued` 和 `steerWaiting` 两个展示字段（展示字段，不是内容，chat-stream-structure A2 的分法）。
- **快照里有、store 里没有的 steer**（重载后、别的 tab）：和排队项一样补一个气泡追加到末尾，内容是
  `preview`，带 `metadata.provisional = true`。
- **排队原因。** 回执的 `queuedReason` 只有发送的那个 tab 知道，重载后就没了。所以快照也带一个当前的
  `queued_reason`（会话级，就是此刻 `_steer_blocker` 的结果，开关开且有排队时才有），排队气泡统一用它；
  它会随状态变化（问题答完后就不是 `question` 了）。文案：`question` → "Queued — sends after you answer the
  question above / 已排队，回答上面的问题后发送"；`permission` → "…after you decide the permission above /
  …处理上面的权限请求后发送"；`busy` 或没有 → 现有的 `chat.queued.label`，一字不改（mock 帧 D 写的 "sends when this turn ends" 不采用，
  开关关时文案不能变）。
- **发出那一刻的样子。** 气泡创建时仍是 `isQueued: true`（不能先知道后端会 steer 还是排队），回执到了
  （一个往返）才变成 Waiting：只是灰字和样式变，位置不变。不在前端猜开关状态。

## 5. 读到 steer 的那一刻：切段

`user_steered` 在内层 handler 里加一个分支（POST 和订阅两条路走同一个 handler，R1 不变）。tab 这时
不在直播（Stop 之后、turn 已结束）就只做第 1 步：标签，不切段。

1. **steer 气泡。** 找 `id === clientId` 或 `metadata.client_id === clientId` 的 user 气泡；写
   `metadata.steered`，清 `steerWaiting`、`isQueued`，并从 `_queuedLocal`、`_steerLocal` 删掉它（读取事件
   可以先于这条消息自己的 POST 回执到达：后端先写 stdin、再回回执；之后到的回执看到已读就忽略 `steer`）。
   找不到（别的 tab、快照还没到）就建一个 provisional 气泡，内容取 `_serverSteering` 里的 preview，没有就空。
2. **上一段是空的 steer 段就撤掉它。** 同一个 tool result 处连着读到两条 steer（X6）时，第一条的段还没有
   内容就被第二条接走；DB 里它没有任何行。所以当前目标是一个空的、由 steer 建出来的段占位时，先删掉它
   （直播和重载一致：两条 user 气泡相邻）。
3. **新段的占位。** id = `rowKey`（就是 `{clientId}-asst`，DB 行的 client_id 也是它，所以 reconcile 和
   `bubbleKeys` 都对得上）。已经有就复用，但它必须是空的（§8 重放会先清掉）；没有就紧接在 steer 气泡后面插入：
   steer 气泡是最后一条就 `append`，否则 `insertBefore(它后面那条)`。后面还在 Waiting 的气泡留在最下面。
4. **换目标。** 当前目标存在 tab 的 turn 记录上（§6），改成新占位；`turnStartOf()` 对新段返回 0。

两条 steer 连着读（D3 T19：S1 开新一轮，S2 在 S1 那一轮里并入）：S1 的段插在 S1 后面，S2 还在最下面；
S2 读到时它的段插在 S2 后面。顺序就是 [段1][S1][段S1][S2][段S2]，和 DB 一样。

**为什么不会有排队气泡夹在中间。** 一条排队的消息 S0 在 steer S1 之前发出时，store 是
[段1][S0 排队][S1]，S1 的段插在 S1 后面，S0 就被夹在段1 和 S1 之间；而 DB 里 S0 的时间是它被 drain 取走的
时刻，排在 turn 之后。后端 B3 让这种情况不出现：会话里已有排队的消息时，新消息也排队（按发送顺序，
后发的不越过先发的），不 steer。

## 6. handler 换目标

当前目标不放在某个 handler 的闭包里，而放在 tab 的 turn 记录上（`turnHandlersOf(tab)`，`beginSubscriptionTurn`
和 POST 发送都已经用它存 `hostedTurn`、`postBubbleId`）：`targetId`，turn 开始时设成初始气泡。原因是错误
handler 是各个调用方用初始 id 单独建的（`pages/chat/turnControls.ts:328,346,418,428,488`、`pages/ChatPage.tsx:2242,2355`），
没有一个 handler 对象可以问；steer 之后出的错如果写进初始气泡，就会出现在 steer 上面。

- `createStreamHandler` 函数体里 33 处 `assistantMessageId` 改成读 `targetOf(tab) ?? assistantMessageId`
  的局部 `target()`；`wrapPostHandler(…, assistantMessageId)`（`:3848` 的调用）只用来识别 POST 自己的气泡，保持初值。
- 错误 handler（`createErrorHandlerInner`，`:3853`）和 complete handler 同样先读 `targetOf(tab)`。
- 不新建第二个 handler：A1 之后 generation guard、`hostedTurn` 都挂在现有对象上，换 handler 要全部搬一遍。
- `MessageStore` 不用改（§2：`_streamingMessageId` 只在空闲时的 merge 里读）。

## 7. 前一段不能被当成 turn 结束

切段后前一段不再是最后一个 assistant 气泡（`TabView.tsx:762-764`），2 秒后它会按 `ended` 排版：标出
RESULT、出现动作行。turn 其实还在跑，这违反"一个 turn 一个结尾"（mock B），也违反不变量 10（turn 跑
着时前面的区域长出新东西）。

规则（TabView 一处算，直播和重载同一条）：

- **`continued`：** 这个 assistant 气泡后面第一条非 system 消息是 `metadata.steered` 的 user 气泡。
  AssistantMessageView 收到 `continued` 时：照常在 2 秒后折叠它的 WORKED 组（不变量 10 允许、mock B 就是
  折叠的）；`turnZones(view, isStreaming, hold, continued)` 不标 RESULT、不判 stopped，最后一段文字按
  note 显示；不出 RunResult、OutputCard、attention、NextSteps（`ended && !continued`）。悬停才出现的动作行
  （Copy 等）照常保留：它不占版面，不改变形状。
- **`afterSteer`：** 这个 assistant 气泡前面第一条非 system 消息是 `metadata.steered` 的 user 气泡。
  不画头像和 `AssistantHeader`，留同宽的空位，内容列和前一段对齐（mock B/C "后半段不另起回复头"）。
- **header 脉冲留在这一串的第一个抬头上。** 脉冲在 `AssistantHeader` 里、跟着 `isStreaming`
  （`AssistantHeader.tsx:55-56`）。第一段不再直播、后面的段又不画抬头，脉冲就没了，只剩尾部的 bee
  （不变量 8 要两个信号）。所以 TabView 给一串（第一段 + 它后面所有 `afterSteer` 的段）的第一个气泡传
  `headerLive`：这一串的最后一段在直播时为真。
- **两条规则只认已读的 steer。** Waiting / 排队的气泡不切段；它们也不会夹在 steer 和它的段之间（段紧接 steer 插入，之后的发送追加在后面），所以不需要跳过规则（实现时变异检查发现跳过规则没有可达的场景，删掉了）。

turn 的用量、耗时、输出卡片只在最后一段：`result` 落在当时的目标气泡上，turn_changes / deliverables 行
由后端写在最后一段的 key 下（后端 D4）。

## 8. 别的 tab、重放、续写、重载

- **别的 tab。** 它们靠快照的 `steering` 建出 Waiting 气泡（§4），`user_steered` 经订阅到达时照 §5 切段。
  feed 推来的 user 行（`transcriptFeed.ts:99-104`）现在对已有气泡什么都不做；改成：行带
  `metadata.steered` 时，把它写到对应气泡上（任何 tab，漏了 `user_steered` 也能补上标签）；气泡是
  `metadata.provisional` 的，同时换成行的内容（preview 只有 200 字）。
- **中途重新订阅（T17）。** 重放从 turn_start 重发整个 turn，其中包括每个 `user_steered`。`beginSubscriptionTurn` 不按
  segments 列表删段：读到 steer 时（§5），已经显示的那一段（id 或 `client_id` 等于 `{cid}-asst`，reconcile 之后是 DB
  id）被清空后复用，重放的内容写进去，所以每段只出现一次，漏收了读取事件的 tab 也一样。占位插在这个 turn 第一个
  "还没发出或已读的 steer"user 气泡之前（Waiting、排队、`steered`）；这个 turn 的提问不在 tab 里时（injected 等），
  只放到底部那些还没发出的气泡之上，否则加在末尾。
  （2026-10-26 审查后改：原来按 `segments` 列表删段，reconcile 给 host 换成 DB id 之后，续写会把 host 自己删掉。）
- **续写和 injected turn。** `hostForInjectedTurn` 现在只看最后一条消息是不是 assistant
  （`streaming-guards.ts:318-319`）。最后一条是 Waiting 或排队的 user 气泡时（问题出现前写的 steer 还在等），
  它会新建气泡加在 steer 气泡下面。改成跳过末尾处于 Waiting / Queued 的 user 气泡，再找 assistant
  （判断用 §4 的同一个推导，不直接读 `isQueued`）。
  POST 续写的 `continuationHostId`（`turnControls.ts:93-99`）先找持有问题的气泡，不用改。
- **重载。** 历史就是 [user][段1][steer user（steered）][段2]，§4 / §7 的规则直接作用在它上面，不需要
  任何额外信息；React key 不变（`bubbleKeys`）。

## 9. 测试（先写成失败的，再实现）

| # | 场景 | 要确认 |
|---|---|---|
| T10a | busy 发送，回执 `steer: true` | 气泡位置不变，从排队样式变成 Waiting；没有 ✕；"Stop & send now" 发 `STOP_TURN_EVENT` |
| T10b | 之后 `user_steered` folded | 气泡原地变成 "Read during the turn"；新段占位紧跟在它后面；之后的文字写进新段，不写进前一段 |
| T10c | `user_steered` opened（前一段是最终回复） | 同上，标签 "Read after the reply" |
| T10d | Waiting 时快照把它挪进 `pending`（D8） | 变成 Queued，有 ✕；之后 drain 发出时照现有规则清掉 |
| T10e | 回执 `queued` + `queuedReason: question`；快照 `queued_reason` | 标签指向上面的问题；原因变化时标签跟着变；开关关（无字段）时文案和现在一字不差 |
| T10f | 重载后（历史行带 `metadata.steered`） | 标签保留；[段1][steer][段2] 的结构和直播结束时逐个相同（同一组 React key、同样的 `continued` / `afterSteer`） |
| T10g | 前一段 `continued` | 2 秒后 WORKED 折叠，不标 RESULT，没有动作行和卡片；后一段没有头像和抬头；用量只在最后一段 |
| T10h | 两条 steer（S1 opened、S2 在 S1 那一轮 folded） | 顺序 [段1][S1][段S1][S2][段S2]；S2 等待期间在最下面 |
| T10i | 别的 tab：只有快照和订阅 | 从 `steering` 建出 Waiting 气泡；切段同 T10b；feed 行到达后 provisional 气泡换成完整内容，不重复 |
| T10j | turn 结束的 reconcile | 本地气泡按 client_id 换成 DB 行，位置和 key 不变，不重复 |
| T17 | 切段之后中途重新订阅、重放 | 按 `segments` 清行，steer 之后那段只显示一次；重放的第一段在 steer 气泡上面 |
| T-host | Waiting 气泡在最下面时出现问题、回答后续写 | 续写接在当前段里，不另起气泡、不出现在 steer 气泡下面 |
| T10k | 同一个 tool result 处连着读到 S1、S2 | S1 的空段被撤掉；重载后结构相同（S1、S2 相邻） |
| T10l | `user_steered` 先于 POST 回执到达 | 标签是已读；之后的回执不把它改回 Waiting；没有排队样式 |
| T10m | Waiting 时快照先去掉它、读取事件后到 | 标签不闪空 |
| T10n | steer 之后、新段还没内容时按 Stop | 回执行和 steer 的 user 气泡不撞；重载结构和直播一致（依赖后端 B2） |
| T10o | 读到 steer 时 tab 已不在直播 | 只改标签，不建段、不换目标 |
| T17b | 重连时 steer 还在 Waiting | 重放的第一段在 steer 气泡上面 |
| T17c | 续写 turn 里又有 steer，然后重放 | 续写那段不重复 |
| B-T1 | 后端：reader 远快于 writer（慢的 consumer），opened 帧序 | 重载顺序 = 直播顺序：前一段的最后一行在 steer 行之前（B1） |
| B-T2 | 后端：有排队消息时 busy 发送 | 排队，不 steer，`queuedReason` = `busy`（B3） |
| T-off | 回执和快照都没有 steer 字段 | 现有的 serverQueue / turnControls / sessionTurnWiring / liveReload 测试全绿，不改一行 |

- 用现有的 harness：`__tests__/serverQueue.test.ts`、`pages/chat/__tests__/turnControls.test.ts`、
  `__tests__/sessionTurnWiring.test.ts`（订阅、重放）、`stores/__tests__/MessageStore.test.ts`。
- `liveReload.property.test.ts` 的 fixture 由 `backend/tests/test_live_reload_scenarios.py` 生成；要加 steer
  场景（folded / opened、两条连读、Stop），得先在后端那边加生成脚本，再在前端断言直播结束和重载后结构
  一致。时间竞态（B1）它模拟不了，由 B-T1 管。
- 每个修复做变异检查（撤掉修复，测试变红）。
- **已落地（2026-10-26）：** 生成器加了 7 个 steer 场景（`STEER_VARIANTS`：folded、opened、两条、同一点两条、
  下面还压着一条 Waiting、Stop 在新段为空时、Stop 在新段有内容后），每个后面再跟一个普通 turn。前端用真实的
  `sendWhileBusy` 发出（`busy_user` 步骤）。T10j（turn 结束的 reconcile）由 property test 的 reconciled 模式在这些
  场景上覆盖，T10n 是 `stop_empty`。变异检查：去掉切段、占位放到末尾、保留空段、B2 改回裸 key，各自变红。

## 10. 改动清单

前端：
- `pages/chat/turnControls.ts` 回执处理：`steer` → `_steerLocal`；`queuedReason` 记在气泡上。
- `hooks/useChatStreamingLifecycle.ts`：`syncQueuedBadges` 写 `steerWaiting`、补 provisional 气泡；
  `applyServerQueue` 存 `_serverSteering`、`queued_reason`、`segments`；turn 记录上的 `targetId`，stream /
  error / complete handler 读它；`user_steered` 分支；`beginSubscriptionTurn` 按 segments 清行和定位。
- `hooks/streaming-guards.ts`：`hostForInjectedTurn` 跳过末尾等待 / 排队的 user 气泡。
- `hooks/transcriptFeed.ts`：provisional user 气泡用行更新。
- `services/chat.ts`：`toStreamingStateEntry` 映射 `steering`、`segments`、`queued_reason`；`types/index.ts`
  加对应字段。
- `pages/chat/components/TabView.tsx`：算 `continued` / `afterSteer`；`AssistantMessageView.tsx`、
  `turnZones.ts`：`continued` 和 `afterSteer` 的效果；`UserMessageView.tsx`：四种灰字、Stop 链接。
- `i18n/locales/en.json`、`zh.json`：新键 `chat.steer.waiting`、`chat.steer.stopAndSend`、
  `chat.steer.readDuring`、`chat.steer.readAfter`、`chat.queued.afterQuestion`、`chat.queued.afterPermission`。

后端（P2 先做，带测试；都是 P1 的问题，开关关着所以没触发）：
- **B1 steer 行的位置由 writer 决定。** 现在 user 行的时间取自 reader 任务里回显的那一刻
  （`backend/core/session_router.py:4315`），assistant 行的时间是 writer 写入时才取（`:2382`）。reader 跑在
  前面时（opened：最终回复、Result、init、回显一起到），前一段最后一行的时间会晚于 steer 行，重载时它被并
  进后一段，两个气泡带同一个 `{cid}-asst`。改法：`confirm_steer` 照旧在回显时确认（`sent=1`、`steered`，H5
  不变），时间写 `MAX(created_at, 回显时间)`；TurnWriter 处理 `user_steered` 时再写一次
  `MAX(created_at, writer 此刻)`。writer 按顺序写行，它此刻的时间晚于前一段的所有行、早于后一段的所有行。
  Stop 已开始（`_stop_receipt_at` 已设）时 writer 不改，保留 T29 的钳制。
- **B2 Stop 回执和产物的 key 带 `-asst`。** 现在用不带后缀的段 key（`session_router.py:5558,5591`），和 steer
  的 user 气泡同 key，`_applyMerge` 又不分角色（§2）。改成 `{row_key}-asst`，和 writer 写的行同 key。普通 turn
  里回执行在历史中本来就并进 assistant 气泡，key 变化不影响它。
- **B3 有排队消息时不 steer。** `_steer_blocker` 之前查一次 `count_pending(session) > 0` → `busy`。
- **快照加 `queued_reason`**：开关开、有排队时取当前 `_steer_blocker` 的结果。
- `backend/tests/test_live_reload_scenarios.py` 加 steer 场景（§9）。

## 11. 上线

1. 前端改动合入，开关仍关：T-off 保证用户看不到变化。按 AGENT R16 单独部署前端，冒烟（发一条消息、
   流式、切 tab 重载）。
2. `steer_busy_send` 默认打开（2026-10-26，XG：不留回退，问题往前修）。冒烟：两条 Bash 中间发"跳过第二条"（T11），看 Waiting → Read
   during；切 tab 再切回、重载，确认顺序和标签。
3. 观察一周后删开关（后端 §8 P3）。

## 12. 风险

- **前端 handler 的机械替换。** 33 处改成读当前目标，漏一处就会把后一段的内容写回前一段。T10b、
  T10h 盯住"新段内容不写进旧段"；替换后函数体里的 `assistantMessageId` 只剩初值和 `wrapPostHandler` 那一处。
- **reconcile 区域。** 这里历史上打过很多补丁（MEMORY P1"Frontend reconcile race"）。本方案不改 reconcile
  算法本身：段占位和 steer 气泡的 id / client_id 与 DB 行一一对应，`_applyMerge` 照现有规则对上。
  T10j、T17 和 property test 覆盖。
- **发出那一刻样式会闪一下**（排队 → Waiting，一个往返）。接受；位置不动。
- **重载后排队原因只有会话级的当前原因**，不是每条消息自己的。排队的消息都被同一个状态挡着，够用。
- **"Stop & send now" 的竞态。** 点了之后、CLI 在 `interrupt()` 期间恰好读到它（后端 T29），它会算作被停掉的
  turn 的一部分、不再回答。标签会显示"Read during the turn"，用户看得出它被读了。接受。
- **没有回显就跑完的 steer 那一轮**（理论情况，replay 开着时没见过）：没有 `user_steered`，前端和 writer 都
  不切段，后一段的内容留在前一段里，steer 行按确认时间排在最后；直播和重载一致，只是没有分段。

## 13. 审查记录

2026-10-25，一位无上下文审查者审了 v1：前提成立（段的形状和 DB、历史合并、React key 一致），但 6 个 HIGH、
5 个 MEDIUM、7 个 LOW。逐条对照代码核实后按原因合成五组，一次改进 v2：

1. **行的顺序要由 writer 决定**（H1）：后端 B1；加 B-T1。
2. **key 冲突**（H4）：后端 B2；T10n。
3. **气泡相对于没发出的气泡的位置**（H2、H3、H5、M10、L13）：后端 B3；重放定位看 Waiting / 排队 / 已读；
   空 steer 段撤掉；读取事件先于回执；Waiting 是粘的。
4. **一串段的显示**（H6、M11、L15）：脉冲留在第一个抬头；Stop 之后只改标签；动作行保留。
5. **状态来源**（M7、M8、M9、L14）：`segments` 只认当前 turn；重放按路径清段；当前目标放在 turn 记录上；
   feed 行的 `steered` 写到任何对应气泡。
另：§2 的事实更正（L12：`retargetStreaming` 不需要、33 处、行号），mock 的 busy 文案不采用（L16），
property test 要后端 fixture（L17），两个边界情况记入风险（L18）。

按 AGENT § Iterative Refinement，不再起第二轮审查：P2 先把 §9 的测试写成失败的，对照真实代码验证；写测试时
发现和设计不符就直接改设计。
