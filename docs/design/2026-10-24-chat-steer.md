---
title: 回合进行中追加消息 —— steer 进正在跑的 turn
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: draft（待 XG 拍板 §9；P0 实验与验收测试未开始）
related: chat-turn-liveness.md（S1 会话活动、S3 排队只在后端）, 2026-10-24-chat-stream-structure.md（turn 内容怎么到前端）, chat-transcript-single-source.md
---

# 回合进行中追加消息 —— steer

## 0. 一句话

现在 turn 跑着的时候用户再发一条，SwarmAI 把它排队，等这一轮结束再作为新 turn 发出。
CLI 本身支持把消息并进正在跑的 turn（在下一次 tool result 时读到），KiroCrew 显示的
"Steered into the running turn" 就是这个。**本设计：CLI 能接的时候直接写给 CLI（steer），
接不了的时候照旧排队；steer 进来的消息在 transcript 里落在 CLI 真正读到它的位置；
CLI 没赶上并入、把它当成下一轮的，由我们像普通 turn 一样接住。** 排队路径保留，作为所有
不能 steer 的情况的兜底，消息不会丢。

范围：桌面 direct session。不含 channel、不含 `priority: "now"`（"停下改方向"，见 §7）。

## 1. 现状

| 环节 | 位置 | 行为 |
|---|---|---|
| 前端判断忙 | `desktop/src/hooks/streaming-guards.ts` `shouldQueueSend` | tab 在 streaming / sending / 等忙 / 重连 / heal grace 时为真 |
| 前端发送 | `desktop/src/pages/ChatPage.tsx` `sendWhileBusy` | 气泡加在 store 末尾，`isQueued: true`；单独 POST，只等 `accepted` |
| 后端受理 | `backend/core/session_router.py` `_run_conversation_impl` | 先写 user 行（`created_at` = POST 时间），`opener.open` 抢不到 turn → `_queue_admitted_send` → `mark_pending_by_id`，回执 `queued: true` + `SESSION_BUSY` |
| 投递 | `session_router.py` `drain_pending` | 会话不忙、IDLE/COLD、没有未答的 AskUserQuestion / 权限（`has_outstanding_tool_use`）、`cli_quiescent` 时，原子 claim 全部待发，合并成一个 turn 发出 |
| 写给 CLI | `backend/core/streaming_orchestrator.py` `write_user_message` | 先 `reader.claim(uuid, kind)`，再 `client.query`；消息带 `uuid`，不带 `priority`；写完把 claim 设成 `unit._turn_stream` |
| 写入闸 | `backend/core/cli_stream_reader.py` `quiescent` | idle、无新通知、`_open is None`、无等待中的 claim，才允许开新 turn |
| 认领 | `cli_stream_reader.py` `_open_turn` / `_on_echo` | 开着 `--replay-user-messages`（`session_unit.py:206`），CLI 读到我们的消息时带 uuid 回显；回显出现在别人的 turn 里时，未开始的 injected turn 转给 claim，否则 `_merged`（两个 stream 都收到后续所有帧） |

所以 SwarmAI 等于自己实现了一遍 `priority: "later"`。代价：用户想中途纠偏，只能等这一轮
跑完或按 Stop。

## 2. CLI 能做什么（实测）

SDK 0.2.168，CLI 2.1.301，Bedrock。脚本在 `/tmp/steer_script/`（`exp2.py`、`exp3.py`，
不进仓库）。消息形状和 `write_user_message` 写的一样，加或不加 `priority`。

| # | 场景 | replay | 结果 |
|---|---|---|---|
| X1 | 两条 Bash 之间写入，不设 `priority` | 关 | 第一条跑完后并入；模型按新消息跳过第二条；1 个 ResultMessage |
| X2 | 同上，`priority: "next"` | 关 | 同 X1 |
| X3 | `priority: "now"` | 关 | 正在跑的 Bash 被中断（`Command was aborted before completion`），当前 turn 以空结果结束，消息作为新 turn；2 个 ResultMessage |
| X4 | `priority: "later"` | 关 | 两条 Bash 都跑完，消息作为新 turn；2 个 ResultMessage |
| X5 | 第一个 tool_use 时写入 | 开 | 回显紧跟在那次 tool_result 之后，带我们的 uuid；1 个 ResultMessage，`num_turns=2` |
| X6 | 第一个 tool_use 时连写两条 | 开 | 两个回显按写入顺序出现在同一次 tool_result 之后；两条都生效；1 个 ResultMessage |
| X7 | 最后一次 tool_result 后 1.5 秒写入（模型在写最终回复） | 开 | 最终回复照常写完、没有体现新消息 → ResultMessage → `init` → 回显 → 新 turn；中间没有 `idle` |
| X8 | 纯文本 turn 开始 2 秒后写入 | 开 | 同 X7 |

结论：

- **落点只有 tool result 间隙。** 有下一次 tool result，消息就在那里并入当前 turn（X1/X5/X6）；
  没有了（纯文本、已在写最终回复），CLI 跑完当前 turn 后把它当下一轮（X7/X8）。这两种结果
  我们都得接住，而且事先无法知道会是哪种。
- **回显就是落点。** 开 replay 时，回显出现的位置就是模型读到它的位置（X5/X6）。reader 不需要
  猜。
- **当前 turn 和回退出来的新 turn 之间 CLI 不报 idle**（X7/X8），现有的
  `_abandon_unconsumed_claims` 不会误判。
- 不设 `priority` 与 `next` 行为一致。我们**显式写 `next`**，不依赖 CLI 默认值。

## 3. 不变量：哪些变，哪些不变

| 不变量 | 来源 | steer 之后 |
|---|---|---|
| 一个会话同时只有一条进行中的活动 | chat-turn-liveness S1 | **不变**。并入的 steer 属于当前活动；回退成新 turn 时，要等上一条活动结束才认领（§4 D6） |
| 排队只在后端，前端不排 | chat-turn-liveness S3 | **不变**。忙时照常 POST，后端决定 steer 还是排队 |
| 只在 CLI quiescent 时写 user 消息（I3） | `write_user_message` docstring、`quiescent` | **变**：新增一个例外，steer 在 turn 进行中写。I3 仍约束"开新 turn"的写入 |
| 每条 user 消息拥有自己的 turn | `_open_turn` / `_on_echo` | **变**：并入的 steer 没有自己的 turn，只在当前 turn 里留一个落点 |
| 写入后 claim 成为 `unit._turn_stream` | `write_user_message` | steer 写入**不得**替换 `_turn_stream`（它属于正在跑的 turn） |

上一轮聊天里我说"steer 会改变 S3 的'同时只有一个进行中 turn'"，不准确：S1/S3 都不变，
变的是 I3 和"消息拥有 turn"这两条。

## 4. 方案

### D1 什么时候 steer，什么时候排队

在 `_run_conversation_impl` 里 `opener.open` 失败、准备转 pending 之前判断。全部满足才 steer：

- 桌面 direct session（channel 照旧排队）；
- unit 活着、有 stream reader、reader 没坏；
- reader 有 open turn，且它是 human turn，或已被 sink 接手的 injected turn（`started`）；
- `has_outstanding_tool_use` 为假（不在等 AskUserQuestion / 权限的回答）；
- 不在 Stop 回收中（`unit._stopping`），`compact` 没持有 `_client_io`；
- 配置开关 `chat.steer_busy_send` 为真（P1 期间默认关，P2 前端上线后打开，§8）。

任一不满足 → 走现在的 `_queue_admitted_send`，行为不变。判断和写入之间 turn 恰好结束，
CLI 会把消息当下一轮（X7），由 D6 接住，不需要再判断一次。

### D2 写入：`write_steer_message`

新函数，和 `write_user_message` 放在一起，不改后者的行为：

- 持有 `_client_io` 写（短暂，和 `send` 一样获取后释放）；
- `reader.claim(uuid, "steer", ...)`，claim 上记下这条消息的 `client_id`；
- 消息带 `"priority": "next"`；
- **不设** `unit._turn_stream`；
- 写失败 → `drop_claim`，再走排队（`mark_pending_by_id`），回执改成 `queued: true`。

回执：`accepted` 带 `steer: true`。POST 到这里结束，后续都走订阅流（和排队一样，HTTP 断开
不影响投递）。claim 的结果由一个服务端任务等待（D6），不挂在 HTTP 请求上。

### D3 reader：回显的新分支

`_on_echo` 里，claim 的 `kind == "steer"` 且有 open turn 时：

- 从 `_claims` 移除，`echo_seen = True`，**不**设 `_merged`、**不**转移 injected turn；
- 往 `_open` 放一个标记项 `SteerFolded(uuid, client_id)`，它在帧序列里的位置就是回显的位置；
- 以 `folded` 结束这个 claim 的 TurnStream（D6 的任务据此收尾）。

没有 open turn 时回显（X7/X8：上一轮已关，`init` 已经 `_open_turn` 把 claim 设成 open），
走现有分支，`cur.uuid == uuid`，就是这条消息自己的 turn。

`_open_turn` 取"第一个 claim"时，steer claim 和普通 claim 一样按写入顺序排；这正是 CLI 读
的顺序（X6）。

### D4 落点的持久化

标记项由正在跑的 turn 的写入方处理。human turn 和 injected turn 都经 `TurnWriter.write`
→ `_persist_stream_event`（`turn_writer.py` 注明这是唯一入口），所以在那里处理一次，两条路
都覆盖。前提是把 SDK 帧转成事件的那一层（`_read_formatted_response`）把标记原样转成
`user_steered` 事件，不吞掉：

1. 把手上暂存的 tool result 先写进本 turn 最新的 assistant 行（`append_to_turn_row`），
   否则它们会被挂到 steer 之后的那一行，和 tool_use 分开；
2. 把 steer 的 user 行 `created_at` 改成现在（回显时间），`sent = 1`；
3. 之后的 assistant 行改用 row_key `"{steer_client_id}-asst"`，也就是"这条 steer 消息的回复"；
4. 发 turn 事件 `user_steered {client_id, row_id}`，和内容事件同一通道、同一顺序。

第 3 步让身份由产生方决定（AGENT R3 #8）：前端的新占位就是 `local-{steer_client_id}-asst`，
reconcile 按 client_id 一一对上，不会出现两个占位共用一个 client_id。重载后按 `created_at`
排序，显示为 [原回复的前半] [steer 气泡] [后半]，和直播一致。

ResultMessage 的用量、耗时、turn 结束事件仍属于原 turn，显示在最后一段之后。

### D5 快照与事件

- session_state 快照新增 `steering: [{client_id, preview}]`：已写给 CLI、还没回显的 steer。
  前端重连后据此恢复气泡状态（和现在的 `pending` 列表同一机制）。
- `user_steered` 进 transcript feed（user 行的 UPDATE 会触发 `transcript_revs`，P0 确认）。

### D6 回退成新 turn，以及兜底

steer 写入后启动一个服务端任务，等这个 claim 的 TurnStream：

- 结束于 `folded` → 什么都不做（D4 已经写完）；
- 拿到帧（CLI 把它当下一轮，X7/X8）→ 等上一条活动结束，`hub().try_claim_turn`，然后像
  injected turn 一样跑完它（复用 `run_injected_turn` 的写入与发布，row_key
  `"{steer_client_id}-asst"`，user 行 `created_at` 改成回显时间）。帧在 TurnStream 里缓冲，
  等待期间不丢；
- 以 `MessageNotConsumed` 结束（CLI 跑过去没读）→ `mark_pending_by_id`，交给 `drain_pending`；
- reader 坏了 / 进程没了 → 同上转 pending，respawn 后由 drain 带 `--resume` 发出。

drain 不会和回退抢：claim 还在 `_claims` 里时 `cli_quiescent` 为假，`drain_pending` 直接返回。

### D7 前端

气泡状态（`UserMessageView.tsx`）：

| 状态 | 何时 | 显示 |
|---|---|---|
| steering | 回执 `steer: true`，或快照 `steering` 里有它 | "Steering into the running turn…"，不可取消（已经写给 CLI） |
| steered | 收到 `user_steered` | "Steered into the running turn"；气泡移到当前 assistant 段之后，之后的内容进新占位 `local-{client_id}-asst` |
| 普通消息 | 回退成新 turn | 和 drain 出来的 turn 一样，订阅流按独立 turn 渲染 |
| queued / not sent | 后端选择排队，或未受理 | 现状不变 |

分段的实现读 `2026-10-24-chat-stream-structure.md` 的 A1 controllers 后再定，原则是：
新段的占位 id 用后端给的 row_key，不在前端另造。

### D8 Stop

用户在 steer 已写入、未回显时按 Stop：CLI 会不会在中断后执行它，P0 实验 E1 决定。
- 会执行 → 它走 D6 成为新 turn，和"消息已发出"一致；
- 不会 → claim 以 `MessageNotConsumed` 结束，转 pending，Stop 回收完后由 drain 发出。
两种结果都不丢消息，界面都显示为"已发送"。不在 Stop 时尝试撤回已写入 stdin 的消息。

## 5. 待查证（P0）

实验（照 `exp3.py` 的形状，开 replay）：

| # | 问题 | 影响 |
|---|---|---|
| E1 | Stop（`client.interrupt()`）时，已写入未回显的 steer 会不会被执行 | D8 |
| E2 | Agent 子 agent 运行中写入：在子 agent 内部的 tool result 并入，还是等父级 tool result？回显有没有 `parent_tool_use_id` | D3 是否要排除子 agent 帧 |
| E3 | 权限回调（`can_use_tool`）挂起时写入：CLI 是否读、何时读 | D1 当前直接排除；确认排除是否必要 |
| E4 | 写入一个 CLI 自开的 injected turn（后台任务通知） | D1 是否允许 |
| E5 | 带图片的 content list | D2 消息形状 |
| E6 | leak continuation（`c85a48c5`）在 steer claim 未回显时写入的顺序 | 可能出现 continuation 的回显落进 steer 自己的 turn → `_merged` 重复。若成立，continuation 在有 steer claim 时不写 |

代码确认：

- `messages` 的 UPDATE 是否触发 `transcript_revs`（D5）；
- 所有用 `"{client_id}-asst"` 找本 turn 行的读者（`TurnWriter`、`persisted_frames` 的
  `turn_client_id`、`mark_sent_batch`、turn 结束的 reconcile、前端 `local-{cid}-asst`），
  改 row_key 后逐个确认（AGENT R27，含测试）。

## 6. 验收测试

| # | 测什么 | 期望 |
|---|---|---|
| T1 | reader：human turn 中 steer claim 的回显 | 原 turn 收到 `SteerFolded`，位置在回显处；无 `_merged`；claim 以 `folded` 结束；原 turn 照常在 ResultMessage 结束 |
| T2 | reader：回显出现在新 `init` 之后（X7） | claim 成为 open turn，`echo_seen`；上一 turn 不受影响 |
| T3 | reader：两条 steer 同一间隙回显（X6） | 两个标记项按写入顺序 |
| T4 | reader：idle 前未回显 | claim 以 `MessageNotConsumed` 结束 → 行转 pending → drain 发出，只发一次 |
| T5 | D1 判断表，每个条件单独为假 | 走排队，回执 `queued: true` |
| T6 | 持久化：并入后 | user 行 `created_at` = 回显时间，`sent=1`；之前暂存的 tool result 在前一行；之后的行 row_key = `{steer_cid}-asst`；重载顺序 = 直播顺序 |
| T7 | 回退：上一活动未结束时 CLI 已开始 steer 的 turn | 等上一活动结束再认领；帧不丢；任意时刻只有一条活动 |
| T8 | 写失败 | `drop_claim` + 转 pending，回执 `queued: true` |
| T9 | `unit._turn_stream` | steer 写入前后不变 |
| T10 | 前端：steering → steered，及重连后按快照恢复 | 气泡位置与标签正确；无重复气泡；新段内容不写进旧段 |
| T11 | 端到端（真实 CLI，可选，冒烟） | 两条 Bash 中间发"跳过第二条"，界面显示 steered，第二条没跑 |

T1–T4、T7 用 fake 帧序列驱动 reader（按 X5–X8 的实测顺序），不依赖真实 CLI。每条测试回退对应
改动后必须变红（AGENT R28）。

## 7. 不做什么

- **`priority: "now"`（停下改方向）。** X3 证明能在不杀进程的情况下中断并执行新消息，比现在的
  Stop（`interrupt` 后回收）轻。但它是另一个交互（按钮还是快捷键、和 Stop 的关系），单独设计。
- **channel。** 多人同时往一个 turn 里 steer 会互相打断，channel 照旧排队。
- **等待 AskUserQuestion / 权限时 steer。** 这时用户该回答问题；E3 之后再看是否需要。
- **合并多条。** steer 每条单独写，不再像 drain 那样合并。
- **用户选择 steer 还是排队。** 先默认 steer；如果实际使用中有人需要"这轮完了再说"，再加。

## 8. 实施顺序

| 阶段 | 内容 | 上线条件 |
|---|---|---|
| P0 | 一个无上下文的 skeptic 先审本设计（重点：D3 的标记项会不会和 `_merged` / injected 转移冲突、D4 改 row_key 的消费方）；E1–E6 实验；§5 代码确认；T1–T9 先写成失败的测试 | 结论写回本文 §5，必要时改 D1/D3/D8 |
| P1 | 后端 D1–D6、D8，开关默认关 | T1–T9 绿；开关关时行为与现在逐字节一致（现有排队测试全绿） |
| P2 | 前端 D7；打开开关 | T10 绿；T11 冒烟通过；按 AGENT R16 单独部署、发一条 steer、切 tab 后重载确认顺序 |
| P3 | 观察一周后删开关 | 日志里 `steer_folded` / `steer_fell_back` / `steer_not_consumed` 计数无异常 |

P1 动 reader、router、写入路径，属于跨模块改动，开工前问 XG 走 native 还是 pipeline（AGENT R1）。

## 9. 待 XG 拍板

1. 忙时默认 steer（推荐），还是给用户一个选择？
2. `now` 是否本次一起做？推荐不做（§7）。
3. P1 走 native 还是 pipeline？

## 10. 已知问题（本设计之外）

- 排队消息被 drain 发出时，user 行保留 POST 时间的 `created_at`（`mark_sent_batch` 不改它），
  重载后可能排进上一轮的 assistant 行中间。这是读代码的推断，未在真实数据上确认；和 D4 第 2 步
  同一种修法（在 CLI 读到时改时间），但不在本设计范围内，单独记录。

## 修订记录

- 2026-10-24 v1：初稿。依据 X1–X8 实测和现有代码阅读。
