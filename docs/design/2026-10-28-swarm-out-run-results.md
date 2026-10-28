---
title: Swarm Out 从 Hive 取回每个 run 的结果
created: 2026-10-28
updated: 2026-10-28
project: SwarmAI
status: r3 — 补上非 pipeline 的 run（native coding、非编码任务）各自能看到什么，待实现
---

# Swarm Out 从 Hive 取回每个 run 的结果

**r3（XG 2026-10-28）：** Hive 上跑的不一定是 pipeline，也可能是 native coding 或者不写代码的任务，这几类都要能看出来做了什么、做得怎样。r2 只对 pipeline 有五维审计，其余只有结局和回复。r3 加了 4.3"这个 run 做了什么"：每个 run 不分类型都列出同一组事实，pipeline 额外带它自己的五维结论。面板按这组事实显示（4.5）。

**r2：** r1 经一次前提审查（correctness），10 条意见按原因归成四组，改成 r2：
- **记录结局的位置改到 `_published_turn`（4.1）。** r1 让 `TurnWriter.close` 写，但 `close` 对没有开始的 turn 也会运行（`session_router.py:4044` 的 `finally`），被拒的请求会多出一行。意外异常也不经过 `TurnWriter.write`，崩溃会被记成"没有终止事件"。`_published_turn` 知道 turn 是否开始，看得到每个事件，也能接住异常。
- **花费按进程累计值求差（4.1、4.2）。** `result` 的 `total_cost_usd` 是 CLI 进程从启动以来的累计值，不是这一轮的（`sqlite.py:4521` 的 `token_cost_rows` 说明，`session_quality._turn_costs` 就是这样求差的）。r1 直接相加会重复计算。
- **结局的取值补全（4.1、4.2）：** 轮数上限是一个 subtype 为 `turn_limit_reached` 的 `result`（`streaming_orchestrator.py:2739`），不是单独的结局。停在问题或审批上的 turn 记 `paused`。被 Stop 的优先于其他记录。最后一条用户消息之后还没有结局的，结果标为"暂定"。
- **读取改为分页（4.2）：** 只读最近 20 个用户 turn（接口的 `turns` 参数，每个 turn 整个返回），更早的标出没读。
- 删掉 r1 里"`_post_only` 的事件不算"这条：这些事件根本不经过写入，规则无从生效。考虑过直接扩展 `turn_notice`，没有采用，原因见第 3 节。

## 1. 问题

Swarm Out 面板现在能说一个 run 在等人、在跑还是"不在跑了"（`backend/core/swarm_out.py` 的 `run_state`），说不出它是做完了、失败了还是被停了，也拿不到它最后写了什么、产出了哪些文件、花了多少。面板上 run 卡片的结果、五维审计和历史里的"结论"都写着 TBD（设计 `2026-10-28-swarm-out-live-dashboard.md` §3、§6）。

要回答的是：一个派出去的任务结束后，用户在本机面板上能看到"它怎么结束的、交了什么、值不值得去 Hive 上细看"，而且 Hive 关机或删掉之后这些还在。

## 2. Hive 上现在记了什么（本轮读的代码）

派发时本机读完 session id 就断开 SSE（`swarm_out.py` 的 `_start_session`），Hive 在没有客户端的情况下继续跑完这个 turn。所以 turn 的结束事件只发给了一个已经不在的连接，**能取回的只有 Hive 数据库里留下的东西**。

| 事实 | Hive 上有没有持久记录 | 依据 |
|---|---|---|
| 最后的回复文字、工具调用和每步结果（压缩为状态+结果） | 有，`messages` 表 | `session_router._persist_stream_event`（`session_router.py:2452`），由 `TurnWriter` 唯一写入（`turn_writer.py` 模块说明） |
| 这一轮产出的文件 | 有，`turn_deliverables` 块（最多 12 个，超出的写 `omitted`） | `session_router.py:53` `_deliverables_block` |
| 这一轮改的代码 | 有，`turn_changes` 块 | `turn_changes.py:635` |
| 用户点了 Stop | 有，`turn_stop` 块 | `turn_stop.py` 模块说明 |
| AI 进程中途重启、被新消息取代、重试用尽 | 有，`turn_notice` 块，`reason` 为 `restarted` / `superseded` / `gave_up` | `turn_notice.py:22` |
| pipeline run 的结局和五维审计 | 有，run 结束时写一个 `pipeline_summary` 块，带 `status`、`landing`、`landedSha`、`audit`（五行） | `pipeline_stage_events.py:447` `summary_block` |
| **turn 正常结束（`result`）：耗时、花费、轮数、token** | **没有。** `result` 是 SSE 事件，不写进 `messages`。token 写进 `token_usage` 表，但 Hive 没有按会话读它的接口 | `streaming_orchestrator.py:3209` 起 yield `result`，token 由 `record_token_usage` 写 |
| **turn 因错误结束（`error` 事件，如模型调用失败）** | **没有。** 错误只发给 SSE 客户端。`_RESULT_FLUSH_EVENTS` 只是在 `error` 时把手里的块写掉，错误本身不落盘 | `session_router.py:2395` |
| **到了轮数上限** | **没有。** 它先 yield 一个 `turn_limit_reached` 事件，再 yield 一个 subtype 为 `turn_limit_reached` 的 `result`，两个都只是 SSE 事件 | `streaming_orchestrator.py:2705`、`:2739` |
| **turn 中途抛出意外异常** | **没有。** 异常从 `_run_conversation_impl` 抛出（`session_router.py:5458`），错误帧由 `routers/chat.py` 的 SSE 包装层构造（`chat.py:223` 起），不经过 turn 的写入 | 前提审查指出 |

结论：从 Hive 现有的数据里能拿到"写了什么、交了什么、是不是被停、pipeline 的审计"，**拿不到"正常结束还是出错结束"**，也拿不到耗时和花费。对一个无人值守的 run，出错结束是最需要知道的结局，而它恰好没有任何记录。这个缺口对 Hive 自己的聊天界面也存在：一个出错结束的 turn，重新打开后看不出它出过错。

## 3. 方案

| | 做法 | 改动量 | 风险 | 取舍 |
|---|---|---|---|---|
| A | 只在本机做：读 Hive 的 `/api/chat/sessions/{id}/messages`，从现有的块里归纳结果 | 小，Hive 不用更新 | 低 | 分不出正常结束和出错结束，没有耗时和花费。面板上最关键的那一格只能写"不知道" |
| **B（推荐）** | Hive 在每个 turn 结束时写一个 `turn_outcome` 块（怎么结束的、错误码、耗时、花费、轮数）。本机读同一个 messages 接口，归纳成一份结果存在本机 | 中：Hive 一处写（`_published_turn`，经 `TurnWriter` 落盘），本机一个读取模块 | 中：新的持久块要让聊天界面和续接历史都跳过它 | 补上唯一缺的事实。写在 turn 唯一的写入点，所有 turn 都有，Hive 自己的界面以后也能用 |
| C | Hive 的 bridge 在 turn 结束时把结果推到 S3（`results/<session>.json`），本机像读问题一样读 S3 | 大：bridge、IAM 策略、Hive 上再写一份归纳 | 中 | 多一份副本和一条权限。好处是 Hive 不可达时也能读到，但本机本来就把结果存下来了，这个好处不大 |
| D | 不加新块，扩展 `turn_notice`（加 `reason` 为 `completed` / `error`） | 小 | 高 | 聊天界面会显示 `turn_notice`（`turnView.ts:536` 把它读成 turn 上的提示），每个正常结束的 turn 都会多一条提示。它的含义是"最后一个调用为什么没做完"，放进"做完了"会让含义变混 |

选 B。理由：缺的只有"turn 怎么结束的"这一个事实，它该在产生的地方记一次（R3 第 8 条：同一个 turn 由它的产生者认定）。四种 turn（`run_conversation`、`continue_with_answer`、`continue_with_cmd_permission`、`run_injected_turn`）都经过 `_published_turn`（调用处 `session_router.py:3564`、`:4112`、`:5765`、`:5832`），它看得到 turn 发布的每个事件，知道 turn 是否开始（`started`），它的 `finally` 在 turn 怎么结束都会运行。`turn_stop` 和 `turn_notice` 已经是同一类"turn 怎么结束"的展示块，`turn_outcome` 和它们同族。本机读 messages 接口，不需要 Hive 新增接口，接口自带 ETag，没变化时返回 304。

## 4. 设计

### 4.1 Hive 写：`turn_outcome` 块

**谁写：** `_published_turn`（`session_router.py:3968`），写入经由这个 turn 的 `TurnWriter`。
- 循环里每个事件交给一个小的记录器（新模块 `core/turn_outcome.py`，纯函数）。它只记三类事实：最后一个 `result`（subtype、is_error、duration_ms、total_cost_usd、num_turns、usage，以及这时 unit 的 `pid`）、最后一个 `error` 事件（code、message、terminal），以及 turn 是不是停在 `ask_user_question` / `cmd_permission_request` 上。
- 从内层生成器抛出的异常，在 `_published_turn` 里接住类型名（不留异常信息，只留类型名），然后照旧抛出。
- `finally` 里，**只有 `started` 为真时**，在 `writer.close()` 之前调用新方法 `writer.record_outcome(block)`：追加到 turn 最新的一行（`append_to_turn_row`，和步骤结果同一种写法），没有行时自成一行。写入时设置 `TRANSCRIPT_FEED_SUPPRESSED`，和 `_write_held` 一样。没开始的 turn（排队超时、会话忙、空消息）不写。
- 块写入失败只记日志，不影响 turn 结束。

**块的形状：**

```
{ type: "turn_outcome",
  ended: "completed" | "turn_limit" | "error" | "crashed" | "paused" | "none",
  subtype: str | null,        # result 的 subtype，≤32 个字符
  code: str | null,           # error 事件的 code，≤64
  message: str | null,        # error 事件的 message，≤300（已经过 _build_error_event 的清理）
  exception: str | null,      # crashed 时的异常类型名，≤64
  duration_ms: int | null, num_turns: int | null,
  cli_cost_total: float | null,  # result 的 total_cost_usd：CLI 进程从启动以来的累计值
  cli_pid: int | null,           # 见到 result 时 unit 的 pid，用来判断是不是同一个进程
  usage: {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens} | null,
  at: ISO 时间 }
```

`ended` 的判定，按顺序：
1. 有异常 → `crashed`。
2. 最后一个终止事件是 `error`，或 `result` 的 `is_error` 为真且 subtype 不是 `turn_limit_reached` → `error`。subtype 为 `output_degenerated`（`streaming_orchestrator.py:1369`）也算 `error`，subtype 照写。
3. `result` 的 subtype 是 `turn_limit_reached` → `turn_limit`。
4. 其他 `result` → `completed`。
5. 没有终止事件，最后停在问题或审批上 → `paused`。这个问题回答后，续接的 turn 会写它自己的结局。
6. 都没有 → `none`。被 Stop 的 turn 另有 `turn_stop` 块，进程被重启的另有 `turn_notice` 块，读的一方把它们合起来，这里不猜。

进程被 SIGKILL 时 `finally` 不运行，没有这个块，读的一方写"没有结束记录"。

**谁读，这次一并改：**
- `context_injector.py:47` 的展示块列表加上 `turn_outcome`：它不进续接历史，模型看不到。
- 前端 `turnView.ts:303` 的 `DISPLAY_TYPES` 加上它，`types/index.ts` 加类型：聊天界面这次不显示它，也不把它当成 turn 的内容。
- 本机的结果归纳（4.2）。

本机自己的会话也会写这个块，每个 turn 多三百字节左右。读消息块的其他地方（例如 `session_quality._final_text`）只取文本块，碰不到它。这次不在聊天里显示它。"出错结束的 turn 重新打开后显示出错原因"是它以后能支持的改进，不在这次的范围里。

### 4.2 本机读：取回、归纳、存下

**新模块 `backend/core/swarm_out_results.py`：**

- `summarize(messages) -> dict`：纯函数，输入 messages 接口返回的列表，输出一份结果：

| 字段 | 来源 |
|---|---|
| `ended` | 看最后一条用户消息之后的部分。有 `turn_stop` 写 `stopped`（优先于其他记录）。否则用其中最后一个 `turn_outcome` 的 `ended`。它是 `none` 或没有这个块时，有 `turn_notice` 写它的 `reason`，都没有写 `no_record`。取值 `completed` / `turn_limit` / `error` / `crashed` / `paused` / `stopped` / `restarted` / `superseded` / `gave_up` / `no_record` |
| `provisional` | 最后一条用户消息之后还没有任何结局记录，或结局是 `paused`：结果可能还会变，界面写"暂定"，不写"做完" |
| `error` | `{code, message}`，只在 `error` 时有。`/runs` 里只给 `code` |
| `outcome_recorded` | 最后一个 turn 有没有 `turn_outcome`（旧版 Hive 没有，界面写"这台 Hive 的版本不记录结束方式"） |
| `ended_at` | `turn_outcome.at`，没有时用最后一行的 `created_at` |
| `turns` | 用户消息的条数 |
| `active_s`、`tokens` | 各 turn 的 `duration_ms`、`usage` 相加 |
| `cost_usd` | 按 `turn_outcome` 的顺序求每个 turn 的花费：`cli_pid` 和上一条相同且 `cli_cost_total` 不比上一条小时，取差值，否则（换了进程，或数值变小）取它本身。规则和 `session_quality._turn_costs` 相同，这次把它抽成两边共用的一个函数 |
| `cost_partial` | 有 turn 缺记录或缺花费（例如合成的 result 不带花费），或有更早的 turn 没读到。这时花费仍给出已知部分，界面写"至少 $N"，不把缺的当成 0 |
| `report` | 最后一个 turn 最后一个助手文本块，前 4000 字。超出时 `report_chars` 给全长，界面写"全文 N 字，去 Hive 页面看"，不悄悄截掉 |
| `deliverables` | 所有 turn 的 `turn_deliverables` 的 `path`，去重，最多 50 条，超出写 `deliverables_omitted`（再加上各块自己的 `omitted`） |
| `changes` | 所有 turn 的 `turn_changes` 合计改了几个文件、增删多少行，以及有没有落地 |
| `pipeline` | 最后一个 `pipeline_summary` 块：`runId`、`status`、`landing`、`landedSha`、`audit`（五行 `{dim, verdict, detail}`）。不是 pipeline run 时为 null，界面写"不是 pipeline run，没有五维审计"，不写 TBD |

所有取自 Hive 的值都先校验类型和长度（和 `_clean_ask` 一样），不合规的字段丢掉并计数。

- `fetch(inst, row)`：用已有的 `_client(inst)` 请求 `/api/chat/sessions/{session_id}/messages?turns=20`（`session_id` 读出来时再用 `KEY_RE` 校验一次），带上次的 ETag。接口按用户 turn 分页，每个 turn 整个返回（`chat.py:1110` 起的 `page_by_turns`）。响应头 `X-Has-More: 1` 时结果标 `older_turns_not_read`，花费标为部分。200 时归纳并存下，304 时只更新检查时间，404 时记"Hive 上已经没有这个会话"。超时用 `HTTP_TIMEOUT_S`，只防挂住。

**存在哪：** `state/swarm-out/results/<dispatch_id>.json`，每个 run 一个文件，原子写（临时文件 + rename）：

```
{ dispatch_id, hive_id, session_id, etag, transcript_rev,
  fetched_at, checked_at, fetch_error: str | null, result: {...} | null }
```

为什么要存：Hive 可能被关机或删掉，那之后本机这份就是唯一的副本。所以这些文件**从不自动删除**，和派发记录 `dispatches.jsonl` 一样保留。

**什么时候取：**
- `list_runs` 算完每行的 `state` 之后，对**运行中的 Hive 上、状态是 `stopped`（原因 `idle` 或 `not_held`）、24 小时内派出**的 run 发起一次带 ETag 的检查。这 24 小时就是面板运行视图本来的时间窗。没变化时是 304，开销是每个 run 一个小请求。
- 检查在后台跑，不拖慢面板的轮询：同一个 run 同时只有一个检查（按 `dispatch_id` 的单飞集合），全局同时最多 2 个，任务的引用存在模块的集合里，完成时移除。这一次轮询返回的是已存的结果，下一次轮询（30 秒后）看到新的。
- 更早的 run 只在用户要时检查：新接口 `POST /api/swarm-out/runs/{dispatch_id}/refresh`，面板的 run 卡片上有一个"刷新结果"。
- 在跑和在等人的 run 不取结果，卡片照旧写在跑或等你。用户在 Hive 页面上接着聊，run 会变回在跑，再停下时 ETag 变了，结果自然更新。
- `idle` 和 `not_held` 都不等于做完：`not_held` 只说明 Hive 内存里没有这个会话（被驱逐，或消息还在排队），`idle` 也可能出现在多轮 run 的两轮之间。所以结局由取回的记录决定，不由状态决定，记录不全时就是 `provisional`。

### 4.3 这个 run 做了什么：每类 run 都有的同一组事实

Hive 上的 run 可能是 pipeline、native coding（直接改代码、跑测试、提交），也可能根本不写代码（写文档、做 deck、调研、回答问题）。不先给 run 分类型再按类型取数据：类型是从事实里看出来的，一个 run 也可能同时改代码又写文档。所以 `summarize` 对每个 run 都输出同一组事实（`work`），读的是 Hive 已经持久化的块：

| 事实 | 来源（Hive 上已经写下的块） | 说明 |
|---|---|---|
| `code`：改了哪些仓库、几个文件、增删多少行、有没有提交或落地（commit sha） | `turn_changes`：`files`（每个文件的 `added`/`deleted`/`gitRoot`）和 `landed`（`turn_changes.py:624`、`:646`） | 只覆盖 Hive 上**绑定了项目的仓库**和 run worktree（`turn_changes.code_roots`，`turn_changes.py:183`） |
| `files`：写了哪些文件，按种类分：文档和页面（`content`）、知识（`knowledge`）、仓库外的文件（`external-diff` / `external-nodiff`） | `turn_deliverables` 的 `kind`（`turn_products.py:35` 的 `PRODUCT_FILE_KINDS`） | 在**没有绑定的仓库**里改代码，会落在"仓库外的文件"这一类，只有路径，没有行数。界面照实写"在未绑定的仓库里改了 N 个文件，看不到行数" |
| `steps`：调用了多少次工具、按工具名计数、失败了几次、后台/子 agent 几个 | `tool_use` 块的 `name`，`tool_result` 的 `is_error`、`subagent.status`（`session_router.py:2367` 起的 `_compact_tool_results`） | 只给计数，不给命令内容。"跑了哪些测试"按语言判断不了（SOUL P7），所以不猜，只给"命令失败几次" |
| `human`：Hive 上问了几次人、批准几次、拒绝几次、还有几个没回答 | `cmd_permission_request` 块（`decision` 写在块上，`permission_prompt.py:52`），`ask_user_question` 块和它记下的回答 | 这就是"自主"那一维在非 pipeline run 上能给的事实 |
| `answer`：最后的回复有多长 | 4.2 的 `report_chars` | 不写代码也不写文件的 run，结果就是这段回复 |
| `pipeline`：profile、结局、是否落地、五维结论 | `pipeline_summary` 块 | 只有 pipeline run 才有 |

由这组事实推出一行"做了什么"，例如"改了 swarmai 4 个文件 +120 −30，已提交 3f2a1c9"、"写了 2 份文档"、"只回答，没有产出文件"、"pipeline · bugfix · 已落地"。多种都有时连起来写。规则在 `summarize` 里，有单元测试，不靠模型判断。

**五维审计只给 pipeline run，其余不造结论。** pipeline 的五维结论来自它自己的确定性规则（`run_protocol/audit.py`）。native 和非编码的 run 没有这些规则的输入（没有测试报告、没有审查记录、没有合同），如果本机拿上面的事实去套出 PASS/WARN，就是在没有依据的地方下结论（SOUL P5）。所以这类 run 的卡片显示的是事实本身：用时、花费、人介入几次、命令失败几次、改了什么。"质量"一项写"这类 run 没有质量检查的记录"，不写 TBD，也不写 PASS。

**已知的看不到的部分，界面照实写：**
- 在 Hive 上没绑定的仓库里改代码，只有文件路径，没有行数和 commit。想要完整的代码改动，要在 Hive 上把那个仓库绑定到项目。
- 提交是否推送到远端、测试是否真的通过，Hive 只记了命令失败与否，没有更多。

### 4.4 接口

`/api/swarm-out/runs` 的每一行加：

```
result: {...} | null,          # 4.2 的归纳和 4.3 的 work，不含 report 正文（只有 report_chars）
result_status: "fetched" | "pending" | "failed" | "not_applicable",
result_error: str | null,      # 中文可读，如 "Hive 没有回答" / "Hive 上已经没有这个会话"
result_checked_at: str | null
```

`not_applicable`：没有 session id 的 run，或 Hive 已删除且本机从没取到过结果。

**新接口 `GET /api/swarm-out/runs/{dispatch_id}/result`：** 返回存下的整份记录，含回复正文。只有面板展开卡片时调用。`/runs` 不带正文，是因为命令行 `swarm_out.py runs` 原样打印 `/runs` 的 JSON，而本机 agent 读的是命令行（见 4.5）。

前端类型 `SwarmOutRuns`（`services/swarmOut.ts`）和 s_swarm-out skill 的 `runs` 说明同步。

### 4.5 面板

- **"不在跑了"一组改名为"结束了"，每张卡片三行，不分 run 的类型：**
  - 第一行，结局：做完 / 出错（错误码和一句原因）/ 到了轮数上限 / 被停 / 进程重启后没接上 / 没有结束记录。结局不清楚时写不清楚的原因，不猜。
  - 第二行，做了什么（4.3 那一行），例如"改了 swarmai 4 个文件 +120 −30，已提交 3f2a1c9"或"写了 2 份文档"。
  - 第三行，数字：结束时间、用时、花费、人介入几次、命令失败几次。缺记录的写"—"并说明。
- **卡片展开后**按有的事实显示，没有的那块不出现：
  - 最后的回复：纯文本，保留换行，不按 Markdown 渲染，也不做链接。
  - 改了的代码：每个仓库的文件和增删行数，提交的 sha。
  - 写了的文件：按种类分组，显示 Hive 上的路径，只显示，不在本机打开。
  - 工具调用：按工具名计数，失败的标出。
  - pipeline 的五维（用 `JudgeTag`，`shapes.ts` 里的五维定义），只有 pipeline run 有。
  - "在 Hive 页面打开"。
- **历史视图：** 每日柱从"派出数"改为按结局分色（做完 / 出错 / 被停 / 不清楚）。五维汇总、按类型、工蜂占用仍然是 TBD。
- 设计要求：动手前读 `s_frontend-design` 的 `data/design-judgment.md`。卡片回答"要不要去 Hive 上看这个 run"，结局和出错原因占最显眼的位置，其余收起。

### 4.6 安全

- Hive 写的所有文字（回复、错误信息、文件路径）都是数据，不是指令。面板以纯文本显示。React 默认转义，不用 `dangerouslySetInnerHTML`，不渲染链接。
- 本机 agent 的入口：命令行 `swarm_out.py runs` 原样打印 `/runs`，所以 `/runs` 只带枚举字段（结局、错误码、数字、文件数和路径、工具名的计数，不带命令和工具参数），**不带回复正文**。正文走单独的 `/runs/{dispatch_id}/result`，命令行不调它，只给人在面板上看。这样 Hive 上一个 run 读到的网页内容，不会经回复变成本机 agent 读到的文字（B 类规则：读入污染再外传的链条）。错误信息 `message` 也是 Hive 写的，所以在 `/runs` 里只给 `code`，`message` 也放进 `/result`。
- 已有的暴露，这次不改：`/runs` 里的问题卡片本来就带 Hive 写的问题文字，命令行也会打印。
- 请求只发往已校验过的 Hive 地址（`_validate_instance`），路径里的 session id 再校验一次。
- 存下的文件在本机的状态目录里，不含 Hive 的凭证。

## 5. 版本兼容

- 旧版 Hive（hive2 及以前）不写 `turn_outcome`：结局写"这台 Hive 的版本不记录结束方式"，其他字段照常（回复、文件、被停、pipeline 审计都能拿到），花费为空。Hive 更新后，新跑的 turn 就有记录。
- `turn_outcome` 是新增的块类型。旧前端读到它时：`DISPLAY_TYPES` 里没有它，会被当成 turn 内容中一个不认识的块。所以前端和后端在同一次发布里一起改，本机 build 时两边同时更新。Hive 的前端和后端在同一个包里，也一起更新。

## 6. 不做

- 不把 Hive 上的交付文件下载到本机，只列路径。
- 不在聊天界面显示 `turn_outcome`。
- 不做跨 run 的五维汇总、按类型统计、工蜂占用。
- 不改回答、拒收、截断、内容哈希的逻辑。
- 不改派发：派发仍然拿到 session id 就断开。

## 7. 验证计划

| 写什么 | 谁读 | 测试 |
|---|---|---|
| `turn_outcome` 块（Hive） | 结果归纳、续接历史（必须跳过）、聊天界面（必须跳过） | 后端：`_published_turn` 在下面每种情况下写一个块，字段正确：result 正常结束、error 事件、subtype 为 `turn_limit_reached` 的 result、`output_degenerated`、内层抛出异常（块里只有类型名，异常照旧抛出）、停在问题上（`paused`）、Stop（`none`）。没有开始的 turn（排队超时、会话忙）不写任何行。长的错误信息被截到上限。块写入失败不影响 turn 结束。`context_injector` 产出的续接历史里没有这个块 |
| 前端跳过 | 聊天界面 | 前端：带 `turn_outcome` 的 turn，`deriveTurnView` 的分段、完成判定和结局跟没有这个块时一样 |
| `summarize` | `/runs` 的 `result` | 后端：每种结局一条（completed、error、crashed、turn_limit、paused、stopped、restarted、gave_up、旧版 Hive 的 no_record）。多 turn 时取最后一条用户消息之后的结局，Stop 优先。最后一条用户消息之后没有结局时 `provisional`。花费：同一进程三个 turn 的累计值求差，换进程（pid 变或数值变小）后重新起算，缺记录时 `cost_partial` 且给已知部分。`X-Has-More: 1` 时 `older_turns_not_read`。回复超过 4000 字时有 `report_chars`。交付文件去重并计 `omitted`。不合规的字段丢掉并计数。pipeline run 带五行审计，非 pipeline 为 null |
| `work`（4.3） | `/runs`、面板 | 后端：四种 run 各一条——pipeline（带五维）、native coding（`turn_changes` 有文件和 landed sha）、在未绑定仓库里改代码（只有 `external-diff` 文件）、非编码（只有 `content` 文件，或只有回复）。同时改代码又写文档的 run 两样都列出。`steps` 的失败数来自 `is_error`。`human` 的批准、拒绝、未回答各自计数。非 pipeline run 没有五维结论字段。`/runs` 里没有命令内容和工具参数 |
| 取回和存储 | 面板、刷新接口 | 后端：200 存下，304 只更新检查时间，404 记会话已不在，超时记 Hive 没回答且保留旧结果。同一个 run 同时只有一个检查。超出 24 小时的 run 不自动检查。原子写：写到一半失败不留下坏文件。没有 session id 的 run 是 `not_applicable` |
| 命令行 | 本机 agent | 后端：`/runs` 的每行 `result` 里没有回复正文和错误信息，`/result` 里有 |
| 面板 | 用户 | 前端：每种结局的第一行文字。四种 run 的第二行文字和展开后出现的区块（非 pipeline 的不出现五维，只回答的不出现文件区块）。缺记录时写"—"和原因。回复以纯文本显示（含 `<a>`、Markdown 的回复不变成链接）。历史每日柱按结局分组 |

**每一处修改都做回退检查：** 回退后对应测试必须变红（`scripts.mutation_check`）。

**最后跑一次的不变量：** Swarm Out 后端测试、`_published_turn`、`TurnWriter`、`context_injector` 和 `session_quality` 花费的测试、`components/swarm-out` 和 `turnView` 的前端测试、`tsc`。

**真机验证：** 本机 build，更新一台 Hive。派四个任务：一个 native coding（在 Hive 上绑定的仓库里改一个文件并提交）、一个写文档、一个只回答问题、一个会出错的（例如在 Hive 上用 Stop）。看面板上每个 run 的结局、"做了什么"、数字和回复是否和 Hive 页面上一致。pipeline run 用已有的一次 run 核对五维。

**评审：** 这份设计先请一位审查者做一次前提审查。代码改完请 correctness 和 security 各审一次，面板部分请 ux 审一次。
