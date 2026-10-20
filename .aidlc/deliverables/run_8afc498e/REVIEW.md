# Review — gates and experts

**Run** `run_8afc498e` · **profile** direct · **project** SwarmAI

> 不 从你的worktree 里 commit to main 必须是自动的啊 有测试决定 你为什么不run 测试 到底哪有问题 我们要的是自动flow 啊

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 4 finding(s), 0 open | Red Team: no |
| Meta-review | RISKS | 1 risk(s) |
| Push-ready | no | exit criteria unmet: ['(no exit contract declared at PLAN)']; unverified: test: 3 continued test(s) passed without the earlier tests of their file, in test file(s) this run changed: backend/tests/tes… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 1 | 1 | 1 | 0 | 0 |
| integration | 1 | 0 | 1 | 0 | 0 |
| meta-review | 1 | 1 | 1 | 0 | 0 |
| red-team | 0 | 0 | 0 | 0 | 0 |
| security | 1 | 1 | 1 | 0 | 0 |

_Dispatch:_ Agent tool: correctness, security, integration, meta-review 第一轮并行；correctness、security 增量审查并行（security NO FINDINGS）

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| security | HIGH | pipeline_test._frontend_baseline 按文件判 HEAD：一个改动把整个测试文件弄坏也会被当成 pre-existing 落地。已整个删除这个机制（前端误报的根因在 test-setup，下一个 run 修）。 | fixed |
| meta-review | HIGH | gone 结果带 notrun，续跑会无限重跑已删除的测试。已撤掉整个 gone 机制：真实的那次过期 id 来自 BUILD 的 ac_coverage，拒绝落地是对的。 | fixed |
| correctness | HIGH | pipeline_test._frontend_jobs：续跑排除的测试文件仍被当成 requested 文件，会被报成没有结果。已只请求未排除的文件。 | fixed |
| integration | MEDIUM | 前端 HEAD 基线占满剩余时间，后端基线来不及跑。随机制删除。 | fixed |

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| LOW | --exclude 按 glob 匹配，文件名含 glob 字符时会多排除；当前 284 个前端测试文件都没有 | OPEN |

## Open items

- run_0b56a214 拆分而来（同一份补丁、干净的测试记录）；前端测试被 test-setup 弄坏的根因修复（zoneMemory）在下一个 run 落地；落地检查用的是 main 上的 runner，先落这个修复，下一个 run 的续跑才能跑完
- [runner] Not tested in this run (regression): 7 test(s) passed when continued alone, without the earlier tests of their file (an order dependence on those would not show): backend/tests/test_pipeline_test.py::test_not_run_counts_only_deadline_unfinished_tests, backend/tests/test_pipeline_test.py::test_past_the_cap_a_fix_recheck_clears_the_red_and_is_counted, backend/tests/test_pipeline_test.py::test_runner_reports_a_test_that_writes_the_repo, backend/tests/test_pipeline_test_order.py::test_cross_file_leak_is_order_dependent_regression_and_flaky_stays_flaky, backend/tests/test_pipeline_test_or…
- [degraded] deliver completed with 13 open gate error(s): Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant ; Fix by class: adversarial_review.findings[0] (security, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (security, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line (+10)
