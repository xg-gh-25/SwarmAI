---
title: N3 终止规则实现方案：自动收敛判定、第 3 轮根因复盘、按结论自动提交或重设计
date: 2026-10-19
created: 2026-10-19
updated: 2026-10-19
project: SwarmAI
status: PROPOSED（16:06 按 XG 要求改为全自动）；实现等 run_82253fe2（Run 1 缩减后的分类器部分）合入 main 后开始
related: pipeline-review-convergence.md §3「N3 规则定稿」（规则与 AC1–AC6 的出处）；c247dca3（轮数硬上限 review_round_gate，已落地）
author: Kiro（基于 run_925edb0e worktree 里的新代码阅读）
---
# N3 终止规则实现方案

> 规则本身（再一再二不再三）在 `pipeline-review-convergence.md` §3 已定稿。本文只讲**怎么实现剩下的 AC1、AC2、AC4**，以及做这几个决定时放弃了什么、还有什么风险。

## 0. 一页摘要

整个过程由系统自动完成，不需要人介入（XG 2026-10-19 16:06）。

| 要做的 | 现状 | 方案 |
|---|---|---|
| **AC2 收敛判定** | 没有「收敛」这个概念。要不要再来一轮由模型自己决定 | 纯函数 `gate2_convergence()` 只读 hook 记录的 reviewer 原话（`recheck_answers`、`reported`），算出 `converged / open / round`。`gate2-recheck` 和 DELIVER 的 `stage-start` / `stage-complete` 都调用它 |
| **AC1 第 3 轮后根因复盘** | 轮数上限已由 `review_round_gate` 拦截（c247dca3），但拦截后让模型「交给人」 | 第 3 轮仍有阻断项时，`gate2-recheck` 不再写复核 prompt，改为写 root-cause expert 的 prompt。这个 expert 是 fresh context，结论由 hook 解析 |
| **AC4 按结论自动处理** | 「没答 RESOLVED」可以被模型自己写的理由豁免 | SOUND（方案对，剩下的是边角问题）→ 剩余项写进 REPORT §10、本地 commit、`push_ready: false`。WRONG-APPROACH（方案根上错了）→ 自动退回 PLAN，每个 run 只能退一次。第二次还是不收敛 → 按 SOUND 处理，run 结束 |

## 1. 依赖：Run 1 带来的东西

> **2026-10-19 15:54 更新：** `run_925edb0e` 已标为 abandoned。它在 DELIVER 遇到 12 个阻断发现，又有 19 条 exit criteria 没达成，于是把范围缩成只保留唯一的阻断分类器（`core/gate2_findings.py` + 三个调用点），交给 direct run `run_82253fe2` 落地。发现台账、按改动路由这部分（N2b/N2c/N4）没有落地，完整补丁保存在 `run_925edb0e/evidence/gate2-ledger-full.patch`。
> 对本方案的影响：本方案不依赖台账（N4），只读 marker 里的 `recheck_answers` / `reported`，这些 main 上已经有了。依赖缩小为 `is_blocking`，等 `run_82253fe2` 合入即可开始。另外，`run_925edb0e` 的 red-team 跑了 4 轮，因为 `review_round_gate` 所在的 daemon 还没重启，规则没有生效。

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
3. **修复范围外的发现**：最新一轮 `reported` 中落在修复 hunk 之外的发现，不计入 open。阻断和非阻断都进 REPORT §10，阻断的同时作为根因复盘的输入（2.2）。一条都不丢。
4. `converged = (round >= 2) and not open`。第 1 轮本身不算收敛，至少要有一轮复核确认修复。第 1 轮如果没有阻断项，就不需要复核，直接判定 converged。

谁用它：

- `gate2-recheck`：converged 时不写任何 prompt，返回 `{"converged": true, "next": "meta-review"}`。
- DELIVER `stage-start` / `stage-complete` 的 `next_action`：converged 时直接指向 meta-review，不再出现「再跑一次 gate2-recheck」的选项。

### 2.2 AC1：第 3 轮后根因复盘

`gate2-recheck` 在写某个 expert 的复核 prompt 之前，先调用 `gate2_convergence`：

- `round < 3`：照常写复核 prompt，标题写「round N of 3」。
- 有任何 expert 已经 `round >= 3` 且还有 open：一个复核 prompt 都不写，改为写 `evidence/prompts/root-cause.md`，返回 `{"ok": true, "root_cause": true, "spawn": ...}`。
- `root-cause.md` 的输入全部由工具组装，模型不经手：需求和验收标准（evaluate artifact）、PLAN 的方案和 Change Spec、三轮的全部发现（从 marker 的 `reported` 读取）、每轮修复引出了哪些新发现（相邻两轮的 `reported` 求差）、`fix.diff`、还没解决的 open 项。
- 只问一个问题：这些问题说明**方案根上错了**，还是**方案对、只剩边角问题**？要求 expert 在第一行写 `ROOT-CAUSE VERDICT: SOUND | WRONG-APPROACH`，下面写理由；如果是 WRONG-APPROACH，还要写出新方案应该绕开什么。
- EXPERT-ID 是 `root-cause`，加进 `runtime_hooks._GATE_VERDICT_RE`，由 SubagentStop hook 解析，结论写进 marker。这和 Gate-0/1 skeptic 的 verdict 是同一套机制。每个 DELIVER 周期只允许一次根因复盘（`review_round_gate` 对 `root-cause` 的上限设为 1）。
- `review_round_gate` 的拒绝文本从「交给 XG」改成「运行 gate2-recheck，它会给出根因复盘的 prompt」。

### 2.3 AC4：按结论自动处理

`stage-complete --stage deliver` 读取 marker 里的 root-cause 结论，模型写的记录不算：

| 情况 | 处理 |
|---|---|
| 已收敛 | 按原有其他条件判断 push_ready |
| 未收敛，结论 SOUND | 剩余的阻断项和非阻断项全部写进 REPORT §10，附上 root-cause 的理由。DELIVER 正常完成，`run-commit` 照常本地提交（代码和 REPORT 在同一个 commit）。`push_ready: false`，原因写「Gate-2 第 3 轮剩 N 个阻断项，根因复盘判定为方案外的边角问题」 |
| 未收敛，结论 WRONG-APPROACH，这个 run 还没退回过 | `stage-complete` 返回 `next_stage: plan`，把 run 退回 PLAN（复用现有的 stage 重新打开机制），写一条 `redesign` intervention，带上根因结论。新 PLAN 必须引用根因结论里「要绕开什么」（validator 检查）。DELIVER 重新进入时，round-1 快照重拍，Gate-2 重新拿到 3 轮额度 |
| 未收敛，WRONG-APPROACH，但已经退回过一次 | 按 SOUND 处理：本地 commit，REPORT 写明「重设计后仍未收敛」，`push_ready: false`，run 结束 |
| 未收敛，还没做根因复盘 | `stage-complete` 拒绝，`next_action` 指向 `gate2-recheck`（它会给出根因复盘的 prompt） |

为什么允许带着阻断项提交：本地 commit 可以撤销，而且没有 push。`push_ready: false` 保证 push 前检查会拦住，完成总结也会醒目地标出来。这符合「可逆、低风险由 agent 决定并记录」的授权边界。

**收紧逃生口：** `_gate2_recheck_errors` 里「没答 RESOLVED」这一类不再接受 `gate2_recheck_unverified_reason` 豁免，唯一的出口是收敛或根因复盘。证据缺失类（marker store 读不到、旧 daemon）仍然只警告。`over_budget_reasons` 不再豁免超额轮次（AC6）。

## 3. 关键决定

| # | 决定 | 选了什么 | 放弃了什么 / 为什么 |
|---|---|---|---|
| D1 | 收敛由谁判断 | 工具，从 hook 记录的 reviewer 原话计算 | 不让模型判断。模型判断正是 7 到 9 轮的根源 |
| D2 | 第 3 轮不收敛怎么办 | 系统自己做根因复盘，按结论自动提交或重设计 | 不交给人（XG 16:06）。人看到的是结果，不是一道选择题 |
| D3 | 根因复盘由谁做 | fresh-context 的 root-cause expert，结论由 hook 从原话解析 | 不让修了三轮的那个上下文自己判断，它已经陷在自己的方案里；也不让模型自己写结论 |
| D4 | 方案对但还剩阻断项 | 本地 commit，REPORT 写清楚，`push_ready: false` | 不回滚：代码和问题一起留下，下一个 run 可以接着修。不 push：push 前检查拦住 |
| D5 | 重设计次数 | 每个 run 最多 1 次 | 无限重设计就是换个形式的无限循环 |
| D6 | 修复范围外的新发现 | 不让循环继续，全部写进 REPORT §10，并作为根因复盘的输入 | 放弃「发现一个修一个」。red-team 每轮都能找到新形状（`run_5d894e22` 第 6 到 8 轮） |
| D7 | 发现没有行号、判断不了是否在 hunk 内 | 按在 hunk 内处理（fail closed） | 多算一轮的成本被 3 轮上限封顶 |
| D8 | 证据缺失时 | 只警告，不阻塞（维持现状） | 硬阻塞会让旧 daemon 卡死所有 run |
| D9 | 旧 run 和进行中的 run | 新的 cutover 时间戳，只对之后创建的 run 生效 | 不半路改规则 |
| D10 | REVIEW 阶段 | 轮数上限已落地；根因复盘这次只接 DELIVER Gate-2 | REVIEW 没有 recheck/fix.diff 机制，接入属于 N5，不扩大范围 |

## 4. 风险

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | root-cause expert 判错：把根本问题判成 SOUND | 带着本该重设计的问题提交 | `push_ready: false` + REPORT 里写明，push 前还有一道检查；统计 SOUND 的 run 后续被返工的比例，偏高就收紧 prompt |
| R2 | 反过来，把边角问题判成 WRONG-APPROACH | 多一次重设计，耗时增加 | 每个 run 只能重设计 1 次，耗时有上限；单独统计重设计次数 |
| R3 | 重设计后改动范围变大，Gate-2 发现更多问题 | 第二次也不收敛 | 按 SOUND 处理并结束；REPORT 写明两次方案和各自的问题，不会无限跑 |
| R4 | 带着阻断项本地 commit，后续 session 不知情直接 push | 有问题的代码被推出去 | push 前检查读 `push_ready`；完成总结第一行标红；commit message 写明「未收敛」 |
| R5 | 修复质量差，很多 run 走到根因复盘 | 耗时增加 | 单独统计根因复盘次数。偏高说明该做 N1（修复前先过行为基线），而不是放宽轮数 |
| R6 | 模型换一个 expert id 重新计轮 | 绕开上限 | 复核只认 round-1 就存在的 expert 的 `<id>-recheck.md`（ledger 绑定）；D7 覆盖检查要求原 expert 完成 |
| R7 | hunk 判定出错（重命名、大段移动） | 范围内外判断错 | D7 fail closed；测试覆盖；上限兜底 |
| R8 | `recheck_answers` 解析失败 | 判不出 RESOLVED，等于未收敛，可能提前走到根因复盘 | 没回答的条目按 NOT RESOLVED 处理（现有语义）；回放时统计解析失败率 |
| R9 | 退回 PLAN 时 stage 重开和 worktree 状态不一致 | run 卡住 | 复用现有的 stage 重新打开和 worktree 同步路径，不新造；加一个端到端测试：DELIVER → PLAN → DELIVER |
| R10 | 都是 daemon hook | 不重启不生效 | 实现完一起 build 并重启，再用真实 run 验证 |

## 5. 验证

- **单元测试**：每个 AC 一组，用真实的 marker 文件和 run.json，参照 `test_review_round_gate.py` 的写法。门禁和收敛判定都做反向验证（关掉核心判断后测试必须变红）。
- **离线回放**（只读，不花 token）：用 `gate2_convergence` 跑 `run_d8762bc8`（7 轮）和 `run_5d894e22`（8 到 9 轮）的历史 marker，输出新规则下它们会在第几轮收敛，或者走到根因复盘；对这两个 run 实际跑一次 root-cause expert，看结论是 SOUND 还是 WRONG-APPROACH，和事后看到的情况是否一致。写进本文 §6，作为规则有效的证据。
- **上线后**：接下来 5 个 full/bugfix run，Gate-2 轮数全部 ≤3、中位数 ≤2；根因复盘次数、两种结论的比例、重设计次数单独统计。

## 6. 回放结果

（实现后填写）
