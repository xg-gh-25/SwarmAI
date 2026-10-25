---
title: 回合进行中追加消息 —— steer 进正在跑的 turn
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-25
project: SwarmAI
status: draft v1.4（§9 已拍板；P0 实验 E1–E9 与 skeptic 审查已完成，结论已并入 D1–D8；T1–T18 失败测试未写）
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
| 前端发送 | `desktop/src/pages/chat/turnControls.ts` `sendWhileBusy`（A1 从 ChatPage 移出） | 气泡加在 store 末尾，`isQueued: true`；单独 POST，只等 `accepted` |
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
  D6 回退出来、还没被接手的 steer turn 不算（这时再发的消息排队）；
- reader 里**没有别的等待中的 claim**（已写入但还没开 turn 的 continuation / 别的消息）。
  已有一条 steer 在等不算阻挡：同一间隙连写多条按写入顺序回显（X6）；
- `has_outstanding_tool_use` 为假（不在等 AskUserQuestion / 权限的回答）。E3 证明 CLI 不会把
  权限挂起时写入的消息当成回答，所以这条不是正确性要求，是产品选择：那时用户该先回答问题；
- 不在 Stop 回收中（`unit._stopping`），不在中断中（`_interrupted`、tool-hang 看门狗的
  `_tool_hang_interrupt_at`），不在 API 错误重试中（`_retrying`），没有待执行的 self-heal 收尾
  （`_graceful_wrap_pending`）。`_client_io` 由 D2 写入时持有，不单独判断；
- 配置开关 `chat.steer_busy_send` 为真（P1 期间默认关，P2 前端上线后打开，§8）。

判断时记下当前 open turn 的身份（`target_turn`），写进 claim（D3 只往它里面并入，F6）。

任一不满足 → 走现在的 `_queue_admitted_send`，回执多带 `queued_reason`（`question` / `permission` / `busy`，D7 标签用），其余行为不变。判断和写入之间 turn 恰好结束，
CLI 会把消息当下一轮（X7），由 D6 接住，不需要再判断一次。

### D2 写入：`write_steer_message`

新函数，和 `write_user_message` 放在一起，不改后者的行为：

- 持有 `_client_io` 写（短暂，和 `send` 一样获取后释放）；
- 先把 user 行转成 **steer 在途**状态（见下），再 `reader.claim(uuid, "steer", target_turn=…)`，
  claim 上记下这条消息的 `client_id`；
- 消息带 `"priority": "next"`；content 和普通消息一样是 content list，图片照常（E5）；
  写入前先按下面"消息组装"拼好前缀；
- **不设** `unit._turn_stream`；
- 写失败 → `drop_claim`，`release_steer_to_pending`，回执改成 `queued: true`。

**steer 在途的 user 行（F3）。** 现有的 `sent = 0` 读者不认识"已写给 CLI、未回显、没有
`pending_seq`"这种行：`claim_pending_batch` 只按 `sent = 0 AND claimed_at IS NULL` 取行
（`session_pending.py`），会把它当排队消息 drain 出去；`list_pending` / `count_pending` 把它显示成
排队；`cancel_unclaimed` 会删掉一条已在 stdin 上的消息；`mark_pending_by_id` 对已是 `sent = 0`
的行返回它的 seq（NULL），`_queue_admitted_send` 把这当成"转换失败"；`mark_sent_batch` 按
`pending_seq IN (...)` 匹配，永远翻不了它。所以 steer 行要一个显式状态：

| 状态 | 列 | 进入 | 离开 |
|---|---|---|---|
| steer 在途 | `sent = 0`，`claimed_at = 写入时间`，`pending_seq = NULL`，`metadata.steer = "inflight"` | D2 写入前 `mark_steer_inflight(id)` | `confirm_steer` / `release_steer_to_pending` |
| 已并入 / 已开 turn | `sent = 1`，`created_at` 见 D4/D6，`claimed_at = NULL`，去掉 `metadata.steer`；并入时另记 `metadata.steered = true`（D7 标签） | `confirm_steer(id, created_at, folded)`：一条 UPDATE | — |
| 转回排队 | `sent = 0`，`claimed_at = NULL`，新的 `pending_seq`，去掉 `metadata.steer` | `release_steer_to_pending(id)` | 和排队消息一样由 drain 发出 |

`claimed_at` 非空让 `claim_pending_batch` 和 `cancel_unclaimed`（返回 `claimed`，和 D7"不可
取消"一致）自然跳过它；`list_pending` / `count_pending` 加 `metadata.steer IS NULL` 过滤，steer
在途的行只出现在 D5 的 `steering` 列表里。

启动恢复：`reopen_dangling_claims` 遇到 `metadata.steer = "inflight"` 的行，调用
`release_steer_to_pending`，而不是只清 `claimed_at`。依据 E7：进程在回显前死掉，`--resume` 时
CLI 不执行它、历史里也没有它，所以转回排队不会重复。剩余风险：回显已经到达、`confirm_steer`
还没写完时 daemon 崩溃，这条行会在重启后再发一次。缩小办法：回显后**先** `confirm_steer`，再处理
后面的帧；`confirm_steer` 不经过 `TurnWriter`（它吞写入错误，`turn_writer.py`），失败时记 ERROR
并重试一次，不转 pending。

**消息组装。** D1 的判断点在 `_run_conversation_impl` 里 `opener.open` 失败处，这时 content 还是
用户原话加附件：正常发送要拼的前缀都在后面（`session_router.py` 发送路径），排队的消息则存下
`terminal_context`，等 drain 发出时再走一遍完整发送路径。steer 不走那段路径，所以要自己拼：

| 前缀 | 正常发送 | steer | 理由 |
|---|---|---|---|
| terminal（`_prepend_terminal_to_query`） | 无条件 | **要**，无条件 | 它把终端内容包成 UNTRUSTED-DATA，是 prompt injection 的防线（R32 B 类），不能少；用户中途说"看终端这个报错"时模型也靠它 |
| UI-SENSE（`_decide_ui_block` + `_prepend_dynamic_context_to_query`） | 只在复用活进程时 | **要**，`warm=True` | turn 进行中进程一定是活的，system prompt 不会重建，query 是唯一通道。`_decide_ui_block` 会更新 `unit._last_ui_block`，这正确：子进程确实收到了 |
| recall（`_recall_query_block`） | 每个 turn 一次 | **不跑** | recall 是 turn 级的一步：它写 `_recall_query_block`、`_recall_snapshot`、`session_registry.recall_snapshot`、`_pending_recall_snapshot`，正在跑的 turn 已经做过；中途再跑会覆盖这个 turn 的 TSCC 记录。代价：回退成新 turn 的那条也没有 recall。用起来发现缺，再单独设计 |
| resume（`_prepend_resume_to_query`） | 冷启动续接时 | 不适用 | 进程是活的 |

`unit._delivered_query_prefix`（TSCC 显示的本 turn 前缀）不覆盖，steer 的前缀不进 TSCC（P1 已知
限制）。为了不让两条路径各拼各的、日后走样，把发送路径里"terminal + UI-SENSE"这两步抽成一个
函数，发送和 steer 都调它，T18 覆盖。

**hook（E8）。** CLI 在并入的那一刻对 steer 触发 UserPromptSubmit（紧挨在回显之前），hook 返回的
`additionalContext` 模型收得到。SwarmAI 注册的五个 UserPromptSubmit hook（`runtime_hooks.py`
`register_runtime_hooks`）逐个看了 turn 中途触发的后果：

| hook | 中途触发 | 结论 |
|---|---|---|
| `user_correction_detector` | 记下 steer 里的纠正 | 想要的：中途插话最常见的就是纠正 |
| `enforcement_injector` | 按 steer 的语言注入每轮提醒 | 正确 |
| `post_compact_injection` | 一次性注入被 steer 消耗 | 正确：上下文照样注入了 |
| `high_signal_capture` | 记一条观察 | 正确 |
| `persist_skill_tracker_reset` | 按"UserPromptSubmit = 新 turn"清空本 turn 已调用的 persist 类 skill | 偏差：steer 之前调过 s_persist、之后手改 MEMORY 时会多报一次 WARN。只是 WARN、不拒绝，P1 接受，记为已知问题 |

回显前 hook 已经跑过，所以纠正检测记下的是并入（或回退成新 turn）的那一条；回显前进程就死了的
steer（E7）不触发 hook，转回排队后由 drain 正常触发一次，不重复记录。

回执：`accepted` 带 `steer: true`。POST 到这里结束，后续都走订阅流（和排队一样，HTTP 断开
不影响投递）。claim 的结果由一个服务端任务等待（D6），不挂在 HTTP 请求上。

### D3 reader：回显的新分支

`_on_echo` 里，claim 的 `kind == "steer"`，且 open turn **就是写入时记下的 `target_turn`**：

- 从 `_claims` 移除，`echo_seen = True`，**不**设 `_merged`、**不**转移 injected turn；
- 往 `_open` 放一个标记项 `SteerFolded(uuid, client_id)`，它在帧序列里的位置就是回显的位置；
- 以 `outcome = "folded"` 结束这个 claim 的 TurnStream（D6 的任务据此收尾）。

open turn 不是 `target_turn` 时（F6：写入后有后台任务结束，CLI 开了一个 injected turn，sink
还没接手），不并入，走现有分支：未开始的 injected turn 转给 claim，steer 成为这个 turn 的主人，
D6 按"拿到帧"处理。

没有 open turn 时回显（X7/X8、E1、E6：上一轮已关，`init` 已经 `_open_turn` 把 claim 设成
open），走现有分支，`cur.uuid == uuid`，就是这条消息自己的 turn。

`_open_turn` 取"第一个 claim"时，steer claim 和普通 claim 一样按写入顺序排；这正是 CLI 读
的顺序（X6、E6）。

**子 agent（E2）。** 子 agent 跑着时写入，CLI 不在子 agent 内部的 tool result 并入，等父级的
Agent tool result 返回后才并入，回显 `parent_tool_use_id = None`。D3 不需要排除子 agent 帧；
代价是 steer 要等子 agent 跑完，D7 的 Waiting 状态可能持续较久（等待的就是气泡正上方的 Agents 面板）。

**claim 的结束原因要能区分（F10）。** 现在 claim 以 `folded` 结束和以 end-of-stream
`_fail_all(None)` 结束看起来一样。claim 结束时带显式 `outcome`：`folded`（并入）、`opened`
（成了自己的 turn，D6 接手）、`not_consumed`（CLI 空闲没读）、`reader_closed`（reader 关闭 /
进程没了，带 `echo_seen`）。D6 只按 `outcome` 分支。

**steer claim 的丢弃规则（F2）。** 现有规则只丢弃"登记后看到过 `running`"的 claim
（`_abandon_unconsumed_claims`），而 `_open_turn` 会清掉这个标记。steer 是在 turn 进行中登记的，
看不到新的 `running`：如果 CLI 跑完 `target_turn` 后空闲、始终没读它，它永远不会被丢弃，reader
永远不 quiescent，之后每次发送都 `SessionBusyError`，`drain_pending` 也一直提前返回。所以 steer
claim 单独一条规则：`target_turn` 结束（`_close_turn`）时，还没回显的 steer claim 记
`target_closed = True`；之后第一个 `idle` 到来时以 `not_consumed` 结束。X7/X8/E6 里 CLI 在
`target_turn` 结束和新 turn 之间不报 `idle`，所以这条规则不会误丢正常回退的 steer。不加超时：
`idle` 是 CLI 的确定信号，超时只会把慢的情况误判成丢失（STEERING #2）。

**continuation 不和 steer 抢（F1，E6）。** leak continuation（`streaming_orchestrator.py`，
`kind="leak"`）和 API 错误重试（`kind="api_retry"`）在 turn 结束时直接写一条消息，不检查别的
claim。如果这时有 steer claim 在等，E6 显示 CLI 按写入顺序先跑 steer 的 turn、再跑 continuation
的 turn：原 turn 的 orchestrator 在等 continuation，D6 在等原 turn 的活动结束，steer turn 里的
提问 / 权限没有读者，一直卡到每条消息的超时、重试杀进程。CLI 若把两条合进一个 turn（E6 没出现，
但没法排除），continuation 的回显落进 steer 的 turn，现有代码设 `_merged`，两边都写一遍。规则：
**有未结束的 steer claim 时，两种 continuation 都不写**，原 turn 照常结束，steer 的 turn 接着跑
（对用户来说，steer 本身就是下一步的指令）。这是硬规则：`write_user_message` 对
`kind in ("leak", "api_retry")` 统一判断，有 steer claim 时不写、返回"跳过"，两处调用方按
turn 正常结束处理。由 T12 覆盖。

### D4 落点的持久化

标记项由正在跑的 turn 的写入方处理。human turn 和 injected turn 都经 `TurnWriter.write`
→ `_persist_stream_event`（`turn_writer.py` 注明这是唯一入口），所以在那里处理一次，两条路
都覆盖。前提是把 SDK 帧转成事件的那一层（`_read_formatted_response`）把标记原样转成
`user_steered` 事件，不吞掉：

1. 把手上暂存的 tool result 先写进本 turn 最新的 assistant 行（`append_to_turn_row`），
   否则它们会被挂到 steer 之后的那一行，和 tool_use 分开；
2. `confirm_steer(id, created_at=现在, folded=True)`：user 行 `sent = 1` + 回显时间 + `metadata.steered`，一条 UPDATE（D2 表）。
   这一步**不经过** `TurnWriter.write`：writer 里的写入不推 live transcript feed（F8，
   `turn_writer.py` / `sqlite.py` 的 feed 抑制），也会吞掉写入错误；
3. 之后的 assistant 行改用 row_key `"{steer_client_id}-asst"`，也就是"这条 steer 消息的回复"。
   row_key 现在固定在 writer 上（`TurnWriter._row_key`），所以 writer 加 `switch_row_key(new)`；
   同时把 `unit._turn_client_id` 改成 `steer_client_id`，让这一 turn 里之后的
   `continue_with_answer` / `continue_with_cmd_permission`（`_continuation_client_id`）和 Stop 回执
   （`_persist_stop_receipt`）都写到新段，而不是写回 steer 之前那段（F5）；
4. 发 turn 事件 `user_steered {client_id, row_id, segments}`，和内容事件同一通道、同一顺序。
   `segments` 是这一 turn 到目前为止的全部 row_key，按顺序。

turn 级的身份不变：发布信封里的 `turn_client_id` 仍是原 turn 的 client_id（前端靠它认 turn），
段的 row_key 只用于行。

**跨段的 tool result（F5，已由实验排除）。** skeptic 担心 steer 之前发出的 tool_use，result 会在回显
之后才到、落进新段。实测不会：子 agent（E2）和同一条消息里的并行调用（E9）都是整批 result 回来
之后才回显。所以 `append_to_turn_row` 不改。只加一个不变量检查：tool result 对应的 tool_use 在
更早的段里时记 ERROR（观察 CLI 行为是否变了，不做恢复）。

**前端重放（F5）。** `beginSubscriptionTurn` 重放时只清 `metadata.client_id === cid` 的行
（`useChatStreamingLifecycle.ts`），中途重新订阅的 tab 会把 steer 之后那段显示两次。改成清掉
`segments` 里的全部 row_key；快照（D5）也带上当前 turn 的 `segments`。

第 3 步让身份由产生方决定（AGENT R3 #8）：前端的新占位就是 `local-{steer_client_id}-asst`，
reconcile 按 client_id 一一对上，不会出现两个占位共用一个 client_id。重载后按 `created_at`
排序，显示为 [原回复的前半] [steer 气泡] [后半]，和直播一致。

ResultMessage 的用量、耗时、turn 结束事件仍属于原 turn，显示在最后一段之后。

**回显前的 user 行。** 现在的 `_run_conversation_impl` 在 POST 时就写 user 行（`created_at` =
POST 时间），排队时再由 `mark_pending_by_id` 转成 `sent = 0`。steer 写入后、回显之前，这条行
如果是 `sent = 1`，任何一次非 streaming 的 reconcile（另一个 tab 开着同一会话、重载）都会按
`created_at` 把它排进正在跑的 turn 中间，正是 `6a75af694` 修掉的排队错位。所以 steer 先把行转成
D2 表里的"steer 在途"（`sent = 0`，不显示），确认时（D4 第 2 步，或 D6 接手新 turn 时）
`confirm_steer` 一次改成 `sent = 1` + 时间。这条 UPDATE 改了 `sent`，也就触发
`transcript_revs`（见 §5）。

**时间取哪个（F7）。** 并入时用回显时间：原 turn 之后写的行（turn_changes、deliverables、
`writer.close`）本来就该排在 steer 段之后。D6 回退成新 turn 时**不**用回显时间：回显到达时原
turn 的 writer 可能还没写完它最后几行，按回显时间会把 steer 排进原 turn 中间（`6a75af694` 修过的
同一类错位）。改用 D6 拿到 turn（`try_claim_turn` 成功）的时间，和 `claim_pending_batch` 用 claim
时间同一个道理。

### D5 快照与事件

- session_state 快照新增 `steering: [{client_id, preview}]`：已写给 CLI、还没回显的 steer。
  前端重连后据此恢复气泡状态（和现在的 `pending` 列表同一机制）。
- 快照里当前 turn 带 `segments`（D4），前端重放按它清行。
- 别的 tab 靠 transcript feed 看到 steer 行：`confirm_steer` 的 UPDATE 改了 `sent`，触发
  `transcript_revs`（§5 已确认）；它在 `TurnWriter` 之外执行，不受 writer 内的 feed 抑制（F8）。

### D6 回退成新 turn，以及兜底

steer 写入后启动一个服务端任务，等这个 claim 的 TurnStream，按 D3 的 `outcome` 分支：

- `folded` → 什么都不做（D4 已经写完）；
- `opened`（CLI 把它当下一轮，X7/X8/E1/E6）→ 等上一条活动结束，拿 turn，然后像 injected turn
  一样跑完它（复用 `run_injected_turn` 的写入与发布，row_key `"{steer_client_id}-asst"`，
  `confirm_steer` 用拿到 turn 的时间，F7）。帧在 TurnStream 里缓冲，等待期间不丢。
  - **等待怎么唤醒（F9）：** 等 hub 的"活动结束"信号，不轮询。`run_injected_turn` 现在不带
    turn opener，给它加一个显式 `opener` 参数，D6 先 `try_claim_turn` 再传进去。
  - **这段时间又来一条消息：** reader 有 open turn 但它不是被接手的 turn，D1 判为不能 steer →
    排队，在 steer 的 turn 之后 drain。
- `not_consumed`（CLI 空闲没读）→ `release_steer_to_pending`，交给 `drain_pending`；
- `reader_closed` 且 `echo_seen = False`（进程在回显前没了：Stop 回收、崩溃、self-heal）→
  `release_steer_to_pending`，respawn 后由 drain 带 `--resume` 发出。E7：这时 CLI 的
  transcript 里只有一条 `queue-operation` 入队记录，`--resume` 既不执行它，模型历史里也没有它，
  所以不会重复；
- `reader_closed` 且 `echo_seen = True`（turn 已开始，进程中途没了）→ **不**转 pending：消息已在
  CLI 历史里，由 `read_with_recovery` 的 `--resume` 续跑处理（F4，否则 drain 会再发一次）。

drain 不会和回退抢：claim 还在 `_claims` 里时 `cli_quiescent` 为假，`drain_pending` 直接返回。

**会话"忙"要看见 steer（F4）。** `is_busy`（`session_router.py`）只看 unit 状态、`_stopping` 和
hub 的 open turn；`has_background_work`（`session_unit.py`）只看后台任务和排队的 injected turn。
两者都不看 reader 的 open turn 和等待中的 claim。所以原 turn 结束、unit 回到 IDLE、D6 还没拿到
turn 的那个间隙里，turn 结束后的 RSS 检查（`streaming_orchestrator.py`）、TTL、eviction、release
都可能把进程杀掉，steer 的 turn 跑到一半就没了。改成：reader 有 open turn 或有等待中的 claim
时，两者都为真。

### D7 前端

mock：`docs/design/2026-10-25-chat-steer-ui-mock.html`。用户不知道什么是 tool result 间隙，
他只关心：我的话它看到没有、什么时候看到的、有没有改变它正在做的事。每个状态回答其中一个问题。

**气泡不移动。** steer 气泡从发出起就待在正在跑的 turn 的最下方（和现在的排队气泡一样在 store
末尾），live 内容在它上面的 assistant 条目里继续长。被读到时：并入，就在原地固定，新段占位
`local-{steer_client_id}-asst` 接在它下面；回退，上一轮在它上面结束，新的一轮接在它下面。两种
结果下已显示的东西都不移动、不重挂（design-judgment Surface 6 不变量 10）。

| 状态 | 何时 | 气泡下的一行灰字 | 回答的问题 |
|---|---|---|---|
| Waiting | 回执 `steer: true`，或快照 `steering` 里有它 | "Read after the current step · **Stop & send now**"；没有 ✕ | 看到了吗？要等多久？ |
| Read during the turn | 收到 `user_steered` | "Read during the turn"，重载后保留 | 它照我说的改了吗？ |
| Read after the turn | D6 接手、这条成为新 turn 的开头 | "Read after the turn"，只在直播时显示，重载后是普通消息 | 为什么上一轮没理我？ |
| Queued | 后端选择排队，或 steer 转回排队 | "Queued — sends after you answer the question above" / "…when this turn ends"，有 ✕ | 为什么这条没插进去？ |

- **等待时长不另起计时。** "当前步骤"就是气泡正上方那条 running strip（或 Agents 面板），工具名和
  计时它已经有了，不在气泡上重复（一件事一个表示）。也不加 spinner：聊天界面只有 header 脉冲和
  尾部的 bee 两个流式信号。
- **不可取消要看得出来。** Waiting 没有 ✕，不用虚线和半透明（那是可取消的排队样式）。
  **Stop & send now** 就是 Stop：进程回收，未读的 steer 按 D8 转回排队，回收完马上发出。很急或者
  发错时用户有出路，不用新增后端能力。以后做 `priority: now`，这个入口直接换成"立即打断"。
- **确认靠模型自己说。** 并入后模型通常会先说一句（"Got it, skipping…"），界面不再另加 ✓。
- **一个 turn 一个结尾。** 并入后的后半段不另起回复头，用量、耗时、输出卡片只在整个 turn 的
  末尾出现一次。
- **排队要说原因。** 回执和快照的 pending 项带 `queued_reason`：`question`（挂着
  AskUserQuestion）、`permission`、`busy`（开关关、channel、中断 / 重试 / 回收中、别的 claim 在等，
  统一显示 "sends when this turn ends"）。原因由 D1 的判断给出。问题卡片挂着时，用户最容易把
  输入框当成回答，标签直接指向上面的问题。
- **持久化的标签。** "Read during the turn" 重载后要能显示：`confirm_steer` 在并入时给 user 行记
  `metadata.steered = true`（D2 表"已并入"一行）。回退的行不记，重载后它本来就是一条普通的新
  turn 开头。

分段的实现读 `2026-10-24-chat-stream-structure.md` 的 A1 controllers 后再定，原则是：
新段的占位 id 用后端给的 row_key，不在前端另造。

### D8 Stop

E1 测的是 CLI 进程留着时的行为：`interrupt()` 后 CLI 以 `error_during_execution` 结束当前 turn，
接着开新 turn 执行已写入、未回显的 steer。但 SwarmAI 的用户 Stop 不留进程：中断后立即回收成
COLD（`session_unit.py` 的 PIT01 处理，`_crash_to_cold_async`），所以 E1 那条路径在我们这里
不会发生（F10）。实际情况：

| Stop 时 steer 的状态 | claim 结束 | 处理 |
|---|---|---|
| 已并入（`folded`） | 已结束 | 它是被停掉的那个 turn 的一部分，和那个 turn 一起停 |
| 已写入、未回显 | `reader_closed`，`echo_seen = False` | 转 pending，回收完后由 drain 发出（E7：`--resume` 不会自己执行它） |
| 回退成新 turn、已开始 | `reader_closed`，`echo_seen = True` | Stop 停的就是这个 turn，不再发 |

第二行和现在排队消息的行为一致：Stop 不清排队（`interrupt_session` 不碰 pending），排队的消息在
Stop 后照常发出。不在 Stop 时撤回已写入 stdin 的消息。

## 5. P0 结论（2026-10-25）

### 实验

SDK 0.2.168，CLI 2.1.301，Bedrock，开 replay。脚本 `/tmp/steer_script/exp4.py <e1..e6>`、
`exp5.py`（E7 写入后杀进程）、`exp6.py <session_id>`（E7 `--resume`）、`exp7.py <e8|par>`（E8、E9），不进仓库。每项跑一次。

| # | 场景 | 结果 | 影响 |
|---|---|---|---|
| E1 | 第一个 tool_use 时写入，紧接着 `interrupt()`，进程留着 | 当前 turn 以 `error_during_execution` 结束 → `init` → 回显 → steer 作为新 turn 执行 | D8：我们的 Stop 会回收进程，这条路径不发生（F10） |
| E2 | 子 agent 内第一个 tool_use 时写入 | 子 agent 的两条 Bash 都跑完；回显在父级 Agent tool result 之后，`parent_tool_use_id = None` | D3 不用排除子 agent 帧；steer 要等子 agent 跑完 |
| E3 | `can_use_tool` 挂起 8 秒期间写入（写文件命令，`sleep; echo` 会被自动放行，不触发回调） | 回调挂起期间不读；放行、tool 跑完后在 tool result 处并入，模型跳过第二条 | D1 排除权限挂起不是正确性要求，保留为产品选择。拒绝权限的路径没测 |
| E4 | 后台任务通知触发的 injected turn 里，第一个 tool_use 时写入 | 在 tool result 处正常并入 | D1 允许已接手的 injected turn |
| E5 | content list 带一张 PNG + 文字 | 在 tool result 处并入，模型认出颜色 | D2 沿用现有 content list |
| E6 | steer 在最终回复阶段写入（回退），再在 ResultMessage 时写 continuation | 两条按写入顺序各成一个 turn（`init 2` steer，`init 3` continuation），没有合并 | D3：continuation 和 steer 互相等的死锁成立，合并重复这次没出现；两者都由"有 steer claim 时不写 continuation"覆盖 |
| E8 | 注册一个 UserPromptSubmit hook，第一个 tool_use 时写入 steer；hook 对含 steer 文字的 prompt 返回 additionalContext | hook 对初始消息和 steer 各触发一次；steer 那次在 tool result 之后、回显之前；模型同时照 steer 和 additionalContext 做了（回复 `DONE BANANA MANGO`） | D2 hook 一节：纠正检测等 hook 对 steer 照常生效 |
| E9 | 同一条消息里并行两个 Bash（3 秒、12 秒），第一个 tool_use 时写入 | 两个 result 都回来之后才回显 | D4：没有跨段 tool result，删掉按 `tool_use_id` 回填 |
| E7 | 写入 0.5 秒后（回显前）关闭客户端杀进程，再 `--resume` | transcript 只有一条 `queue-operation` 入队记录；`--resume` 后 20 秒无自发活动，模型列出的历史里没有这条消息 | D6/D8：回显前进程没了 → 转 pending 不会重复。只测了一个时间点 |

### skeptic 审查

一个没有上下文的 skeptic 对照源码审了 v1.1，报 5 个 HIGH、4 个 MEDIUM、1 个 LOW。前提成立
（CLI 在 tool result 间隙并入，回显标出落点，两种结果都能接住），但 v1.1 照写不能进 P1。每条都
已在源码里核对，修法并入正文：

| # | 级别 | 问题 | 改在 |
|---|---|---|---|
| F1 | HIGH | leak / API 错误 continuation 在 steer claim 未结束时写入：互相等到超时，或合并后两边各写一遍 | D3 硬规则 + T12 |
| F2 | HIGH | 回合中途登记的 steer claim 看不到新的 `running`，CLI 不读时永不丢弃，会话永远忙 | D3 丢弃规则 + T4/T13 |
| F3 | HIGH | `sent = 0`、`pending_seq = NULL` 的行不被排队代码支持：会被 drain、显示成排队、能被取消删除、转 pending 静默失败 | D2 "steer 在途"状态 + T14 |
| F4 | HIGH | 进程死掉时可能重复投递；`is_busy` / `has_background_work` 看不见 reader 里的 steer，间隙里的回收会杀掉 steer 的 turn | D6 按 `echo_seen` 分支、忙判断扩展 + T15 |
| F5 | HIGH | row_key 拆分漏了 `TurnWriter._row_key`、`_turn_client_id`（提问 / 权限续写、Stop 回执）、前端重放、跨段 tool result | D4 + T16/T17 |
| F6 | MEDIUM | steer 可能并进一个 sink 还没接手的 injected turn | D1 记 `target_turn`，D3 只并入它 |
| F7 | MEDIUM | 回退成新 turn 时用回显时间，会排进原 turn 最后几行之前 | D4 时间规则 |
| F8 | MEDIUM | `TurnWriter` 内的写入不推 live feed | D4 第 2 步移出 writer |
| F9 | MEDIUM | D1 漏了中断中、重试中、别的 claim 等待中、self-heal 收尾；D6 唤醒方式未定义 | D1、D6 |
| F10 | LOW | `folded` 与 end-of-stream 结束无法区分；用户 Stop 回收进程，E1 不适用 | D3 `outcome`、D8 |

代码确认：

- ~~`messages` 的 UPDATE 是否触发 `transcript_revs`（D5）~~ **已确认（2026-10-24）：只有
  `UPDATE OF content, sent, metadata` 触发**（`database/sqlite.py` `_TRANSCRIPT_REV_DDL`，
  `messages_rev_update`）。只改 `created_at` 不触发：别的 tab 和 resync 看不到顺序变化。D4 第 2
  步因此要和 `sent` 0→1 放在同一条 UPDATE 里（`confirm_steer`），不必改触发器；
- row_key 的消费方已由 skeptic 逐个对过（F5），结论在 D4。还没对的：
  `backend/tests/test_turn_writer.py`、`test_cli_stream_reader.py` 里假设"一个 turn 一个 key"的
  断言，P1 改 writer 时一起看（AGENT R27，测试也是消费方）。

## 6. 验收测试

| # | 测什么 | 期望 |
|---|---|---|
| T1 | reader：human turn 中 steer claim 的回显 | 原 turn 收到 `SteerFolded`，位置在回显处；无 `_merged`；claim 以 `folded` 结束；原 turn 照常在 ResultMessage 结束 |
| T2 | reader：回显出现在新 `init` 之后（X7） | claim 成为 open turn，`echo_seen`；上一 turn 不受影响 |
| T3 | reader：两条 steer 同一间隙回显（X6） | 两个标记项按写入顺序 |
| T4 | reader：`target_turn` 结束后 CLI 报 idle，steer 始终没回显 | claim 以 `not_consumed` 结束（不依赖 `running`）→ `release_steer_to_pending` → drain 发出，只发一次；reader 回到 quiescent |
| T5 | D1 判断表，每个条件单独为假 | 走排队，回执 `queued: true` |
| T6 | 持久化：并入后 | user 行 `created_at` = 回显时间，`sent=1`；之前暂存的 tool result 在前一行；之后的行 row_key = `{steer_cid}-asst`；重载顺序 = 直播顺序 |
| T7 | 回退：上一活动未结束时 CLI 已开始 steer 的 turn | 等上一活动结束再认领；帧不丢；任意时刻只有一条活动 |
| T8 | 写失败 | `drop_claim` + 转 pending，回执 `queued: true` |
| T9 | `unit._turn_stream` | steer 写入前后不变 |
| T10 | 前端：Waiting → Read during / Read after / Queued，及重连后按快照恢复 | 气泡从发出到读取不移动、不重挂；标签按 D7 表（Waiting 无 ✕，Queued 有 ✕ 和原因）；重载后只有并入的保留标签；无重复气泡；新段内容不写进旧段 |
| T11 | 端到端（真实 CLI，可选，冒烟） | 两条 Bash 中间发"跳过第二条"，界面显示 steered，第二条没跑 |
| T12 | leak / api_retry continuation 时有未结束的 steer claim（F1，E6 帧序） | 不写 continuation；原 turn 正常结束；steer 的 turn 由 D6 接手；没有 `_merged`，没有重复行 |
| T13 | tool-hang 看门狗中断、进程留着，steer 未回显（F2） | 下一个 idle 以 `not_consumed` 结束 claim；之后的发送不报 `SessionBusyError` |
| T14 | user 行状态机（F3 + XG 要求）：POST → steer 在途 → 并入 / 开 turn / 转 pending / 启动恢复 | 每一步的 `sent`、`claimed_at`、`pending_seq`、`metadata.steer`、`created_at` 按 D2 表；在途行不被 `claim_pending_batch` 取走、不出现在 `list_pending` / `count_pending`、`cancel_unclaimed` 返回 `claimed`；`reopen_dangling_claims` 把在途行转成排队并分配 seq；转 pending 后 `mark_sent_batch` 能翻它 |
| T15 | 原 turn 结束到 D6 拿到 turn 的间隙（F4） | `is_busy`、`has_background_work` 为真；RSS 检查 / TTL / eviction 不杀进程；`reader_closed` + `echo_seen` 时不转 pending |
| T16 | 并入后同一 turn 里出现 AskUserQuestion / 权限 / Stop 回执（F5） | 续写的行用 `{steer_cid}-asst` |
| T18 | steer 的消息组装 | 有终端上下文时带 UNTRUSTED-DATA 包装的 terminal 块；带 UI-SENSE 块（与上次相同时为 `UI_STATE_UNCHANGED`）；没有 recall 块；`_delivered_query_prefix` 不变；发送路径和 steer 调同一个组装函数 |
| T17 | 前端：并入后中途重新订阅、重放（F5） | 按 `segments` 清行，steer 之后那段只显示一次 |

T1–T4、T7、T12、T13 用 fake 帧序列驱动 reader（按 X5–X8、E1–E9 的实测顺序），不依赖真实 CLI。
每条测试回退对应改动后必须变红（AGENT R28）。

## 7. 不做什么

- **`priority: "now"`（停下改方向）。** X3 证明能在不杀进程的情况下中断并执行新消息，比现在的
  Stop（`interrupt` 后回收）轻。但它是另一个交互（按钮还是快捷键、和 Stop 的关系），单独设计。
- **channel。** 多人同时往一个 turn 里 steer 会互相打断，channel 照旧排队。
- **等待 AskUserQuestion / 权限时 steer。** 这时用户该回答问题。E3 证明 CLI 不会把这时写入的
  消息当成回答，以后想放开不需要改 reader；拒绝权限的路径要先补测。
- **合并多条。** steer 每条单独写，不再像 drain 那样合并。
- **用户选择 steer 还是排队。** 先默认 steer；如果实际使用中有人需要"这轮完了再说"，再加。

## 8. 实施顺序

| 阶段 | 内容 | 上线条件 |
|---|---|---|
| P0 | ~~skeptic 审查；E1–E6 实验；§5 代码确认~~ 已完成（2026-10-25，另加 E7–E9），结论在 §5，已改 D1–D8。剩下：T1–T9、T12–T16、T18 先写成失败的测试 | — |
| P1 | 后端 D1–D6、D8，开关默认关 | T1–T9、T12–T16、T18 绿；开关关时行为与现在逐字节一致（现有排队测试全绿）；实现完后再请一个无上下文的 skeptic 审一次 diff |
| P2 | 前端 D7；打开开关 | T10、T17 绿；T11 冒烟通过；按 AGENT R16 单独部署、发一条 steer、切 tab 后重载确认顺序 |
| P3 | 观察一周后删开关 | 日志里 `steer_folded` / `steer_fell_back` / `steer_not_consumed` 计数无异常 |

## 9. 已拍板（XG，2026-10-24）

1. 忙时默认 steer，不给用户选择（§7 最后一条）。
2. `now` 本次不做，单独设计（§7）。
3. P1 走 native，在自己的 worktree 里做：先写失败的测试，每个修复做变异检查（回退修复，测试
   变红）；改动跨 reader、router、writer，实现完后的 skeptic 审查不能省（§8 P1）。
4. 另加：上线前把 steer 的 user 行状态机写成测试（T14）。排队消息排错位置的 bug（§10）根源就是
   user 行何时、以什么状态进入记录没有测试覆盖。

## 10. 已知问题（本设计之外）

- ~~排队消息被 drain 发出时，user 行保留 POST 时间的 `created_at`，重载后可能排进上一轮的
  assistant 行中间。~~ **已修复（`6a75af694`，2026-10-24）**，并在真实数据上确认（会话
  `63d28a0f`：22:42:20 排队的消息排在那一轮 22:40–22:55 的 assistant 行中间，它的回答被合进了
  上一轮的气泡）。修法：`claim_pending_batch` claim 时把 `created_at` 改成 claim 时间（不是
  `mark_sent_batch`）；claim 在 `sent = 0` 时发生，之后 `mark_sent` 的 `sent` 0→1 触发 rev。
  同一次修复还改了前端：`MessageStore._applyMerge` 对只在 store 里的消息（排队、未发出、未持久化）
  原来按时间字符串插入，气泡是 UTC 带 `Z`、记录是无时区的本地时间，在 UTC+8 下会往上错约 8 小时；
  现在它留在 store 里它后面那条消息之前，没有就放最后。D7 的 steer 气泡按 client_id 对上记录行，
  不走这条规则。

## 修订记录

- 2026-10-24 v1：初稿。依据 X1–X8 实测和现有代码阅读。
- 2026-10-25 v1.4：D2 补"消息组装"（terminal 和 UI-SENSE 要拼、recall 不跑，抽成共用函数）和 hook 一节
  （E8：UserPromptSubmit 对 steer 触发；五个 hook 逐个看过，`persist_skill_tracker_reset` 多报 WARN 记为
  已知问题）；E9：并行调用整批 result 后才并入，D4 删掉按 `tool_use_id` 跨段回填；加 T18。
- 2026-10-25 v1.3：D7 按用户视角重写成四个状态（Waiting / Read during / Read after / Queued），附
  mock `2026-10-25-chat-steer-ui-mock.html`；气泡从发出到读取不移动；排队带 `queued_reason`；并入的行记
  `metadata.steered`；T10 改写。
- 2026-10-25 v1.2：P0 完成。E1–E7 实测（§5）；skeptic 审查 F1–F10 全部在源码里核对后并入：
  D1 补闸门和 `target_turn`；D2 新增"steer 在途"行状态和 `confirm_steer` /
  `release_steer_to_pending`；D3 补 `outcome`、steer 专用丢弃规则、continuation 硬规则；D4 row_key
  拆分落到 `TurnWriter` 和 `_turn_client_id`、跨段 tool result、前端重放按 `segments`、时间规则；
  D6 按 `echo_seen` 分支、忙判断看 reader；D8 按"Stop 回收进程"重写。§6 改 T4，加 T12–T17。
  §9 记录拍板结果。
- 2026-10-24 v1.1：§10 已修复（`6a75af694`）；§5 `transcript_revs` 已确认；D4 补"回显前的
  user 行"（回显前 `sent = 0`，回显时 `sent` 与 `created_at` 同一条 UPDATE）；§1 `sendWhileBusy` 位置更正。
