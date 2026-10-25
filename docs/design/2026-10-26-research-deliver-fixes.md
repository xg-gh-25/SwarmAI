---
title: research run 在 DELIVER 的三个问题
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r1 — 已实施
---

# research run 在 DELIVER 的三个问题

来源：用 pipeline 调研审批弹窗时跑的 `run_872ffe42`（research profile，见
`2026-10-26-approval-prompt-false-positives.md`）。这个 run 应该以 `no_code` 结束，实际结束在
`not_landed / report_mismatch`，Quality 显示 BLOCK，两条经验也没能记下来。

| # | 现状（代码） | 改法 | 测试 |
|---|---|---|---|
| 1 | `next_step` 在 DELIVER、`reflect-prepare` 之后对所有 profile 都先评估 LG-1。research 没有 TEST 报告，LG-1 不通过，于是 `next` 给出 `report-tests`，而 research 根本没有 TEST | research（不落地的 profile）在 `reflect-prepare` 之后，`next` 给出 `stage-complete`，理由是"research 不落地，结束 DELIVER" | rig 跑 research 到 DELIVER，`reflect-prepare` 之后 `next.command` 是 `stage-complete`；照着 `next` 走到底，结束时 `landing == "no_code"` |
| 2 | `land` 对 research run 先记下一次 `stage_attempt(land)`，再在 `tree.build_code_trees` 报 "repos must be a non-empty mapping"，留下一个没关闭的落地尝试；之后 `stage-complete` 按 not-landed 结束 | `_normal_land` 和人工落地的 prepare，在写任何事件之前就拒绝 research run，报错写明"research 不落地，用 stage-complete 结束 DELIVER" | 对 research run 调 `land`：返回错误，事件数不变；之后照 `next` 走完，`landing == "no_code"` |
| 3 | `reflect.prepare`：只要 CLI 算出了去重候选，就不允许 `relation.kind == "new"`。候选是关键词召回的前 3 条，项目有 DDD 时几乎总能召回到，所以新经验基本写不进去；报错也不列出候选是哪些 | 允许 `new`：候选仍由 CLI 计算，原样记在这条经验的 `candidates` 里，所以"agent 判断它们无关"这件事有据可查；agent 选的 `entry` 不在候选里时，报错列出候选 | 有候选时 `new` 被接受，`candidates` 记在结果里；`refines` 指向一个不在候选里的条目时，报错里有候选的文字；原来"必须关联"的测试改为上面的行为 |

不改：RF-1 到 RF-4 的检查本身；去重候选的召回方式；非 research profile 的 DELIVER 流程。

变异检查：每一项分别去掉改动，对应的测试必须变红。

实施：`next_step`（research 在 DELIVER 给 `stage-complete`）、`engine._normal_land` 和 `_prepare_land_request`（research 在写任何事件之前就拒绝，错误文字 `RESEARCH_LANDS_NOTHING`）、`reflect.prepare`（允许 `new`；目标不在候选里时列出候选）、DELIVER 卡片加一句。`test_pipeline_v1_cards` 和 `test_run_protocol_events` 各有 1 个失败，在 main 上同样失败（来自 `1bff6525f`），和这次改动无关。
