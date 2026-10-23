---
title: 回合进行中只有一个事实 — 结束"中途没反应、蜜蜂没了、追问卡在队列"
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DESIGN — 待 XG 拍板 §7 的两个问题；未写代码
related: chat-transcript-single-source.md（S3 会话状态唯一来源，本文是它没覆盖到的那一半）, streaming-spinner-ssot.md（2026-06-30，同类，未按设计落地）
---

# 回合进行中只有一个事实

## 0. 一句话

"这个会话现在有没有一轮在处理"在系统里**没有一个事实**。后端只报子进程状态
（`unit.state`），它在冷启动时说 `cold`、在 Stop 回收前说 `idle`，两个方向都不等于"有没有
回合"；前端于是靠聊天路径上 57 处 `setIsStreaming` 写入加 30 秒超时去**猜**。猜错一次，就是蜜蜂消失、
没有任何显示、追问进了前端自己的队列出不来。修法：**后端从受理一次发送到这一轮结束，维护一条
"会话活动"记录，作为唯一来源；回合的失败处理只有一套；前端的蜜蜂和排队只看这份记录，删掉所有
猜测逻辑。** 先写验收测试，再改。

范围：不含部署重启（开发期才有，XG 2026-10-23 明确排除）。

## 1. 问题清单（2026-10-20 至 10-23 日志，全部查证过）

| # | 现象 | 证据 | 根因 |
|---|---|---|---|
| 1 | 发出消息约 30 秒后蜜蜂消失，之后什么都不显示 | `frontend.log` 278 次 `forcing clear`，其中 122 次后端报 `cold`；10-23 13:41:34 发出，13:42:11 清掉，后端 13:42:17 才进 streaming；16:07:18 发出，16:07:58 清掉，16:08:26 才重新显示 | RC1 |
| 2 | 冷启动期间订阅流和 streaming-state 都看不到这一轮 | hub 的 `turn_start` 要等 CLI 的 `session_start`（`session_router._published_turn`, `start_on_session_start=True`），冷启动的 4–68 秒里只有 POST 上有 `turn_phase` | RC1 |
| 3 | 蜜蜂被清掉后又有排队消息时，不再重新显示 | `shouldArmSpinnerFromBackend`：`hasQueuedMessage` 为真直接返回 false（`streaming-guards.ts:381`） | RC1 + RC3 |
| 4 | Stop 之后的回收杀掉了下一条消息的进程（重试救回，首字 19.7 秒） | 10-22 19:20:26 `interrupt succeeded` 后转 IDLE，`_arm_recovery_checkpoint` await 了 3.4 秒，期间两个注入回合和用户发送都进来了，19:20:30 `_crash_to_cold_async` 杀掉了新回合的进程（`session_unit.py` interrupt 成功分支） | RC1 |
| 5 | 注入回合（后台 agent 完成通知）收到空回复：不重试、不说明，回复丢失 | 10-22 19:21:20 `injected_turn_failed ... API returned empty response` → `_crash_to_cold_async`，无 turn_notice（`session_router.run_injected_turn`） | RC2 |
| 6 | 前端日志 7824 条 `[OT01-GenGuard] discard stale stream event`，几乎都是 heartbeat | 订阅流 `turn_start` 时新建 `subRender` handler（`useChatStreamingLifecycle.ts:4744`），POST 那条 stream 之后的事件按设计被丢弃，但仍以 WARN 记成"丢失终结事件的证据" | RC3（观测噪音，不是丢内容） |
| 7 | 10-23 13:43:40–13:44:22 事件循环停了约 40 秒 | stack 落在 `reflect_job._hook → pipeline_human.owned_runs` 的目录扫描 | 已修 `9570fb9ce`，本文不再处理，只把它纳入 §4 的测试 |

"已修"之外的 #1–#6 都还在 main 上。

## 2. 根因

### RC1 "有没有回合在处理"没有一个事实

后端对外只有 `unit.state`（`core/session_state.session_state_entry`：`streaming = state == STREAMING`）。
它描述的是**子进程**，不是**回合**，两个方向都会说错：

- **说"没有"，其实有**：受理一次发送后，到 CLI 发出 `session_start` 之前（槽位、recall、构建
  prompt、冷启动 spawn、`--resume` 加载），unit 是 `cold` 或 `idle`。这段在正常机器上 4–6 秒，内存
  紧时实测 17.8 秒、61.9 秒（`wrapper_aenter_ms`）。hub 的回合也要等 `session_start` 才开始，所以
  订阅流同样看不到。
- **说"空闲"，其实不能用**：用户 Stop 后，`interrupt()` 先转 IDLE，再 await 写恢复检查点，最后才杀
  进程（#4）。中间这段 IDLE 会被发送、排队送出、注入回合当成"可以开始新一轮"。

前端拿不到这个事实，只能猜：`forceClearStreamVerdict`（30 秒 settle、120 秒 hard-cap、60 秒
queue 豁免、`isResuming` 豁免）、`shouldArmSpinnerFromBackend`（12 秒 flap-guard、有排队就不显示），
加上聊天路径 5 个文件里 57 处 `setIsStreaming` 写入（另有 SkillsPage 自己的 15 处，不在范围内）。每条规则都是某次 bug 的补丁，冷启动一慢就互相打架
（#1、#3）。2026-06-30 的 `streaming-spinner-ssot.md` 提过"spinner 只由后端状态驱动"，
`chat-transcript-single-source.md` S3 也写了"前端的转圈只看这份状态"，但落地的 S3 只是把同一份
`unit.state` 推得更快，前端的猜测逻辑一条没删。

### RC2 回合失败有三套处理

| 入口 | 失败后 | 位置 |
|---|---|---|
| 用户发送 / 频道 / 排队送出 | `_retry_with_resume`，最多 3 次，`turn_notice` | `session_unit.send` → `retry_manager` |
| 回答提问 / 审批后继续 | `_retry_continuation` | `continue_with_answer`, `continue_with_permission` |
| 注入回合（CLI 自开） | 记一条 WARNING，杀到 cold，结束 | `session_router.run_injected_turn` |

注入回合是唯一不重试、不说明原因的入口（#5）。这违反了已定的规则：除了用户 Stop，任何中断都要
自动恢复，最多 3 次。

### RC3 排队有两个地方

- 后端：SESSION_BUSY 时写进 `session_pending`，drain worker 在回合结束后送出，`pending_count` 由
  session state 报告。
- 前端：tab 认为在 streaming 时，消息存进 `tabState.queuedMessage`（`ChatPage.tsx` 发送路径，后续
  输入会拼进去），等前端认为回合结束后再由 `drainQueuedMessage` 发出。

前端这份队列的"什么时候送出"取决于前端的 `isStreaming`，也就是 RC1 里猜出来的值。猜错时，
消息停在前端，后端 `pending_count=0`，用户看到"在排队"，实际谁也不会送出它（#3）。

`GenGuard` 的 WARN（#6）是同一结构的副作用：回合开始后订阅流接管渲染，POST 那条流还在送事件，
被按设计丢弃，却被记成异常。

## 3. 方案

### S1 会话活动：受理到结束，一条记录（解 RC1）

**事实只放一处**：hub 里这个会话"打开的回合"（`session_events` 已有 `active` 回合日志）。改两点：

1. **回合从受理开始**，不再等 CLI 的 `session_start`。`run_conversation` 在发出受理回执
   （`accepted`，用户行已写库、send 账本已占位）时就 `turn_start`；之前只在 POST 上的
   `turn_phase`（preparing / starting / waiting）从此是这一轮的普通事件，订阅流和刷新都能看到。
   受理之前的回复（排队、SESSION_BUSY、校验错误）照旧只在 POST 上。四个入口（发送、提问续接、
   审批续接、注入）都已经经过 `_published_turn`，只改它的开始点。
2. **`session_state_entry` 报告活动，不报子进程**：新增 `activity` 字段，取值
   `idle | preparing | starting | waiting | streaming | waiting_input | stopping`，由
   "hub 有没有打开的回合 + 回合最后一个阶段 + unit 是否 WAITING_INPUT" 算出。`streaming` 字段保留，
   含义改为 `activity != idle`，旧客户端照常工作。同一个生成函数，推送和轮询都用它（S3 原样保留）。

**Stop 回收期间不说 idle**：用户 Stop 的成功分支改成 STREAMING → DEAD → COLD，不经过 IDLE；恢复
检查点在离开 STREAMING 之前写。期间 `activity = stopping`，回合在 COLD 之后才 `turn_end`。新的发送
在受理时看到有打开的回合，进 `session_pending`，回收完成后由 drain 送出，不会再落在将要被杀的进程上
（#4）。`_send_generation` 的过期检查保留。

热路径代价：每个阶段变化一次内存赋值 + 一次 `session_state` 发布（每轮最多 6 次，发布实测 2.2 µs/条，
见 chat-transcript-single-source §7.1）。不读库、不碰文件、不进 chat 等待的线程池。实现时补实测。

### S2 回合失败只有一套处理（解 RC2）

一个函数决定所有入口的失败：可重试的（进程被杀、空回复、断流）用 `--resume` 重开，最多 3 次，每次
写一条 `turn_notice`；用完后写 `gave_up` 说明；用户 Stop 是唯一不重试的结束。注入回合改走续接的
那条重试（它没有用户消息可重发，重开后让模型接着回答后台结果）。删掉 `run_injected_turn` 里
"记 WARNING、杀到 cold、结束"的分支。

### S3 前端只镜像，不猜（解 RC1、RC3）

- **蜜蜂 = `activity != idle`**。来源只有推送的 `session_state`，订阅流断开时用轮询结果，同一个
  reducer。tab 的 `isStreaming` 变成这个 reducer 的派生值，只有它能写；POST 的生命周期、订阅流的
  `turn_start`、ChatPage 的各条路径不再写它。阶段文字（"Starting session · Ns"）来自同一份 activity，
  不再靠 POST 的 `turn_phase` 事件。
- **Stop 的即时反馈**：点 Stop 时本地显示"正在停止"，下一份 activity 到来即以它为准。这是唯一的本地
  状态，不参与判断回合是否结束。
- **排队只在后端**：回合进行中发的消息照常 POST，后端受理时放进 `session_pending` 并回执
  `queued: true`；排队条只显示后端的 pending 列表。drain 已经会把多条一起送出
  （10-22 19:21:20 `pending_delivered count=2`），前端的拼接逻辑不再需要。
- **删除清单**：`forceClearStreamVerdict`、`shouldArmSpinnerFromBackend` 及其 settle / hard-cap /
  flap-guard / queue 豁免、`_idleStreamingSince`、`_streamingSinceHardStart`、`_streamClearedAt`、
  `tabState.queuedMessage` 及 `drainQueuedMessage`、reconcile 循环里的 force-clear 与 re-arm 分支。
  每删一项先确认没有别的读者（AGENTS.md "Change Discipline" 第 2 条），留下的写进 §6 并说明理由。
- **GenGuard 日志**：被订阅流接管的 POST 事件是预期的，降为 debug；只有真正跨回合的丢弃才记 WARN。

## 4. 验收测试（先写，在现在的 main 上必须是红的）

| # | 测试 | 验什么 | 现在的结果（预期） |
|---|---|---|---|
| T1 | 后端：发送受理后让 spawn 卡 60 秒，期间每秒读 `session_state_entry` | `activity` 依次是 preparing → starting → waiting，从不为 idle；hub 在受理时已有打开的回合 | 红：报 `cold`，无回合 |
| T2 | 前端：把 T1 的状态序列（推送 + 轮询交错）喂给 tab reducer | 蜜蜂全程显示，阶段文字连续 | 红：第 30 秒 force-clear |
| T3 | 冷启动期间再发一条 | 进 `session_pending`，排队条来自后端；回合结束后送出；蜜蜂不熄 | 红：消息停在前端队列 |
| T4 | 等审批时 Stop，紧接着发送 | 新回合的进程不被回收杀掉，`retries=0` | 红：复现 10-22 19:20 |
| T5 | 注入回合收到空回复 | 重试，写 `turn_notice`；3 次后写 `gave_up`；刷新后一致 | 红：回复丢失、无说明 |
| T6 | 性质测试：任意顺序的 activity 推送、轮询结果、订阅流断开/重连 | 每一步 蜜蜂 == (最新 activity != idle) | 红 |
| T7 | 事件循环上不做目录扫描（守住 #7） | PostToolUse hook 在 1 万个 run 目录下返回 < 5 ms | 绿（`9570fb9ce`），做回归 |

T6 用 KNOWN 棘轮：现在已知不一致的情况列进 KNOWN，每个阶段只许减少，空了才算完成。T1–T5 做完后
把对应场景加进 `liveReload.property.test.ts` 的生成器，保证"实时显示 == 刷新"在这些场景下也成立。
每个修复做反向验证：把修复撤掉，对应测试必须变红。

## 5. 实施顺序

| 阶段 | 内容 | 依赖 | 风险 |
|---|---|---|---|
| P0 | T1–T7 写出来，确认红 | — | 低 |
| P1 | S1 后端：回合从受理开始；`activity` 字段；Stop 不经过 IDLE | P0 | 中：动 `_published_turn` 和 interrupt，要过 session lifecycle steering 的 6 条不变量 |
| P2 | S2：失败处理合成一套，注入回合接入 | P1 | 中 |
| P3 | S3 前端：reducer 唯一写 `isStreaming`；排队只在后端 | P1 | 中：57 处写入逐个迁移 |
| P4 | 删除清单逐项核对、删除；GenGuard 降级 | P3 | 低 |

每阶段一个 commit，在自己的 worktree 里做，ff-merge 到 main。P1 和 P3 各做一次独立的
fresh-context 审查。

## 6. 不做什么

- 不处理部署重启打断回合（开发期才有）。
- 不新增 unit 状态，5 个状态不变；`stopping` 只是 activity 的取值。
- 不改对话记录（rev、快照 + 变更）和发送账本，它们已经是唯一来源。
- 不处理空闲后 prompt 缓存失效导致的首字慢（另一个问题，见上一轮讨论）；本文只保证这段时间里
  界面一直显示"在等模型"。

## 7. 要 XG 拍板

1. **回合从受理开始**（S1 第 1 点）。好处：只有一个事实，冷启动期间订阅流和刷新也看得到这一轮。代价：
   受理后、模型开口前，气泡里就会出现这一轮（只显示阶段行）。另一种做法是保留现在的开始点，另存一份
   "受理中"记录，但那就是第二个来源，不推荐。
2. **去掉前端队列**（S3 排队）。回合进行中连发几条，现在前端会拼成一条；改后是后端排队，drain 时一起
   送出，模型看到的是几条消息，不是拼好的一条。

## 8. 待查证（写测试时确认，不影响方案）

- V1：订阅流 `turn_start` 之后，POST 带来的 `turn_phase` 被 GenGuard 丢弃，阶段行可能在
  `session_start` 到首字之间消失（10-23 16:08:26–16:11:48 那 3.4 分钟）。S3 让阶段文字改读 activity，
  无论是否属实都会消掉；T2 会给出答案。
- V2：10-23 13:43:42 一次 `STORE-CLOBBER destroy`（1998 字符 → 0）。原因未查，P0 时复现。

## 9. 修订记录

- v1（2026-10-23）：初稿。
