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
