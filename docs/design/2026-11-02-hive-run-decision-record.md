---
title: Hive run 的决定 — 谁能定、怎么留痕；大部分不需要人
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r2 design only（无代码）。两个会话的设计合成一份：权限一节来自 Swarm Out E2E 会话（2026-11-02，四件真实任务后）的分工表，留痕一节来自本会话 r1；未送审
tags: [swarm-out, hive, accountability, traceability, autonomy, zero-touch]
related:
  - docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md（agent 对自己派的单下命令，命令带理由）
  - docs/design/2026-11-01-hive-approvals-impl.md（等待按类别，agent 代答，H8 记录）
  - docs/design/2026-11-01-brain-to-hive-zero-touch.md（派出去的会话上归人的动作当场拒绝）
  - docs/design/2026-10-31-swarm-run.md（lead run 对子单的处理，§5.8、§9、§10）
  - docs/design/2026-10-31-swarm-out-run-review.md（大卡片、命令面板）
---

# Hive run 的决定：谁能定、怎么留痕

## 0. 原则

**XG 2026-11-02：** "我们是 autonomous，谁发的谁负责，减少 human 注意力浪费，不要什么都往 human 上放。""你可以把你对每个 Hive run 的决定理由都记下来：
你什么时候审过了、最终的决定是啥；如果需要人介入，写明为什么、需要人哪些方面做决定。决策也 traceable。"

两件事，缺一不可：

- **谁能定（§2）：** 派单的 agent 对它派的单负责到底。产品只把三类事留给人：送出本机（push、发布、对外发消息）、不可逆的数据损失、问的是人的意图而 agent 读不出。
  其余的由 agent 定，产品用确定性检查守住边界，不靠 agent 自觉。
- **怎么留痕（§3）：** 每个决定一条记录，谁定的都一样记。能由系统观察到的事实由命令自己写，agent 只写它判断的部分（结论和理由）。人不在执行环里，但随时能在
  卡上看到每个决定是谁、什么时候、根据什么、为什么。

## 1. 现状（对照 2026-11-02 的 main）

| 事实 | 出处 |
|---|---|
| agent 能对自己派的单下：通过、修正重跑（最多 2 次）、停止、丢弃（什么都不会丢时）、收回、落地；每个命令带 `--why`，后端拒绝没有理由的 agent 命令 | `core/swarm_out_actor.py` 文件头、`check`、`discard_blocker`（247-267）；`scripts/swarm_out.py:220-235` |
| "已手动落地"（`manual_landed`）只给人：它会结单并释放 Hive 工作区，连同还没落地的提交 | `core/swarm_out_actor.py:119-122` |
| 落地把 `base..tip` 压成**一个** commit 写到本机分支，消息由 host 写；Hive 的提交留在 `refs/hive/...` | `core/hive_return.py:24-29` |
| 受保护路径：仓库自己在 `.swarmai/pipeline.toml` `[hive_return] protected` 里声明，加上产品对所有仓库都保护的 `GIT_PROTECTED`（`.gitattributes`、`.lfsconfig`、`.gitmodules`、`.husky/**`、`.githooks/**`、`.pre-commit-config.yaml`）；agent 的落地请求里的 `approve_protected` 一律忽略 | `core/hive_return.py:20-30,150-167,313-316`；`.swarmai/pipeline.toml:14-22` |
| 2026-11-02 E2E 实测：编码单改到了受保护路径，agent 不能落地，只好在本机 cherry-pick；系统不知道代码已进 main，单子卡在"等你"，只能人点"已手动落地"结单（点"允许受保护文件"会再提交一遍） | DailyActivity 2026-11-02 10:22（Swarm Out E2E 会话） |
| 派出去的会话上，归人批准的动作（危险命令里人的类别、出机器的外部工具、保留动作）当场拒绝，不产生等待；agent 只回答 `HOST_CLASSES` 的等待 | `core/security_hooks.py:2175-2191,2358-2368`；`core/approval_class.py` |
| agent 的命令和代答记在这一单的 settle 记录 `commands` 里（`by: agent`、会话、命令、理由、时间）；只留最近 50 条 | `core/swarm_out_actor.py:56-57,136-171` |
| 大卡片只读回代答（`host_answers`），agent 的其它命令和理由没有地方显示；`swarm_out_actor` 文件头说"面板显示 accepted by the agent: <reason>"，前端没有这段 | `core/swarm_out.py:1905-1912`；`desktop/src/components/swarm-out/RunOverlay.tsx:77-104` |
| 人的命令不进 `commands`：通过写在 `verdict`（只有 `pass` 一个值，加备注和时间，没写是谁） | `core/swarm_out_settle.py:69,160-163` |
| 交给人（`escalate`）另存一个文件：会话、理由、时间 | `core/swarm_out_wait_card.py:395-425` |
| agent 读 Hive 回复用 `read`，回复截在 4000 字（ToDo 1a3ac749） | `core/swarm_out_results.py:54` |
| agent 读 Hive 交回的代码：今天没有 Swarm Out 命令，只能自己 `git show refs/hive/...`，系统看不到 | `scripts/swarm_out.py` 子命令表（181-227） |
| 重启 daemon 没有确定性的门，只有 AGENT R12 的文字规则；push 由人（STEERING #5，`push` 是保留动作） | `core/security_hooks.py`（无 launchctl / prod.sh 规则）；`core/daemon_guard.py` 只防"从 daemon 子进程里重启" |
| 仓库会被当作指令读进 agent 上下文的文件 | `core/repo_guidance.py:50` `GUIDANCE_FILES = ("AGENTS.md", "CLAUDE.md", ".swarmai/review.md")` |

## 2. 谁能定

### 2.1 一张表

| 决定 | 谁 | 产品的确定性检查 |
|---|---|---|
| 派发、执行方式 | agent | 派工单和准入（已有） |
| 回答 Hive 的等待 | agent（`HOST_CLASSES`）；其余当场拒绝 | Hive 侧按类别拒收（已有） |
| 通过、修正重跑、停止、丢弃 | agent | `swarm_out_actor` 的检查（已有） |
| 落地普通路径 | agent | `return-land` 的现有检查（已有） |
| 落地仓库声明的受保护路径（构建、CI、依赖清单、守护代码等） | **agent，改** | 先用 `swarm_out diff` 看过改动（§3.2 记为事实），带理由；卡上和交付总结里标出"改了受保护路径"（§2.3） |
| 落地指令文件（§2.2） | **没有人落 Hive 的字节，改** | agent 要采纳就在自己的提交里重写；Hive 的原文永远不进这些文件 |
| 落地 `GIT_PROTECTED`（git 钩子、属性、子模块、pre-commit） | **没有人落 Hive 的字节，改** | 同上：它们会在下一次 commit 时在本机执行 Hive 写的代码，等于执行远程代码 |
| 结单（含 cherry-pick 落地、接手自己做之后） | **agent，改**（今天的 `manual_landed`） | §2.4 的核对通过才结单 |
| push、对外发布、不可逆的数据损失 | 人 | 已有的保留动作和危险命令审批 |
| 问的是人的意图，agent 读不出 | 人（agent `escalate`） | 交给人的条目写明要人定什么、为什么 agent 定不了（§3.3） |
| 派单的会话不在了 | 人 | 产品写一条交给人的记录 |

### 2.2 指令文件：Hive 的原文不落进去，agent 自己写

指令文件是 agent 会当作指令读的文件：产品知道的 `GUIDANCE_FILES`（任何目录下的 `AGENTS.md`、`CLAUDE.md`，`.swarmai/review.md`）和 `.claude/**`，
加上仓库在 `.swarmai/pipeline.toml` `[hive_return] instructions = [...]` 里自己声明的（比如 SwarmAI 声明 `backend/context/**`）。按路径判断，不靠 agent 判断。

- `return-prepare` 把这些路径单独列出来，`return-land` 对它们一律不落，不管谁调。
- agent 认为改动有用，就读 `swarm_out diff`，在本机用自己的话写一遍，作为自己的提交；在这一单的决策记录里记一条 `reauthored`（哪些路径、本机 commit）。
- 理由：这些文件下一次会被读进 agent 的上下文，Hive 写的字是数据（AGENT R32 B 类）。让 agent 重写，内容经过它的判断，就成了它自己的改动，和 Swarm Run
  底线第 2 条"Hive 的结果只是材料"一致。这样防注入靠的是结构，不靠把事情推给人（XG 2026-10-31：防注入是安全能力，不是 escalate 的理由）。
- 以前说的"在本机重做同样改动就能绕过受保护路径"不再是漏洞：重写就是指令文件唯一的落地方式，每次都有记录，并由 §2.4 核对。

### 2.3 仓库声明的其它受保护路径：agent 定，人看得见

- agent 用 `swarm_out diff` 看过这部分改动（系统记下），带理由落地；`return-land` 对有这条记录的 agent 请求接受 `approve_protected`。
- 改了受保护路径的单，卡上和交付总结里有一行固定的标注："落地改了受保护路径：<路径>，理由：<agent 的理由>"。这是通知，不阻塞，按路径确定，不靠 agent 判断
  "这算不算放松防护"。
- **剩下的风险：** 改了守护代码（比如 `security_hooks.py`）的改动，要重新构建、重启 daemon 才生效；重启今天只有 R12 的文字规则，没有确定性的门（§1）。
  所以"agent 落地一条放松防护的改动、再自己重启"在机制上做得到。本设计不在这里加门，因为 agent 在本机直接改这个文件本来也做得到（原生编码不设门，SOUL P7）；
  它留在决策记录和卡上的标注里，人收结果、决定 push 时看得到。要不要给"重启 daemon"补一道确定性的门，另起讨论。
- **待 XG 定（唯一的待定项）：** E2E 会话的分工表里写的是"改动放松防护、或往约束文件注入指令性文字时通知用户介入"。本设计把后半句换成 §2.2 的结构做法，
  把前半句换成上面这条不阻塞的标注。如果 XG 认为守护代码的改动仍要人先看，改法是：仓库在 `[hive_return]` 里另列 `person = [...]`，这些路径的落地进 Needs You，
  条目写明要人定什么。默认不列。

### 2.4 结单前的核对

agent 结单（今天的 `manual_landed`）时，系统对这一单交回的每个仓库核对，全部满足才结单：

- 对每个交回的 tip：把它合进本机 main（`git merge-tree`，只用对象，不碰工作区），合并结果的 tree 等于 main 的 tree。也就是说改动已经全部在 main 上了。
  这个核对对压成一个 commit 的落地、cherry-pick、agent 改了顺序都成立；按单个 commit 比 patch-id 不行，压成一个以后就对不上了。
- 合并结果和 main 不一样的路径，每条都要有一条决策记录说明去处：`reauthored`（agent 自己重写的本机 commit，系统核对它在 main 上），或 `not_taken`（不采纳，带理由）。
  没有说明的路径一条也不允许，结单被拒，拒绝信息列出这些路径。
- 接手自己做（agent 觉得自己写比打回更快）也走同一个核对：Hive 的改动要么在 main 上，要么逐条说明去处。

这样 E2E 里卡住的那种情况（cherry-pick 后单子挂在"等你"）由 agent 自己结单，不用人点；而"结单时把还没落地的提交一起丢掉"在机制上做不到。

## 3. 怎么留痕

### 3.1 一条记录的格式

每一单一份决策记录，就是 settle 记录里的 `commands`（不另建存储，字段名不改，旧记录照读）。每一条：

| 字段 | 内容 | 谁写 |
|---|---|---|
| `at` | 时间 | 后端 |
| `by` | `agent` / `person` / `product`（产品规则做的决定：当场拒绝、会话不在了交给人、结单核对） | 后端 |
| `session` | agent 的会话（`by: agent` 时） | 后端 |
| `decision` | `dispatch` `answer_wait` `rerun` `stop` `accept` `return_land` `reauthored` `not_taken` `settle` `discard` `escalate` `refused` `handed_over` | 后端，按命令 |
| `observed` | 系统看到的事实，见 §3.2 | **后端**，不由 agent 填；请求里带了也忽略 |
| `why` | 结论和理由，一句到几句 | agent（`--why`，必填）或人（面板备注，可空） |
| `for_person` | 只在交给人时有，见 §3.3 | agent 或后端 |

### 3.2 `observed`：系统看到的，不是 agent 说的

- **这一单当时的状态：** 结果的 `ended_at`、检查格各项过没过、交回了什么（仓库、tip、提交数、改动的路径和受保护类别）、已经重发几次、等待的类别和 Hive 给的选项。
- **agent 在这一单上做过的事：** 从上一个决定到这一个决定之间，这个会话对这一单调过哪些 Swarm Out 命令、读的是哪一版：`read`（读到的是哪个 `ended_at`、
  是否被截断）、`diff`（看了哪些路径）、`card`、`return-prepare`、`watch`。每个 CLI 命令在后端记一笔，决定发生时汇总进这一条。
- **结单核对的结果：** 每个 tip 是否已在 main 上，`reauthored` 的 commit 是否在 main 上，有没有没说明去处的路径。

时间线上的"审过了"因此是一条事实（"读了 `ended_at` 为 X 的结果，看了这 7 个文件的 diff，检查格 11 项全过"），不是 agent 的一句声明。
agent 在 Swarm Out 之外做的检查（在自己 worktree 里跑测试、派专家审）写在 `why` 里，是它的说法；以后如果要也变成事实，另做。

**新增 `swarm_out diff <dispatch> [--path <p>]`：** 读交回的 `refs/hive/...` 的 `base..tip` 改动，按数据给出（和 `read` 一样的数据框）。agent 看代码改动用它，
"看过改动"才记得下来。

### 3.3 交给人的条目

`for_person` 三项：

- **要人定什么：** 问题和选项（就是等待卡上的那几项），或者需要人做的动作（比如"push 到 origin/main"）。
- **为什么 agent 定不了：** `intent`（问的是人的意图）、`session_gone`（派单会话不在了）、`person_class`（push、发布、不可逆数据损失，或仓库 `person` 列表里的路径）。
- **agent 的建议：** 可选。

Needs You 的条目点进去，落在这一单"决定"页的这一条上。

### 3.4 谁在什么时候写

每条都由执行这个决定的代码，在决定生效的同一个锁里写，不靠 agent 记得去写：`swarm_out_actor.record` 扩成上面的格式；`dispatch`、`record_wait_answer`、
`escalate`、结单核对、面板上人的每个命令（通过、修正重跑、停止、丢弃、回答等待）各写一条。`verdict` 等原有字段照旧，读它们的地方不改。

Hive 上当场拒绝的动作：**待查**今天是否随 Hive 记录交回 Host（`security_hooks._log_prompt` 记在 Hive 本地）。交回的，Host 收回时写成 `by: product` 的 `refused`；
没交回的，补上交回。

**不截断：** 去掉 `MAX_COMMANDS = 50`（AGENT R23）。agent 重发最多 2 次、pipeline 决定 `decision_cap` 默认 2；代答 Hive 本地审批和问题没有上限，但条数随这一单
结束，每条几百字节，留全不是负担。

### 3.5 卡上怎么看

- 大卡片的"自主"页改成**"决定"页**：从派发到结单的一条时间线，每条一行"时间 · 谁 · 定了什么 · 理由"，展开看 `observed`。Autonomy Judge 五维放在时间线上方；
  代答就是时间线里的 `answer_wait`，不再单独列。
- 交给人的条目用琥珀色（`tone.ts`：琥珀色只表示需要人），`for_person` 写全。§2.3 的"改了受保护路径"用普通的标注色，不用琥珀色：它不等人。
- 列表行不变：只显示"全自主 / 介入 N 次"，N 数 `by: person` 的决定。

### 3.6 和 Swarm Run 的关系

lead run 对子单的处理就是子单决策记录里 `by: agent` 的条目。lead run 的 REPORT、交付总结和 Swarm Run 卡按 `parent_run` 读子单的决策记录，不在 lead run 那边
另记 `child_action`。Swarm Run 设计下一版跟着改（§5.8、§9、§10）。

## 4. 不做

- 不用模型评判 agent 的决定，不因为理由写得不好而拦命令。
- 不另建决策存储，不改 `verdict` 等现有字段的含义。
- 不让 agent 判断"这算不算放松防护"来决定要不要找人：要找人的路径由仓库按路径列出（§2.3），默认没有。
- 不在列表行上显示 agent 的决定数。

## 5. 验证计划

**谁读：** 大卡片"决定"页、Needs You 跳转、交付总结和 Swarm Run 的 REPORT（读者先设计好：§3.5、§3.6）。

| # | 不变量 |
|---|---|
| D1 | 一单从派发到结单的每个决定，在 `commands` 里各有一条，按时间排序；人、agent、产品的都在 |
| D2 | `observed` 只由后端写，内容等于决定那一刻的事实；请求里带的 `observed` 被忽略 |
| D3 | agent 的命令不带 `--why` 被拒绝（已有，回归照跑）；不要求 agent 自报"看了什么" |
| D4 | `return-land` 对指令文件和 `GIT_PROTECTED` 的路径从不落 Hive 的字节，人和 agent 调都一样 |
| D5 | agent 落地仓库声明的受保护路径，只在这一单上有 `diff` 看过这些路径的记录时才接受；否则拒绝并说缺什么 |
| D6 | 结单核对：有任何交回的改动不在 main 上、又没有 `reauthored`（commit 在 main 上）或 `not_taken` 说明的路径，结单被拒并列出路径；压成一个 commit 落地、cherry-pick 落地都能通过 |
| D7 | 交给人的每一条都有 `for_person`，原因是三项之一；Needs You 只有三种来源：`escalate`、会话不在了、`person_class` |
| D8 | 一单超过 50 条决定时，最早的还在；旧记录（没有 `decision`、`observed`）照常显示 |
| D9 | 列表行的"介入 N 次"只数 `by: person`，和今天的口径一样 |

**审查：** security（§2：指令文件、`GIT_PROTECTED`、agent 落地受保护路径、结单核对）+ api-contract（`commands` 的新字段、`diff` 命令、新旧 Host 和前端互通）
+ ux（"决定"页，先读 `s_frontend-design` 的 `design-judgment.md`，先出 mock）。最多两轮。

## 6. 分期

- **P0：** §2.4 结单核对和 agent 结单（解掉 E2E 里卡住的那一类）；记录格式和所有写入点（§3.1、§3.4），`observed` 的单子状态和结单核对部分；去掉 50 条上限；
  修正 `swarm_out_actor` 文件头里失实的注释。
- **P1：** `swarm_out diff` 和 CLI 调用记入 `observed`；§2.2 指令文件和 `GIT_PROTECTED` 不落 Hive 字节、`reauthored`；§2.3 agent 落地受保护路径和标注；
  `[hive_return] instructions` 配置项。
- **P2：** 大卡片"决定"页和 Needs You 跳转（先出 mock）；查清并补上 Hive 当场拒绝的交回；Swarm Run 按 §3.6 读子单的记录。

## 7. 修订记录

- r1（2026-11-02）：按 XG 的思路起草留痕：一单一份记录，交给人的写明要人定什么。
- r2（2026-11-02）：和 Swarm Out E2E 会话的分工表合成一份。① 加"谁能定"一节：受保护路径由 agent 定并标注；指令文件和 `GIT_PROTECTED` 不落 Hive 的字节，
  agent 重写；`manual_landed` 交给 agent，前提是结单核对（合并 tree 等于 main，或逐条说明去处）。② 依据不再由 agent 自报：去掉 r1 的必填 `--checked`，
  改成系统记下的 `observed`（单子状态、agent 调过的 Swarm Out 命令、结单核对结果），agent 只写结论和理由；新增 `swarm_out diff`。③ 唯一待定：守护代码的改动
  要不要人先看（§2.3），默认不要。
