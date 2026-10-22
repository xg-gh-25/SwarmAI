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

## 3. A 组（run 卡死）

**根因和统一修法：** 事件写进日志之前没有人检查它是否合法，失败路径又会留下没关闭的 stage_attempt，两者都会让 `fold` 读不出 run。修法是两条规则：

1. **非法的事件写不进去。** `AuditStore` 在锁内先用 lifecycle（以及 finding 事件的 finding ledger）把"现有日志 + 新事件"折叠一遍，折叠失败就拒绝写入；事件触发的 `transition` 也在同一把锁里追加，别的写入者插不进中间。所有 v1 写入者都通过 `engine._store` 拿到这个 store。
2. **每个尝试都以一组完整的 check 结果关闭。** `complete_stage` 和 `normal_land` 打开尝试后，任何一个 check 没有求值（文档被 schema 拒绝、前面的守卫已失败、中途抛错）都记成 `not evaluated: <原因>` 的失败；lifecycle 分类失败原因时不看这些。

| 条目 | 处理 |
|---|---|
| A1 | `normal_land` 先打开尝试再做 carry；carry 失败记成 LG-4（rebase 冲突记 `merge_conflict:` 前缀）；任何异常前把剩余守卫记成 not evaluated。lifecycle 按 §A3.7 分类：LG-3/DV-1 → `report_mismatch`；LG-1/LG-4 可重试，第 3 次 → `land_conflict` / `final_stale`；LG-2 → `tests_red` / `tests_incomplete`；LG-5 → `validation_failed`。commit 本身失败（守卫通过后 main 又动了）时返回 `retry`，下一次 land 的尝试取代它；空的 landing-progress 文件会被清掉，否则下一次的新计划会被拒。缺 final 不再 `KeyError`（G-3）。`landing_bases` 取集成分支的真实 HEAD（B3 的前半，A1 的重试依赖它） |
| A2 | 新增 `engine.finish_not_landed()`：渲染 run 目录版 REPORT、写 `render`、开一个只有 DV-1 的 DELIVER 尝试、`stage_complete` → `reflecting`。所有 not_landed 结局和 research 的 no_code 都走它；`land` 遇到已有未落地结论时直接走它，lifecycle 也拒绝这种 run 的 `land landed`（G-1）。reason 由 `_set_pending` 只写一次 |
| A3 | 尝试编号只由 lifecycle 计算（engine `_next_attempt`）。编号是事件身份的一部分，所以人回答之后不再清零（清零会让新尝试和旧尝试的 event_id 相同，被当成重复丢掉）；改为记 `attempt_base`，熔断按回答之后的次数算 |
| A4 | Gate-0 的轮次和结论只看最近一次 human_answer 之后的事件 |
| A5 | schema 失败也写一次尝试：被拒的 check 记失败并带原因，其余记 not evaluated。只有本阶段带 `stage` 效果的 check 能挡住写入，DS-1、DS-3 等只记录 |
| A6 | RF-1~4 移出落地守卫，改在 `reflect-prepare` 时作为 DELIVER 尝试求值；第 3 次不过时经验置 0、`reflect: degraded`；`degraded` 不会被 `reflect_complete` 改回 `ok` |
| A8 | DC-2 从文档读：REQUIREMENTS 的 clarification 或 DESIGN_SPEC 的 `chosen` 带 `decided_by: agent_after_cap`（设计 §A1.2 的记法），不需要新事件 |
| F-3 | finding ledger 接受 `partial` 答案的行；`unparsed` 答案不再写 finding / status |
| A7 | **留到 C 组那批**：hookless 执行器得到 `needs_hooked_session` 需要 run_owner_gate 给 `land` 发 token（hook 改动）；`infrastructure_failed` 的生产者和审计区不可写的处理一起做 |
| A9 | **问 XG**：方案说"按设计 §A1.2"让 awaiting_human 时的 session_lost 进入 paused，但 §A1.2 表里 `崩溃 / 用户停止 → paused` 只从 `active` 出发；`fc6d2ab2`（人机交互设计 `docs/design/pipeline-v1-human-loop.md`）有意让等人的 run 继续等，问题不丢。两者冲突，属于协议本身，暂不改 |

证明：`tests/test_pipeline_v1_real_e2e.py` 中 A 组的用例全部转为必须通过（熔断不落地、Gate-0 两轮否定、Gate-0 否定后人回答、失败尝试后提 decision、任意命令序列后日志可折叠）。其余用例仍依赖 B1 或 C 组。
