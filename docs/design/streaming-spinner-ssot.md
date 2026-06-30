---
title: OT01 系统化修复 — Spinner 由后端状态权威驱动
date: 2026-06-30
created: 2026-06-30
updated: 2026-06-30
project: SwarmAI
status: DESIGN — 待 XG 拍板范围
related: OT01 (frontend reconcile race, #1 复发 bug, 5+ 次同类), EVOLUTION CLASS_B
author: Kiro 根因分析 + Swarm 代码/日志交叉验证（合并稿）
---

# OT01 系统化修复 — Spinner 由后端状态权威驱动

## 0. 一句话

Spinner 现在是**前端 `isStreaming` 标志的纯派生**，而 `isStreaming` 是一个靠前端在 2700 行
多 tab 管线里**完美观测每一个 SSE 终结事件**才能跟后端对齐的「第二真相源」——漏一个 `result`
事件，spinner 就钉死，只能等 15s+30s 兜底轮询救。**修复 = 让后端 session 状态成为 spinner 的
唯一真相源，让「假 spinner」结构上不可能出现**，而不是再加一个守卫。

---

## 1. 症状（XG 原话）

> 「前端 streaming response 停了，而且还有个 spinner 一直认为当前 tool call 或命令没结束，
> 但实际上 backend 早就不在这一步了。直到我看到 spinner 在那 run 了 10 几分钟，我 Stop 然后
> resume 后，前端又把在 DB 里已经存好的 response 再给展示出来。」

拆成三个独立事实：

1. **streaming 渲染停了** — 长 turn 进行中，新内容不再刷到 UI。
2. **spinner 钉死在某个 tool call 上**（"Running: Bash"），后端早就过了这一步。
3. **Stop → resume 后，DB 里早已存好的 response 被重新展示出来**。

影响：重会话（700–1500 条消息的 autonomous pipeline session）高频触发，用户无法判断进度，
体感「整个 app 没法用」。

---

## 2. 根因（代码 + 日志 + DB 交叉验证，非推断）

### 2.1 这**不是**什么（已证伪，别再查）

| 假设 | 证伪证据 |
|------|---------|
| 后端卡 / SSE 断 | 后端 session 正常 STREAMING→IDLE 循环、干净结束并持久化到 DB；3 天 **0** premature disconnect / **0** stall / **0** reconnect |
| 数据丢失 / DB 截断 | 症状 #3 自证：resume 后 DB 里 response **完整**展示 → 数据一直安全（`data.db` 实时写入） |
| 渲染卡死 (rAF-wedge) | 新 input 走同一个 `_notify`→`if(_rafId===null)` 门（`MessageStore.ts:147,482`）；若 rAF 真 wedge 则新 input 也被吞，但它能解冻 → `_rafId` 没 wedge |
| 主线程 O(n²) 重渲染 | `MessageBubble`/`MarkdownRenderer` 均 `memo`'d（`MessageBubble.tsx:99`），历史消息 bail out，成本集中在流式那一条 — **Kiro 初判的渲染饱和假设在此被证伪** |

### 2.2 真因：`isStreaming` ↔ 后端状态「双向漂移」，spinner 是其纯派生

```
后端 session 真实状态 (SSOT)          前端 isStreaming (第二真相源)         spinner
state: streaming/idle/waiting  ──SSE event──>  tabState.isStreaming  ──derive──> "Running: Bash"
↑ 权威                          ↑ 靠完美观测每个终结事件                 ↑ 纯函数
/streaming-state 端点已暴露        漏一个 result → 钉死 true              isStreaming && lastBlock==tool_use
```

1. **spinner 是 `isStreaming` 的纯派生** — `deriveStreamingActivity(isStreaming, messages)`
   首行 `if (!isStreaming) return null`（`useChatStreamingLifecycle.ts:217`）。只要
   `isStreaming===true` 且最后一个 block 是未配对 `tool_result` 的 `tool_use`，就**永远**显示
   "Running: <tool>"。
2. **`isStreaming` 靠 SSE 终结事件触发 `setIsStreaming(false)`，漏一个就钉死** — 在快速 abort+recycle
   churn（Stop / 发新消息 → abort 旧流 → 后端 STREAMING→IDLE disconnect-recovery + 子进程
   recycle→cold 重生）中，该 turn 的 `result` / `[DONE]` 没被前端处理 → `isStreaming` 钉死 true → spinner 钉死。

   > **✅ 真因已锁（2026-06-30，AC5 live trail 抓到用户原始事故，前后端时间线对齐）。**
   >
   > **演进**：(0) Kiro 早先「gen guard `:2297` 中途自作废」**被证伪**（18 个 `incrementStreamGen`
   > 全在终结/新流边界、无一在活流中途；`latestCompleteGen` 已修该家族 `:3918/:3964`；cross-tab 也
   > 证伪：有 tabId 时 guard 已 per-tab）。(1) 一度判为「(b1) SSE 丢失 / (b2) gen 污染 二选一、静态
   > 不可判定」。(2) **run_251ea3ee 的常开探针抓到铁证，锁定为 (b1) 的精确变体 + 上游机制：**
   >
   > **真因 = stream supersession（新流在旧 turn 仍在飞时抢占 `streamGen`，旧流的终结事件被守卫丢弃）。**
   >
   > live trail（session f790f427 / tab bce9e6a5，前端 UTC、后端本地，+8 对齐）：
   > - 后端 19:02:04 `result_usage` + `streaming→idle`（gen-6 turn 真实结束）。
   > - 同刻前端 `[OT01-GenGuard] discard context_warning/system_prompt_metadata`，**capturedGen 6 / tabGen 7**
   >   → gen-6 turn 的 `result` 投递了却被丢（gen 已被顶到 7）。
   > - 19:02:19→19:03:34 gen-6 handler 持续丢 heartbeat（tabGen 7→8）；19:03:44 `[OT01-Complete] [DONE] dropped`
   >   capturedGen **6** / liveGen **8** → 连 `[DONE]` 也被丢 → 该 turn 的 `setIsStreaming(false)` 永不执行。
   > - 后端 19:04:26 `Received stop request` → 19:04:46 cold→idle→streaming(--resume) = **用户 Stop+resume，DB 内容补出**。
   >
   > 判定：**是 (b1) 的精确变体——终结事件「投递了但被 gen guard 丢弃」，不是网络丢，也不是 (b2) cross-tab**
   > （全程 `globalStreamGen==tabStreamGen`、`capturedTabId==activeTab`）。**上游**：`streamGen` 6→7→8 是在
   > gen-6 的 `result` 到达**之前**就被推进的——即旧 turn 还活着时，同一 tab/session 上又起了新流把 gen 顶上去
   > （后端 `SESSION_BUSY` 今日 16 次佐证旧流未结束、新流又上同一 session）。
   >
   > **仍待锁的最后一环**：到底是谁在旧流仍活时起了新流（候选：auto-resend / reconnect-restream /
   > queued-drain 过早触发 / streaming 中再发消息走了并行流而非 queue）。这决定 B-3 的精确落点。
3. **唯一救援是兜底轮询，而且很慢** — reconcile loop 每 **15s** poll `/streaming-state`，发现
   「后端 idle 但前端 streaming」→ 标记 `_idleStreamingSince` → 再等满 **`settleMs=30_000`（30s
   沉降窗口，`streaming-guards.ts`）** → 下个 poll 才 `force-clear`。**假 spinner 实际存活最长 ~45s**，
   churn 不断重置时钟时更久 → 你看到的「10 几分钟」。
4. **症状 #3 的机制** — `isStreaming` 钉死期间，渲染层被 streaming 相位门挡着不去 reconcile DB
   （`MessageStore.reconcile` 在 streaming phase 是 NO-OP）。Stop→resume 触发一次 DB 重读 →
   早已存好的 response 才「补」出来。**这证明数据一直在，问题纯在「前端状态没跟上后端」。**

### 2.3 日志铁证（`~/.swarm-ai/logs/frontend.log` 全量）

| 信号 | 含义 | 计数 |
|------|------|------|
| `[StreamReconcile] Backend idle but tab streaming — forcing clear` | **假 spinner**（后端 idle / 前端 streaming），靠轮询救 | **204** |
| `[StreamReconcile] Backend streaming but tab idle — arming spinner` | 反向漂移（后端 streaming / 前端 idle） | **12** |
| 同 tab `forcing clear → 30s → arming spinner` 配对 | 前端把一个 turn 的 `result` **和**下个 turn 的 `session_start` 都漏了 | 4224dc3d、54e8689b（06-30） |

> 修正记录：Kiro 初报 4 / 2 次（只数了一个窗口），实际 **204 / 12**，频率比初判高 ~10 倍。
> 根因方向 Kiro 判对，量级由 Swarm 全量日志修正。204 次假 spinner 正是「转 10 几分钟」的铁证。

### 2.4 这是 memory 里的第 5+ 次同类病

EVOLUTION / MEMORY 反复记录：`isStreaming` 作为「第二真相源」是 OT01 的结构性病根，处方一直是
同一句——**让后端 session 状态成为 spinner 的唯一真相源**。前 33 次补丁都在 layer 1-9
（backend/SSE/store/reconcile-timing）打补丁，没动这个「第二真相源」本身。

### 2.4.1 regression 还是原始债？——原始架构债，近期被放大（git 实证）

结论：**不是某次改坏的 regression，是原始架构债；最近因 recycle 更激进而高频化。**

| 件 | 引入 | 性质 |
|----|------|------|
| `deriveStreamingActivity`（spinner = `isStreaming` 纯派生） | **2026-03-01**（chat 体验清理 spec） | **原始设计**——「前端 isStreaming 做 spinner 真相源」从 ~4 个月前就在 |
| StreamReconcile 救火循环（force-clear / arming） | 2026-06-07 | 对**首次** spinner-hang 的**补丁**（memory 同日「Spinner-Hang Root Cause」） |
| `SWARMAI_SELF_HEAL` 默认 ON | 2026-06-18 | ↑ recycle 频率 |
| tool_call_leak 检测 → kill+resume | ~2026-06-24 | ↑ recycle 频率 |
| route-A churn-immune 硬上界 | 2026-06-30 | 最新补丁（与前 33 个同层） |

三点判定：
1. **病根原始**：「前端 `isStreaming`/`streamGen` 当第二真相源、靠观测事件对齐后端」自 2026-03-01 即存在，
   天生扛不住后端 recycle+resume——非某次改坏。
2. **「复发」即铁证**：此病 **5+ 次复发、33 次补丁**（6-07 / 6-17 / 6-21 / 6-26×N / 6-27…）。真 regression 修一次
   就好；修 5 次还回来 = 定义上的**原始架构债**，每次「修」都在补丁层打转，没动 3-01 那个真相源。
3. **近期被放大（=「感觉像 regression」的来源）**：触发它的 churn 在 6 月中下旬加码（self-heal 默认 ON 6-18 +
   tool_call_leak kill 6-24）→ recycle 大涨 → supersession 从「偶发卡一下」升级为「recycle 风暴卡 10 分钟」。
   **病根没变，暴露它的炮火变猛了。**

→ 这也解释为何 route-A 这类补丁治不了根：它与过去 33 个同层；只有 B-1（后端 SSOT）动到 2026-03-01 那个原始
真相源，才是治本。

### 2.5 极端形态：recycle 风暴（route-A 的覆盖缺口 + B-1 最强证据）

2026-06-30 19:09–19:15 抓到一次 **>120s spinner**（route-A 的 ≤120s 兜底**没救**），现场比基础 case 严重得多：

**后端 5 分钟 recycle 4 次**（session f790f427）：
- 19:09:20 用户 Stop → flush_recycle（poisoned）
- 19:09:40 用户 Stop → flush_recycle（poisoned）
- 19:12:50 `tool_call_leak_detected` → force_kill_tree（后端自动杀）
- 19:14:41 `tool_call_leak_detected` → force_kill_tree（后端自动杀）

每次 recycle = dead→cold→idle→streaming（`--resume` 续上**同一逻辑 turn**），而前端**每次都当成新流、把
`streamGen` 顶一格**。trail：丢弃的不再是 metadata/heartbeat，而是**真正的答案内容**——`assistant` /
`text_start` / `content_block_stop`，**capturedGen 22 / tabGen 27 共 53 条全丢**；还有一条 **gen-5 老流到现在
还在吐内容、也全丢** → **多条被遗弃的活流同时在漏**。gen 18→20→21→23→25→27 与那 4 次 recycle 一一对应。

**🔴 route-A 缺口（实质改变对其覆盖面的认知，Swarm 必须知道）**：`hard_cap` 分支放在
`backend_streaming` / `resuming` / `active`(cold) 四个 alive 守卫**之后**（为不误杀活流，正确）。但 recycle 风暴里
后端**永远**在 streaming / cold / resuming 其一 → verdict 恒为 `reset-and-skip` → **`hard_cap` 永远到不了** →
所以 >120s。**route-A 明确覆盖不了「后端在 churn/streaming、但前端在丢弃它内容」这一类**（日志佐证：该窗口
`arming spinner`/`forcing clear` 在 cold↔streaming 间反复 flip，稳不下来）。

**🟢 B-1 最强证据**：前端的 **gen-per-stream 模型在结构上无法存活后端的 recycle+resume**。而后端那套
recycle+resume 是**有意设计且正确的**（memory KDD 2026-06-30：Stop→COLD / tool_call_leak / self-heal 都对）。
一个 turn 在后端被 recycle 4 次 = 在前端被切成 4+ 段、每段都把前一段遗弃成 stale 丢掉。**内容全在 DB、session
状态是真相，两者都穿越 recycle 存活；只有让 UI 由它们驱动（B-1/B-2），recycle 风暴才会对用户隐形。**
`isStreaming`/`streamGen` 恰恰是唯一扛不住 recycle 的东西。

> **B-3 再收窄**：触发不只是「新 send」，而是 **recycle/resume 把同一逻辑 turn 的在产流遗弃**。修复要让
> resume 续的是**同一条权威流**（gen 跟着 resume 走、不是 +1 顶掉），并把真正死掉的旧流 abort 掉别再漏。

---

## 3. 设计原则

1. **唯一真相源（SSOT）**：spinner 的「这个 tab 在不在跑 / 跑哪个 tool」由**后端权威状态**驱动，
   不由前端拼凑的 `isStreaming` 派生。后端 `/streaming-state`（`backend/routers/chat.py:812-916`）
   **已存在且已返回所需全部字段**（`streaming` / `state` / `post_disconnect_flushing` /
   `waiting_input`）——这是「激活已有能力」，不是造新机制。
2. **错误状态结构上不可能**（STEERING #1：结构性预防 > 兜底）：不再依赖「前端完美观测每个终结
   事件」。漏一个事件应在**秒级**自愈，而非 45s+。
3. **🚨 不许拆旧守卫**（O030 陷阱，本设计头号风险）：`forceClearStreamVerdict` 里的 `settleMs` /
   `postDisconnectFlushing` / `backendIsStreaming` 短路守卫**全有来历**——它们是为防「长 turn 后端
   还在 flush 时被误清、截断答案 / 误清活流」才加的（`streaming-guards.ts`、
   `useChatStreamingLifecycle.ts:1276` 注释）。**任何「缩短 dwell / 快节奏轮询」的改动，必须证明
   不会重新打开这些守卫当初堵的洞**（截断答案、误清活流）。
4. **不盲改**（OT01 第 34 次防线）：BUILD 前先解盲观测，用真实 freeze 日志确认改动命中根因。
   注意 `logForwarder.ts:88-95` **只 patch `error`/`warn`** → 探针必须用 `console.warn`/`error`，
   用 `console.debug`/`log` 在打包生产环境是**瞎的**（不落盘）。

### 3.1 不变量（实现后必须恒真）

- **INV-1**：后端该 session 已 idle 后，`isStreaming` 必须能在 **≤ 5s（硬上界 ≤ 15s）** 内变 false，
  无论前端是否观测到 `result`。
- **INV-2**：`streamGen` 类代际只在**真正开启新流**（new send / 显式 abort+restart）时变更，
  **绝不**在一条仍在飞的流的生命周期内变更到使其自身事件作废。
- **INV-3**：tool-activity 指示器在 `tool_result` 配对 / turn 边界**确定性清除**，不靠 buffer 末块形态。
- **INV-4**：「backend genuinely streaming / flushing → never force-clear」承重不变量**保留**（防误杀长 turn）。
- **INV-5**：每条恢复 / 守卫路径都有一个**会真正进入它**的测试（项目 meta-lesson，第 5+ 次）。

---

## 3.5 架构北极星：前端 = 投影，后端 = SSOT

> 这一节是所有改动的「北极星」。B-1/B-2/B-3 必须朝它走，**不得反向再加镜像**。

### 根上的病：权威被复制了两层

streaming 的**传输**（SSE 推 token 做低延迟显示）没问题。坏的是**状态权威模型**：前端维护了一套
**自己的权威 streaming 状态机**（`isStreaming` + `streamGen` + `tabState.streamState` + reducer），
去**镜像**后端的 session 状态机（COLD→IDLE→STREAMING→WAITING_INPUT→DEAD），靠「在一条可中断、
会 recycle、会 churn 的通道上完美观测每一个 transition 事件」保持一致。这是结构性病根——**权威被复制**，
而且是两层：

| 维度 | 唯一真相源（应有） | 前端搞的镜像（病根） | 现状同步手段 | 复发的 bug |
|------|------|------|------|------|
| **内容** | DB（后端每 block 落盘，canonical） | 乐观 streamed buffer（第二权威） | 事件 + reconcile 合并 | OT01 truncation / 内容冻结 |
| **状态** | 后端 session 状态机 | `isStreaming` 派生标志（第二权威） | 观测 `result` + 15s 轮询 | 假 spinner / 双向漂移 |

我们追的每个 bug 本质都是「某个镜像和它的源头对不上」。33 次补丁都在「把镜像同步得更准」上打转，
从没质疑「前端为什么要持有这份权威」。**你不可能通过更努力地观测一条 lossy 通道来根除同步失败**——
只要镜像还在，多 tab × abort × resume × recycle × churn 的边界条件就无穷无尽。

### 目标模型：前端是纯投影（projection），不持有权威

- **liveness / spinner**：纯由**后端 session 状态**决定（推送或快轮询）。前端不再问「我有没有看到
  `result`」——它只有「后端说这个 tab 在跑吗」的投影。
- **内容**：streamed token 是 DB-canonical 消息之上的**一次性乐观覆盖层（overlay）**；DB（经快游标/
  订阅）才是源，token 只负责「抢先画」。reconcile 变 trivial——覆盖层可丢弃，不是第二权威。
- 一句话：**前端不该有 `isStreaming` 这个「真相」，它只该有一个后端状态的投影。**

```
现状（两套状态机互相镜像）          北极星（单一权威 + 纯投影）
后端状态机  ⇄(漏拍)⇄  前端状态机      后端状态机(唯一) ──push/快轮询──> 前端投影(只读)
DB(canonical) ⇄(merge)⇄ streamed buffer  DB(唯一) ──cursor──> UI ← token 乐观覆盖层(可丢弃)
```

### B-1/B-2/B-3 = 通往北极星的 strangler 迁移步

| 步 | 把哪份权威交还源头 | 北极星方向 |
|----|------|------|
| **B-1** | spinner 权威 `isStreaming` → 后端 session 状态 | 状态投影化 |
| **B-2** | 内容权威 streamed buffer → DB（冻结时一次性 reconcileFromDb） | 内容投影化 |
| **B-3** | 消除 stream supersession：旧 turn 未终结时不让新流抢占 gen | 缩小镜像表面积 |

### 北极星禁止的反模式

- ❌ 新增任何「前端推导的权威 streaming 状态」（再加一个 `isXxxing` 标志/Set/reducer 分支）。
- ❌ 用「再加一个守卫 / 再调快一点轮询」来补镜像漂移（治标，养病）。
- ❌ 让 streamed buffer 在 DB 之外承担「内容真相」（任何 DB-loses 的 merge 分支）。

### 真正的反转是独立项目，不在本 run

「彻底反转权威 + 拆 2700 行 god-file（`useChatStreamingLifecycle`）」是 memory 里拖了几个月的真正杠杆，
也是这仓库回归密度最高处。**不在着火时重写**：本 run 只做 B 三件套（已是朝北极星的增量），
把「权威反转 + god-file 拆解」记为**独立后续项目**（见 §7 / 北极星追踪），有意识排期。

---

## 4. 方案（A / B / C）

| | 约束 | What | Effort | Risk | Tradeoff |
|--|------|------|--------|------|----------|
| **A** | 立即缓解 | 只缩短 stuck-streaming 那一路的 reconcile dwell（`settleMs` 30s→~3s，poll 15s→可选更快），假 spinner 存活从 ~45s 降到几秒 | **S** | 低* | 没消除「漏事件→钉死」的根；churn 下仍可能反复；*改 `settleMs` 直接踩原则 3——**必须验证不误清活流/截断** |
| **B** ⭐ | 结构性正解 | spinner 的「在不在跑」改由**后端权威状态主驱动**：`/streaming-state` 从「15s 兜底」升级为 streaming 期间「主驱动 + 快节奏 ~2-3s」；`isStreaming` 降级为乐观本地提示，后端状态一到立即覆盖；tool-activity 在 `tool_result`/turn 边界**确定性清除** | **M** | 中 | 真正系统性，根除第 5+ 次复发；god-file 易回归，需重测所有 OT01 守卫不被破坏 |
| **C** | 后端推送 | 不靠前端轮询，后端 streaming-state 变化通过 SSE **主动推送** state-change 事件，前端被动接收 | **L** | 高 | 最干净（零轮询延迟），但动 SSE 协议 + 所有 channel 适配，blast radius 最大；过度工程 |

### 推荐：**B**，A 作为同一 run 内的「先止血」子步骤

- XG 明确「修不好别停」——A 只把 45s 缩成几秒，根没除，churn 下还会犯，第 35 次复发只是时间问题。
- C 动 SSE 协议、跨所有 channel，过度工程；B 已能让「假 spinner 结构上不可能」。
- B 的核心资产**已存在**：`/streaming-state` 已返回全部权威字段，reconcile loop 已在消费它——
  B 是把它从「兜底」提为「主驱动 + 确定性清除指示器」，是**激活+升级，不是新建**。

### B 的强制范围（三件套，缺一不可）

⚠️ 「B = 后端状态主驱动 spinner」**只解决症状的一半**。XG 的症状有两面：(1) spinner 假转、
(2) streaming 内容冻结。只做「后端驱动 spinner」会让 spinner 诚实（后端 idle 就秒清），但
**内容在 turn 中途照样冻结**——只是从「卡 10 分钟」变成「卡几秒后 spinner 消失、内容靠 DB
reconcile 补出」。比现状好，但没治本。因此 B 必须**同时**包含以下三件：

| # | 件 | 解决 | 角色 | 落点 |
|---|----|------|------|------|
| **B-1** ⭐ | **后端状态主驱动 spinner** — `/streaming-state` 从「15s 兜底」升级为 streaming 期间「主驱动 + 快节奏 ~2-3s」；`isStreaming` 降级为乐观本地提示，后端状态一到立即覆盖。**trail 已证 B-1 直接溶解整个 bug 类**：事故中后端 19:02:04 就 idle，若 spinner 由后端状态驱动，这一刻就清——丢不丢那个 `result` 都无关 | 症状 (1) 假 spinner | **类级根治** | `useChatStreamingLifecycle.ts` reconcile loop + `deriveStreamingActivity` |
| **B-2** | **冻结内容的恢复路径** — 检测「后端 `last_persisted_seq` 前进了，但前端 buffer 没动且 `lastRealEventRef` stale」→ 开一条**相位安全的一次性 `reconcileFromDb`** 窄通道，打破 §2.4 的 streaming phase-gate 死锁（幂等、按 id 合并、more-complete-content-wins） | 症状 (2) 内容冻结 | backstop | `MessageStore` 相位门 + reconcile loop + `/streaming-state` 加 `last_persisted_seq`/`last_activity_ts` |
| **B-3** | **触发点根治 — 已锁为 stream supersession（非 `streamToken`）**。AC5 trail 证：旧 turn 仍在飞时新流抢占 `streamGen`（6→7→8），旧流的 `result`/`[DONE]` 被守卫丢弃。修复方向：**旧 turn 未终结前不起新流——要么 queue 到旧流终结，要么 abort 旧流并把 abort 当作确定性终结（清 `isStreaming`）**，使「仍未到达的终结事件被孤立丢弃」结构上不可能。⚠️ 原 `streamToken`「防中途自作废」设计**作废**（该机制不存在）。最后一环（谁起的新流：auto-resend / reconnect / drain / streaming 中再发）待再抓一次日志锁死 | 让冻结**根本不发生** | 治本 | gen-bump 调用点 + send/resend/reconnect/drain 路径 + abort 终结化 |

**依赖关系**：没有 B-3，B-1/B-2 永远是兜底（冻结仍会发生，只是快速补救）；没有 B-2，B-1 只让
spinner 诚实却不还内容；三件齐备才满足设计原则 2「错误状态结构上不可能」。**B-1/B-2 必须继承
§3 原则 3 的旧守卫语义**（`flushing`=still alive=保持 spinner），不得绕过（守 INV-4）。

### 落地顺序（同一 goal run 内）

1. **Cycle 1 — 解盲观测**（前置）：✅ **已由 run_251ea3ee 交付（AC5）**。常开 `console.warn`（非 debug——
   logForwarder 只落盘 error/warn）在两个决定性点：gen-guard discard（`:2313`）+ complete-handler no-op
   （`:3936`，丢失的 `[DONE]`）。带消歧字段 `capturedTabId vs activeTabIdRef.current`（cross-tab vs own-turn）
   + tab/global `streamGen`。**现在等一次真实 freeze 的 trail 来判定 (b1) vs (b2)，再定 B-3。**
2. **Cycle 2 — 止血（route-A churn-immune 硬上界，≈A）**：✅ **已由 run_251ea3ee 交付**。新增
   `_streamingSinceHardStart`（SEND-owned、churn 不可重置的绝对时钟）+ `forceClearStreamVerdict`
   的 `hard_cap` 分支（默认 120s），**放在全部四个 alive 守卫之后** → 永不误清活流（守 INV-4 + O030）；
   mutation-proven。比纯 A（缩 settle）更优：是绝对地板而非补漂移的条件守卫。**效果：最坏从 10 分钟封到 ≤120s**。
   注意：它只是兜底守卫（北极星禁止作为终态），且 B-2 的内容补救走既有 force-clear DB reconcile（`:1591`）→
   **是 ≤120s 延迟补，不是冻结即时 reconcile**。
   **🔴 已确认覆盖缺口（§2.5）**：`hard_cap` 在四个 alive 守卫之后，recycle 风暴中后端恒为 streaming/cold/
   resuming → hard_cap 永不触发 → 实测 >120s。**route-A 不覆盖「后端 churn/streaming + 前端丢弃其内容」**——
   只有 B-1 能解。
3. **Cycle 3 — 根治（B 三件套，待 Cycle 1 trail）**：
   - **B-1** 后端状态主驱动 spinner（+ `tool_result`/turn 边界确定性清除 tool-activity，INV-3）；
   - **B-2** 冻结内容恢复路径（相位安全一次性 `reconcileFromDb` + `/streaming-state` 加 seq/ts 字段）；
   - **B-3** 触发点根治（消除 stream supersession：旧 turn 未终结不起新流 / abort 即终结，INV-2；
     **非** `streamToken`，原前提已作废）。
4. **回归锁 + mutation**：测试复现「漏 result→spinner 钉死」与「buffer 冻结+后端推进」，修复后转绿，
   翻转修复转红；承重守卫测试（INV-4）全绿。

---

## 5. 关键文件（已核实）

| 文件 | 角色 |
|------|------|
| `desktop/src/hooks/useChatStreamingLifecycle.ts:217` | `deriveStreamingActivity` — spinner 纯派生源 |
| `desktop/src/hooks/useChatStreamingLifecycle.ts:2297` | generation guard — 静默丢弃 stale 事件（头号嫌疑） |
| `desktop/src/hooks/useChatStreamingLifecycle.ts:1186-1628` | reconcile loop（15s poll + 双向救火 + force-clear） |
| `desktop/src/hooks/streaming-guards.ts:308-440` | `forceClearStreamVerdict` + `settleMs=30_000`（⚠️ 旧守卫，勿拆） |
| `backend/routers/chat.py:812-916` | `/streaming-state` 端点（权威状态源，**已返回所需全部字段**） |
| `desktop/src/services/chat.ts:481-505` | `getStreamingState` 前端类型映射 |
| `desktop/src/services/logForwarder.ts:88-95` | 只 patch `error`/`warn` → 探针用 `console.debug` 在生产是瞎的 |
| `desktop/src/stores/MessageStore.ts` | streaming 相位门控 reconcile（解释症状 #3） |

---

## 6. 测试计划 / DoD

### 测试（每条都「真正进入路径」，INV-5）

| ID | 验证 | 类型 |
|---|---|---|
| T0 | Cycle1 探针在「中途 gen 变动 + 后续 result」序列下落盘 discarded + bump reason | 诊断 |
| T1 | turn 中途 gen 变动后，`result` 仍被处理、`isStreaming→false`（旧实现 RED） | 单测 |
| T2 | `streamToken`：旧流事件被丢、当前流事件被收 | 单测 |
| T3 | 后端状态主驱动：后端 idle → `isStreaming` ≤5s 变 false，无需观测 `result`（INV-1） | 单测 |
| T4 | 硬上界：churn 反复重置 settle，但独立硬时钟超界后强制恢复 | 单测 |
| T5 | **承重回归（INV-4）**：正常长 turn（后端 streaming / flushing、buffer 正常推进）**绝不**被 force-clear / 误 reconcile | 单测 |
| T6 | tool-activity：后端 idle 时裸 tool_use 末块不显示 running（INV-3） | 单测 |

### DoD（goal run 收敛条件）

1. OT01 探针可落盘 `frontend.log`，带 `isStreaming`/`backendState`/`streamGen`/`_rafId`/`document.hidden` 等判别字段。
2. 真实 freeze 日志确认根因层（预期：`isStreaming` 钉死 + 后端 idle），非推断。
3. 长 turn 进行中 spinner 与后端状态秒级一致；漏 `result` 不再钉死。
4. vitest 回归复现「漏 result→spinner 钉死」，修复后绿，mutation 翻转转红。
5. 旧守卫（截断/活流保护）测试**全绿**——证明没拆当初堵的洞。
6. STORE-CLOBBER 同根性判定记录在案（见 §7）。

---

## 7. 伴生项（独立 follow-up，本 run 不混入）

**STORE-CLOBBER**（`reason=destroy, sessionId=undefined, prevChars→0`，frontend.log 高频）：新 tab 的
store 从未绑 sessionId → 无 DB 恢复路径。**初判与 OT01 不同根**（tab 关闭/销毁路径，非 streaming 状态
漂移），列为独立 follow-up。Cycle 1 解盲后若日志显示它与 freeze 同时出现，再重判同根性。

**`tool_call_leak_detected`（独立 bug，与 OT01 同病：检测-对抗 vs 无害化）**

现象：模型（Opus，原生 tool-use 模式）偶尔把 tool-call 当**纯 XML 文本**吐出（`<invoke name="...">`），
SDK 当 `TextBlock` 交回；`streaming_orchestrator.py:669` 正则检测 → 丢块 → `force_kill_tree` + 裸 `--resume`
重试。低频（今日 2 次，均在 f790f427），但**与 OT01 因果相连**：每次 leak→kill→resume 都给前端顶一格 gen、
喂进 §2.5 的 recycle 风暴。

**根因诊断（已核实，非缓解）**：
- 触发 = **上下文语法污染**。全仓库仅 4 个文件含字面 `<invoke name=`，全是 meta（检测器 `streaming_orchestrator.py`/
  `session_utils.py`、其测试、`s_autonomous-pipeline/REVIEW_PATTERNS.md` 的 RP42 举例）。泄漏发生在 pipeline 跑
  OT01 的 turn——上下文同时塞着这些文件 → 模型被字面语法 priming → 把它当文本复现。**这些都不是「教文本协议」的指令，是
  incidental 举例。**
- 放大 = **裸 `--resume` 重放致因**。日志铁证：同一 resume id `e9d7c08d`，Retry 1→2 连泄两次（19:12:50、19:14:41）
  → 同上下文同输出 → **按构造的死循环**。

**🚫 不做症状缓解**（降污染 / 改 recovery 都是「在现象上缝」）：模型吐文本这件事**做不到结构性不可能**
（概率模型 + meta 仓库必然把该语法读进上下文）。可结构性避免的是**伤害**，伤害是自找的。两条不变量让伤害**按构造消失**：

- **INV-L1（接住意图，别丢弃）**：文本通道里的 tool-call 仍是一个 tool 意图。正确响应是用与检测器**同一套正交门控**
  （strip fenced/inline-code + 要求消息内无真实 `ToolUseBlock`）把 `<invoke>` 解析为 tool_use 并执行/续跑——而不是
  丢块 + 整条 recycle。「丢一个有效意图再重生」是系统强加的伤害。若精度上不敢执行（meta turn 讨论语法的误执行风险），
  退一步也必须是**同上下文就地纠正**（注入「请用工具通道重发」并续跑），**绝不 kill+recycle**。
- **INV-L2（重试必改输入）**：由上下文确定性致因导致的失败，重试时**必须改变输入**（去掉致因 priming / 加纠正约束），
  否则按构造无限循环。裸 `--resume`（原样重放致因）违反它，必须废弃。满足这条，retry-loop 及其喂给 recycle 风暴的部分
  直接不可能存在。

**元层面**：这与 OT01 是**同一种病**——系统「**检测到坏状态 → 对抗它（丢/杀）**」，而非「**让坏状态无害 / 成为权威**」。
OT01 无害化 = 后端状态做 spinner SSOT（recycle 对 UI 隐形）；tool_call_leak 无害化 = 接住文本 tool 意图 + 重试必改输入
（泄漏对 turn 隐形）。两者共用北极星：**别加检测-对抗的守卫，让错误状态结构上无害。**

**北极星追踪（独立后续项目，非本 run）**：彻底「反转权威（前端=纯投影）+ 拆解 2700 行
`useChatStreamingLifecycle` god-file」。本 run 的 B 三件套是朝它的 strangler 增量；这一步是把增量
收口为终态——删除 `isStreaming` 作为权威、streamed buffer 降级为纯覆盖层。memory 里拖了几个月的真正
杠杆，需有意识独立排期 + 充分回归预算，**不在热修期做**。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| **拆旧守卫导致截断答案/误清活流**（O030，头号风险） | B 改动前逐条复核 `settleMs`/`postDisconnectFlushing`/`backendIsStreaming` 守卫来历；后端状态主驱动必须**继承**其语义（flushing=still alive=保持 spinner），而非绕过 |
| god-file 回归（2700 行 + chat.py CRITICAL 模块） | strangler 式：旧 reconcile 兜底保留到新主驱动路径过集成测试；adversarial 重跑所有 OT01 守卫测试 |
| 盲改（OT01 第 34 次） | Cycle 1 解盲前置，真实 freeze 日志确认命中根因再改；探针走 warn/error 级（否则生产不落盘） |
| 前端改动不进生产 | `npm run build:all` + 重打包 + 退出重启 app（dist/.app 时间戳为证）；后端 2a 字段 → `prod.sh build` + daemon 重启（重启需 XG 批准） |
| auto-commit 卷走改动 | 改完立即 `git commit` 指定文件，main-only |

---

## 9. Open Questions

1. Cycle 1 铁证若显示 bump 来源**不是** gen guard（而是别的 store 写入路径 / active-tab 门控），
   Cycle 3 设计需相应调整。
2. `streamToken` 改造对 queued-drain / auto-resend / permission-continue 等「同 tab 续流」路径的
   影响范围需逐一过（这些是历史 `incrementStreamGen` 调用点）。
3. dwell 硬上界取值需结合真实长 turn 分布（避开正常重 cold-resume 的 think 阶段，防误清）。

---

## 10. 决策记录

- 本病是 OT01 / `isStreaming` 第二真相源家族第 5+ 次复发；处方与 memory 一字不差：
  **backend 状态做 SSOT，让错误状态不可能**。
- **不**单独采用 A（缩短 settle）作为终态：只救 ~45s 短类，救不了 churn 拖长类，且踩 O030 守卫风险。
- **B 不是单点改动，是三件套（B-1 后端驱动 spinner + B-2 冻结内容恢复 + B-3 代际触发根治），缺一不可**：
  没 B-3 永远是兜底，没 B-2 只诚实 spinner 不还内容。仅做「轮询调快」不算 B。
- 顺序：解盲铁证（Cycle1）→ 止血（Cycle2≈A，可选，需证不破守卫）→ 根治（Cycle3=B 三件套）→ 回归锁+mutation。
- C（SSE 推送）作为未来可选优化，本 run 不做（过度工程、blast radius 跨 channel）。
- **根因已锁（AC5 live trail，2026-06-30）**：经三步演进——(0) Kiro「`:2297` 中途自作废」**被证伪**；
  (1) 一度判「(b1)/(b2) 静态不可判定」；(2) 探针抓到用户原始事故，锁定为 **stream supersession**：
  旧 turn 仍在飞时新流抢占 `streamGen`（6→7→8），旧流的 `result`/`[DONE]` 被 gen guard 丢弃 →
  `setIsStreaming(false)` 永不执行。是 (b1) 的精确变体（终结事件**投递了但被丢弃**，非网络丢），
  排除 (b2)。详见 §2.2 ✅ trail。
- **B-3 重定义**：从「`streamToken` 防中途自作废」（前提已作废）改为「**旧 turn 未终结不起新流 /
  abort 即确定性终结**」。**B-1（后端 SSOT spinner）经 trail 证实可直接溶解整个 bug 类**。
- 仍待锁：谁在旧流仍活时起了新流（auto-resend / reconnect / drain / streaming 中再发）。
- **极端形态已抓到实证（§2.5，>120s）= recycle 风暴**：后端 5 分钟 recycle 4 次（2 Stop + 2
  `tool_call_leak`），每次 resume 都被前端当新流顶 gen，遗弃在产流，53 条真实内容被丢。
  **确认 route-A `hard_cap` 覆盖不了此类**（恒被 alive 守卫短路）→ 这是 **B-1 必须做**的最硬证据。
  `tool_call_leak_detected` 列为独立 follow-up（§7）。
- **交付现状**：run_251ea3ee = Cycle 1（AC5 诊断常开）+ Cycle 2（route-A churn-immune 硬上界，≤120s 止血）。
  **B-1/B-2 即时版 / B-3 均未做**——前端仍持有 `isStreaming` 第二真相源，北极星未达，只是止血。

---

## 11. Post-mortem — 后来加的那些，是在做正确的事吗？

抛开 2026-03-01 的原始病根，单独审视「后来加的东西」。不是「都是补丁所以都错」——分三桶。

### 三桶

**桶 A — 真做对了，且在对的层（后端子进程健康）**
PIT01 Stop→COLD recycle、zombie 检测、RSS caps、`--resume` 续接、self-heal 的 checkpoint/wrap-up。
它们让**后端成了健壮、自愈、可恢复的权威**。没错——**恰恰因为这桶，后端才「配得上」做 SSOT**；
B-1 之所以是显而易见的解，正是这桶挣来的（后端状态 + DB 可信、能穿越崩溃）。

**桶 B — 直觉对、落点错（补丁层，且变成承重墙）**
StreamReconcile 救火循环（6-07）、route-A 硬上界（6-30）。作为**止血兜底**合理（没法一夜重写前端），
但：(1) 是「检测漂移→对抗」，没消除漂移；(2) **变成承重墙**——被当成了解；(3) 恰在最需要时失效
（recycle 风暴里 hard_cap 被 alive 守卫短路）。= 为错误的理由做了件看似合理的事，且给了**虚假安全感**。

**桶 C — 在加重病 / 跟错对象较劲**
- tool_call_leak 检测→kill→裸 resume：检测-对抗 + 重放致因，自造死循环（§7）。
- 一大堆 `isStreaming`/`streamGen`/`latestCompleteGen` 修补：**整类都在「让镜像跟得更准」**——它们存在的
  唯一理由就是那面本不该存在的镜子。每个都是「给一个不该需要它的设计打的正确补丁」。

### 元判断：两半在朝相反方向进化，没人认领中间那条缝

- **后端**这半：「我是权威，我会自由 recycle + resume。」（桶 A，对）
- **前端**这半：「我自己存一份权威，努力追踪你的。」（桶 C，错）

**后端越擅长 recycle，前端那面镜子就越碎。** 6-18 self-heal 默认开、6-24 leak-kill 都是后端在「正确地」
变得更爱 recycle——而这正是把前端镜像从「偶尔对不上」打成「recycle 风暴卡 10 分钟」的炮火。两半各自优化
自己的正确性，**没有人拥有它俩之间那条缝（streaming-state 同步）**。所有 bug 都住在缝里，33 个补丁从来是
「哪半冒烟修哪半」，从没修缝。

### 结论

- 后端那些：**是**，而且让 B-1 成为可能。
- 前端镜像补丁：**不是**——在一个被后端自身（正确的）进化日益拖垮的设计上加码；每个局部理性，合起来是
  「一边擦亮镜子，一边被镜的东西学会瞬移」。
- 真正的失误**不是「加了补丁」**（止血有时必要），而是**「把补丁当成解、几个月没排 B-1（修缝）」**。

### 留给未来的规则（防再次「哪半冒烟修哪半」）

1. **认领那条缝**：任何动 streaming-state 的改动，必须同时回答「这让前端更依赖自己的镜像，还是更信任后端权威？」——
   只接受后者。
2. **新增前必问**：这是在「让某状态权威/无害」（✅），还是在「检测坏状态并对抗它」（🚩 桶 B/C，先停下想）？
3. **跨半改动要看对侧**：后端加重 recycle/kill 前，先确认前端能否无害承受其 churn（本案没做 → 放大成风暴）。
4. **补丁标注保质期**：止血补丁（桶 B）落地时即写明「interim，待 B-x 替代」，不许沉淀成承重墙。

---

## 12. 重评估（2026-06-30，B-1/B-2 + bounded-leak 落地后）— B-3 降级

落地进度（SwarmAI run_35aa06b1 / run_e80f4c9b / run_37008f2d）：
- **B-1**（`9366c01b`）：reconcile cadence 15s→3s（streaming 期），spinner ≤3s 自愈。authority 未动。
- **「B-2」（shipped, `13b0c9ab`+`5094f9da`）**：terminal(`dead`) 跳过 30s settle、立即 force-clear。
  ⚠️ **注意混淆**：这与本文档原 B-2（reconcileFromDb 内容恢复）**不是一回事**——shipped 的是 force-clear
  细化（spinner 清得快），**真正的「内容恢复」件仍未做**。
- **bounded tool_call_leak**（`9d941eeb`，= INV-L2）：1st leak→corrective-resume（改输入）、2nd→clean terminal，
  断掉自循环。**删掉了风暴 4 次 recycle 里的 2 次（leak 那两次）。**

### 重评估结论：B-3（保 live 流过 recycle）降级，可能直接砍

严重症状基本已解：「卡 10 分钟」→「≤3s blip + DB 补」；风暴最大触发源（leak 自循环）已断 → 多次 recycle 风暴
变稀有。残余仅剩：**风暴持续期**（后端在 dead↔cold↔streaming 连续翻，alive 守卫**正确地**挡 force-clear）内容
仍会冻到风暴结束——但这种连续风暴现在罕见。

B-3 不值得作为下一步，理由三条：
1. **撞北极星**：B-3 = 把脆弱的 live 流镜子修得更结实（让它扛过子进程死 N 次），而非靠 DB 权威。北极星说内容
   权威是 DB、token 是可丢弃覆盖层——B-3 方向相反。
2. **最高风险**：要动 `streamGen` + 所有 send/resend/reconnect/drain/recycle 路径，lifecycle god-file，回归密度最高。
3. **回报递减**：严重 case 已被前几件解，B-3 只换来「风暴期内容更顺」，而风暴已稀有。

### 若残余值得补 → 做「DB 投影」，不是 B-3

北极星正确的残余补法（替代 B-3）：后端 `last_persisted_seq` 前进、前端 buffer 没动时，**直接从 DB 把内容投影
出来**（哪怕还在 streaming 相位的窄通道），而非抢救 live 流。方向对（DB = 内容权威）、风险低于动 streamGen。
这其实就是本文档**原 B-2 的内容恢复件**（至今未做），不是 supersession 的 B-3。

### 门控：先观测，再决定

遵循本轮纪律「别修没观测到的东西」。B-1/B-2 刚落：
1. 重新 build + 用一阵，看 freeze 是否消失 / 仍冻几十秒；盯 `frontend.log` 的 `OT01-GenGuard` 频率与 dwell。
2. **基本没了** → B-3 关闭，不做。
3. **风暴期仍冻且够频繁** → 做 **DB 投影（原 B-2）**，**不做** B-3（live-stream supersession）。

> 一句话：**B-3 大概率是错的投资（高风险 + 撞北极星，严重症状已解）。真要补残余，补 DB 投影。先观测再说。**