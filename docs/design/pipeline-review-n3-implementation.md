---
title: N3 终止规则实现方案：收敛判定、第 3 轮交接、push_ready 只认收敛或人的决定
date: 2026-10-19
created: 2026-10-19
updated: 2026-10-19
project: SwarmAI
status: PROPOSED — 待 XG 确认；实现等 run_925edb0e（Run 1，N2 + N4）合入 main 后开始
related: pipeline-review-convergence.md §3「N3 规则定稿」（规则与 AC1–AC6 的出处）；c247dca3（轮数硬上限 review_round_gate，已落地）
author: Kiro（基于 run_925edb0e worktree 里的新代码阅读）
---
# N3 终止规则实现方案

> 规则本身（再一再二不再三）在 `pipeline-review-convergence.md` §3 已定稿。本文只讲**怎么实现剩下的 AC1、AC2、AC4**，以及做这几个决定时放弃了什么、还有什么风险。

## 0. 一页摘要

| 要做的 | 现状 | 方案 |
|---|---|---|
| **AC2 收敛判定** | 没有「收敛」这个概念。循环继续不继续，由模型自己决定是否再跑一次 `gate2-recheck` | 新增一个纯函数 `gate2_convergence()`，只读 hook 记录的 reviewer 原话（marker 里的 `recheck_answers`、`reported`），算出 `converged / open / round`。`gate2-recheck` 和 DELIVER 的 `stage-start` / `stage-complete` 都调用它，不再由模型判断 |
| **AC1 第 3 轮交接** | 轮数硬上限已由 `review_round_gate` 在派 sub-agent 时拦截（c247dca3），但 `gate2-recheck` 照样会写出第 4 轮的 prompt，模型撞墙后才知道 | 某个 expert 已完成 3 轮还有未解决项时，`gate2-recheck` 不再写它的 prompt，返回 `escalate` 和交接清单（写入 `evidence/gate2-escalation.json`），退出码非 0 |
| **AC4 push_ready** | 「reviewer 没答 RESOLVED」可以被模型自己写的 `gate2_recheck_unverified_reason` 豁免 | push_ready 只在两种情况为 true：已收敛；或者有一条**人做出的**决定记录。人的决定走现有的审批对话框（PermissionManager）加一次性 hook token，模型自己写不出来 |

## 1. 依赖：Run 1 带来的东西

Run 1（`run_925edb0e`）合入后提供：

- `core/gate2_findings.py`：唯一的阻断判定 `is_blocking(f)`。HIGH 在任何置信度下都算阻断；MED 在置信度 ≥7 或置信度缺失时算阻断。本方案所有「阻断」都调用它，不另写判定。
- reviewer 回答的结构化记录：SubagentStop marker 里的 `recheck_answers`（`{"<n>": "RESOLVED" | "NOT RESOLVED"}`）和 `reported`（这一轮报出的发现）。这些由 hook 从 reviewer 原话中解析，模型不经手。
- `_gate2_recheck_errors` 已经做到「NOT RESOLVED 是粘性的，只有新的修复才能清掉」。

**实现前必须重读合入后的代码。** Run 1 还在 Gate-2，函数签名可能还会变。

## 2. 方案

### 2.1 AC2：`gate2_convergence(run_id, run_dir, run_state, artifact)`

放在 `core/gate2_findings.py` 旁边的新模块 `core/gate2_convergence.py`，是纯函数，只读文件，方便测试和离线回放。

输入：当前 DELIVER 周期（从 round-1 快照的 `taken_at` 开始）内，本 run 每个 expert 的 marker 行；`fix.diff` 的 hunk 行号范围。

计算：

1. **轮次**：每个 expert 的轮数 = 本周期内它已完成的 sub-agent 数，和 `review_round_gate` 用同一套计数。
2. **未解决项（open）**：最新一轮里答了 `NOT RESOLVED` 的条目，加上最新一轮 `reported` 中满足 `is_blocking` 且**落在修复 hunk 内**的新发现。
3. **修复范围外的发现**：最新一轮 `reported` 中落在修复 hunk 之外的发现，不计入 open。非阻断的进 REPORT §10；阻断的进交接清单（2.2）。一条都不丢。
4. `converged = (round >= 2) and not open`。第 1 轮本身不算收敛，至少要有一轮复核确认修复。第 1 轮如果没有阻断项，就不需要复核，直接判定 converged。

谁用它：

- `gate2-recheck`：converged 时不写任何 prompt，返回 `{"converged": true, "next": "meta-review"}`。
- DELIVER `stage-start` / `stage-complete` 的 `next_action`：converged 时直接指向 meta-review，不再出现「再跑一次 gate2-recheck」的选项。

### 2.2 AC1：第 3 轮后交接

`gate2-recheck` 在写某个 expert 的复核 prompt 之前先调用 `gate2_convergence`：

- 该 expert 的 `round < 3`：照常写 prompt，标题从「round 2 of 2」改成「round N of 3」。
- 该 expert 的 `round >= 3` 且还有 open：不写 prompt，把它放进 `escalate`。
- 只要 `escalate` 非空：
  - 写 `evidence/gate2-escalation.json`，内容：每条 open 的 file:line 和摘要；每一轮修复后新报出了哪些发现（按轮列出，从 marker 的 `reported` 求差得到）；`fix.diff` 的路径；三个选项（接受风险并提交 / 拆成后续 run / 回到 PLAN 重新设计）。
  - stdout 返回 `{"ok": false, "escalate": [...], "escalation_path": ...}`，退出码 3。这个命令本来就没有 `--force`，以后也不加。
- 同时在 run.json 记一条 `review_escalation`（stage、轮次、时间、清单路径）。完成总结和 REPORT §10 从这里读，人在完成总结里能看到「第几轮交给了谁」。

### 2.3 AC4：push_ready 只认「收敛」或「人的决定」

**人的决定怎么记录，才能保证不是模型自己写的：**

新增 `artifact_cli.py run-decide --project P --run-id R --decision accept|split|redesign --note "..."`：

1. 新增 PreToolUse 门禁 `run_decide_gate`（Bash）：命令里出现 `run-decide` 时，**一律**走 PermissionManager 的审批对话框（和危险命令同一个 UI，今天已修好 4h 超时）。对话框里显示交接清单的摘要和所选的选项。
2. 人点 Approve 之后，门禁才用 `core/hook_token` 签发一次性 token，并通过 `updatedInput` 把它前缀到这条命令上。这个机制和 `run_owner_gate` 一样：token 记录只存在 daemon 内存里，CLI 通过 owner-only socket 把它烧掉。
3. `run-decide` 没有有效 token 就拒绝。成功后写 intervention `{kind: "review_escalation_decision", decision, note, approval_request_id, decided_at}`。

**push_ready 规则**（`pipeline_validator.push_ready_upper_bound`，放在 Run 1 的 `_blocked_findings` 判定之后）：

- `gate2_convergence(...).converged` → 按原有其他条件判断。
- 否则，有 `review_escalation_decision` 且 `decision == "accept"` → 可以为 true；未解决项写进 REPORT §10 和完成总结，标为「人已接受的剩余风险」。
- 否则 → false，原因写「Gate-2 未收敛，等你决定」。
- `split` / `redesign` → push_ready 永远是 false；`split` 把 open 项写成后续 run 的需求草稿，`redesign` 让 run 回到 PLAN。

**收紧逃生口：** `_gate2_recheck_errors` 里「没答 RESOLVED」这一类不再接受 `gate2_recheck_unverified_reason` 豁免，只认 `run-decide`。证据缺失类（marker store 读不到、旧 daemon 没有抓回答）仍然只给警告，不阻塞，这部分保持不变。`stage-complete` 里的 `over_budget_reasons` 也不再豁免超额轮次（AC6）。

## 3. 关键决定

| # | 决定 | 选了什么 | 放弃了什么 / 为什么 |
|---|---|---|---|
| D1 | 收敛由谁判断 | 工具，从 hook 记录的 reviewer 原话计算 | 不让模型判断。模型判断正是今天 7 到 9 轮的根源 |
| D2 | 修复范围外的新发现 | 不让循环继续，但阻断项进交接清单，非阻断项进 REPORT §10 | 放弃「发现一个修一个」。red-team 每轮都能找到新的攻击形状，`run_5d894e22` 第 6 到 8 轮全是这类。代价是这些问题这次不一定修，由人决定 |
| D3 | 发现没有行号、无法判断是否在 hunk 内 | 按在 hunk 内处理（fail closed，偏质量） | 多算一轮的成本已经被 3 轮上限封顶；少算可能把真正的修复回归放过去 |
| D4 | 人的决定怎么防伪 | 审批对话框 + daemon 内存里的一次性 token | 不用「模型在记录里写一句 XG 同意了」，那个可以伪造。也不靠解析用户消息里的关键词，太脆。复用已有的 PermissionManager 和 hook_token，不新造机制 |
| D5 | 证据缺失时 | 只警告，不阻塞（维持现状） | 硬阻塞会让旧 daemon、读不到 marker 的情况卡死所有 run。新 daemon 上线后这类情况应该接近 0，用 profiler 观察 |
| D6 | 旧 run 和进行中的 run | 用新的 cutover 时间戳，只对之后创建的 run 生效 | 不改正在跑的 run 的规则，避免半路改规则 |
| D7 | REVIEW 阶段 | 只靠 `review_round_gate` 的轮数上限（已落地），不做收敛判定 | REVIEW 没有 `gate2-recheck` 和 fix.diff 这套机制，接入要先做 N5。这次不扩大范围 |
| D8 | 实现时机 | 等 Run 1 合入后基于新代码实现 | 三个 AC 改的函数正是 Run 1 改写的那些。现在并行改一定冲突，而且会基于即将被替换的旧判定写代码 |

## 4. 风险

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 修复质量差，大量 run 第 3 轮交给人 | 人被打扰的次数多，等待时间算 blocked | 单独统计交接次数。偏高说明该做 N1（修复前先过行为基线），而不是放宽轮数 |
| R2 | 交接时人不在 | run 在审批框上最多等 4 小时；超时按未决定处理，push_ready 保持 false，不会自动提交 | 这正是需要人的地方；profiler 把这段时间记为 blocked，不污染速度数据 |
| R3 | 模型换一个 expert id 重新开始计轮（比如 `correctness2`） | 绕开轮数上限 | 复核只认 round-1 就存在的 expert 的 `<id>-recheck.md`（ledger 绑定），新 id 没有 round-1 prompt，复核不被承认；D7 覆盖检查也会要求原 expert 完成 |
| R4 | hunk 判定出错（重命名、大段移动） | 把范围内的问题当成范围外，或者反过来 | D3 fail closed；测试覆盖重命名和移动；上限兜底 |
| R5 | marker 里的 `recheck_answers` 解析失败（reviewer 没按 `R<n>:` 格式回答） | 判不出 RESOLVED，等于未收敛，可能多跑一轮直到交接 | 现有 prompt 已要求格式；未回答的条目按 NOT RESOLVED 处理（现有语义）；回放时统计解析失败率 |
| R6 | Run 1 合入后的代码和本文假设不一致 | 方案细节要调整 | 实现第一步就是重读；签名变化只影响接线，判定逻辑在独立模块里 |
| R7 | `run-decide` 新门禁和其他 Bash 门禁的 `updatedInput` 冲突（链上 last-wins） | token 被覆盖，决定写不进去 | 和 `run_owner_gate` 一样：只对 `run-decide` 这一条命令改写；在 hook_builder 里排在 run_owner_gate 之后，并加测试断言两者不会同时改写同一条命令 |
| R8 | 这些都是 daemon hook | 不 build、不重启就不生效，今天的修复都处于这种状态 | 实现完一起 `./prod.sh build` 并重启，再用一个真实 run 验证 |

## 5. 验证

- **单元测试**：每个 AC 一组，用真实的 marker 文件和 run.json，参照 `test_review_round_gate.py` 的写法。门禁和收敛判定都做反向验证（关掉核心判断后测试必须变红）。
- **离线回放**（只读，不花 token）：用 `gate2_convergence` 跑 `run_d8762bc8`（7 轮）和 `run_5d894e22`（8 到 9 轮）的历史 marker，输出两件事：新规则下它们会在第几轮收敛或交接；交接清单里会有哪些条目。写进本文 §6，作为规则有效的证据。
- **上线后**：接下来 5 个 full/bugfix run，Gate-2 轮数全部 ≤3、中位数 ≤2；交接次数和 blocked 时间由 profiler 单独统计。

## 6. 回放结果

（实现后填写）
