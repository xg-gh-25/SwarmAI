---
title: 回合进行中只有一个事实 — 结束"中途没反应、蜜蜂没了、追问卡在队列"
date: 2026-10-23
created: 2026-10-23
updated: 2026-11-01
project: SwarmAI
status: P1–P4 DONE（v2.4.0 已发布）；忙时发送默认先 steer，见 2026-10-24-chat-steer.md
related: chat-transcript-single-source.md（S3 会话状态唯一来源，本文是它没覆盖到的那一半）, streaming-spinner-ssot.md（2026-06-30，同类，未按设计落地）
---

# 回合进行中只有一个事实

## 0. 一句话

"这个会话现在有没有一轮在处理"在系统里**没有一个事实**。后端只报子进程状态（`unit.state`），
冷启动时说 `cold`，Stop 回收前说 `idle`，两个方向都不等于"有没有回合"。前端于是靠聊天路径上
57 处 `setIsStreaming` 写入加 30 秒超时去**猜**。猜错一次，蜜蜂就消失，界面上什么都没有，追问
留在前端自己的队列里出不来。修法：**后端从这一轮被准许运行开始，到这一轮结束，维护一条带序号的
"会话活动"，作为唯一来源；排队只在后端；前端的蜜蜂只看这份活动，发送按钮看本地请求状态，删掉
所有猜测逻辑。** 先写验收测试，再改。

范围：不含部署重启（开发期才有，XG 2026-10-23 明确排除）。

## 1. 问题清单

证据来自 `~/.swarm-ai/logs/backend-daemon.log*` 和 `frontend.log`（2026-10-20 至 10-23），
代码位置是 main `43794443f`。日志条目不在仓库里，P0 的测试按这些时间线复现。

| # | 现象 | 证据 | 根因 |
|---|---|---|---|
| 1 | 发出消息约 30 秒后蜜蜂消失，之后什么都不显示 | 日志：278 次 `forcing clear`，其中 122 次后端报 `cold`；10-23 13:41:34 发出，13:42:11 清掉，后端 13:42:17 才进 streaming；16:07:18 发出，16:07:58 清掉，16:08:26 才重新显示 | RC1 |
| 2 | 冷启动期间订阅流和 streaming-state 都看不到这一轮 | 代码：hub 的 `turn_start` 要等 CLI 的 `session_start`（`session_router.py:3801-3840`）；冷启动时只有 POST 上有 `turn_phase` | RC1 |
| 3 | 蜜蜂被清掉后，只要前端有排队消息，就不再重新显示 | 代码：`streaming-guards.ts:376-382` | RC1 + RC3 |
| 4 | Stop 之后的回收杀掉了下一条消息的进程（重试救回，首字 19.7 秒） | 日志：10-22 19:20:26 `interrupt succeeded`，转 IDLE，3.4 秒后 19:20:30 `_crash_to_cold_async` 杀掉新回合的进程。代码：`session_unit.py:4162`（转 IDLE）、`:4223-4224`（之后才回收），读流也会单独转 IDLE（`streaming_orchestrator.py:2672-2675`） | RC1 |
| 5 | 注入回合（CLI 自开）真正失败时（进程被杀、流出错），不重试也不说明 | 代码：`session_router.py:3459-3463` 只记 WARNING 再杀到 cold。注：10-22 19:21 那次"空回复"已由 `1d060eee`（10-22 19:34）改为安静结束，那是正确行为（另一条消息接管了 CLI），不算问题 | RC2 |
| 6 | 前端日志 7824 条 `[OT01-GenGuard] discard stale stream event`，几乎都是 heartbeat | 代码：订阅流 `turn_start` 时新建 `subRender`（`useChatStreamingLifecycle.ts:4744`），之后 POST 流的事件按设计丢弃，却记成 WARN | RC3（日志噪音，不是丢内容） |
| 7 | 10-23 13:43:40–13:44:22 事件循环停了约 40 秒 | 日志 stack：`reflect_job._hook → pipeline_human.owned_runs` 扫目录 | 已修 `9570fb9ce`，只做回归测试 |

## 2. 根因

### RC1 "有没有回合在处理"没有一个事实

后端对外只有 `unit.state`（`session_state.py:38`：`streaming = state == STREAMING`）。它描述的是
**子进程**，不是**回合**，两个方向都会说错：

- **说"没有"，其实有**：受理一次发送后，到 CLI 发出 `session_start` 之前（槽位、recall、构建 prompt、
  冷启动 spawn、`--resume` 加载），unit 是 `cold` 或 `idle`。正常 4–6 秒，内存紧时实测 17.8 秒和
  61.9 秒（`wrapper_aenter_ms`）。hub 的回合同样要等 `session_start` 才开始。
- **说"空闲"，其实不能用**：用户 Stop 后，`interrupt()` 和读流各自都会先转 IDLE，再写恢复检查点、
  杀进程（#4）。中间这段 IDLE 会被发送、drain、注入回合当成"可以开始新一轮"。

前端拿不到这个事实，只能猜：`forceClearStreamVerdict`（30 秒 settle、120 秒 hard-cap、60 秒排队豁免、
`isResuming` 豁免）、`shouldArmSpinnerFromBackend`（12 秒防抖、有排队就不显示），加上聊天路径 5 个
文件里 57 处 `setIsStreaming` 写入（SkillsPage 自己的 15 处不在范围内）。每条规则都是某次 bug 的补丁，
冷启动一慢就互相打架（#1、#3）。`streaming-spinner-ssot.md`（06-30）和 `chat-transcript-single-source.md`
S3 都写过"转圈只看后端状态"，但落地的 S3 只是把同一份 `unit.state` 推得更快，前端的猜测逻辑没删。

### RC2 回合失败的处理按入口分开写

| 入口 | 失败后 | 位置 |
|---|---|---|
| 用户发送 / 频道 / 排队送出 | `_retry_with_resume`，最多 3 次，`turn_notice`，用完发 `ALL_RETRIES_EXHAUSTED` | `session_unit.send` → `retry_manager.py:640-656` |
| 回答提问 / 审批后继续 | `_retry_continuation`，续接提示 "Continue the task where you left off" | `session_unit.py:4458-4461`, `streaming_orchestrator.py:343` |
| 注入回合 | 空回复：安静结束（正确）。其他失败：WARNING，杀到 cold，结束 | `streaming_orchestrator.py:2949-2962`, `session_router.py:3459-3463` |

注入回合真正失败时，既不重试也不说明（#5）。这违反了已定的规则：除了用户 Stop，任何中断都要自动
恢复，最多 3 次。另外 `turn_notice` 的 `gave_up` 被标成"历史值、不再产生"（`turn_notice.py:21-24`），
重试用完时库里没有说明，只有 POST 上的一条错误事件。

### RC3 排队有两个地方

- 后端：用户行在占槽位**之前**写库并回执 `accepted`（`session_router.py:4081-4085`）；之后遇到
  SESSION_BUSY 或排队超时，这条转成 `session_pending`（`:4110-4147`, `:4856-4912`），drain worker
  在 unit 进入 IDLE 时送出（`:3308-3311`，`drain_pending` 只处理活着的 IDLE unit，`:3532-3535`）。
- 前端：tab 认为在 streaming 时，消息存进 `tabState.queuedMessage`（`ChatPage.tsx` 发送路径，后续
  输入拼进去，附件在送出时才读，`:1921-1990`, `:2616`），由 `drainQueuedMessage` 在前端认为回合
  结束后发出；排队气泡可取消（`UserMessageView.tsx:135-146`, `ChatPage.tsx:2703-2730`）。

前端这份队列的送出时机取决于前端猜出来的 `isStreaming`。猜错时，消息停在前端，后端
`pending_count=0`，用户看到"在排队"，实际谁也不会送出它（#3）。`GenGuard` 的 WARN（#6）是同一结构
的副作用：回合开始后订阅流接管渲染，POST 流的事件被丢弃，却被记成异常。

## 3. 方案

### S1 会话活动：准许运行到结束，一条带序号的记录（解 RC1）

**事实放在 hub 里**：每个会话"打开的回合"（`session_events` 已有 `active` 回合日志，一个会话同时
只有一个，`session_events.py:157-163`）。

1. **原子地占住回合（claim）**（P1 落地形状，v3）：`hub.try_claim_turn(session_id, turn_id)`，同步执行、
   中间没有 await；已有打开或占住的回合时返回 False。发送路径在用户行写库之后、占槽位之前调用它
   （`TurnOpener.open`），之后再回执 `accepted`：
   - 占成功：从此这一轮"在进行中"（`open_turn_id` / `open_phase` 读得到），阶段 `preparing`（含等槽位、
     recall）→ `starting` → `waiting`，由这一轮在 POST 上的 `turn_phase` 推进。**`turn_start` 仍在
     `session_start` 时发布**（和以前一样），所以发布到订阅流的回合事件不变：旧前端、liveReload 夹具
     （去掉新增的 `session_activity` 与新字段后与 main 逐字节相同）都照常。v2 写的是"受理时就发
     `turn_start`"，实测会让所有在 `session_start` 前结束的发送（排队、SESSION_BUSY）在别的 tab 留下
     空气泡（liveReload 18 例新分歧），所以改为占住 + 活动字段表达。
   - 占失败（已有一轮，或 Stop 正在回收）：这条直接转 `session_pending`，POST 收到 `accepted`
     （`queued: true`, `pendingSeq`）和以前一样的 SESSION_BUSY 回复（`pendingSeq` / `pendingId`），
     不占回合。
   - 占住后没开始就结束（QUEUE_TIMEOUT、unit 回 SESSION_BUSY、取消）：`_published_turn` 的 finally 释放。
     SESSION_BUSY / QUEUE_TIMEOUT 的错误事件标 `_post_only`，只给 POST，不发布。
   - 提问续接、审批续接、注入回合仍在第一个事件时开回合（不占）。注入回合和 drain 在有打开或占住的
     回合时不开始，等它结束再跑。
2. **`activity` 字段，增量加，不改旧字段**：`session_state_entry` 新增
   `activity ∈ {idle, preparing, starting, waiting, streaming, waiting_input, stopping}`、`activity_seq` 和
   `pending`（排队列表）。由 `session_state.derive_activity` 一处算出：Stop 标志 → `stopping`；unit
   `WAITING_INPUT` → `waiting_input`；有打开或占住的回合 → 它的阶段；unit `STREAMING` → `streaming`；
   否则 `idle`。`streaming` 的含义**不变**（仍是 `state == STREAMING`）：旧前端、`daemon-lib.sh` 的部署
   排空都照常工作。
3. **`activity_seq` 定先后**：活动变化时 `hub.set_activity` 给一个新序号（自 epoch 起的微秒数，且大于
   上一个），跨 daemon 重启和 `drop_session` 也只增不减。推送（`session_activity` 增量、`session_state`
   快照）和轮询都带它，tab 只接受更大的。
4. **新会话的第一条**：`accepted` 已带 `sessionId`。前端收到就绑定 tab 的 session，并立即订阅
   （`watchOpenTabs`），不再等 `session_start`（改前只在 `session_start` 绑定，
   `useChatStreamingLifecycle.ts:2943-2979`）。之后 `session_start` 的 id 必须相同，不同就记 ERROR
   并以 `session_start` 为准（会话身份规则不变）。

**Stop：从按下到进程回收完都是 `stopping`**（P1 落地形状，v3）

- `interrupt()` 改为一层包装（实体是 `_interrupt_impl`，内容不变）：用户 Stop（`autonomous=False`）
  从进入到回收完，unit 的 `_stopping` 为真，`activity = stopping`；结束时发 `stop_done`。
- v2 写的是"读流和 interrupt 两处都不转 IDLE"。实现时没有改状态机：unit 内部照旧短暂经过 IDLE，但
  每个能在它上面开始一轮的入口都先看 `_stopping`：`TurnOpener.open` 不占（消息排队）、`drain_pending`
  和注入回合不开始、`SessionUnit.send()` 在推进 `_send_generation` **之前**有界地等它结束（≤10 s）。
  这样 interrupt 的过期检查（`_send_generation`）、超时分支、`clear_identity=False`、自动打断回 IDLE 的
  路径一行都没动；10-22 19:20 的问题（等待中的发送推进了 generation，interrupt 于是跳过回收、把带毒的
  进程交给它）也被堵住。T4 按这个不变量写。
- **drain 按电平判断**（v4）：`drain_pending` 只看当前状态：会话不忙（`SessionRouter.is_busy`：有打开或占住的
  回合、Stop 正在回收、unit 在 STREAMING / WAITING_INPUT）、unit 是 IDLE 或 COLD（COLD 用 `--resume` 冷启动，
  槽位照常申请），就送。触发只有一个入口 `kick()`：进入 IDLE 时；以及消息因会话忙而转 pending 之后，等占着
  会话的那一轮（或占位、或 Stop）结束时（hub 的 `waiters` 标记）。
- **"会话忙不忙"只有一个判断** `SessionRouter.is_busy`，release、驱逐、TTL、orphan、过期 COLD 清理都用它，
  看得到"已占位但还没开始发"的回合。占位记下占它的任务，任务结束的占位不算数。

**热路径代价**（P1 实测，见 §9 v3）：阶段变化只发 `session_activity` 增量，不读库；完整快照（含排队
列表，读 SQLite 一次，`session_pending.list_pending`，`pending_count` 由它的长度得出，没有多读一次）
只在 unit 状态变化、订阅流连上、取消排队时发。全部在事件循环上，不进 chat 等待的线程池。

### S2 回合失败：一个策略，按入口传参数（解 RC2）

> 实现（P3）：没有 turn_failure_policy 函数；续接和注入回合共用 SessionUnit.read_with_recovery()。

一个函数 `turn_failure_policy(origin, error, persisted_prefix)` 决定所有入口怎么处理失败：

| 参数 | 用户发送 | 续接（提问 / 审批） | 注入回合 |
|---|---|---|---|
| 可重试的错误 | 进程被杀、断流、空回复 | 同左 | 进程被杀、断流（**空回复不在内**：那是另一条消息接管了 CLI，安静结束） |
| 重开时发给模型的话 | 原用户消息（`--resume`） | "Continue the task where you left off" | 后台结果说明（同一个 `background_finished` 块的内容，不重新写块） |
| 已写库的前缀 | 本轮已写的行 | 同左 | `background_finished` 块已写（`session_router.py:3422-3442`），重试只重读回复，不再写这个块 |
| 幂等键 | `client_id` | `client_id` | `injected-{uuid}`，同一轮的重试沿用 |

- 最多 3 次，每次写一条 `turn_notice(restarted, cause)`。
- 用完后写 `turn_notice(gave_up)`，进库，刷新后可见（`gave_up` 重新成为在用的取值，改
  `turn_notice.py` 的说明），POST 上的 `ALL_RETRIES_EXHAUSTED` 保留。
- 用户 Stop 是唯一不重试的结束，写现有的 `turn_stop` 回执。
- 删掉 `run_injected_turn` 里"WARNING、杀到 cold、结束"的分支（`:3459-3463`）。

### S3 前端：蜜蜂只镜像活动，按钮看本地请求（解 RC1、RC3）

前端有两件不同的事，分开存：

| | 回合活动（蜜蜂、阶段文字） | 本地请求（输入框、Send / Stop 按钮） |
|---|---|---|
| 来源 | 后端 `activity` + `activitySeq`（推送为主，订阅流断开时用轮询） | 这个 tab 自己发出的请求 |
| 取值 | `idle … stopping`（S1） | `localSending`（这个 tab 有 POST 开着）；未受理的气泡标 `sendState: 'failed'` |
| 谁写 | 一个 reducer，唯一写 `tab.isStreaming`（`isStreaming = activity ∉ {idle}`） | 发送和 Stop 的代码 |

- **蜜蜂只看活动**。POST 生命周期、订阅流 `turn_start`、ChatPage 各路径不再写 `isStreaming`。阶段文字
  ("Starting session · Ns")也读 activity，不再靠 POST 的 `turn_phase` 事件。
- **Send / Stop 按钮看两者**：本地请求在 `building / posting` 时，或活动不是 idle 时，显示 Stop
  （`ChatInput.tsx:1070-1108` 改前只看 `isStreaming`）。
- **未受理的消息不丢**：`failed`（后端连不上、`accepted` 没到）时，用户气泡标"未发送 · 重试"，输入
  内容保留。同一个 `client_id` 重试，后端账本保证只算一次。
- **消息存进 MessageStore 的阶段保护**（`useChatStreamingLifecycle.ts:2494-2503`）跟着 handler 创建，
  属于本地渲染，不变，不参与"回合在不在"的判断。
- **排队只在后端**：回合进行中发的消息照常 POST，后端 `TurnOpener.open`（`try_claim_turn`）占不到时，能 steer 就写进正在跑的回合（`steer_busy_send` 默认开），否则转 pending，回执
  `queued: true`。
  - 完整快照带排队列表（`client_id`、预览文字、时间），读 `session_pending` 里未认领的行（现在只有
    内部的 `peek_pending_batch`，`session_pending.py:438`）。
  - 取消：新增 `DELETE /sessions/{id}/pending/{client_id}`。只删未被认领的行，并释放
    `send_requests` 的占位；已被 drain 认领的返回 409，界面显示"已在发送"。
  - 附件在点 Send 时就生成内容（改前是送出时才读），这是一处行为变化：排队期间改了的文件，送出的是
    点 Send 时的版本。
  - 连发几条不再拼成一条；drain 会把它们作为几条消息一起送出（10-22 19:21:20 日志
    `pending_delivered count=2`）。
- **删除清单**：`forceClearStreamVerdict`、`shouldArmSpinnerFromBackend` 及其 settle / hard-cap / 防抖 /
  排队豁免、`_idleStreamingSince`、`_streamingSinceHardStart`、`_streamClearedAt`、`tabState.queuedMessage`、
  `drainQueuedMessage`、reconcile 循环里的 force-clear 与 re-arm 分支、57 处直接写 `setIsStreaming`。
  每删一项先确认没有别的读者，留下的写进 §6 并说明理由。
- **GenGuard 日志**：被订阅流接管后丢弃的 POST 事件降为 debug，只有跨回合的丢弃记 WARN。
- SkillsPage 自己的生成状态不动（`SkillsPage.tsx:389`, `:953-987`）。

## 4. 验收测试

P0 先写全部测试，在现在的 main 上逐条确认是红的，以"预期失败"提交（pytest `xfail(strict=True)`，
vitest `it.fails`）：现在通过了反而报错。每个阶段把它解决的那几条改回普通测试。`liveReload` 的
KNOWN 列表保持为空（`multi-tab-isolation-principles.md:238-241`），活动测试单独成文件，不进它。

| # | 测试 | 怎么造场景 | 验什么 |
|---|---|---|---|
| T1 | 冷启动全程可见（后端） | 真实 `run_conversation` + 真实库，`SessionUnit.send` 换成脚本：在发出 `starting`、`waiting` 之前各停一次（v3：recall 那一步不单独停，它在 `preparing` 里） | 停在 starting 前读到 preparing、停在 waiting 前读到 starting，从不为 idle；hub 有进行中（占住）的回合；`activity_seq` 递增 |
| T2 | 冷启动全程可见（前端） | 用当前完整的 `useChatStreamingLifecycle`（和 liveReload 一样的 harness），把 T1 的活动序列按推送和轮询交错喂进去，假时钟走 90 秒 | 蜜蜂全程在，阶段文字连续；现在第 30 秒被 force-clear |
| T3 | 并发发送 | 第一条冷启动停住时发第二条 | 第二条的 POST 立即结束，回执 `queued: true`，进 pending，没有开第二轮；第一轮结束后 drain 把它作为下一轮（`source=drain`）送出，pending 清空 |
| T4 | Stop 后紧接着发送 | 在 `_arm_recovery_checkpoint` 里放闸门；闸门停住时 | v3：`activity == stopping`；`TurnOpener.open` 拒绝；此时开始的 `send()` 不推进 `_send_generation`、不往下走；放行后 unit 到 COLD（会话 id 保留）、发一次 `stop_done`、`activity == idle`，等待的 send 才继续 |
| T5 | 注入回合真失败 | 注入回合读流中途杀进程 | 重试，写 `turn_notice(restarted)`，`background_finished` 只有一个；3 次后写 `gave_up`，刷新后可见 |
| T5b | 注入回合空回复 | 复现 10-22 19:21 的零输出 success | 安静结束，**不**重试（守住 `1d060eee`） |
| T6 | 活动的顺序（性质测试） | 随机交错：推送、轮询（带新旧 seq）、订阅流断开/重连、本地请求变化 | 每一步：蜜蜂 == 最大 seq 那份 activity ≠ idle；Send/Stop 符合 S3 的表 |
| T7 | 后端连不上时发送 | POST 失败，无 `accepted` | 气泡"未发送 · 重试"，输入保留；重试用同一个 `client_id`，库里一条 |
| T8 | 取消排队 | 未认领时取消、认领后取消 | 前者删掉并释放账本；后者 409 |
| T9 | 事件循环上不扫目录（守住 #7） | 1 万个 run 目录下触发 PostToolUse hook | 返回 < 5 ms |

每个修复做反向验证：把修复撤掉，对应测试必须变红。T1–T5 的场景加进 `liveReload.property.test.ts`
的生成器，保证"实时显示 == 刷新"在这些场景下也成立。

## 5. 实施顺序

后端先做成增量、旧前端照常可用；前端一次切换。

| 阶段 | 内容 | 依赖 | 风险 |
|---|---|---|---|
| P0 | T1–T9 写好，确认红，以预期失败提交 | — | 低 |
| P1 | 后端：`try_open_turn`；回合从准许运行开始；`activity` + `activitySeq`（增量，旧字段不变）；Stop 期间 `stopping`、两处不转 IDLE；`turn_end` 触发 drain、COLD 也能 drain；排队列表和取消接口；热路径实测 | P0 | 中：动 `_published_turn`、interrupt、读流、drain，逐条对照 session lifecycle steering 的 6 条不变量 |
| P2 | 前端一次切换：活动 reducer；本地请求状态；`accepted` 绑定 session 并订阅；排队改读后端；取消；删除清单 | P1 | 中：57 处写入一次迁完 |
| P3 | S2：失败策略合一，注入回合接入，`gave_up` 进库 | P1 | 中 |
| P4 | 删除清单逐项核对；GenGuard 降级；文档同步到实现 | P2、P3 | 低 |

每阶段一个 commit，在自己的 worktree 里做，ff-merge 到 main。P1 和 P2 写完各做一次独立的
fresh-context 审查。旧的 `streaming` 字段一直保留（部署脚本在读）。

## 6. 不做什么

- 不处理部署重启打断回合（开发期才有）。
- 不新增 unit 状态，5 个状态不变；`stopping` 只是 activity 的取值。
- 不改对话记录（rev、快照 + 变更）和发送账本，它们已经是唯一来源。
- 不处理空闲后 prompt 缓存失效导致的首字慢；本文只保证这段时间界面一直显示"在等模型"。
- 不动 SkillsPage 的生成状态。

## 7. XG 已拍板（2026-10-23）

1. 回合从准许运行开始算：模型开口前，气泡里先出现这一轮的阶段行。
2. 去掉前端队列：连发几条由后端排队，drain 时作为几条消息一起送出。

## 8. 待查证（P0 写测试时确认）

- V1：订阅流 `turn_start` 之后，POST 带来的 `turn_phase` 被 GenGuard 丢弃，阶段行可能在
  `session_start` 到首字之间消失（10-23 16:08:26–16:11:48）。S3 改读 activity 后无论如何都会消掉；T2 给出答案。
  **结论（P2a）**：蜜蜂和阶段只看会话活动，这个丢弃不再影响显示；P4 把它的日志降为 debug。
- V2：10-23 13:43:42 一次 `STORE-CLOBBER destroy`（1998 字符 → 0），原因未查，P0 时复现。
  **结论（P4）**：`frontend.log` 里 10-22 以来的 5 次都是 `reason: destroy`、`phase: idle`。`MessageStore.destroy()`
  在生产代码里只有关 tab 会调用（`ChatPage.doCloseTab`；`useUnifiedTabState.cleanupTabState` 也调，但它目前没有生产调用者），关 tab 时内容消失是预期的，
  不是丢消息。探针里 destroy 那一支删掉；reconcile 时内容骤减的那一支保留。
- V3：提问 / 审批等待时，hub 的回合是否已经结束（续接会另开一轮）。S1 的 `waiting_input` 按"没有打开的
  回合但 unit 是 WAITING_INPUT"算；如果回合其实还开着，改为按回合阶段算。T1 的变体验证。
  **结论（P1）**：`session_state.derive_activity` 先看 unit 状态，WAITING_INPUT 就是 `waiting_input`，与 hub 的
  回合开没开无关，所以不依赖这个问题的答案。

## 9. 修订记录

- v1（2026-10-23 `43794443f`）：初稿。
- P0（2026-10-23）：`backend/tests/test_turn_liveness_p0.py`（T1 T3 T4 T5 T8 预期失败，T9 回归）、
  `desktop/src/__tests__/turnLiveness.p0.test.ts`（T2 T6 T7 预期失败）。每条都确认是在它要验的断言上红
  （`raises=AssertionError`；前端先以普通 `it` 跑一遍看失败信息）。T5b 已由 `test_injected_turn_empty.py`
  覆盖，没有重复写。反向验证样例：把 interrupt 成功分支的转 IDLE 去掉，T4 变成 XPASS(strict)。
- v4 实现（2026-10-23）：按 v4 设计落地，和设计文字不同的两处（实现时验证出来的）：
  - **RC-A 的兜底用"占位的任务已结束"，不用弱引用**。核实后：HTTP 断开时回合本来就是"脱离"而不是关闭
    （`sse_with_heartbeat` 把 consumer task 留着跑完），asyncio 的 asyncgen 终结器也会调度 `aclose()`，所以
    弱引用多数时候根本不会先于 finally 失效，起不到兜底作用。改为 hub 记下占位时的 `asyncio.current_task()`，
    读占位时任务已结束就当没有（`SessionEventHub._live_claim`），这是确定的。`routers/chat.py` 和
    `channels/gateway.py` 在各自的 finally 里 `aclose()` 内层生成器，`skill_creator` 用 `aclosing`。
  - **RC-B 不是"每个回合结束都 kick"，而是"有人因为忙而排队时才 kick"**。每个回合结束都 kick 会在每轮之后
    多一次 drain 和一次库读，测试里也真的出现了 drain 在 patch 失效后走真实 `send()` 的泄漏。现在：消息因会话
    忙而转 pending（受理时占不到、drain 被挡下、drain 输掉竞争、SESSION_BUSY）时 hub 记一个 `waiters` 标记；
    回合结束、占位释放、Stop 结束时 `take_waiters` 为真才 kick；进入 IDLE 照旧 kick。转 pending 那一刻如果
    会话已经不忙（占着的那一轮恰好在 await 期间结束），立即 kick（`SessionRouter.queued_while_busy`）。
    防循环自然成立：启动失败的 drain 不记 `waiters`。删掉 `_drain_deferred`、`_drain_allow_cold`。
  - 新测试 `backend/tests/test_turn_liveness_p1.py` 7 条（关闭生成器释放占位、任务结束的占位无效、清理路径
    不动占位、drain 输掉竞争后由赢家结束送出、Stop 结束早于回合结束仍送出、等 Stop 超时回 SESSION_BUSY、
    取消与认领竞争只有一个赢家且 client_id 可重发）。反向验证：去掉占位释放、任务兜底、release 的忙判断、
    回合结束的 kick、Stop 超时报忙，各自对应的测试变红。RC-D 的"同一事务"没有确定性的反向验证（并发窗口
    靠时序），只验了结果。
  - 测试设施：`test_send_requests.py` 和
    `test_sse_disconnect_chain.py::TestSessionBusyErrorEvent` 不跑 drain（它们的替身让 IDLE 的 unit 报
    SESSION_BUSY，生产里不会出现，排队的消息因此看起来可送，会在 patch 失效后走真实 `send()`；drain 由
    `test_session_drain.py` 和两个 liveness 测试文件覆盖）。
    `test_live_reload_scenarios.py` 的生成器同样关掉自动 drain（它自己按顺序调 `drain_pending`；不关时第 21 个场景
    出现第二个并发 drain，注入回合撞上 STREAMING）。liveReload 80/80，夹具去掉新字段后仍与 main 相同。
    相关后端测试 135 个文件逐个串行：除 `test_daemon_plist_reload.py`（main 上同样超时）外全绿。
- P2 实施计划（2026-10-23，写代码前）：一个分支三个 commit，每个单独成立（前端和同一仓库的后端一起发）。
  - **P2a 蜜蜂只看活动**。tab 新增 `activity`、`activitySeq`、`localSending`。
    - 唯一写 `tab.isStreaming` 的是 `applyActivity(tabId, activity, seq)`：`seq` 大于已知的才生效，
      `isStreaming = activity !== 'idle'`。来源三个：订阅流的 `session_activity`、`session_state`（含加入时的
      快照）、轮询。
    - 轮询里没有这个会话（unit 已释放）时：只有当这次轮询是在 tab 最近一次收到活动**之后**发出的，才把它当
      idle（seq 不变，之后更大的 seq 照常覆盖）。这是"读到时它已不存在"的确定事实，不是超时猜测。
    - POST 路径和 ChatPage 原来的 `setIsStreaming(v)` 全部改成 `setLocalSending(v)`：只表示"这个 tab 有一个
      POST 开着"，不碰蜜蜂。保留原来随它走的本地状态机（`streamState`）派发。
    - Send / Stop 按钮、"该不该先排队"看 `isStreaming || localSending`（S3 的表）。
    - 活动从非 idle 变 idle 的那一刻（一个地方）：按原 force-clear 的做法从库里对齐尾部、清
      `_postDisconnectUncertain`、有前端排队消息就送出（前端队列在 P2c 删）。
    - 删掉：`forceClearStreamVerdict`、`shouldArmSpinnerFromBackend`、`desyncConvergeVerdict` 及 reconcile
      里对应的三个分支、post-disconnect 分支里"后端 idle 才清"的判断（改由上面的 idle 边沿）、
      `_idleStreamingSince`、`_streamingSinceHardStart`、`_streamClearedAt`。`healGraceExpiryVerdict` 只决定
      POST 断开后报不报"连接断开"，不碰蜜蜂了，留到 P2b 和本地请求状态一起处理。
    - T2、T6 去掉 `it.fails`。
  - P2a 实现（2026-10-23）：按上面的计划，另外几处：
    - `accepted` 到达时，第一次发送的 tab 就绑定会话并订阅（`watchOpenTabs`），冷启动期间就能看到活动；
      原来要等 `session_start`。
    - 订阅流的 `turn_start` 不再碰本地请求；POST 断开时 `localSending` 变 false（POST 确实没了，回合还在
      不在由活动决定）。
    - 前台恢复（App Nap）那一处原来读 `streaming` 清蜜蜂，改为应用读到的活动。
    - `useChatStreamingLifecycle` 导出 `applyActivity`（订阅、轮询、ChatPage 前台恢复和测试都调它）。
    - 测试：`turnLiveness.p0.test.ts` T2、T6 转为普通测试，新增 T2b（`accepted` 绑定会话）、T6b（推送丢了由
      轮询补上，亮起再熄灭）、T6c（轮询不再列出会话 = idle，只在这次轮询发出之后没有新消息时）。反向验证：
      去掉 seq 判断、推送处理、"轮询发出之后"这个条件、轮询处理、`accepted` 绑定，各自对应的测试变红。
    - 旧测试：删 `reconcile-race-condition.test.ts`（整个文件测 force-clear）和 `streaming-guards.test.ts` 里
      三个已删函数的部分；`useChatStreamingLifecycle.test.ts` 里测请求的断言改看 `isSending`，测蜜蜂显示
      （活动标签、计时）的改为通过活动开始和结束一轮；`sessionTurnWiring`、`multiTabStreamingIsolation`
      按后端实际行为同时发出 `session_activity`。
  - P2a 独立审查（2026-10-23，4 BLOCKER + 2 MAJOR + 1 MINOR + 1 未证实，逐条核实）：
    - 修了：
      - **等待输入时不能回答 / 审批**：`waiting_input` 让蜜蜂亮着（它就是"一轮还在"），两个续接入口却以
        `isStreaming` 拦掉。改用 `canContinueTurn`：本地请求开着不行，一轮在跑且不是在等输入不行。
      - **活动变 idle 时消息存储的流阶段没结束**（`turn_end` 丢了，只等到轮询的 idle）：idle 边沿在这个 tab
        没有 POST 开着时调 `endStreaming()`，并把"streaming"的 tab 状态改回 idle，再对齐数据库。
      - **会话换了，seq 还按旧会话比**：活动记下它属于哪个会话（`activitySid`）。会话变了就从头来（idle、
        seq 0），不做回合结束的动作；在每次应用活动前、每次轮询、每次切 tab 时检查。
      - **本地请求开着时关 tab / 落地 / 断线重发不把它算忙**：三处都加上 `localSending`。
      - **会话还没受理时按 Stop 什么都不做**：没有会话时取消这个 tab 的请求、清 `localSending`。
      - **前台恢复用旧读数强制合并**：和轮询一样记下发出时间；读完后按 tab 当前状态决定，tab 有更新的消息
        或这一轮在跑就不动。
      - 新测试 R1–R3，反向验证各自变红。
  - P2a 第二轮审查（复核上面的修复，2 MAJOR + 1 MINOR，都修了）：
    - **活动本来就是 idle 时，再来一个新的 idle 不做回合结束的事**：busy 的活动和回合结束都丢了（断线），
      tab 的消息存储还在流阶段。现在收到更新的 idle、本地没有请求开着、而 tab 看起来还在一轮里（存储在
      streaming、状态 streaming、重连 / 恢复中、断线模式）时，同样做回合结束的事。测试 R2b。
    - **读附件期间按 Stop 拦不住，期间切 tab 消息会发到新 tab**（后一半 main 上也有）：发送开始时记下发起它的
      tab 和一个令牌（`tab._sendToken`）；Stop 或更新的一次发送清掉它，关 tab 让 tab 消失。`await
      buildContentArray` 之后令牌不对就直接结束，什么都不写、不开 POST；之后的处理、占位、POST 都用发起的
      tab。POST 开了令牌清掉（之后 Stop 走 abort）。ChatPage 没有驱动 `handleSendMessage` 的测试环境，所以
      R4 是源码守卫：await 之后先检查令牌、检查在 POST 之前、中间不读当前 tab、Stop 两个分支都清令牌；
      反向验证（去掉检查、Stop 不清令牌）变红。
    - 过期的注释和测试标题（说 `setLocalSending` 写 `isStreaming`、断线时打 `_reconcileStreamStart`）改掉。
    - 已知限制（未证实，记下）：活动 seq 在 daemon 重启后取墙钟微秒。重启后墙钟如果比重启前倒退得更多，
      新活动会被当成旧的，直到墙钟追上。正常重启（几秒）不会发生。
  - **P2b 本地请求状态**：`localSending` 换成 `building → posting → accepted | failed`；没受理的消息标
    "未发送 · 重试"，同一个 `client_id` 重发。T7 去掉 `it.fails`。
  - P2b 实现（2026-10-24，按独立审查重做过一次）：
    - **一条规则，一个地方**：每个发消息的 POST 开之前调 `noteSend(tab, request)`（主发送、前端队列送出、
      QUEUE_TIMEOUT 重试、重试按钮四处），记下这个请求和它的 `client_id`（`tab._openSendCid`）。这个 tab 的请求
      结束时（`setLocalSending(false)`：每一种结束都经过它，出错、断线超时、`[DONE]` 没回执、自愈失败），如果那个
      `client_id` 一直没被受理（还在 `tab._unsentRequests` 里），它的用户气泡标 `sendState: 'failed'`，显示
      "未发送 · 重试"。用户按 Stop 结束的不标。`accepted` 到了就从 `_unsentRequests` 删掉、清标记。
      第一版只在错误处理的最后一个分支标，用 tab 级的"最近受理的 client_id"判断，审查指出断线超时、`[DONE]`
      无回执、前端队列送出这几条路径都漏了，而且队列送出时重连会重发上一条消息。
    - 标记只在本地；它其实已经写进库的话，下一次对齐时库里的行替换掉它。保留的请求只跟着还能重试的气泡：
      每次标记时清掉对应气泡已不存在（或已不是"未发送"）的请求；tab 重置（新会话）时全部清掉。
    - 前端队列送出和 QUEUE_TIMEOUT 重试也装上自己的 `retryStreamFn`（连接期重连重发的是这一条，不是上一条）。
    - 重试（`pages/chat/retrySend`）发同一个请求、同一个 `client_id`：气泡移到末尾，换一个空的回复占位；这个 tab
      有一轮在跑或自己有请求开着时不动。
    - 和 S3 原文不同：输入框不保留文字。文字已经在气泡里，重试就是发它，保留在输入框里会变成两份。
    - `ChatPage.sendKeyedInvariant.test.ts`（每个 streamChat 都带 clientId 的守卫）认得 `noteSend(...)` 和带类型的
      请求对象；反向验证：去掉主发送、队列送出、超时重试任一处的 clientId 都变红。
    - 测试：T7 转为普通测试；T7b（`accepted` 清标记和请求）、T7d（受理过的不标）、T7e（`[DONE]` 无回执也标）、
      T7f（Stop 不标）、T7g（请求跟着气泡走）、`UserMessageView.unsent.test.tsx`、`retrySend.test.ts`。反向验证：
      结束时不标、受理过也标、Stop 也标、不清理、受理不删请求、不显示、忙时也重试、不用 tab 当前会话，各自变红。
  - P2b 第二轮审查（1 BLOCKER + 2 MAJOR + 1 MINOR，都修了）：
    - **POST 中途断开被当成"没发出去"**：断开处理把本地请求关掉，就把这一次发送消耗掉了，标了"未发送"，而重连
      还会再发；重试按钮也能在重连的同时再发一次。现在断开只结束请求、不消耗这次发送（`keepSend`）；每个重连
      函数（主发送、队列送出、超时重试、重试）自己重新打开请求、重新 `noteSend`，所以只有真正算数的那次结束才
      标记；重连或"恢复后自动重发"还在等着时，重试按钮不动。测试 T7h、`retrySend` 忙时不动。
    - **审批决定的 POST 断线重连会重发上一条消息**：它没装自己的重连函数，沿用了上一条消息的。决定不是幂等的，
      所以不重发：审批 POST 开时清掉 `retryStreamFn`。测试 R5（源码守卫）。
    - **每个 streamChat 都带 clientId 的守卫按变量名全局匹配**：另一个函数里同名但不带 clientId 的请求会被放过。
      改为按所在函数匹配，并加了反例（两个函数里同名的请求，一个带一个不带，后一个必须报出来）。
    - **Stop 之后保留的请求不删**：Stop 结束的发送，请求直接删掉。测试 T7i。
    - 反向验证：断开也消耗、Stop 不删、重连时也允许重试、审批不清重连函数、守卫去掉主发送或队列送出的 clientId，
      各自变红。
  - **P2c 删前端队列**：忙时照常 POST，后端回执 `queued: true`；排队气泡读快照的 `pending`，取消调
    `DELETE /pending/{client_id}`（409 显示"已在发送"）；删 `tabState.queuedMessage`、`drainQueuedMessage`、
    `shouldQueueSend` 的排队分支、drain 退役镜像。
  - P2c 实现（2026-10-24）：前端队列删了，排队只在后端。
    - **忙时发送**（`ChatPage.sendWhileBusy`）：会话有一轮在跑（或可能在跑：本地请求开着、重连中、断线不确定）时，
      消息照常 POST，但不接管这个 tab 的流处理（不动 stream generation、不建 handler，否则正在渲染的那一轮会被当成
      过期丢掉）。后端排队（回执 `queued: true`，再一个 SESSION_BUSY），或者此时会话已空出来就直接跑——那一轮和
      drain 出来的一样从订阅流到达。附件在点 Send 时读。连发几条就是几条消息。没受理的标"未发送 · 重试"（P2b）。
    - **排队显示只看服务端的列表**：后端在消息转 pending 的地方（受理排队、drain 输掉竞争、history_dropped、unit
      报 SESSION_BUSY、QUEUE_TIMEOUT）推一次 `session_state`（`SessionRouter.publish_queue`），里面带 `pending`。
      用户气泡在它的 `client_id` 在列表上且没被认领时显示"已排队"和取消。推送按 eventSeq 排序；轮询只在发出时间晚于
      最近一次推送时才用。刚发出、回执还没到的那条先算排队，直到回执之后读到的列表决定（`tab._queuedLocal`）。
    - **草稿在点 Send 时取走，没发出去就还给原 tab**（普通发送和忙时发送一样）：点 Send 的那一刻这个 tab 一定是当前 tab，
      所以就在那时清输入框；读附件之后不再碰当前输入框（用户可能已切到别的 tab）。读失败、Stop、tab 关了、重置了或换了会话，
      什么都不发，文字和附件放回它自己的 tab（`restoreDraft` / `pages/chat/draftToRestore.ts`），不覆盖用户在那里新输入或新加的内容。
    - **一次只准备一条**：读附件期间（`tab._preparingSend`，一个属于这次发送的令牌，只有它自己能清）再点 Send 不做事。
    - **列表不完整时不下结论**：`pending` 最多 50 条；只有明确给了 `pending_count` 且不超过列表长度才算完整（`pendingComplete`），否则不在列表上的消息保持原来的标记，回执后的
      本地排队也不结束；状态里没有 `pending`（旧后端）时不动标记。会话换了（重置、重新绑定），队列字段全部重来（eventSeq 按会话计）。
      忙时发送记本地排队之前先按当前会话重绑一次。取消成功时，同一会话的每个 tab 都删掉那个气泡（恢复的布局里可能有两个）。
    - **取消**调 `DELETE /pending/{client_id}`：成功删气泡，文字放回它自己的 tab（`restoreDraft`，取消期间切走了也一样，不覆盖新输入；附件不回填）；409 提示"已在发送"。
    - **Stop 和重置都会终止还在准备的发送**：两处都清 `_sendToken` 和 `_preparingSend`；忙时发送读完附件后检查准备权还归自己
      （`stillMine`），普通发送除了令牌还检查会话没变（`sendSid`）。否则重置后旧草稿会发进新会话，Stop 拦不住忙时发送。
    - **刷新后排队的消息照样看得到**：历史不返回没发出的行（`sent=0`），所以列表上有、store 里没有的未认领条目，按 `preview`
      补一个排队气泡（id 用 `client_id`），能看到也能取消；送出后库里的行按 `client_id` 替换它。
    - **排队推送合并**：`session_state.publish` 已安排、还没开始读的那次推送会被后来的变化共用（按 session 记任务，任务已结束或
      被取消就重新安排），一阵连发只读一次队列。
    - 删掉：`tabState.queuedMessage` / `_queuedAt` / `drainPending`、`drainQueuedMessage`、`onDrainQueue` 和它在结果、
      错误、完成、Stop、idle 边沿的所有调用、结果时为排队消息保持蜜蜂、SESSION_BUSY 时在本地重新排队
      （`queuedMessageFromRetryPayload`、`retryPayloadHasAttachments`、`SessionBusyRetryPayload`）、drain 回合的
      临时气泡清理、`drain-lifecycle-state-transitions.test.ts`。
    - 留着：`computeDrainRetirement`（服务端 drain 了而订阅流没渲染时从库里拉那一轮）、`isWaitingForBusy` 提示、
      streaming-machine 的 `RESULT.hasQueuedMessage`（现在恒为 false，P4 删）。
    - 测试：`serverQueue.test.ts`（推送顺序、在途轮询不覆盖推送、回执前后的本地排队、忙时发送的请求不被别的标记清掉、
      截断列表、旧后端、换会话、一次只准备一条、映射、取消的 200/409/404、ChatPage 忙时发送的源码守卫）；后端 `test_a_message_queued_while_busy_pushes_the_queue`。
      反向验证：推送不排序、轮询不排序、本地排队提前结束、被认领的也算排队、清理时删掉在途请求、排队不推送，各自变红。
- v4 第二轮独立审查（2026-10-23，3 BLOCKER + 3 MAJOR + 1 MINOR，逐条核实）：
  - 修了：
    - **DEAD 清理不看占位**：`LifecycleManager._cleanup_dead` 在 `_busy()` 为真时跳过（占住的 send 会自己把
      DEAD 恢复）。
    - **QUEUE_TIMEOUT 之后没有人再 kick**（main 上同样存在：消息转了 pending，提示说"有槽位时自动发送"，
      但只有 IDLE 边沿才 drain，而这个 unit 一直是 COLD）：超时的会话记入 `SessionRouter._awaiting_slot`，
      任一 unit 让出槽位（离开 STREAMING / WAITING_INPUT，或进程结束）时 kick 它们，集合随即清空。
    - **history_dropped 转 pending 后没有 kick**（main 上同样存在，原注释写明"要等下一次交互"）：改走
      `queued_while_busy`，这一轮结束后在 COLD 上 `--resume` 送出。重试只有一次：drain 的回合不写自己的行，
      不会再走这条路。
    - **用户行写库失败时不占位**：现在照样占位；占不到时回 SESSION_BUSY 并带 `retryPayload` 交回客户端，
      不再和正在跑的那一轮并发。
    - **排队预览仍在事件循环上解析整条 content**：预览改由 SQLite 计算（`session_pending._PREVIEW_SQL`，
      在连接线程上，`json_each` 只取文本块），Python 只解析 metadata。
    - **`drop_session` 不清 `waiters`**：清掉。
    - **测试设施掩盖泄漏**：删掉 `tests/conftest.py` 的"取消残留任务"（回到 main 的版本，相关 8 个文件不靠它
      也全绿）；`test_turn_liveness_p1.py` 用 fixture 显式关掉自己建的 router 的 drain worker 并等完它排的
      publish，跑完没有 "Task was destroyed"（main 上 `test_session_drain`/p0 同样为 0）。
    - 新测试 5 条（DEAD 清理、drop 清 waiters、QUEUE_TIMEOUT 在槽位让出时 kick、写库失败仍排队等占位、
      预览不在循环上解析），各自的反向验证都变红（写库失败那条是挂住，被超时打断）。
  - 没修，留给 P3（两条在 main 上同样存在，和 P1 的改动无关）：
    - **TaskManager 的问答**：`_run_task` 在外层回合还开着（unit 处于 WAITING_INPUT）时，用一次新的
      `run_conversation` 送用户的回答；main 上 `SessionUnit.send` 对 WAITING_INPUT 直接报 SESSION_BUSY，
      结果相同（任务被标成 blocked，回答被排进队列）。正确的做法是关掉外层回合，再用
      `continue_with_answer` 和原 `tool_use_id` 继续，属于 P3"每个入口一个失败策略"。
    - **drain 失败回滚后不重试**：回滚的行要等下一次 IDLE 边沿或下一次发送。要做的是有界的延迟重试，也是 P3
      的失败策略。P2 之后前端会显示后端队列，用户看得到这些消息，也可以取消。
  - 保留：`test_send_requests.py`、`test_sse_disconnect_chain.py::TestSessionBusyErrorEvent`、liveReload
    生成器里不跑自动 drain。三处的替身都让 IDLE 的 unit 报 SESSION_BUSY，生产里不会出现。
- v4 设计（2026-10-23，P1 代码未提交）：P1 实现后的独立审查找出 4 条 BLOCKER、4 条 MAJOR，归到四个根因，
  按根因改设计，不逐条打补丁：
  - **RC-A 占位有自己的寿命，和真正在跑这一轮的东西脱节**。它只靠 `_published_turn` 的 finally 释放，而
    `routers/chat.py:426`、`channels/gateway.py:1915`、`core/skill_creator.py:123` 都用裸 `async for`，外层
    关闭时内层要等 GC 才关（审查 #1）；而且 release / 驱逐 / TTL / orphan / 过期 COLD 清理只看 unit 状态，
    看不到占位，会杀掉或清掉一个已受理的回合（#4）。**改法**：
    1. 占位绑在拥有它的 `TurnOpener` 上（hub 存弱引用）。拥有它的帧没了，占位就失效，不靠 finally 或调用方
       的关闭习惯；finally 照旧释放，只是让它更及时。
    2. "这个会话忙不忙"只有一个判断：`SessionRouter.is_busy(sid)` = unit 在 STREAMING / WAITING_INPUT，或
       `_stopping`，或有有效占位。release、驱逐、TTL、orphan、过期 COLD 清理全部只用它。
    3. 三个调用方改成 `aclosing(...)`（让关闭及时，不是正确性所必需）。
  - **RC-B drain 的触发是"边沿 + 特例集合"**：进入 IDLE、回合结束、`stop_done` 各触发一次，再加
    `_drain_deferred`、`_drain_allow_cold` 两个集合，各自在不同时刻消费（#2 权限过早用掉、#3 drain 与发送
    竞争时没人重放、#8 集合不释放）。**改法：按电平判断**。所有"会话可能空出来了"的时刻（回合结束、占位
    释放、`stop_done`、进入 IDLE、消息转 pending）都调用同一个 `kick(sid)`。`drain_pending` 只看当前状态：
    不忙、有未认领的 pending、没有待回答的工具调用，就送；COLD 也送（`--resume`）。防循环靠刚结束的这一轮
    本身：**没送出任何东西的 drain 回合**结束时不 kick，其余都 kick。删掉两个集合。#3 的竞争自然消失：
    drain 没占到位，说明别人占着，那一轮结束时一定 kick。
  - **RC-C Stop 等待会放弃**（#5）：`send()` 等 Stop 超过 10 秒就照常往下走。**改法**：超时回 SESSION_BUSY
    （消息已是 pending，`stop_done` 会 kick），绝不在 `_stopping` 为真时推进 generation。
  - **RC-D 排队操作不在一个事务里**（#6）：取消先删行、再在另一个事务里释放账本。**改法**：同一个 SQLite
    事务里删 pending 行、改 `send_requests`。#7 核实后降为 MINOR：附件入队时已外置成文件
    （`message_attachments.externalize_attachments`），content 里没有 base64；列表只取前 50 条。
  - **测试（#9）**：新增确定性闸门用例：`accepted` 后立即关闭外层生成器 → 占位释放；`claim_pending_batch`
    期间用户发送抢占 → 那一轮结束后送出；`stop_done` 早于 `turn_end` → 送出；Stop 超过等待上限 →
    SESSION_BUSY 且不推进 generation；取消与认领、重试并发；release / 驱逐 / TTL 遇到占位不动。去掉
    `test_sse_disconnect_chain` 里关掉 drain 的 stub。
- v3 / P1（2026-10-23）：后端落地，旧前端不变照常工作。和 v2 不同的三处（都是实现时验证出来的，正文已同步）：
  - 不在受理时发 `turn_start`，改为受理时**占住**回合（`try_claim_turn`），`turn_start` 仍在 `session_start`
    发布。原因：受理时发 `turn_start` 让 liveReload 出现 18 例"别的 tab 留下空气泡"（排队 / SESSION_BUSY
    在 `session_start` 前结束的发送）。现在 liveReload 80/80，夹具去掉新增的 `session_activity` 与新字段后与
    main 逐字节相同。
  - Stop 不改状态机，改为所有入口看 `_stopping`（见 S1 Stop 一节）。
  - 回合结束不是每次都 drain，只重放被这一轮挡下的那次 drain；COLD 只在 Stop 之后 drain。
  - 测试：T1 T3 T4 T8 转为普通测试（T3 补上"第一轮结束后 drain 送出第二条"，T4 按新不变量重写），T5 仍预期
    失败（P3）。反向验证：去掉占住 → T1 T3 红；`send()` 不等 Stop → T4 红；去掉回合结束重放 drain → T3 红；
    去掉 `_stopping` → T4 红。
  - 相关后端测试 89 个文件逐个串行跑：除 `test_daemon_plist_reload.py`（main 上同样超时）外全绿；
    `test_session_injected_turn.py` 偶发卡住，main 和本分支都是 1/8。改动的既有测试：只看回合信封
    （`session_activity` 不属于任何回合）、eventSeq 只要求递增、源码扫描改看 `_interrupt_impl`、`LIVE_KEYS`
    加三个新字段、两个 fixture 在 loop 关闭后不再取消 drain worker、`test_sse_disconnect_chain` 的
    SESSION_BUSY 用例关掉后台 drain（`database.db` 是 MagicMock）。
  - 热路径实测（`scripts/bench_turn_liveness.py`，机器 load 20–28）：活动变化一次 p50 2.7–3.1 µs、p95 ≤ 7.1 µs（0/1/4
    个订阅者）；不变 0.9 µs；`TurnOpener.open` p50 5.9 µs、p95 17 µs。完整快照 `session_state_entry`
    p50 0.21 / 0.37 / 0.79 ms（排队 0 / 10 / 100 行），只在状态边界发。阶段增量远低于 1 ms 的预算。
- v2（2026-10-23）：按独立审查（12 条，3 条 BLOCKER）修订：
  - #5 事实修正：注入回合空回复已由 `1d060eee` 改为安静结束，是正确行为；剩下的是真失败不重试。
  - 开回合改为原子的 `try_open_turn`，在占槽位之前；已有一轮就转 pending，不会有两个打开的回合（BLOCKER）。
  - Stop：读流和 interrupt 两处都改；`turn_end` 触发 drain，COLD 也能 drain，否则 pending 没人送（BLOCKER）。
  - `streaming` 字段不改含义，`activity` 增量加；后端先发、前端一次切换（BLOCKER）。
  - 加 `activitySeq`，推送和轮询之间也能定先后。
  - 新会话从 `accepted` 绑定 session 并订阅。
  - 前端分开"回合活动"和"本地请求"；未受理的消息标"未发送 · 重试"。
  - 排队列表和取消接口要新做；附件改为点 Send 时生成。
  - S2 改成一个策略按入口传参数，`gave_up` 重新进库。
  - 测试改用闸门制造并发，预期失败提交，KNOWN 保持为空，补 T5b、T7、T8。
  - 热路径：阶段增量不读库，完整快照只在边界发；要实测端到端耗时。
- P3 实现（2026-10-24）：S2 落地，和 S2 原文不同的三处（实现时核实出来的）：
  - **注入回合的重试用续接的同一套**，不另写一份：`SessionUnit.read_with_recovery()` 是答题续接、审批续接、
    注入回合三处共用的读法（读失败就走 `_retry_continuation`：`--resume` + 续接提示 "Continue the task
    where you left off"）。S2 原文说重开时发后台结果说明；不需要，`--resume` 后 CLI 自己的记录里就有那条
    任务通知。`background_finished` 块在读之前写一次，重试不会再写。路由里"WARNING、杀到 cold"的分支只剩
    读之外的错误（比如写库失败）才用，读里的失败已由策略处理。
  - **`turn_notice(restarted)` 每回合只写一次**（`retry_manager` 原来就这样，避免每次重试刷一行），
    S2 原文说"每次写一条"，按实现改。
  - **`gave_up` 在三个放弃点都写**：重试用完（`ALL_RETRIES_EXHAUSTED`）、连续 OOM 到上限
    （`OOM_LIMIT_REACHED`）、spawn 重试用完（`SPAWN_FAILED`）。都在错误事件之前，随回合写库，刷新后可见；
    前端文案改成"AI 进程重启不起来（原因）；发一条消息继续"，旧的"自动续接放弃"说法删掉。
  - **TaskManager 的问答**：问题出现时让外层流自己结束（不提前关，后面的收尾代码要跑），再用
    `continue_with_answer`（问题的 `tool_use_id`，答案按问题文本做 key，和聊天的答题表单一样）续上；后续问题同样处理。
  - **drain 失败后延迟重试**：两处回滚（投递抛错、结束了没投递）都调 `_retry_drain_later`：15s / 60s / 240s
    三次，之后消息留在队列里（前端看得到、能取消）。投递成功、放弃、会话没了，都清计数。
  - 测试：T5 转普通测试（真的策略代码，只替换 resume 本身）；`test_session_kill_recovery.py` 加放弃写
    `gave_up` 的两条路径；新文件 `test_turn_liveness_p3.py`（TaskManager 两条、drain 重试三条）。
    反向验证 10 处，各自变红。
  - 审查（42780697）的修复：
    - **退避期间按 Stop 会被重试忽略**（main 上就有，普通发送一样）：重试在两次尝试之间 unit 是 COLD，`interrupt()`
      原来什么都不记。现在重试整个过程标 `_retrying`，期间的 Stop 记下 `_user_stopped_current_turn`，退避结束后先看它，
      不再 respawn。
    - **`TurnAbandoned` 被当成失败**：它是控制流（CLI 健康、保留），`read_with_recovery` 直接抛出，不走重试。
    - **续接时次数已经用完**（前面的阶段用掉了）：原来 crash 后直接抛出，没有 `gave_up`；现在和重试循环一样写
      `gave_up` + `ALL_RETRIES_EXHAUSTED`。
    - **延迟 drain 的定时器**：每个会话记下自己的 timer，投递成功、放弃、`disconnect_all`（先于取消 worker）都取消它，
      不会在关停时把 worker 重新拉起来。
    - **TaskManager 多个问题**：回复本身是按问题文本做 key 的 JSON 对象时按它答；纯文本就是用户对整张卡的原话，每个问题都给这句。
    - 前端补上 `spawn_failed` / `rate_limit` / `api_error` / `zombie` / `crash` 的说法，`unknown` 不显示原因。
    - 另外发现 P2c 带进来的测试挂起：`test_send_requests.py` 的排队推送任务在测试的 loop 关掉时还拿着连接池里的连接，
      会话 teardown 一直等。测试里加 fixture 等完这些推送；`session_state.publish` 的合并也不再被别的（已关闭）loop 上
      没跑的任务挡住。
- P4 收尾（2026-10-24）：
  - **删除清单逐项核对**：`forceClearStreamVerdict`、`shouldArmSpinnerFromBackend`、`_idleStreamingSince`、
    `_streamingSinceHardStart`、`_streamClearedAt`、`tabState.queuedMessage`、`drainQueuedMessage`、reconcile 的
    force-clear / re-arm 分支、聊天路径上的 `setIsStreaming`，生产代码里都已没有（SkillsPage 自己的 `setIsStreaming`
    按 §S3 不动）；三处还提到 `setIsStreaming` 的注释改成现在的写法。
  - **streaming-machine 的 `drain_pending`**：只有 `RESULT.hasQueuedMessage=true` 能进入，P2c 之后没人这样发。整个模式、
    `DRAIN_COMPLETE` 事件、`drainQueued` 字段删掉，`RESULT` 不再带参数。
  - **GenGuard 日志**：订阅流接管这个 tab 自己的 POST 同一回合时（`beginSubscriptionTurn` 发现 `postBubble`），记下被接管的
    POST 流代号（`tab._handedOverGen`）；那个 POST 后来的事件按设计丢弃，记 debug。其余的丢弃（被更新的发送取代的旧流）照旧记 WARN。
  - §8 三条待查证都有了结论（见上）。
