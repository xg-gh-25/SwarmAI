---
title: Pipeline v1 fixing log
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: OPEN
---

# Pipeline v1 修复记录

执行 `~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-22-pipeline-v1-fixing-spec.md`（下称"方案"）时的新增发现、归组和偏离。每组 commit 的说明引用本文。

## 1. G 组的真实端到端测试找到的新问题

| # | 问题 | 归组 | 证明 |
|---|---|---|---|
| G-1 | BUILD 熔断（`checks_exhausted`）之后，DELIVER 上的 `land` 仍把没测过的代码提交到 main，run 以 `completed(landed)` 结束；REVIEW / TEST 根本没跑 | A2 | `test_checks_exhausted_ends_not_landed_and_never_commits` |
| G-2 | 仓库没有 `validations.json` 时，`pipeline_test.py` 直接报 `runner error`（`git show <base>:validations.json` 失败）；空的 `entries` 也被拒 | B6 | 手工复现，见下文 B6 |
| G-3 | `land` 在 `_carry_moved_finals` 里对缺 final 的 repo 抛 `KeyError`，CLI 打印 traceback（非 JSON） | A1 | 首次复现时 final 缺失 |

## 2. 第 4 路审查（方案 §8）的结论

子 agent `67d530841b4656f6` 只读审查，报 11 项（3 HIGH、8 MED）。逐条核对后：

| # | 结论 | 处理 |
|---|---|---|
| F-1 小改动可能完全不派 correctness | **不是问题**：expert 简化设计 E1 和 v13 §A1.4 规定 < 50 行、非安全风险的改动不派审查者；RV-1a 在没有 handout 时通过与之一致 | 不改 |
| F-2 `answer` 的发生键是 `handout_event_id`，设计 §A3.1 要求 `tool_use_id` | 属实。之前为防"一个 handout 记两次答案"改成了 handout；正确做法是发生键回到 `tool_use_id`，唯一性由 bridge 在锁内按 handout 检查 | 并入 C 组 |
| F-3 partial / unparsed 答案写出的 `finding_status` / `finding_occ` 让 `findings.fold` 抛错，run 读不出来 | 属实，属于"run 卡死"类 | 并入 A 组 |
| F-4 handout 没把 prompt 全文存进审计区 blob | 属实 | 并入 D 组 |
| F-5 Gate-0 第一行之后还能有第二个 VERDICT | 属实 | 并入 B 组 |
| F-6 "无输出只重派一次"没有执行路径 | 属实 | 并入 B 组 |
| F-7 答案复用（§C6.2）只有孤立 helper，RV 检查不认 `answer_reuse` | 属实 | 并入 B 组 |
| F-8 `.py/.ts/.js` 的纯注释改动被当成可执行，docs finding 关不掉 | 属实 | 并入 B 组 |
| F-9 ±10 行聚类不是强制事实，`review-address` 接受任意 items | 属实 | 并入 B 组 |
| F-10 FIXING_SPEC 不是由事件生成，结构不符 §B6 | 属实 | 并入 B 组 |
| F-11 REVIEW.md 缺审计字段；来源记录缺规则版本 | 属实 | REVIEW.md 并入 D5，来源记录并入 B7 |
