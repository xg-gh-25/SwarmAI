---
title: 回合进行中只有一个事实 — 结束"中途没反应、蜜蜂没了、追问卡在队列"
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: P0 DONE — 验收测试已提交（预期失败）；P1 未开始
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

1. **原子地开回合**：新增 `hub.try_open_turn(session_id, turn_id, source)`，同步执行、中间没有 await。
   已有打开的回合时返回 False。发送路径在 `accepted` 回执之后、占槽位之前调用它：
   - 开成功：发布 `turn_start`，这一轮从此可见。槽位排队、recall、spawn 都在这一轮里，阶段
     `preparing`（含"等槽位"）→ `starting` → `waiting` → `streaming`。
   - 开失败（已有一轮）：这条直接转 `session_pending`，POST 回执 `queued: true`，不发 `turn_start`。
   - `accepted` 仍不带 turnId（只在 POST 上），后面的 `turn_start` 带 turnId 和 eventSeq，POST 照旧
     从它绑定回合。SESSION_BUSY / QUEUE_TIMEOUT 发生在回合里时，回合以一条 `turn_notice` 结束，
     消息转 pending（"已受理的消息不丢"的规则不变）。
   - 提问续接、审批续接、注入回合、drain 都走 `try_open_turn`。注入回合开失败时照旧进
     `enqueue_injected`，等这一轮结束再跑。
2. **`activity` 字段，增量加，不改旧字段**：`session_state_entry` 新增
   `activity ∈ {idle, preparing, starting, waiting, streaming, waiting_input, stopping}` 和 `activitySeq`。
   `streaming` 的含义**不变**（仍是 `state == STREAMING`）：旧前端、`daemon-lib.sh` 的部署排空
   （`_daemon_other_busy_sessions` 读的就是它）都照常工作。
3. **`activitySeq` 定先后**：每个会话一个单调递增的序号，在开回合、换阶段、结束回合、进出
   WAITING_INPUT 时同步加一。推送和轮询都带它，tab 只接受比自己已有的更大的序号。现在推送靠
   `_LATEST` 只给推送之间排序（`session_state.py:101-116`），较慢的轮询仍可能覆盖较新的推送；序号
   解决这一点。
4. **新会话的第一条**：`accepted` 已带 `sessionId`。前端收到就绑定 tab 的 session，并立即订阅
   （`watchOpenTabs`），不再等 `session_start`（现在只在 `session_start` 绑定，
   `useChatStreamingLifecycle.ts:2943-2979`）。之后 `session_start` 的 id 必须相同，不同就记 ERROR
   并以 `session_start` 为准（会话身份规则不变）。

**Stop：从按下到进程回收完都是 `stopping`**

- 读流（`streaming_orchestrator.py:2672-2675`）和 `interrupt()`（`session_unit.py:4162`）两处"被打断后
  转 IDLE"都改为：用户 Stop 时不转 IDLE，回合保持打开、`activity = stopping`；恢复检查点写完、
  `_crash_to_cold_async(clear_identity=False)` 完成、unit 到 COLD 后，才结束回合。
- 自动打断（工具挂起的 watchdog，`autonomous=True`）不变：它不回收，照旧回 IDLE。
- 过期检查（`_send_generation`）、超时分支、`clear_identity=False` 都保留。
- 期间新来的发送：`try_open_turn` 失败，转 pending。
- **从 COLD 也能 drain**：现在只有"进入 IDLE"触发 drain，而 Stop 之后 unit 停在 COLD，pending 会
  一直没人送。改为回合结束（`turn_end`）就触发一次 drain；`drain_pending` 接受有 `_sdk_session_id`
  的 COLD unit，由 `run_conversation` 冷启动并 `--resume`（槽位照常申请）。"进入 IDLE 时 drain"保留。

**热路径代价**：分两种发布。阶段变化只发 `{activity, activitySeq}` 增量，不读库，一次内存赋值加
一次 `hub.publish`。完整快照（含 `pending_count` 和排队列表，要读 SQLite）只在开回合、结束回合、
pending 变化、订阅流连上时发。P1 要实测"生成 + 发布 + 推给 N 个订阅者"的端到端耗时（pending 0/10/100
行，订阅者 1/4 个），阶段增量在事件循环上的预算是每次 < 1 ms。全部在事件循环上，不进 chat 等待的
线程池。

### S2 回合失败：一个策略，按入口传参数（解 RC2）

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
| 取值 | `idle … stopping`（S1） | `building`（拼附件）→ `posting` → `accepted`；或 `failed`（未受理，后端连不上等） |
| 谁写 | 一个 reducer，唯一写 `tab.isStreaming`（`isStreaming = activity ∉ {idle}`） | 发送和 Stop 的代码 |

- **蜜蜂只看活动**。POST 生命周期、订阅流 `turn_start`、ChatPage 各路径不再写 `isStreaming`。阶段文字
  ("Starting session · Ns")也读 activity，不再靠 POST 的 `turn_phase` 事件。
- **Send / Stop 按钮看两者**：本地请求在 `building / posting` 时，或活动不是 idle 时，显示 Stop
  （`ChatInput.tsx:1070-1108` 现在只看 `isStreaming`）。
- **未受理的消息不丢**：`failed`（后端连不上、`accepted` 没到）时，用户气泡标"未发送 · 重试"，输入
  内容保留。同一个 `client_id` 重试，后端账本保证只算一次。
- **消息存进 MessageStore 的阶段保护**（`useChatStreamingLifecycle.ts:2494-2503`）跟着 handler 创建，
  属于本地渲染，不变，不参与"回合在不在"的判断。
- **排队只在后端**：回合进行中发的消息照常 POST，后端 `try_open_turn` 失败就转 pending，回执
  `queued: true`。
  - 完整快照带排队列表（`client_id`、预览文字、时间），读 `session_pending` 里未认领的行（现在只有
    内部的 `peek_pending_batch`，`session_pending.py:438`）。
  - 取消：新增 `DELETE /sessions/{id}/pending/{client_id}`。只删未被认领的行，并释放
    `send_requests` 的占位；已被 drain 认领的返回 409，界面显示"已在发送"。
  - 附件在点 Send 时就生成内容（现在是送出时才读），这是一处行为变化：排队期间改了的文件，送出的是
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
| T1 | 冷启动全程可见（后端） | 三个闸门：recall 里停住、spawn 里停住（`_ensure_spawned`，`STARTING` 在 `session_unit.py:2628` 发出后）、spawn 完成后发 query 之前停住（`WAITING`，`:2829`）。每个闸门处读 `session_state_entry` 和 hub | 依次是 preparing / starting / waiting，从不为 idle；hub 有打开的回合；`activitySeq` 递增 |
| T2 | 冷启动全程可见（前端） | 用当前完整的 `useChatStreamingLifecycle`（和 liveReload 一样的 harness），把 T1 的活动序列按推送和轮询交错喂进去，假时钟走 90 秒 | 蜜蜂全程在，阶段文字连续；现在第 30 秒被 force-clear |
| T3 | 并发发送 | 同一会话同时两条 POST | 只有一个 `turn_start`；第二条回执 `queued: true` 进 pending；第一轮结束后送出 |
| T4 | Stop 后紧接着发送 | 在 `_arm_recovery_checkpoint` 里放闸门；闸门停住时发新消息，再放行 | 新消息进 pending，没有进程被杀在它头上；回收完成后 drain 从 COLD 送出，`retries=0`。现在的代码在这里会转 IDLE 并接下新消息 |
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
- V2：10-23 13:43:42 一次 `STORE-CLOBBER destroy`（1998 字符 → 0），原因未查，P0 时复现。
- V3：提问 / 审批等待时，hub 的回合是否已经结束（续接会另开一轮）。S1 的 `waiting_input` 按"没有打开的
  回合但 unit 是 WAITING_INPUT"算；如果回合其实还开着，改为按回合阶段算。T1 的变体验证。

## 9. 修订记录

- v1（2026-10-23 `43794443f`）：初稿。
- P0（2026-10-23）：`backend/tests/test_turn_liveness_p0.py`（T1 T3 T4 T5 T8 预期失败，T9 回归）、
  `desktop/src/__tests__/turnLiveness.p0.test.ts`（T2 T6 T7 预期失败）。每条都确认是在它要验的断言上红
  （`raises=AssertionError`；前端先以普通 `it` 跑一遍看失败信息）。T5b 已由 `test_injected_turn_empty.py`
  覆盖，没有重复写。反向验证样例：把 interrupt 成功分支的转 IDLE 去掉，T4 变成 XPASS(strict)。
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
