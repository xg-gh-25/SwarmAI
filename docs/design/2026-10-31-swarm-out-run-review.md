---
created: 2026-10-31
updated: 2026-10-31
title: "Swarm Out：几十个 run 时怎么找、怎么判断（列表 · 大卡片 · 命令面板）"
status: accepted (XG 2026-10-31，按 mockup r5 实现)
supersedes: docs/design/2026-10-30-dispatch-center.md §4–§6 的界面和命令部分（验收、了结的后端规则沿用，改动见 §4）
mockup: Knowledge/Designs/mockups/2026-10-30-swarm-out-run-review.html（r5，工作区内）
---

# Swarm Out：几十个 run 时怎么找、怎么判断

**XG 2026-10-30/31：**
- 要人 review、judge 的部分不够明显；一张 card 信息太多，要按需展开；以后几十个 run 并行，大 card 没法找。
- Details 用 overlay；Autonomy 是算出来的、有几个维度，放进 overlay 的一个 tab。
- 命令太多，要一个单独的命令面板；选项越少越好，我们做的是 autonomous，要有取舍。
- 退回和撤单是一个意思，留一个：丢弃了就不能再跑、也打不开 Hive 会话。
- 名字用标准中文 + 英文，走 i18n；需要输入的框要大。
- Brain 除了 SwarmAI 和 General 都会变（增、删），整个 Swarm Out、派单中心、skill 都不能写死。

## 1. 用户场景 → 动作

一单从派出到了结，人只在这几件事上出手：看它在干嘛、它跑偏了、它问你、它交回来了。每个状态只给这一刻能做的，**最多三个命令**：一个主命令、"打开会话"、沉底的"丢弃"。

### 1.1 五个命令（i18n 标准名，界面只用这一套）

| key | 中文 | English | 做什么 |
|---|---|---|---|
| `accept` | 通过 | Accept | 达到验收标准，这一单了结。交回提交的单子：把提交落地到本机 main（`hive_return.land`），受保护路径照旧单独确认 |
| `rerun` | 修正重跑 | Re-run with correction | 写清哪里要改，作为新消息发回同一个 Hive 会话；工作区还在；旧结论随新一轮作废 |
| `discard` | 丢弃 | Discard | 这一单不要了：在跑就先停，结局记"丢弃"，了结并删 Hive 工作区；以后不能再跑，会话打不开。可写一句为什么（可不写） |
| `stop` | 停止 | Stop | 只停当前这一步，会话和工作区都在，之后可以修正重跑 |
| `open_session` | 打开会话 | Open session | 在 Hive tab 打开这一单的会话；跑的时候要纠偏就在里面直接说 |

砍掉的：退回、撤单（合成丢弃）；补充指令（用打开会话直接说）；已手工落地（交回提交的单子合不上时，"通过"变成"我已手工合进去，通过"，即 `manual-landed`）；接着做（已了结不续，要做就新派一单）。

### 1.2 状态（人看到的）和动作表

后端按这张表给 `/runs` 的每一行下发 `stage` 和 `actions`（§3.2），前端只渲染。

| stage | 中文 / English | 主命令 | 其他 |
|---|---|---|---|
| `running` | 运行中 / Running（含派出中） | stop | open_session · discard |
| `needs_answer` | 等你回答 / Needs your answer | （回答：问题卡在大卡片里） | open_session · discard |
| `awaiting` | 待验收 / Awaiting acceptance（回答、文档、提交） | accept | rerun · open_session · discard |
| `stopped` | 已停止 / Stopped（你按了停止，或它停了没交回东西） | rerun（会话还在时） | open_session · discard |
| `accepted` | 已通过 / Accepted | — | open_session（只读） |
| `discarded` | 已丢弃 / Discarded | — | — |
| `expired` | 已过期 / Expired（30 天没动，或 Hive 没了） | — | — |

`accepted` 但文件 / 教训还没到齐时，单子不再出现在"等你"里，后台等它们到了再真正了结（§3.1）。"已退回""派出中撤单""已通过等文件"这些中间态不给人看。

## 2. 界面

### 2.1 列表（Swarm Out 仪表盘"运行"视图）

- 三个分区：**等你**（等你回答 > 待验收 > 已停止，同类按等的时长，永远在最上面）、**在跑**、**最近完成**（默认折叠）。已了结的进历史。
- 一行 = 状态点 · 标题（一行截断）· Brain · 执行方式 · Hive · 自主度一词（全自主 / 介入 N 次 / Judge PASS|WARN|BLOCK）· 要你做的事（一个动词标签）· 等了多久 · **"打开会话 ↗"常驻** · 在跑时 **"■ 停止"**。花费、token、步骤不在列表里。
- 找单子不靠搜索：分区 + 排序 + Brain 筛选 chip（chip 从行里的 Brain 生成，带数字，不写死任何 Brain）+ 大卡片里的上一单 / 下一单。

### 2.2 大卡片（overlay）

- 盖在 Swarm Out tab 上，宽约 1080px（窗口窄时铺满），关掉回到列表原位。打开时冻结"等你"的顺序，关掉再刷新；`J`/`K` 走上一单 / 下一单，判断完自动到下一个等你的。
- **左边看**：顶上一条琥珀色"验收标准 + 机器核对"（核对只标 ✓ / △，不挡命令）；下面 tab：交付物（回答 / 文档 / 提交，按交付类型注册的视图）· 最后的回复 · **自主度** · 过程 · 花费 · 教训。
- **右边做（命令面板）**：顶上"现在：状态 · 等了多久"；主命令实心；"也可以"一组；丢弃沉底、隔开、红框。每条命令 = 名字 + 一句后果 + 快捷键。光标在输入框里时单键快捷键不生效。
- 选"修正重跑…"：面板原地变成至少 6 行高的输入框（写给 Hive 上的 agent，原样发过去）+ "发送并重跑 ⌘↩" / "取消 Esc"。选"丢弃"：面板原地变成确认，写明后果，可写一句为什么（进"重复纠正率"），"确认丢弃 ⌘↩"。
- **自主度 tab**：pipeline run 列 Autonomy Judge 五维（Run Isolation、Quality 为硬约束，Autonomy 为主要目标，Speed、Token Cost 为效率；不做综合分，任一硬约束 BLOCK 整体 BLOCK），每维结论 + 数字；native run 列人工介入明细（批准、拒绝、回答问题、你在会话里说的话、修正重跑、没答过期）+ 失败步骤。标题行的那一个词点了直接切到这个 tab。

## 3. 后端：Host 是唯一权威

### 3.1 了结和清理不再靠界面刷新

现在 `swarm_out_settle.annotate` 和 release 只在 `GET /runs`（界面 30 秒一次）里跑，没人看就不了结、不放 Hive 空位。改成：
- `swarm_out.refresh_runs()`：现在 `list_runs` 的全部工作（读日志、问 Hive、attach 结果、annotate、发 release），结果存成内存快照（带时间）。
- 后台循环：daemon 起来后每 60 秒跑一次 `refresh_runs()`（只在 desktop 模式、派发日志非空时），一次只跑一个。
- `GET /runs`：快照不到 20 秒就直接返回，否则当场刷新一次。Needs You 只读快照，从不自己刷新（`attention_authority` 不持有状态）。

### 3.2 `derive` 给出 stage 和 actions

`swarm_out_settle.derive` 在现有 next / settles 之外产出 `stage`（§1.2）和 `actions`（有序列表，每项 `{key, primary, variant?}`）。规则：
- 执行方能力决定可用命令：有会话（`session_id`）才有 open_session / rerun / stop；交回提交的单子 accept 的 `variant` 是 `land`（能快进）或 `manual`（`unsealed`：提交没封进 bundle，或合不上）；回答 / 文档是 `verdict`。
- 已了结的 stage 来自 settle 记录的 outcome：passed → accepted，abandoned → discarded，gone / expired → expired。
- `stopped`：结局不是 completed（stopped / error / crashed / turn_limit / gave_up …），或停了没有结果；`rerun` 只在会话还在时给（`result_error` 不是 `NO_RESULT_ERRORS`）。
- 验收绑在被判的那次结果上（`ended_at`），新一轮自动作废，沿用。
- `verdict` 只有 `pass`（不通过就是修正重跑或丢弃，没有单独的 fail）。

### 3.3 新增 / 改动的人专用路由（routers/swarm_out.py，`run_owner_gate` 拦命令）

- `POST /runs/{id}/rerun {text, ended_at}`：会话还在、单子没了结才行；用 dispatch 时同一个办法（`POST /api/chat/stream` 带 `session_id`，读到会话名就断开，Hive 接着跑）把 text 发进同一个会话。记一次修正重跑。
- `POST /runs/{id}/stop`：转发 Hive 的 `chat/stop/{sid}`。
- `POST /runs/{id}/discard {note?}`：在跑先 stop（失败也继续，记下）；`abandoned_at` + note；立刻了结（outcome abandoned），发 release。返回 `{stopped, settled}`。
- 现有：`verdict`（只收 pass）、`manual-landed`、`return/prepare` + `return/land`（accept 的 land 变体走它们）。`abandon` 由 discard 取代，`return/reject` 在新界面里没有调用方：两个都删掉（删前 grep 确认）。

### 3.4 已了结的会话关上（swarm_out_remote）

Host 的 Hive 会话代理（`swarm_out_remote`）在转发前查这一单的 settle 记录：
- discarded / expired：所有路由都拒绝（410），Hive tab 打不开。
- accepted：只读路由放行，写路由（stream、answer-question、cmd-permission-continue、compact、refresh、stop、pending 删除）拒绝（409）。
- 前端：已丢弃 / 已过期的行没有"打开会话"；已通过的会话以只读方式打开（输入框换成一行"这一单已通过，会话只读"）。

### 3.5 过期

`annotate` 里，没了结、最后一次变化（发出、结果结束、记录最后一条，取最晚）超过 `hive_records.RETENTION_DAYS` 的单子直接了结，outcome `expired`（Hive 自己也会按 30 天清工作区，release 照常发）。第 27 天起的提醒沿用。

### 3.6 人工介入按会话记录数

`swarm_out_results.summarize` 的 `work.human` 加 `said`：第一条之后的每一条用户消息（开新一轮的和 steer 的都算）。修正重跑从 Host 发出，也落在会话记录里，自然被数到；在 Hive tab 里直接打的字同样被数到。"全自主" = approved + denied + answered_questions + said = 0。

### 3.7 Needs You 汇总（attention_authority）

新来源 `swarm_out`，读 §3.1 的快照，每类最多一条汇总：
- N 个 Hive 提问在等你 → BLOCKING；
- N 单待验收 / 已停止 → REVIEW；
- N 单 3 天内清理 → REVIEW。
点开：打开 Swarm Out tab（`dispatch.context.kind = "swarm_out"`，前端认这个 kind 就切到 Swarm Out，不往聊天里注入）。逐单处理只在 Swarm Out 里。

### 3.8 Brain 不写死

- Swarm Out、派单中心、`s_swarm-out` skill、i18n 里没有写死的 Brain 名（2026-10-31 查过）；固定的只有随产品出厂的默认 SwarmAI Brain（`project_agent_seed`）和保留名 General。
- Brain 被删：交回的文件已经会落到 General 的 `deliverables/hive/`（`hive_files.destination`）。列表行照旧显示派单时的 Brain 名，Brain 已不在时加"已删除"。Brain chip 只从行里来。

## 4. 不做什么

- 不在列表里放验收按钮：不看交付物就点通过，等于没验收。
- 不做行内展开：几十行里展开一张大卡，列表被推乱、位置丢失。
- 不做搜索、批量通过：分区 + 排序 + chip + 上一单 / 下一单够用；批量通过违背"看了才通过"。
- 不续已了结的单子；不做"已退回"状态。

## 5. 验证计划（定在这里，最后逐行报告）

**谁读新写的东西：** `stage` / `actions`：列表行、大卡片命令面板、Needs You 汇总；settle 的 `abandoned_at` / note / expired：derive、代理拦截、历史；`human.said`：自主度 tab、列表的自主度一词；快照：`GET /runs`、Needs You。

**最终测试集（不变量）：**
1. 每个 stage 的 `actions` 恰好是 §1.2 那一行（参数化，覆盖有 / 无会话、land / manual / verdict）。
2. 没人打开界面时，后台循环照样了结到期的单子并发 release；`GET /runs` 在快照新鲜时不重算。
3. discard：在跑的先 stop，再了结；stop 失败也了结；之后代理对该会话所有路由返回 410；accepted 的会话写路由 409、读路由放行。
4. rerun：发进同一个会话（请求体带 session_id），已了结或无会话时拒绝；新一轮结束后旧 verdict 作废。
5. 过期：超过 RETENTION_DAYS 的未了结单子 outcome 为 expired。
6. `human.said` 数第一条之后的用户消息（含 steer）；全自主的判断用它。
7. Needs You：快照里有提问 / 待验收时各出一条汇总，不调用 `refresh_runs`。
8. 人专用：rerun / stop / discard / verdict / manual-landed / return/land 被 `run_owner_gate` 拦；URL 编码、`..` 段也拦。
9. 前端：列表分区与排序、Brain chip 来自数据、大卡片只渲染后端 actions、修正重跑 / 丢弃的输入在面板原地展开、输入框里单键快捷键不生效、已丢弃无打开会话。

**审查：** 后端 correctness + security（代理拦截、人专用路由）+ concurrency（后台循环与 `/runs` 并发）；前端 correctness + ux。每类最多两轮。

## 6. 分步

1. 后端权威：`refresh_runs` 快照 + 后台循环；derive 的 stage / actions；expired；verdict 只收 pass。
2. 后端动作：rerun / stop / discard 路由 + 门；删掉 abandon / return/reject（确认无调用方）。
3. 代理关门（§3.4）+ `human.said`（§3.6）。
4. Needs You 汇总（§3.7）。
5. 前端：列表行、大卡片 + 命令面板 + 自主度 tab、i18n、Needs You 点开 Swarm Out；SettleBar 退场。
6. 测试集、mutation check、审查、提交落地。
