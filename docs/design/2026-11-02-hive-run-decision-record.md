---
title: Hive run 的决策记录 — 每个决定都留痕，大部分不需要人
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r1 design only（无代码）
tags: [swarm-out, hive, accountability, traceability, autonomy]
related:
  - docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md（agent 对自己派的单下命令，命令带理由）
  - docs/design/2026-11-01-hive-approvals-impl.md（agent 代答等待的记录 H8）
  - docs/design/2026-11-01-brain-to-hive-zero-touch.md（派出去的会话上归人的动作当场拒绝）
  - docs/design/2026-10-31-swarm-run.md（lead run 对子单的处理，§5.8、§9、§10）
  - docs/design/2026-10-31-swarm-out-run-review.md（大卡片、命令面板）
---

# Hive run 的决策记录

## 0. 原则

**XG 2026-11-02：** "你可以把你对每个 Hive run 的决定理由都记下来：你什么时候审过了、最终的决定是啥；如果需要人介入，写明为什么、需要人
哪些方面做决定。决策也 traceable。这样每个 Hive run 的卡上都能看到 Hive run 的情况、发起人或 agent 的决定、人的决定；只不过大部分不需要人的决定。"

所以：

- **每个决定一条记录**，不管是谁做的（派单的 agent、人，还是产品规则），都写在同一处、同一种格式。
- **放手的前提是能问责。** 人不在执行环里，但随时能在卡上看到：谁、什么时候、根据什么、定了什么、为什么。
- **要人决定的，写明要人定什么、为什么 agent 定不了。** 人看到的是一个具体的问题，不是"有事找你"。

## 1. 现状（对照 2026-11-02 的 main）

| 事实 | 出处 |
|---|---|
| agent 的每个命令（通过、修正重跑、停止、丢弃、收回、落地）在这一单的 settle 记录 `commands` 里记一行：`by: agent`、会话、命令、理由、时间 | `core/swarm_out_actor.py:136-148` `record` |
| 命令必须带理由：CLI 的 `--why` 是必填（stop / discard / accept / rerun / return-* / answer / escalate），后端在 `check` 里拒绝没有理由的 agent 命令 | `scripts/swarm_out.py:220,224,235`；`core/swarm_out_actor.py` 文件头 |
| agent 代答的等待也记在 `commands`：等待的类别、问了什么、选了什么、理由 | `core/swarm_out_actor.py:154-171` `record_wait_answer` |
| 大卡片只读回代答的等待（`host_answers`），在"自主"页列出；agent 的其它命令和理由**没有地方显示**（`swarm_out_actor` 文件头说"面板显示 accepted by the agent: <reason>"，前端没有这段，是失实的注释） | `core/swarm_out.py:1905-1912`；`desktop/src/components/swarm-out/RunOverlay.tsx:77-104` |
| 人的命令不进 `commands`：通过写在 `verdict`（只有 `pass` 一个值，加备注和时间，没写是谁），丢弃、落地写在各自的状态里 | `core/swarm_out_settle.py:160-163`；`core/swarm_out_actor.py:137-138` 的注释 |
| 派单时的执行方式和理由（`execution_why`）写在派工单上，派发记录里带着这一单用的 Project Agent | `core/task_spec.py:237-242`；`core/swarm_out.py:321-334` |
| agent 把等待交给人（`escalate`）另存一个文件：会话、理由、时间；Needs You 显示理由 | `core/swarm_out_wait_card.py:395-425`；`core/attention_authority.py:714-770` |
| 人回答 Hive 的等待：桥的回答文件带 `answered_by: person`，Hive 记录里带着 | `core/swarm_out_bridge.py:292-294`；`core/hive_records.py:369,707` |
| `commands` 只留最近 50 条，更早的丢掉 | `core/swarm_out_actor.py:56-57` |

**缺的：** ① 卡上没有一条完整的时间线，决定散在派工单、`commands`、`verdict`、escalation 文件和 Hive 记录五处；② 理由只是 agent 写的一句话，
没有记下它做决定时**看到的是什么**；③ 交给人的只有一句理由，没写要人定什么、为什么 agent 定不了；④ 人的决定没有"是谁、为什么"的一行；
⑤ 记录满 50 条丢最早的（AGENT R23：记录不截断）。

## 2. 设计

### 2.1 一条记录的格式

每一单一份决策记录，就是 settle 记录里的 `commands`（不另建存储，字段名不改，旧记录照读）。每一条：

| 字段 | 内容 | 谁写 |
|---|---|---|
| `at` | 时间 | 后端 |
| `by` | `agent` / `person` / `product`（产品规则做的决定，比如当场拒绝、会话不在了交给人） | 后端 |
| `session` | agent 的会话（`by: agent` 时） | 后端 |
| `decision` | 决定是什么：`dispatch` `accept` `rerun` `stop` `discard` `return_land` `answer_wait` `escalate` `refused`（当场拒绝）`handed_over`（交给人） | 后端，按命令 |
| `saw` | 做决定时后端看到的事实：结果的 `ended_at`、机器核对的结果（检查格各项过没过）、交回了什么（提交数和 tip、文件数）、这一单已经重发几次、等待的类别和原文位置 | **后端**，取当时的快照，不由 agent 填 |
| `checked` | agent 说它看了什么：读了哪些报告和文件、跑了哪些测试、有没有派专家审、审出什么（几行） | agent（`--checked`） |
| `why` | 理由，一句话 | agent（`--why`，已有）或人（面板里写的备注） |
| `for_person` | 只在交给人时有：要人定什么（问题和选项，就是等待卡上的那几项）、为什么 agent 定不了（`intent`：问的是人的意图 / `session_gone`：派单会话不在了 / `out_of_authority`：需要的是人才能批的动作）、agent 的建议（可选） | agent 或后端 |

**`saw` 是这份设计的关键。** agent 写的 `checked` 和 `why` 是它的说法，事后可能和事实对不上；`saw` 是后端在同一时刻记下的事实。两者放在一起，
人复盘时一眼就看得出 agent 是"看了结果和测试才通过"，还是"检查格有两项没过就通过了"。不用模型去评判 agent 的决定。

### 2.2 谁在什么时候写

每条记录都由**执行这个决定的那段代码**写，在决定生效的同一个锁里，不靠 agent 记得去写：

- agent 的命令：`swarm_out_actor.record` 扩成写上面的格式；`saw` 从这一单当时的 `/runs` 行和结果记录取。
- 派发：`dispatch` 成功时写一条 `dispatch`，`why` 用派工单的 `execution_why`，`saw` 写选了哪台 Hive、哪个 Brain 版本。
- 代答：`record_wait_answer` 照旧，补上 `saw`（等待的类别、Hive 给的选项）。
- 交给人：`escalate` 写一条 `escalate`（带 `for_person`）；后端发现派单会话不在了，写一条 `by: product` 的 `handed_over`。
- 人的决定：面板的通过、修正重跑、停止、丢弃、回答等待，也写一条（`by: person`，`why` 是人写的备注，没写就空着）。`verdict` 等原有字段照旧，
  不改读它们的地方。
- 当场拒绝：派出去的会话上 Hive 拒掉的动作（zero-touch），如果 Hive 记录里交回了，Host 收回时写成 `by: product` 的 `refused`。**待查：** Hive 上的
  拒绝今天是否随 Hive 记录交回（`security_hooks._log_prompt` 记在 Hive 本地）；没有的话，P1 补上交回。

**必填：** agent 的 `accept`、`rerun`、`discard`、`return_land` 不带 `--checked` 就拒绝（和今天不带 `--why` 一样）。这是确定性检查，
只查有没有写，不查写得好不好；写得好不好由人复盘和 `saw` 对照来看。

**不截断：** 去掉 `MAX_COMMANDS = 50`。多数决定有自己的上限（agent 重发最多 2 次，pipeline 决定 `decision_cap` 默认 2）；代答 Hive 本地审批和问题没有上限，但一单里的条数随它自己的生命结束，每条是几百字节的 JSON，留全不会成为负担。

### 2.3 卡上怎么看

- 大卡片的"自主"页改成**"决定"页**：一条时间线，从派发到了结，每条一行："时间 · 谁 · 定了什么 · 理由"；展开看 `saw` 和 `checked`。
  现在"自主"页的内容（Autonomy Judge 五维、代答列表）放在时间线上方，代答本身就是时间线里的 `answer_wait` 条目，不再单独列一遍。
- 交给人的条目用琥珀色（`tone.ts` 的规矩：琥珀色只表示需要人），并把 `for_person` 写全：要你定什么、为什么 agent 定不了、它建议什么。
- 列表行不变：仍然只显示"全自主 / 介入 N 次"，N 数的是 `by: person` 的决定。
- Needs You 的条目点进去，直接落在这一单"决定"页的那一条上。

### 2.4 和 Swarm Run 的关系

lead run 对子单的处理就是子单决策记录里的 `by: agent` 条目（lead run 是派它的 agent），不在 lead run 那边另记 `child_action` 事件。lead run 的
REPORT、交付总结和 Swarm Run 卡按 `parent_run` 读子单的决策记录。一个事实只有一个来源，子单在 Swarm Out 和 Swarm Run 卡上看到的是同一份。
Swarm Run 设计下一版跟着改（§9、§10 去掉 `child_action`）。

## 3. 不做

- 不用模型评判 agent 的决定，也不因为理由写得不好而拦命令。
- 不另建决策存储，不改 `verdict` 等现有字段的含义。
- 不在列表行上显示 agent 的决定数。列表回答的是"人插手了几次"。

## 4. 验证计划

**谁读：** 大卡片的"决定"页、Needs You 的跳转、Swarm Run 的 REPORT（读者先于写入方设计好：§2.3、§2.4）。

| # | 不变量 |
|---|---|
| D1 | 一单从派发到了结的每个决定，在 `commands` 里各有一条，按时间排序；人和 agent 的都在 |
| D2 | `saw` 由后端在决定生效的同一个锁里写，内容等于那一刻的 `/runs` 行和结果；agent 的请求里带 `saw` 会被忽略 |
| D3 | agent 的 accept / rerun / discard / return_land 不带 `--checked` 被拒绝，什么都不发生 |
| D4 | 交给人的每一条都有 `for_person`，三项原因之一；Needs You 的条目跳到这一条 |
| D5 | 一单超过 50 条决定时，最早的还在 |
| D6 | 旧记录（没有 `saw`、没有 `decision` 字段）照常显示，按命令名推出 `decision` |
| D7 | 列表行的"介入 N 次"只数 `by: person`，和今天的口径一样（`runReview.ts` 的测试照旧绿） |

**审查：** api-contract（`commands` 的新字段，新旧 Host 和前端互通）+ ux（"决定"页，先读 `s_frontend-design` 的 `design-judgment.md`，先出 mock）。最多两轮。

## 5. 分期

- **P0：** 记录格式和所有写入点（§2.1、§2.2），`--checked` 必填，去掉 50 条上限；修正 `swarm_out_actor` 文件头里失实的那句注释。
- **P1：** 大卡片"决定"页和 Needs You 跳转（先出 mock）；查清 Hive 上的当场拒绝是否交回，没有就补。
- **P2：** Swarm Run 按 §2.4 读子单的决策记录。

## 6. 修订记录

- r1（2026-11-02）：按 XG 的思路起草。
