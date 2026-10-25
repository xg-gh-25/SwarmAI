---
title: 回合进行中追加消息 —— steer 进正在跑的 turn
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-25
project: SwarmAI
status: draft v1.6（§9 已拍板；E1–E11 与三轮 skeptic 审查已完成，结论已并入 D1–D8；D6 改为接管成普通发送，这一版待复审；失败测试未写）
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
| 一个会话同时只有一条进行中的活动 | chat-turn-liveness S1 | **不变**。并入的 steer 属于当前活动；回退成新 turn 时，上一条活动结束的那一刻把它接管成一次普通发送（§4 D6） |
| 排队只在后端，前端不排 | chat-turn-liveness S3 | **不变**。忙时照常 POST，后端决定 steer 还是排队 |
| 只在 CLI quiescent 时写 user 消息（I3） | `write_user_message` docstring、`quiescent` | **变**：新增一个例外，steer 在 turn 进行中写。I3 仍约束"开新 turn"的写入 |
| 每条 user 消息拥有自己的 turn | `_open_turn` / `_on_echo` | **变**：并入的 steer 没有自己的 turn，只在当前 turn 里留一个落点 |
| 写入后 claim 成为 `unit._turn_stream` | `write_user_message` | steer 写入**不得**替换 `_turn_stream`（它属于正在跑的 turn）；只有 D6 把 steer 接管成普通发送时，才由发送路径把它设成 steer 的 claim，和任何新 turn 一样 |

上一轮聊天里我说"steer 会改变 S3 的'同时只有一个进行中 turn'"，不准确：S1/S3 都不变，
变的是 I3 和"消息拥有 turn"这两条。

## 4. 方案

### D1 什么时候 steer，什么时候排队

在 `_run_conversation_impl` 里 `opener.open` 失败、准备转 pending 之前判断。全部满足才 steer：

- 桌面 direct session（channel 照旧排队）；
- unit 活着、有 stream reader、reader 没坏；
- reader 有 open turn，且它是用户发起的 turn：human turn（含它的 leak / api_retry 续写），或 D6 已
  接管成普通发送的 steer turn。**injected turn 一律不 steer，排队**（v1.6 收窄，C）：后台任务触发的
  turn 和 steer 的交互（F6、H-a、M-e）是前三轮问题最多的地方，这一版整块拿掉。代价是 E4 那种场景
  （后台任务回来后 CLI 自己开的 turn）不能 steer，只能排队；
- reader 见过 CLI 的 session_state 帧（`_state_seen`）。不发这类帧的 CLI 由 `_close_turn` 直接
  `_mark_idle`，D3 的丢弃规则看不到 idle 与 running 的先后（H3）；
- reader 里**没有别的等待中的 claim**（已写入但还没开 turn 的 continuation / 别的消息）。
  已有一条 steer 在等不算阻挡：同一间隙连写多条按写入顺序回显（X6）；
- `has_outstanding_tool_use` 为假（不在等 AskUserQuestion / 权限的回答）。E3 证明 CLI 不会把
  权限挂起时写入的消息当成回答，所以这条不是正确性要求，是产品选择：那时用户该先回答问题；
- 不在 Stop 回收中（`unit._stopping`），不在中断中（`_interrupted`、tool-hang 看门狗的
  `_tool_hang_interrupt_at`），不在 API 错误重试中（`_retrying`），没有待执行的 self-heal 收尾
  （`_graceful_wrap_pending`）。`_client_io` 由 D2 写入时持有，不单独判断；
- 配置开关 `chat.steer_busy_send` 为真（P1 期间默认关，P2 前端上线后打开，§8）。

判断时记下当前 open turn（`target_turn`），写进 claim。它只用来判断"写入时那条活动是否已经结束"
（D3 丢弃规则、D6 接管时机），不决定消息并进哪个 turn（D3）。

**判断和登记之间不能有 await（H4）。** 顺序：`mark_steer_inflight`（异步 DB 写）→ 同一段同步代码里
重新判断 D1、记 `target_turn`、`reader.claim` → 写 stdin。重判不通过 → `release_steer_to_pending`，
回执 `queued: true`。否则在 DB 写的那次 await 期间 open turn 可能已经换了（原 turn 结束、开了
injected turn），claim 记下的就是一个过期的判断。写 stdin 本身的 await 不要紧：claim 已登记，
CLI 没收到消息之前不会有回显。

任一不满足 → 走现在的 `_queue_admitted_send`，回执多带 `queued_reason`（`question` / `permission` / `busy`，D7 标签用），其余行为不变。判断和写入之间 turn 恰好结束，
CLI 会把消息当下一轮（X7），由 D6 接住，不需要再判断一次。

### D2 写入：`write_steer_message`

新函数，和 `write_user_message` 放在一起，不改后者的行为：

- 持有 `_client_io` 写（短暂，和 `send` 一样获取后释放）；
- 按 D1 的顺序：先把 user 行转成 **steer 在途**状态（见下），再同步重判、
  `reader.claim(uuid, "steer", target_turn=…)`，claim 上记下这条消息的 `client_id` 和组装好的
  content（D6 接管后重试要用）。`notif_mark` 用默认值（claim 时的通知计数）。v1.5 改成上一次 idle
  时的计数，E11 证明是错的（D3）；
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
| 已回显、待接管 | 同"steer 在途"，另记 `metadata.steer = "echoed"`、`metadata.steer_echo_at` | 回显在一个新 turn 的开头（outcome `opened`），D6 还没接管：`mark_steer_echoed(id)` | D6 接管时 `confirm_steer` |
| 已并入 / 已开 turn | `sent = 1`，`created_at` 见下，`claimed_at = NULL`，去掉 `metadata.steer`；并入时另记 `metadata.steered = true`（D7 标签） | `confirm_steer(id, created_at, folded)`：一条 UPDATE，并推 live feed（M2，见下） | — |
| 转回排队 | `sent = 0`，`claimed_at = NULL`，新的 `pending_seq`，去掉 `metadata.steer` | `release_steer_to_pending(id)`：提交后自己调 `queued_while_busy` / drain kick（L2），并把 `unit._last_ui_block` 置空（L4） | 和排队消息一样由 drain 发出 |

**回显那一刻就留下持久的记号（H5）。** v1.4 让 D4 的 writer 在处理标记项时 `confirm_steer`，
而 `reader_closed` 且已回显的分支根本不确认：这两条路上，行会一直停在 `sent = 0` + `claimed_at` +
`steer = "inflight"`——历史里看不见（`sent = 0` 被过滤），`cleanup_expired` 不删它（`sqlite.py`
只删 `sent IS NULL OR sent = 1` 的过期行），下次启动 `reopen_dangling_claims` 把它放回排队，消息
就再发一次，可它已经在 CLI 的 transcript 里了。所以 reader 在 `_on_echo` 里同步回调
`on_steer_echo(uuid, outcome)`，回调安排一次 DB 写，不等 writer、不等 turn 结束：

- `folded`：`confirm_steer`，时间用回显时间。
- `opened`：只写 `mark_steer_echoed`，行仍不显示。这时原 turn 还没写完它最后几行（E10：回显通常
  比原活动结束早，中间隔着 metadata、RSS 检查、`writer.close`），用回显时间会排进原 turn 中间（F7）。
  D6 接管时再 `confirm_steer`，时间用接管时间。

回调在 reader 任务里同步运行，安排出去的写继承的是 reader 任务的 contextvars；
`TRANSCRIPT_FEED_SUPPRESSED` 只在 `TurnWriter.write` 里设，reader 任务里不会有，所以 feed 不会被挡。

**`confirm_steer` 要推 live feed（M2）。** live feed 只由 `db.messages.put/update` → `_report` 推出
（`sqlite.py`）；`session_pending` 里的裸 SQL UPDATE 只经触发器改 rev，不推 `transcript_row`，别的
tab 要到下一次 resync 才看见。所以 `confirm_steer` 写完调一次 `_report`（或走 `db.messages.update`）。
确认由 `on_steer_echo` 安排的写或 D6 接管时执行，都不在 `TurnWriter.write` 里，`TRANSCRIPT_FEED_SUPPRESSED` 不会挡住它。

`claimed_at` 非空让 `claim_pending_batch` 和 `cancel_unclaimed`（返回 `claimed`，和 D7"不可
取消"一致）自然跳过它；`list_pending` / `count_pending` 加 `metadata.steer IS NULL` 过滤，steer
在途的行只出现在 D5 的 `steering` 列表里。

启动恢复：`reopen_dangling_claims` 先在同一个事务里处理 steer 行：`"echoed"` 的按已发处理
（`sent = 1`，`created_at = steer_echo_at`：它已在 CLI 的 transcript 里），`"inflight"` 的转回排队
（分配 `pending_seq`）；再清其余行的 `claimed_at`；后一条 UPDATE 排除 `metadata.steer IS NOT NULL`
的行（L1）。现在它是一条 UPDATE 清掉所有 claimed 行（`session_pending.py`），先跑它会留下一条
`pending_seq = NULL` 的排队行：`claim_pending_batch` 会取走它，`mark_sent_batch`（`pending_seq IN`）
却永远翻不了它。依据 E7：进程在回显前死掉，`--resume` 时 CLI 不执行它、历史里也没有它，所以转回
排队不会重复。剩余风险：回显已到、回显时的那一次 DB 写还没完成时 daemon 崩溃，这条行会在重启后再发一次。
窗口就是一次 UPDATE；写失败时记 ERROR 并重试一次，不转 pending。

**消息组装。** D1 的判断点在 `_run_conversation_impl` 里 `opener.open` 失败处，这时 content 还是
用户原话加附件：正常发送要拼的前缀都在后面（`session_router.py` 发送路径），排队的消息则存下
`terminal_context`，等 drain 发出时再走一遍完整发送路径。steer 不走那段路径，所以要自己拼：

| 前缀 | 正常发送 | steer | 理由 |
|---|---|---|---|
| 附件转换（`_convert_non_native_blocks_to_path_hints` / `_convert_unsupported_blocks_to_path_hints`） | 无条件 | **要** | 它们在 D1 判断点之后才跑（`session_router.py`）。跳过时 docx、音频、视频 block 原样进入正在跑的对话：API 拒绝这个 turn 的下一次请求，`--resume` 的历史也一起坏；文件也不会存进 Attachments/（H6） |
| terminal（`_prepend_terminal_to_query`） | 无条件 | **要**，无条件 | 它把终端内容包成 UNTRUSTED-DATA，是 prompt injection 的防线（R32 B 类），不能少；用户中途说"看终端这个报错"时模型也靠它 |
| UI-SENSE（`_decide_ui_block` + `_prepend_dynamic_context_to_query`） | 只在复用活进程时 | **要**，`warm=True` | turn 进行中进程一定是活的，system prompt 不会重建，query 是唯一通道。`_decide_ui_block` 会更新 `unit._last_ui_block`：CLI 读到了就对；写失败、`not_consumed` 或回显前
进程没了，CLI 没收到这个块，`release_steer_to_pending` 把 `_last_ui_block` 置空，drain 重发时带完整
的块，而不是 `UI_STATE_UNCHANGED`（L4） |
| recall（`_recall_query_block`） | 每个 turn 一次 | **不跑** | recall 是 turn 级的一步：它写 `_recall_query_block`、`_recall_snapshot`、`session_registry.recall_snapshot`、`_pending_recall_snapshot`，正在跑的 turn 已经做过；中途再跑会覆盖这个 turn 的 TSCC 记录。代价：回退成新 turn 的那条也没有 recall。用起来发现缺，再单独设计 |
| resume（`_prepend_resume_to_query`） | 冷启动续接时 | 不适用 | 进程是活的 |

`unit._delivered_query_prefix` 不只给 TSCC 用：每次回复结束时它被拼进 `system_prompt_metadata` 的
`full_text`，System Prompt 弹窗和安全扫描面板都读它（`session_unit.py`）。steer 的 terminal 块是
UNTRUSTED-DATA，不出现在扫描面板里就是之前 Gate-1 拦过的那类缺口（M7）。处理分两种：

- 并入的 steer：不覆盖 `_delivered_query_prefix`，加进本 turn 的列表 `_steer_query_prefixes`，拼
  `full_text` 时按顺序接在后面；turn 开始时随 `_reset_turn_state` 清空。
- 接管成新 turn 的 steer（D6）：它的前缀就是这个新 turn 的前缀，接管时写进 `_delivered_query_prefix`
  （在 `_reset_turn_state` 之后），和普通发送一样。v1.5 里它会被 reset 清掉，哪儿都看不到。为了不让两条路径各拼各的、日后走样，把发送路径里
"附件转换 + terminal + UI-SENSE"这三步抽成一个函数，发送和 steer 都调它，T18 覆盖。

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
steer（E7）一般不触发 hook，转回排队后由 drain 正常触发一次。例外：hook 已经触发、回显还没到时
进程死掉（E8 的时间顺序），drain 重发时会再记一次（L6）。只是多一条纠正记录，P1 接受，记为已知问题。

回执：`accepted` 带 `steer: true`。同一 client_id 重复 POST 时（`send_requests` 幂等回执），在途的
行也要回 `steer: true`、`queued: false`；现在的回执按 `queued: sent == 0` 算，会让前端显示成可取消的
Queued，取消又返回 `claimed`（L3）。POST 到这里结束，后续都走订阅流（和排队一样，HTTP 断开
不影响投递）。之后由 reader 的回调和 D6 收尾，不挂在 HTTP 请求上。

**谁负责没读到的 steer。** reader 在 steer claim 结束时同步回调 `on_steer_end(uuid, outcome,
echo_seen)`。已被 D6 接管的 claim 由接管它的那次发送收尾（D6），回调不管；没被接管、也没回显的
（`not_consumed`，或接管前进程就没了，比如原 turn 里按了 Stop）→ `release_steer_to_pending`。

### D3 reader：回显的新分支

`_on_echo` 里 claim 的 `kind == "steer"` 时，按 open turn 分三种情况。**steer 永远不走 `_merged`**
（H4）：现有代码的兜底分支是 `self._merged = claim`（`cli_stream_reader.py` `_on_echo` 末尾），之后
每一帧两个 stream 都收到、各写一遍。

| open turn | 处理 | outcome |
|---|---|---|
| 就是这条 steer 自己的 turn（`cur.uuid == uuid`：上一轮已关，`init` 时 `_open_turn` 已把 claim 设成 open；X7/X8、E1、E6） | 现有分支 | `opened` |
| 未被接手的 injected turn（F6：写入后有后台任务结束，CLI 先开了它） | 现有分支：转给 claim，steer 成为这个 turn 的主人 | `opened` |
| 其他任何 open turn：human turn、已被 sink 接手的 injected turn、D6 已接管的另一条 steer 的 turn | 从 `_claims` 移除，`echo_seen = True`，往 `_open` 放标记项 `SteerFolded(uuid, client_id)`（位置就是回显的位置），**不**设 `_merged` | `folded` |

第三行不再要求 open turn 等于 `target_turn`。v1.4 只对 `target_turn` 并入，其余情况掉进 `_merged`：
在最终回复阶段连发两条 steer 时，CLI 把 S1 当下一轮，再在 S1 的 tool result 处并入 S2；S2 的
`target_turn` 是原 turn，S1 的 turn 又不是 injected，S2 就被 merged 了。现在 S2 并进 S1 的 turn，
由读 S1 这个 turn 的那一方（D6 接管后的普通发送）写标记项。每次回显都同步回调 `on_steer_echo`（D2）。

D1 不再在 injected turn 里 steer，但 injected turn 仍可能在回显时是 open turn：steer 写在 human turn
里，之后后台任务结束，CLI 先开了通知的 turn。所以表里第二、三行的 injected 情况保留。

`_open_turn` 取"第一个 claim"时，steer claim 和普通 claim 一样按写入顺序排；这正是 CLI 读
的顺序（X6、E6）。

**`notif_mark` 用默认值（E11 撤回 v1.5 的改法）。** E11：原 turn 里后台任务结束，CLI 不单独开通知
turn，而是在下一次 tool result 处把 `<task-notification>` 并进原 turn；前台 Bash 也发
`task_started` / `task_notification`。所以一个普通 turn 里通知计数就会上涨，和"有没有通知 turn 排在
前面"无关。v1.5 取上一次 idle 时的计数，会让 steer 自己的 `init` 永远被认成 injected turn（H-a）。
默认值是 claim 时的计数：claim 之前的通知已经并进原 turn（E11）；claim 之后才结束的后台任务，CLI
如果先开它的 turn，`init` 按 injected 处理，steer 的回显若落在里面就按表里第二行转过来。剩下没覆盖的
情况：后台任务恰好在原 turn 写最终回复时结束（没有 tool result 可并入），CLI 先跑哪个没测，T22
用两种顺序各测一遍。

**子 agent（E2）。** 子 agent 跑着时写入，CLI 不在子 agent 内部的 tool result 并入，等父级的
Agent tool result 返回后才并入，回显 `parent_tool_use_id = None`。D3 不需要排除子 agent 帧；
代价是 steer 要等子 agent 跑完，D7 的 Waiting 状态可能持续较久（等待的就是气泡正上方的 Agents 面板）。

**claim 的结束原因要能区分（F10）。** 现在 claim 以 `folded` 结束和以 end-of-stream
`_fail_all(None)` 结束看起来一样。claim 结束时带显式 `outcome`：`folded`（并入）、`opened`
（成了自己的 turn，D6 接管）、`not_consumed`（CLI 空闲没读）、`reader_closed`（reader 关闭 /
进程没了，带 `echo_seen`）。`on_steer_echo` / `on_steer_end` 和 D6 只按 `outcome` 分支。

**steer claim 的丢弃规则（F2、H3）。** 现有规则只丢弃"登记后看到过 `running`"的 claim
（`_abandon_unconsumed_claims`），因为写入时可能已经有一个 idle 在路上：那个 idle 在 CLI 读这条
消息之前发出，不能据此丢弃（`cli_stream_reader.py` 该函数的 docstring）。steer 同样会碰上：恰好在
原 turn 结束时写入，帧可能是 Result → idle → running → init → 回显。v1.4 的规则"`target_turn`
结束后第一个 idle 就丢"会在这里误丢，转回排队后 drain 再发一次，模型收到两遍。

规则和现有规则同一个语义：`target_turn` 结束时，还没回显的 steer claim 记 `target_closed = True`，
并清掉 `_running_seen`；之后**先看到 `running`、再到 `idle`**，也就是 CLI 跑了别的东西又空闲了、
始终没读它，才以 `not_consumed` 结束。只来 idle、没有 running 时继续等。E10 的实测帧序（Result →
idle → running → init → 回显）在这条规则下不会误丢。

"`target_turn` 结束"指它的任何结束路径，不只 `_close_turn`（M-d）：CLI 在 turn 还开着时报 idle，
reader 以 `TurnAbandoned` 结束它，不经过 `_close_turn`，并且先调 `_abandon_unconsumed_claims`
（`cli_stream_reader.py`）。所以每条结束路径都先设 `target_closed`、清 `_running_seen`，再做丢弃
检查；否则 turn 中途的 running（requires_action → running）会让 steer 被误丢，或者 `target_closed`
永远不设、D6 永远不接管。

"CLI 空闲、始终不读"靠 D6 兜住：那时 steer 已被接管成普通发送，读第一帧受现有的 INIT_TIMEOUT
约束，超时后走普通发送的重试（D6）。不新加超时数字（STEERING #2）。D1 要求 `_state_seen`，所以
不发 session_state 帧的 CLI 不会走到这里。

**continuation 不和 steer 抢（F1，E6）。** leak continuation（`streaming_orchestrator.py`，
`kind="leak"`）和 API 错误重试（`kind="api_retry"`）在 turn 结束时直接写一条消息，不检查别的
claim。如果这时有 steer claim 在等，E6 显示 CLI 按写入顺序先跑 steer 的 turn、再跑 continuation
的 turn：原 turn 的 orchestrator 在等 continuation，D6 在等原 turn 的活动结束，steer turn 里的
提问 / 权限没有读者，一直卡到每条消息的超时、重试杀进程。CLI 若把两条合进一个 turn（E6 没出现，
但没法排除），continuation 的回显落进 steer 的 turn，现有代码设 `_merged`，两边都写一遍。规则：
**有未结束的 steer claim 时，两种 continuation 都不写**，原 turn 照常结束，steer 的 turn 接着跑
（对用户来说，steer 本身就是下一步的指令）。这是硬规则：`write_user_message` 对
`kind in ("leak", "api_retry")` 统一判断，有 steer claim 时不写、返回"跳过"。跳过之后两处调用方的
收尾不一样（M1）：

- leak：turn 照常结束（strip 过的内容已经写下）。
- api_retry：现在跳过 continuation 会往下掉进错误处理，`error_text` 取 `_api_error_text`，
  `_is_retriable_error` 为真，接着 raise、杀进程、`--resume` 重试（`streaming_orchestrator.py`
  api_error_continue 之后）。这时 steer 可能已经回显，就会撞上 H5 那类重复。所以跳过时显示这个 API
  错误、按不可重试处理，unit 回到 IDLE，不 raise；steer 的 turn 本身就是这次重试。检查要放在
  `_api_error_wait`（5–15 秒）和 `_api_error_continuations += 1` 之前，不白等、不白占一次计数。
  CLI 在一个 `is_error` 的 Result 之后会不会照常执行排着的 `next` 消息，E1–E11 都没测，补 E12。
  如果不执行，steer 就会卡在 INIT_TIMEOUT，走 D6 的普通发送重试（重发一次 steer），不会丢。

由 T12a / T12b 覆盖。

### D4 落点的持久化

标记项由正在跑的 turn 的写入方处理。human turn 和 injected turn 都经 `TurnWriter.write`
→ `_persist_stream_event`（`turn_writer.py` 注明这是唯一入口），所以在那里处理一次，两条路
都覆盖。前提是把 SDK 帧转成事件的那一层（`_read_formatted_response`）把标记原样转成
`user_steered` 事件，不吞掉：

1. 把手上暂存的 tool result 先写进本 turn 最新的 assistant 行（`append_to_turn_row`），
   否则它们会被挂到 steer 之后的那一行，和 tool_use 分开；
2. user 行的确认不在这里：回显那一刻 `on_steer_echo` 已经 `confirm_steer`（D2）。writer 只处理 assistant 行；
3. 之后的 assistant 行改用 row_key `"{steer_client_id}-asst"`，也就是"这条 steer 消息的回复"。
   row_key 现在固定在 writer 上（`TurnWriter._row_key`），所以 writer 加 `switch_row_key(new)`；
   同时把 `unit._turn_row_key` 改成 `steer_client_id`（新字段，见下），让这一 turn 里之后的
   `continue_with_answer` / `continue_with_cmd_permission` 和 Stop 回执（`_persist_stop_receipt`）
   都写到新段，而不是写回 steer 之前那段（F5）；
4. 发 turn 事件 `user_steered {client_id, row_id, segments}`，和内容事件同一通道、同一顺序。
   `segments` 是这一 turn 到目前为止的全部 row_key，按顺序。

**段的 key 单独放一个字段（H1）。** v1.4 直接改 `unit._turn_client_id`，下一帧就被改回去：router
主循环每收到一个事件都执行 `if client_id and unit._turn_client_id != client_id:
unit._turn_client_id = client_id`（`session_router.py`，`async for event in unit.send(...)` 循环里）。
所以 `_turn_client_id` 保持 turn 的身份，不动；新加 `unit._turn_row_key`，并入时改成 steer 的
client_id。

它的取值（M-c）：`_reset_turn_state` 把 `_turn_client_id` 置成 None，human turn 的 id 要到 router
主循环才写上，所以"turn 开始时等于 `_turn_client_id`"拿到的是 None。改成：`_reset_turn_state` 把
`_turn_row_key` 也置 None；主循环写 `_turn_client_id` 的同一处，`_turn_row_key` 为 None 时也写成
同一个值，已有值时不动（并入后的值不会被下一帧改回）；injected turn 由 `begin_injected_turn` 直接设。

读 `_turn_client_id` 的地方逐个分（复审核对过源码）：

| 位置（`session_router.py`） | 用途 | 改读 |
|---|---|---|
| 主循环的重写 | 写 turn 身份 | 身份；`_turn_row_key` 为 None 时一并写 |
| Stop 的 `turn_stop-products` | 写行 | 行 key |
| Stop 回执 `_persist_stop_receipt` | 写行 | 行 key |
| `_continuation_client_id` | 同时给 TurnWriter 和 `_published_turn` | 拆开：writer 用行 key，发布用身份（L5：发布 `{_turn_client_id}-asst` 保持不变） |

要跟着改的测试：`test_assistant_client_id_correlation`、`test_turn_stop`、`test_session_events_publish`、
`test_pipeline_attention`（AGENT R27，测试也算消费方）。

**跨段的 tool result（F5，已由实验排除）。** skeptic 担心 steer 之前发出的 tool_use，result 会在回显
之后才到、落进新段。实测不会：子 agent（E2）和同一条消息里的并行调用（E9）都是整批 result 回来
之后才回显。所以 `append_to_turn_row` 不改。只加一个不变量检查：tool result 对应的 tool_use 在
更早的段里时记 ERROR（观察 CLI 行为是否变了，不做恢复）。

**前端重放（F5、M3）。** `beginSubscriptionTurn` 重放时只清 `metadata.client_id === cid` 的行
（`useChatStreamingLifecycle.ts`），中途重新订阅的 tab 会把 steer 之后那段显示两次。改成清掉
`segments` 里的全部 row_key；快照（D5）也带上当前 turn 的 `segments`。它还用 `appendMany` 把重放的
占位加在 store 末尾，也就是 Waiting 或已并入的 steer 气泡下面，重放出来的第一段就跑到 steer 下面去
了。改成：这个 turn 里有 steer 气泡时，占位插在第一个 steer 气泡之前；没有时照旧加在末尾。T17 检查
顺序，不只检查有没有重复。

第 3 步让身份由产生方决定（AGENT R3 #8）：前端的新占位就是 `local-{steer_client_id}-asst`，
reconcile 按 client_id 一一对上，不会出现两个占位共用一个 client_id。重载后按 `created_at`
排序，显示为 [原回复的前半] [steer 气泡] [后半]，和直播一致。

ResultMessage 的用量、耗时、turn 结束事件仍属于原 turn，显示在最后一段之后。

**回显前的 user 行。** 现在的 `_run_conversation_impl` 在 POST 时就写 user 行（`created_at` =
POST 时间），排队时再由 `mark_pending_by_id` 转成 `sent = 0`。steer 写入后、回显之前，这条行
如果是 `sent = 1`，任何一次非 streaming 的 reconcile（另一个 tab 开着同一会话、重载）都会按
`created_at` 把它排进正在跑的 turn 中间，正是 `6a75af694` 修掉的排队错位。所以 steer 先把行转成
D2 表里的"steer 在途"（`sent = 0`，不显示），回显时（并入）或接管时（回退，D6）由 `confirm_steer` 一次改成
`sent = 1` + 时间。这条 UPDATE 改了 `sent`，也就触发
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
  `transcript_revs`（§5 已确认），并显式 `_report` 推出这一行（D2，M2）；它由 `on_steer_echo` 或 D6 接管执行，不在
  `TurnWriter.write` 里，不受 writer 内的 feed 抑制（F8）。

### D6 回退成新 turn：接管成一次普通发送

v1.5 的 D6 另造了一个 turn 的主人（"steer 驱动方"，复用 `run_injected_turn`），第三轮审查在它身上
又找出 3 个 HIGH、5 个 MEDIUM：接手条件和回显的先后对不上（H-a、H-b）、没回显的 steer 进了
`read_with_recovery` 的 `--resume` 重试（H-c）、hub claim 记错任务（M-a）、显示成 injected turn
（M-e）。根因相同：hub claim、重试、来源、行 key、Stop、续写这些，普通发送都已经有，新主人要一样样
重接。v1.6 删掉这个新主人：**回退成新 turn 的 steer，就是一条"已经写进 stdin"的普通发送。**
做法和 drain 一样：drain 用后台任务调 `run_conversation(..., _drained_pending=True)` 发一条排队消息，
D6 用后台任务调 `run_conversation(..., _adopted_steer=adoption)` 发这条 steer，只是不再写 stdin。

**什么时候接管（H-a、H-b）。** 在 `_published_turn` 的 `finally` 里，`turn_end` 发布之后、
`take_waiters` 之前（hub 释放这个 turn 的同一段同步代码，中间没有 await）检查 reader。满足以下全部
条件就接管：

- 有一条没结束的 steer claim（还没回显的，或者回显已到、outcome 是 `opened`、还没被接管的），
  取写入顺序最早的那条；不看回显到没到（E10：回显通常比原活动结束早）；
- unit 是 IDLE，没有 `_stopping`（Stop 回收中不接管，见 D8）；
- reader 没有排着的、未被接手的 injected turn。有的话不接管，injected sink 照常先跑；它结束时
  `_published_turn` 的 `finally` 再检查一次。这个条件在 sink 跑完那个 turn 之后一定变假（H-a 说的
  "永远为真"出在用通知计数做条件，这里不用）。

接管在同一段同步代码里完成：先用 `asyncio.ensure_future` 建好接管任务，再
`try_claim_turn(session_id, turn_id, task=接管任务)`（新参数：claim 记的是接管任务，不是正在结束的
那个 turn 的任务；M-a），再把 claim 标成 `adopted`。接管任务拿着这个已经 open 的 opener 进
`run_conversation`。所以从原 turn 释放到 steer 的 turn 开始，hub 一直有主人：这期间 POST 来的消息
`opener.open` 失败，按 D1 判断（open turn 是已接管的 steer turn，可以 steer；还没 `init` 时排队）。

**普通发送路径的"接管"模式（`_adopted_steer`）。** 和普通发送只差这几处，其余全部照旧：

| 步骤 | 普通发送 | 接管模式 |
|---|---|---|
| user 行 | 写一条新行 | 不写，行已存在（在途 / 已回显） |
| D1 判断、消息组装 | 做 | 不做：消息已经组装好、写出去了；它的前缀写进这个 turn 的 `_delivered_query_prefix`（D2） |
| `opener.open` | 自己抢 hub | 用接管时已 open 的 opener |
| `unit.send` 的 quiescence 闸 | 检查 | 跳过：reader 不 quiescent 正是因为这条 steer claim，它就是我们的 |
| `unit.send` 里改写 query 的几步（self-heal 收尾、channel 收尾、hard floor、续接前缀、stop note） | 做 | 首次不做（消息已写出）；重试时照做 |
| `_stream_response` 的 `write_user_message` | 写 | 首次不写，`unit._turn_stream = steer claim`；只用一次，重试时照常写 |
| 发布 | `source="human"`，`turn_client_id` = 这条消息的 client_id | 一样，用 steer 的 client_id；row_key `"{steer_client_id}-asst"` |
| user 行确认 | — | 接管时 `confirm_steer`（时间 = 接管时间，F7）：回显已到就立刻做，没到就等 `on_steer_echo` |

**重试与收尾（H-c）。** 接管后这个 turn 的一切失败都走普通发送的 `_retry_with_resume`，它重发的是
query 本身，不是 `read_with_recovery` 那句通用的"继续"提示：

- 没回显时进程没了或 INIT_TIMEOUT：`--resume` 后重发这条 steer。E7：没回显的消息不在 CLI 历史里，
  所以只会被执行一次。v1.5 的"转回排队"同时存在就会重复，所以接管后的 claim 不再由 `on_steer_end`
  转回排队（D2）。
- 回显后进程没了：和普通消息在 turn 中途崩溃一样（重试重发原消息）。这不是 steer 特有的行为。

接管任务结束时像 drain 一样收尾（`_finalize_pending_delivery` 的同一套判断）：见到过回答（有内容的
assistant 事件）就确认 user 行，如果还没确认；没见到回答就结束了（Stop、不可重试的错误、
`not_consumed`），而行还没确认 → `release_steer_to_pending`。不会出现"已确认却没回答"的悬空行：
确认只在回显之后，而回显之后的失败由重试接着跑。

**空档里的 RSS 检查（M-b）。** turn 结束后的 RSS 主动重启在原 turn 的 generator 里、IDLE 之后、接管
之前运行（`streaming_orchestrator.py`），只看 `has_background_work`，看不到 steer claim；
`compact()` 因 reader 不 quiescent 拒绝，但杀进程照样进行。改成：`not cli_quiescent` 时直接返回。
这只是让 RSS 检查跳过，不放宽 `is_busy`。

**v1.5 的空档问题怎样解决。** 接管之后 unit 是 STREAMING，hub 有 turn：`is_busy`、Stop、TTL、
eviction、release、injected sink 都按"有 turn 在跑"对待，不用改。`is_busy` / `has_background_work`
一行都不改（H2 不复发）。

drain 不会和接管抢：claim 还在 `_claims` 里时 `cli_quiescent` 为假，`drain_pending` 直接返回；
接管后 hub 有主人，drain 本来就不发。

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
| Read after the turn | outcome `opened`（回显出现在这条 steer 自己的 turn 开头） | "Read after the turn"，只在直播时显示，重载后是普通消息 | 为什么上一轮没理我？ |
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
| 已接管成普通发送、还没回显 | 接管任务以 Stop 结束、没见到回答 | 接管任务收尾时 `release_steer_to_pending`，回收完后发出（Stop 不重试，所以不会和重试重复） |
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
| E7 | 写入 0.5 秒后（回显前）关闭客户端杀进程，再 `--resume` | transcript 只有一条 `queue-operation` 入队记录；`--resume` 后 20 秒无自发活动，模型列出的历史里没有这条消息 | D6/D8：回显前进程没了 → 转 pending（或接管后的重试重发）不会重复。只测了一个时间点 |
| E10 | 在 ResultMessage 到达时写入（a），以及在随后的 idle 帧时写入（b）（`exp8.py e10a/e10b`） | 两次帧序相同：Result → idle → running → init → 回显 → 回复 → Result → idle。CLI 读了它；写入之后、读取之前可以先来一个 idle | D3 丢弃规则必须"running 之后的 idle"（H3）；D6 接管不能等"还没回显"（H-b） |
| E11 | 原 turn 里起一个后台 Bash（3 秒），再跑一个前台 Bash（10 秒），然后写长回复；最终回复阶段写 steer（`exp8.py e11`） | 后台任务的 `<task-notification>` 在前台 Bash 的 tool result 处并进原 turn，没有单独的通知 turn；前台 Bash 也发了 `task_started` / `task_notification`；Result 之后直接 `init` → 回显 → steer 的 turn | 通知计数在普通 turn 里也会上涨 → 撤回 v1.5 的 `notif_mark` 改法（D3） |

### skeptic 审查

一个没有上下文的 skeptic 对照源码审了 v1.1，报 5 个 HIGH、4 个 MEDIUM、1 个 LOW。前提成立
（CLI 在 tool result 间隙并入，回显标出落点，两种结果都能接住），但 v1.1 照写不能进 P1。每条都
已在源码里核对，修法并入正文：

| # | 级别 | 问题 | 改在 |
|---|---|---|---|
| F1 | HIGH | leak / API 错误 continuation 在 steer claim 未结束时写入：互相等到超时，或合并后两边各写一遍 | D3 硬规则 + T12a/T12b |
| F2 | HIGH | 回合中途登记的 steer claim 看不到新的 `running`，CLI 不读时永不丢弃，会话永远忙 | D3 丢弃规则 + T4/T13 |
| F3 | HIGH | `sent = 0`、`pending_seq = NULL` 的行不被排队代码支持：会被 drain、显示成排队、能被取消删除、转 pending 静默失败 | D2 "steer 在途"状态 + T14 |
| F4 | HIGH | 进程死掉时可能重复投递；`is_busy` / `has_background_work` 看不见 reader 里的 steer，间隙里的回收会杀掉 steer 的 turn | D6 按 `echo_seen` 分支；忙判断的扩展在 v1.5 撤掉，改成 D6 接手（G2）+ T15 |
| F5 | HIGH | row_key 拆分漏了 `TurnWriter._row_key`、`_turn_client_id`（提问 / 权限续写、Stop 回执）、前端重放、跨段 tool result | D4 + T16/T17 |
| F6 | MEDIUM | steer 可能并进一个 sink 还没接手的 injected turn | D3：未接手的 injected turn 转给 claim（v1.5 起不再要求只并入 `target_turn`） |
| F7 | MEDIUM | 回退成新 turn 时用回显时间，会排进原 turn 最后几行之前 | D4 时间规则 |
| F8 | MEDIUM | `TurnWriter` 内的写入不推 live feed | 移出 writer；改为回显时由 `on_steer_echo` 确认（D2） |
| F9 | MEDIUM | D1 漏了中断中、重试中、别的 claim 等待中、self-heal 收尾；D6 唤醒方式未定义 | D1、D6 |
| F10 | LOW | `folded` 与 end-of-stream 结束无法区分；用户 Stop 回收进程，E1 不适用 | D3 `outcome`、D8 |

### skeptic 第二轮（v1.4，2026-10-25）

另一个没有上下文的 skeptic 只审 v1.2–v1.4 新增的部分，报 6 个 HIGH、7 个 MEDIUM、6 个 LOW。
H1–H6 和 M1–M4、M7 我在源码里复核过；其余是 reviewer 对照源码核对的，P1 写测试时再确认。
按成因分组：

| 组 | 成因 | 条目 | 改在 |
|---|---|---|---|
| G1 | turn 的身份放在一个每帧都会被改写的共享字段里 | H1（`_turn_client_id` 中途改，下一帧被主循环改回）、L5（续写发布的 `turn_client_id` 会跟着变） | D4：新字段 `_turn_row_key`，身份和行分开 |
| G2 | 用放宽共享判断来补"原 turn 结束到回显"的空档 | H2（放宽 `is_busy` 挡住 injected sink，提问时永远结束不了）、M4（空档里的新消息和 Stop 处理不对）、M6（空档里没人看着，CLI 卡住就一直占着会话） | D6：原活动结束时立刻接手，不放宽任何判断 |
| G3 | 丢弃和归属规则只考虑了一种帧序 | H3（Result 边界写入被误丢、重复投递；T13 与 E1 相反）、H4（第二条 steer 掉进 `_merged`；判断和登记之间有 await）、M5（默认 `notif_mark` 让通知的 turn 被认成 steer 的） | D1 同步重判 + `_state_seen`；D2 `notif_mark`；D3 新表、丢弃规则 |
| G4 | 行状态机没有一个所有出口共用的确认点 | H5（已回显的行卡在在途，重启后重发）、L1（`reopen_dangling_claims` 顺序）、L2（release 不 kick drain）、L3（重复 POST 回执显示成 Queued）、L6（hook 后进程死掉，纠正记两次）、M2（裸 SQL 不推 live feed） | D2：回显时确认、`_report`、恢复顺序、release 自己 kick |
| G5 | steer 绕过了发送路径里按消息做的步骤 | H6（附件转换）、M7（安全扫描面板看不到 steer 的 terminal 块）、L4（`_last_ui_block` 在 CLI 没收到时也更新） | D2 消息组装 |
| G6 | 跳过 continuation 后的收尾没区分 | M1（api_retry 跳过后会 raise、杀进程） | D3 continuation 规则 |
| G7 | 前端重放把占位加在末尾 | M3（重放的第一段跑到 steer 气泡下面） | D4 前端重放 |

G2 的 v1.5 改法（"原活动结束就接手"）在第三轮审查里又出了问题，见下一节。

### skeptic 第三轮（v1.5，2026-10-25）与方向调整

第三轮只审 v1.5，报 3 个 HIGH、5 个 MEDIUM、6 个 LOW。3 个 HIGH 我都在源码里复核过。三轮下来的规律：
并入当前 turn 那条路比较干净，问题集中在"回退成新 turn"（D6）——它另造了一个 turn 主人，hub
claim、重试、来源、行 key、Stop、续写都要重接，每接一处漏一处。XG 拍板走 B+C（§9 第 5 条）：D6 改成
接管成普通发送（B），injected turn 不 steer（C）。

| # | 级别 | 问题 | v1.6 |
|---|---|---|---|
| H-a | HIGH | `notif_mark` 取上次 idle 的计数后，"有通知排在前面"永远为真；steer 的 turn 被转给 claim 后既没有 D6 读，也没有 sink 读，回复丢失 | `notif_mark` 回到默认值（E11）；接管条件改成"有未接手的 injected turn 排着"，sink 跑完就变假（D6） |
| H-b | HIGH | 回显通常比原活动结束早（E10），"还没回显才接手"多数情况不触发 | 接管不看回显到没到（D6）；`opened` 的确认时间用接管时间，回显时先记 `echoed`（D2） |
| H-c | HIGH | 没回显的 steer 进了 `read_with_recovery` 的 `--resume` 重试，发通用"继续"提示，再加上转回排队，重复 | 接管后走普通发送的重试，重发的是 steer 本身；接管后不再转回排队，收尾像 drain（D6） |
| M-a | MEDIUM | `try_claim_turn` 记的是正在结束的那个任务，任务结束后 claim 被当过期丢掉 | 先建接管任务，`try_claim_turn(task=)`（D6） |
| M-b | MEDIUM | turn 结束后的 RSS 检查仍在空档里杀进程 | `not cli_quiescent` 时跳过（D6） |
| M-c | MEDIUM | human turn 的 `_turn_row_key` 在 `_reset_turn_state` 里拿到 None | 主循环在它为 None 时一并写（D4） |
| M-d | MEDIUM | `target_closed` 只在 `_close_turn` 设，`TurnAbandoned` 路径漏了 | 每条结束路径都设，且在丢弃检查之前（D3） |
| M-e | MEDIUM | 复用 `run_injected_turn` 让 steer 的 turn 显示成 injected，并带上后台任务那一行 | 不再复用，走普通发送，`source="human"`（D6） |
| L | LOW | 接管的 steer 前缀被 reset 清掉；Stop 回收中不应接手；api_retry 检查要在等待和计数之前；`confirm_steer` 的 contextvars 说明写错了任务；§5 F6 行和 D3 矛盾 | D2、D6、D3、本节 F6 行 |

还要补的实验：

| # | 场景 | 要确认 |
|---|---|---|
| E12 | API 错误的 Result（`is_error`）之后，排着的 `next` 消息会不会被执行 | 决定 T12b 的帧序；不执行时 steer 走 INIT_TIMEOUT 后的普通重试，不会丢 |

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
| T1 | reader：human turn 中 steer claim 的回显 | 原 turn 收到 `SteerFolded`，位置在回显处；无 `_merged`；claim 以 `folded` 结束；`on_steer_echo` 被同步调用一次；原 turn 照常在 ResultMessage 结束 |
| T2 | reader：回显出现在新 `init` 之后（X7） | claim 成为 open turn，`echo_seen`；上一 turn 不受影响 |
| T3 | reader：两条 steer 同一间隙回显（X6） | 两个标记项按写入顺序 |
| T4 | reader：`target_turn` 结束后 CLI 跑了别的（running）又报 idle，steer 始终没回显 | claim 以 `not_consumed` 结束 → `release_steer_to_pending` → drain 发出，只发一次；reader 回到 quiescent。只有 idle、没有 running 时 claim 不结束 |
| T5 | D1 判断表，每个条件单独为假 | 走排队，回执 `queued: true` |
| T6 | 持久化：并入后 | user 行在回显时就是 `sent=1`、`created_at` = 回显时间（writer 还没处理标记项时也是）；之前暂存的 tool result 在前一行；之后的行 row_key = `{steer_cid}-asst`；重载顺序 = 直播顺序 |
| T7 | 回退：上一活动未结束时 CLI 已开始 steer 的 turn（回显已到，E10 顺序） | `_published_turn` 的 `finally` 里同步接管；hub claim 记的是接管任务，原 turn 的任务结束后 claim 仍在；帧不丢；任意时刻只有一条活动；发布的 `source` 是 human |
| T8 | 写失败 | `drop_claim` + 转 pending，回执 `queued: true` |
| T9 | `unit._turn_stream` | steer 写入前后不变 |
| T10 | 前端：Waiting → Read during / Read after / Queued，及重连后按快照恢复 | 气泡从发出到读取不移动、不重挂；标签按 D7 表（Waiting 无 ✕，Queued 有 ✕ 和原因）；重载后只有并入的保留标签；无重复气泡；新段内容不写进旧段 |
| T11 | 端到端（真实 CLI，可选，冒烟） | 两条 Bash 中间发"跳过第二条"，界面显示 steered，第二条没跑 |
| T12a | leak continuation 时有未结束的 steer claim（F1，E6 帧序） | 不写 continuation；原 turn 正常结束；steer 由 D6 接管；没有 `_merged`，没有重复行 |
| T12b | api_retry continuation 时有未结束的 steer claim（M1） | 检查在 `_api_error_wait` 和计数之前；不写 continuation；显示 API 错误，不 raise、不杀进程、不 `--resume`；unit 回到 IDLE 后 D6 接管（帧序按 E12） |
| T13 | tool-hang 看门狗中断、进程留着，steer 未回显（F2、H3） | 按 E1：steer 作为新 turn 执行，outcome `opened`；之后的发送不报 `SessionBusyError` |
| T14 | user 行状态机（F3、G4 + XG 要求）：POST → steer 在途 → 并入 / 开 turn / 转 pending / 启动恢复 | 每一步的 `sent`、`claimed_at`、`pending_seq`、`metadata.steer`、`created_at` 按 D2 表；在途行不被 `claim_pending_batch` 取走、不出现在 `list_pending` / `count_pending`、`cancel_unclaimed` 返回 `claimed`；`reopen_dangling_claims` 先把在途行转成排队并分配 seq，再清其余行，不留 `pending_seq = NULL` 的排队行；转 pending 后 `mark_sent_batch` 能翻它；`release_steer_to_pending` 自己 kick drain 并清 `_last_ui_block`；`confirm_steer` 推一条 `transcript_row`；每个有回显的 outcome 结束后行都是 `sent = 1` |
| T15 | 原 turn 结束到 steer 的 turn 开始（F4、G2、M-b） | 接管后 unit 是 STREAMING，`is_busy` 为真，Stop 判为 turn 在跑；`is_busy` / `has_background_work` 的代码没改；原 turn 结束后的 RSS 检查在 `not cli_quiescent` 时跳过、不杀进程 |
| T16 | 并入后同一 turn 里出现 AskUserQuestion / 权限 / Stop 回执（F5、H1） | 续写的行用 `{steer_cid}-asst`，即使之后每帧主循环都在刷 `_turn_client_id`；发布的 `turn_client_id` 仍是原 turn 的 |
| T18 | steer 的消息组装（G5） | 非原生附件（docx / 音频 / 视频）转成路径提示并存进 Attachments/；有终端上下文时带 UNTRUSTED-DATA 包装的 terminal 块；带 UI-SENSE 块（与上次相同时为 `UI_STATE_UNCHANGED`）；没有 recall 块；`_delivered_query_prefix` 不变，steer 的前缀进 `_steer_query_prefixes`、出现在 `full_text` 里；发送路径和 steer 调同一个组装函数 |
| T17 | 前端：并入后中途重新订阅、重放（F5、M3） | 按 `segments` 清行，steer 之后那段只显示一次；重放的第一段在 steer 气泡之上 |
| T19 | reader：最终回复阶段连写两条 steer，CLI 把 S1 当下一轮、在 S1 的 tool result 处并入 S2（H4） | S1 `opened`，S2 `folded` 进 S1 的 turn；无 `_merged`；每一帧只写一次 |
| T20 | injected turn（H2 回归、C） | 没有 steer 时照常实时渲染，turn 里提问能被回答、turn 能结束；injected turn 跑着时发的消息排队，`queued_reason = busy` |
| T21 | reader：Result 边界写入，帧序按 E10（H3） | claim 不被 idle 丢弃，outcome `opened`，只投递一次 |
| T22 | reader：原 turn 里有后台任务结束（E11 帧序：通知并进原 turn），之后写 steer；另测后台任务在最终回复阶段结束、CLI 先跑通知 turn 的顺序（H-a） | E11 顺序：Result 之后的 `init` 归 steer；另一顺序：通知 turn 按 injected 由 sink 先跑，跑完后接管 steer；两种顺序下 steer 都只得到自己的回复，没有无人读取的 turn |
| T24 | 接管后没回显就 INIT_TIMEOUT / 进程没了（H-c） | 普通发送的重试重发 steer 一次，不发通用"继续"提示；不转回排队；回答只出现一次 |
| T25 | 接管后、回显前按 Stop（D8） | 接管任务收尾时转回排队，回收完后发出一次 |
| T26 | 接管任务的收尾（D6，drain 同一套判断） | 见到回答 → 行已确认；没见到回答就结束 → 未确认的行转回排队；已回显的不转 |
| T23 | 在途行重复 POST（L3） | 回执 `steer: true`、`queued: false` |

T1–T4、T7、T12a/b、T13、T19、T21、T22、T24 用 fake 帧序列驱动 reader（按 X5–X8、E1–E12 的实测顺序），不依赖真实 CLI。
每条测试回退对应改动后必须变红（AGENT R28）。

## 7. 不做什么

- **`priority: "now"`（停下改方向）。** X3 证明能在不杀进程的情况下中断并执行新消息，比现在的
  Stop（`interrupt` 后回收）轻。但它是另一个交互（按钮还是快捷键、和 Stop 的关系），单独设计。
- **channel。** 多人同时往一个 turn 里 steer 会互相打断，channel 照旧排队。
- **在 injected turn 里 steer（v1.6 收窄，C）。** 后台任务回来后 CLI 自己开的 turn 跑着时，新消息
  排队。E4 证明 CLI 能接，以后要放开，先单独设计它和 injected sink 的交互。
- **等待 AskUserQuestion / 权限时 steer。** 这时用户该回答问题。E3 证明 CLI 不会把这时写入的
  消息当成回答，以后想放开不需要改 reader；拒绝权限的路径要先补测。
- **合并多条。** steer 每条单独写，不再像 drain 那样合并。
- **用户选择 steer 还是排队。** 先默认 steer；如果实际使用中有人需要"这轮完了再说"，再加。

## 8. 实施顺序

| 阶段 | 内容 | 上线条件 |
|---|---|---|
| P0 | ~~skeptic 审查；E1–E6 实验；§5 代码确认~~ 已完成（2026-10-25，另加 E7–E9），结论在 §5，已改 D1–D8 | — |
| P0b | ~~E10、E11；复审 v1.5~~ 已完成，转成 v1.6。剩下：E12；无上下文 reviewer 只审 v1.6 改动（重点：普通发送的接管模式、`_published_turn` 里的接管时机、接管任务的收尾）；然后把 T1–T9、T12a–T16、T18–T26 写成失败的测试 | 复审没有新的 HIGH |
| P1 | 后端 D1–D6、D8，开关默认关；P1a（D2 行状态，含 `echoed`）不依赖 D6，可以先做 | T1–T9、T12a–T16、T18–T26 绿；开关关时行为与现在逐字节一致（现有排队测试全绿）；实现完后再请一个无上下文的 skeptic 审一次 diff |
| P2 | 前端 D7；打开开关 | T10、T17 绿；T11 冒烟通过；按 AGENT R16 单独部署、发一条 steer、切 tab 后重载确认顺序 |
| P3 | 观察一周后删开关 | 日志里 `steer_folded` / `steer_fell_back` / `steer_not_consumed` 计数无异常 |

## 9. 已拍板（XG，2026-10-24）

1. 忙时默认 steer，不给用户选择（§7 最后一条）。
2. `now` 本次不做，单独设计（§7）。
3. P1 走 native，在自己的 worktree 里做：先写失败的测试，每个修复做变异检查（回退修复，测试
   变红）；改动跨 reader、router、writer，实现完后的 skeptic 审查不能省（§8 P1）。
4. 另加：上线前把 steer 的 user 行状态机写成测试（T14）。排队消息排错位置的 bug（§10）根源就是
   user 行何时、以什么状态进入记录没有测试覆盖。
5. （2026-10-25）第三轮审查后定 B+C：回退成新 turn 的 steer 接管成一次普通发送，不再另造 turn 主人；
   injected turn 里不 steer（§5 第三轮、D6、§7）。

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

- 2026-10-25 v1.6：E10、E11 实测；第三轮 skeptic（审 v1.5）3 HIGH / 5 MEDIUM / 6 LOW。按 XG 定的 B+C：D6 改成
  在 `_published_turn` 的 `finally` 里把 steer 接管成一次普通发送（`run_conversation(_adopted_steer=)`，像 drain
  一样），删掉 v1.5 的 steer 驱动方；D1 不在 injected turn 里 steer；`notif_mark` 回到默认值；D2 加"已回显、待
  接管"状态；D3 `target_closed` 覆盖所有结束路径；D4 `_turn_row_key` 的取值和读者表；RSS 检查在不 quiescent
  时跳过。加 E12（待做）和 T24–T26，改 T7、T12b、T15、T20、T22。
- 2026-10-25 v1.5：第二轮 skeptic（审 v1.2–v1.4）19 条，按成因分 G1–G7 并入（§5）。主要变化：D4 新字段
  `_turn_row_key`，不再改 `_turn_client_id`；D6 改成原活动结束就接手 steer 的 turn，`is_busy` /
  `has_background_work` 不再放宽；D3 steer 永不 `_merged`，丢弃规则要求 running 之后的 idle；D2 回显时
  确认 user 行并推 live feed、启动恢复顺序、消息组装补附件转换和安全扫描面板；api_retry 跳过后不 raise。
  加 E10、E11（待做）和 T12b、T19–T23，改 T4、T13、T15。
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
