---
title: Swarm Run 实现方案（对照 2026-11-03 的 main）
created: 2026-11-03
updated: 2026-11-03
project: SwarmAI
status: 实现中。设计见 2026-10-31-swarm-run.md（r7.12）；XG 2026-11-03："你直接把整个 Design 给做了"，走 native
related:
  - docs/design/2026-10-31-swarm-run.md
  - docs/design/2026-11-02-hive-run-decision-record.md
---

# Swarm Run 实现方案

设计（r7.12）的能力和底线不变。这里写下对照今天的代码怎么落地、哪几处做得比设计简单，以及每一步怎么验证。

## 1. 和设计不同的地方

| 设计 | 实现 | 为什么 |
|---|---|---|
| `child wait --any / --all / <wp>` 设定叫醒时机（§5.5） | 不设。Conductor 对子单和普通派单一样：有要处理、还没看过的变化，会话空闲时就叫醒；lead run 被叫醒后自己看还要等谁，不想处理就结束 turn。`child wait` 就是对这个 run 的子单 `watch` | 少一种要存、要同步的状态。叫醒按"已看过"去重，几单一起交回只叫醒一次 |
| "引擎把所有子单了结"（§5.7） | Conductor 在每次刷新后做：子单的 lead run 已经结束（landed / abandoned / failed）、子单还没了结的，记为"由 Swarm Run 处理"并按现有规则释放 | 引擎在 CLI 进程里，结束的路径有好几条；daemon 的刷新循环本来就在看每一单，一处就够，也不会漏 |
| 工作图（§5.9 "还没派"） | lead run 的计划写在 run 目录的 `children.json`（`child plan` 写），派过的从 Swarm Out 的派发记录读；卡上"还没派"= 计划里有、记录里没有的 | 不另加事件种类；派发的真相只在 Swarm Out |
| 冻结（§5.1 `freeze`） | 第一单子单派发时，把 Brain 打包的字节和摘要、Project Agent revision、host 当前模型存进 run 目录，记一条 `frozen_spec` 事件；之后的子单推这份包、对齐这个模型。Project Agent 在 run 期间改了，子单派发拒绝并说明 | 推的就是冻结那一刻的字节，不靠"摘要一样就跳过"去碰运气；Project Agent 很少改，改了说明要重新想 |
| 每台 Hive 人配置的并行数（§12） | 不加配置。照现有 `pick_hive`（先看开着的单数，再看 streaming 数和内存）和 Hive 的 `saturated` | 现在只有两台 Hive；先拿 §16 的数据 |
| P1"子单先在本机另一个 worktree 里跑" | 直接派到 Hive | 单路 E2E 已经在真机跑通（2026-11-03），本机模拟要另写一套执行端 |

## 2. 分步（每步一个或几个 commit，落本机 main）

1. **派发从指定 commit 开始**：`repo_ship.resolve` / `contains` / `pack(pinned=)`；派工单的 `base`；准入：`returns=commits` 加不在本机分支上的 base 拒绝（子单除外，它的结果不落 main）。
2. **子单的身份和归属**：派发记录加 `parent_run`、`wp`、`project`；`/dispatch` 收到 `parent_run` 时确定性检查：run 存在、没结束、调用会话是 run 当前的 owner、同一个 `wp` 已派不到 3 次。新函数 `swarm_run.owner_of(row)`：子单 = lead run 当前的 owner（读 run 的审计日志，`owner_transfer` 依次折叠），否则 `dispatched_from`。所有读 `dispatched_from` 判归属的地方（actor、record、wait_card、follow、wake、card、attention）改成调它。
3. **子单的命令和结局**：`row_blocker` 对子单拒绝 `return_land`、`manual_landed`（人和 agent 都拒），`ask_person` 同样拒；`return_prepare` 照旧可用。新结局 `handled`（由 Swarm Run 处理）：子单的 `accept` 不要求落地；`derive` 对子单不给人"通过""修正重跑"，给 lead run 的命令另算；Conductor 在 lead run 结束后了结剩下的子单。
4. **`pipeline-v1 child`**：`plan` / `dispatch [--batch]` / `status` / `wait` / `accept` / `rerun` / `stop` / `discard` / `ask` / `answer` / `escalate` / `merge` / `apply`。引擎先查调用者是 run 的 owner、子单属于这个 run，再调 daemon 的同一套路由（`by: agent`）。`run_owner_gate` 把 `child` 当作要查 owner 的命令。事件 `child_dispatched`、`frozen_spec` 进 `EVENT_SPECS` 和 `lifecycle.IGNORED`。
5. **合并工具**：`child merge` 先 `return-prepare`，再用 `merge-tree` 把子单的 tip 依次接到 `refs/swarm/snapshots/<run>/chain` 上（链的第一个 commit 是 worktree 当前的改动，含未跟踪的新文件），去掉 `.aidlc/deliverables/`，遇到 `GIT_PROTECTED` 拒绝；`child apply` 把链的 tip 铺进 worktree（`read-tree --reset -u`，再把索引放回 HEAD），HEAD 仍是 base。铺之前 worktree 又有新改动的，先把它接到链上。`hive_return.prepare` 的前置提交：子单的可以在本 run 的快照 ref 上。
6. **冻结**：见 §1；`pick_hive` 不选正被别的 Swarm Run 冻结同名 Brain 的 Hive，指名的拒收。
7. **叫醒和来源**：wake 按 `owner_of` 分组，lead run 的会话用 `[Swarm Run 自动继续]` 开头。Host 发给子单的修正带 `origin: "host"`，Hive 记进消息的 metadata，`summarize` 只数 person 的 `said`，host 的另计。
8. **Swarm Run 卡**：`pipeline-v1 child dispatch` 之后，后端按记录生成 `swarm_run` 块（和派发卡同一套持久化）；前端 `SwarmRunCard` 复用 `RunList` / `RunOverlay`，按 `parent_run` 过滤，加"还没派"分区和卡头；`GET /api/swarm-out/swarm-runs/{run_id}` 给卡头和计划（进操作登记表）。大卡片里子单的命令面板显示"由 lead run 验收"和子单的决策记录。
9. **引擎侧**：BUILD 的 `next` 在有开着的子单时写"在等 N 个子单"；BUILD 卡片加 §7 的默认做法；REPORT 加"Hive 子单"一节（每单的 wp、Hive、结局、lead run 的处理），REFLECT 从这里取数。
10. **P3**：research profile 加 PLAN 和 REVIEW（D7）。最后做，先看前面九步的代价。

## 3. 验证

每步写的测试先红后绿，每处修法做变异检查。最终跑的集合就是 §14 的 I1–I18，对应如下：

| 不变量 | 测试 |
|---|---|
| I1、I7 | `test_swarm_run_dispatch.py`：子单的派发记录只多 `parent_run`、`wp`、`project`、`base`；冻结后的第二单推同一份包、同一个模型 |
| I2、I6 | 同上：别的会话、别的 run、第 4 次派同一个 `wp` 都被拒；`test_swarm_out_ops.py` 照旧全绿 |
| I3、I4 | `test_swarm_run_children.py`：子单的 `return-land`、`manual-landed`、`ask-person` 被拒，`return-prepare` 可用；lead run 落地照旧要 LG-1 / TS-1 |
| I5、I14 | 已有的 zero-touch 和 hive-approvals 测试照跑 |
| I8 | `test_swarm_run_merge.py`：merge 只写快照 ref；apply 不冲掉 worktree 里已有的改动；main 不变；`GIT_PROTECTED` 和 `.aidlc/deliverables/` |
| I9、I17 | `test_swarm_out_wake.py` 加子单：按 owner 分组、空闲才发、开头标记 |
| I10、I18 | `test_swarm_run_children.py`：lead run 结束后子单记为"由 Swarm Run 处理"并释放；花费按 dispatch_id 各算各的 |
| I11 | 已有的 Swarm Out 和 pipeline 测试照跑（受影响的文件） |
| I12、I13 | `runReview.test.tsx` / `swarmRunCard.test.tsx`：同一套组件；子单"待验收"不进"等你"；子单的 actions 不含通过和修正重跑；只被 lead run 修正过的显示"全自主" |
| I15 | `test_swarm_run_dispatch.py`：冻结期间同名 Brain 的另一个版本不推到那台 Hive |
| I16 | 子单状态只从快照读：`child status` 不读事件；引擎里没有 `child_returned` |

审查：`child` 命令和底线检查 → security；合并工具、Conductor 了结、`owner_of` → concurrency + state-machine；派发参数和新路由 → api-contract；卡片 → ux。每位最多两轮。最后用两台 Hive 跑一次真的 Swarm Run（一个能拆成两单的小改动）。

## 4. 第一轮审查的修复说明（security、correctness、concurrency + state-machine、api-contract + ux）

每条发现都对照代码核实过。按原因分组，一组一个修法：

| 组 | 原因 | 修法 | 证明它的测试 |
|---|---|---|---|
| A（HIGH） | 路由 `_allowed` 对人的命令在调 `row_blocker` 之前就返回，子单的拒绝对人从来不生效（人可以把子单落到 main） | `_allowed` 对人也取 row、调 `row_blocker`（它对人只做子单的拒绝） | 路由测试：人对子单 `return/land`、`manual-landed`、`verdict`、`rerun` 得 403 |
| B（HIGH） | `/spec` 收不到 `parent_run`，起点不在本机分支的子单在 spec 就被拒 | `SpecBody.parent_run` 传给 `task_spec.build`，`child_cli` 发给 `/spec` | 路由测试：`/spec` 带 `parent_run` 把它交给 build |
| C（HIGH） | `@worktree` 每次都挪同一个 base ref，前一批子单的起点失去 ref（取回被拒、可能被 gc） | 每次一个新的 ref（`b<n>-<repo>`），run 期间不删 | 两次 `@worktree`，第一次的 ref 还在、指向原 commit |
| D | 权限检查和 Conductor 在事件循环上读审计日志；`freeze` 打包时占着 `lead()` 也用的锁；`check_child_dispatch` 在锁外读派发记录 | 路由在线程里调 `row_blocker`；Conductor 的 `ended` 在线程里算；`freeze` 用每个 run 自己的锁、在共享锁外打包；派发记录在锁里读 | 并发测试：`freeze` 打包时 `lead()` 不等；锁内读日志 |
| E | lead run 结束后：等着决定的子单永远不了结、`unknown` 的子单不停就记成已处理、lead 的命令和叫醒还在 | run_end 处理过的子单像丢弃一样只要不在跑就了结；Conductor 停掉所有不是 stopped 的子单，`unknown` 的等状态清楚再处理；lead 结束后拒绝它的 accept / rerun / return-prepare，叫醒跳过这些子单 | 各一条测试 |
| F | 同一个 wp 的预算能被绕过：一次调用里重复同一个 wp、两条并发调用、改计划换 wp 名 | CLI 去重；daemon 对没带 `again` 的首次派发拒绝已派过或在途的 wp；计划改写不能删改已派的 wp；每个 run 派发总数上限（`MAX_RUN_DISPATCHES`） | 各一条测试 |
| G | `.aidlc/deliverables/` 只去掉了新增的，子单改、删已有的照样带进来 | 合并后这个目录整体恢复成链上原样 | 子单改一个、删一个已有的 deliverable，合并后都是原样 |
| H | merge / apply 中途 git 出错，前面已上链的结果丢失 | 每个子单 / 仓库各自 try，失败记一条，列表照常返回 | 注入一次 git 失败 |
| I | apply 合并期间 agent 改了 worktree，`read-tree -u` 会冲掉 | 铺之前再算一次 worktree 的树，变了就拒绝 | 注入一次中途修改 |
| J | CLI 在派发后、记事件前死掉，REPORT 漏掉这个子单 | REPORT 以 Swarm Out 的派发记录为准，事件只补 `again_of` | 只有记录没有事件，REPORT 也列出 |
| K | 子单的 lead 会话不在时，`needs_person` 为 null，子单因为有"丢弃"被放进"等你"、标成琥珀色 | 子单不退回旧规则：只有人的等待才算等人 | `needs_person` 对子单只看人的等待 |
| L | "停止全部"失败只在 tooltip；读不到 lead run 时还显示按钮 | 失败写一行；读不到就不显示；run 结束后不再轮询卡头 | 前端测试 |
| M | 杂项：`child` 的 JSON 两种失败写法、`view` 在 engine 为空时 500、`base` 单项没长度上限、TS 类型缺 `host_said` / `lead_actions` / `parent_title`、大卡片只写 run id、中文"子任务 / 子单"混用、"还没派"排在"最近完成"前、混合叫醒只给 Swarm Run 的说明 | 逐条照改 | 已有测试补断言 |
| N | 旧 Hive 不认 `origin`，lead 的修正在它那里算成人说的话 | 不改：列表和大卡片的自主度读 Host 的决策记录（`person_decisions`），只有旧记录的"说的话"格会多算；Hive 升级后消失。记为剩下的风险 | — |

不改：apply 会在 lead run 审之前把子单的文件放进它的 worktree（security INFO）。这是设计的意图（结果是材料，落地前过 lead run 自己的 REVIEW 和 TEST），和 agent 审过才落地是同一类风险，在这里写明。

落地情况：A–M 都已改完，每条修法都写了先红后绿的测试，并做了变异检查（`scripts.mutation_check`：后端 27 处、前端 4 处，全部被抓到）。有一处没留：C 里"ref 只建不挪"的旧值检查。`@worktree` 本来就在 run 的操作锁里取新编号，两次调用不会撞名，所以这个检查从来抓不到问题，已删掉。REPORT 只在 run 写过计划（`children.json`）或记过子单时才读派发记录，普通 run 的审计不读。

