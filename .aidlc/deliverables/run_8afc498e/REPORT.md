# Autonomous Pipeline Report: 前端测试的续跑能跑完：已有结果的测试文件不再重跑

**Run ID:** run_8afc498e | **Project:** SwarmAI | **Profile:** direct
**Date:** 2026-10-20 06:18 UTC | **Duration:** 13.9 min (in progress) | **Tokens:** 712,143 (measured per response, cache read 5,698,796)
**Gate 1:** N/A | **Files:** 3 | **Tests:** not recorded

## TL;DR
**解决了什么问题**
- 原来：前端测试的相关集合很大时（比如改了每个测试都会加载的 test-setup），自动续跑每次都从头重跑整个集合，95 秒跑不完就被杀掉，永远收敛不了，改动就落不了地。
- 现在：续跑会排除已经有结果的测试文件，只跑剩下的；按已完成的前端文件数计算进度；剩下的全部跑完时，那个未完成占位会被标为已完成。大集合可以分几次调用跑完，然后由测试决定自动落地。

**怎么做的**
- 续跑给 vitest 传 --exclude（已有结果的文件），排除列表进缓存 key，部分结果不写缓存
- run index 记录 desktop_done，停滞判断把它算作进展
- vitest 跑到结束时合并结果，答复 desktop::vitest 占位

**遗留问题**（5 条，见 §10）

## 1. Requirement
前端测试的自动续跑能跑完：已有结果的测试文件不再重跑（落地检查自动化的前置修复）

## 2. Evaluation
Recommendation: **?** | Scope: ?

## 3. Design & Approach
**⏭ Skipped** — Skipped — not required for profile 'direct'


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| build | Split: the runner fix lands first, the frontend test-setup f | judgment | the landing check runs main's runner: a run that c |
| build | The exclusion list is part of the cache key; a partial front | judgment | found while testing: the second continuation was s |
| deliver | 撤掉前端 HEAD 基线和 gone 分类，范围缩小为续跑修复 | judgment | 审查发现基线会放过整个文件损坏；那次过期 id 来自自己的 ac_coverage，拒绝是对的；go |

**Summary:** 0 mechanical, 0 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**4 found · 4 fixed · 0 open**
- [HIGH] pipeline_test._frontend_baseline 按文件判 HEAD：一个改动把整个测试文件弄坏也会被当成 pre-existing 落地。已整
- [HIGH] gone 结果带 notrun，续跑会无限重跑已删除的测试。已撤掉整个 gone 机制：真实的那次过期 id 来自 BUILD 的 ac_coverage，拒绝
- [HIGH] pipeline_test._frontend_jobs：续跑排除的测试文件仍被当成 requested 文件，会被报成没有结果。已只请求未排除的文件。
- [MEDIUM] 前端 HEAD 基线占满剩余时间，后端基线来不及跑。随机制删除。

### 7.6 Completion Audit
**Requirements met:** 1/1
**All green:** yes


## 8. Files Changed
- `backend/scripts/pipeline_test.py`
- `backend/tests/test_pipeline_test.py`
- `backend/tests/test_pipeline_test_recheck_od.py`

## 10. Known Gaps & Attention Flags
- 未通过 push-ready：exit criteria unmet: ['(no exit contract declared at PLAN)']; unverified: test: 3 continued test(s) passed without the earlier tests of their file, in test file(s) this run changed: backend/tests/test_pipeline_test.py; Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant to design_updates [{id, statement, why_m; Fix by class: adversarial_review.findings[0] (security, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (security, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line; Fix by class: adversarial_review.findings[1] (meta-review, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant to design_updates [{id, statement, wh; Fix by class: adversarial_review.findings[1] (meta-review, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[1] (meta-review, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line; Fix by class: adversarial_review.findings[2] (correctness, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant to design_updates [{id, statement, wh; Fix by class: adversarial_review.findings[2] (correctness, HIGH) needs root_cause — why the DESIGN allowe
- run_0b56a214 拆分而来（同一份补丁、干净的测试记录）；前端测试被 test-setup 弄坏的根因修复（zoneMemory）在下一个 run 落地；落地检查用的是 main 上的 runner，先落这个修复，下一个 run 的续跑才能跑完
- [runner] Not tested in this run (regression): 7 test(s) passed when continued alone, without the earlier tests of their file (an order dependence on those would not show): backend/tests/test_pipeline_test.py::test_not_run_counts_only_deadline_unfinished_tests, backend/tests/test_pipeline_test.py::test_past_the_cap_a_fix_recheck_clears_the_red_and_is_counted, backend/tests/test_pipeline_test.py::test_runner_reports_a_test_that_writes_the_repo, backend/tests/test_pipeline_test_order.py::test_cross_file_leak_is_order_dependent_regression_and_flaky_stays_flaky, backend/tests/test_pipeline_test_order.py::test_no_time_for_the_prefix_rerun_is_unknown_never_flaky, backend/tests/test_pipeline_test_order.py::test_prefix_plans_cut_at_the_furthest_failing_arg, backend/tests/test_pipeline_test_order.py::test_same_second_same_size_edit_drops_the_stale_pyc
- [degraded] deliver completed with 13 open gate error(s): Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant ; Fix by class: adversarial_review.findings[0] (security, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (security, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line (+10)
- LOW 风险：--exclude 按 glob 匹配，文件名含 glob 字符时会多排除；当前 284 个前端测试文件都没有


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-20 06:18 UTC*
