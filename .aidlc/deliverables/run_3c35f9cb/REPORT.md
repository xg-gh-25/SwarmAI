# Autonomous Pipeline Report: Completion summary: measured test counts, in-run fixes leave 遗留问题

**Run ID:** run_3c35f9cb | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 08:21 UTC | **Duration:** 44.4 min (in progress) | **Tokens:** 0 (估算 / estimate)
**Gate 1:** N/A | **Files:** 4 | **Tests:** 1458 run, 0 failed, 2 pre-existing, 5 flaky

## TL;DR
**解决了什么问题**
- 原来：The quality line showed the author's hand-written test counts (1218/1218 通过) while the runner had measured 3 pre-existing failures, and a risk fixed in the run still appeared under 遗留问题 because the summary ignored the mitigation field writers use to mark it
- 现在：TEST stage-complete stores the runner's counts, so the summary and REPORT read e.g. 测试 1217/1220 通过 · 3 个既有失败; a risk marked fixed/accepted (resolved:true, or status/mitigation 'fixed: …') leaves 遗留问题, while proposals like 'fixed in follow-up' stay listed

**怎么做的**
- Derived tests_total / failed / pre_existing_failures / flaky_failures from latest-regression evidence at TEST stage-complete (timeouts stay failed)
- Carried the new counts through the REPORT phrase and the summary door; the quality line names them
- Extended the one done-predicate to mitigation/disposition with a separator-anchored rule
- Documented resolved:true + status text in push-ready.md so gates and summary agree; 14/14 mutations killed

**遗留问题**（2 条，见 §10）

## 1. Requirement
修完成总结的两处不准：(1) 质量行写『1218/1218 通过』，实际 1215 通过 + 3 个既有失败——测试计数来自作者手写的 TEST artifact(failed:0)，不是 runner 实测；(2) 遗留问题照列已在本 run 修掉的 meta_review 风险——review_summary 只认 resolved/status/resolution 标记，而没有任何文档告诉作者怎么标记一条风险已修。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 修 ‘两处不准： ⏎ 质量行： 写的是"1218/1218 通过"，实际是 1215 个通过，另有 3 个之前就存在的失败。 ⏎ 遗留问题： "validator 和 renderer 之间没有一致性测试"那条风险已经在本 run 修掉了，但总结里还列着。原因是总结只要有 meta_review 的风险就照列，不看它是否已解决。’

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: 质量行写 1218/1218 通过，实际 1215 + 3 个既有失败 | confirmed | ● | Given regression evidence with 1217 passed and 3 pre-existing failures, when TEST stage-complete runs and the summary renders, then the quality line reads 1217/1220 通过 · 3 个既有失败 |
| FR-2 | user-story | user-stated: XG: 已修掉的 meta_review 风险还列在遗留问题 | confirmed | ● | Given a meta_review risk with mitigation 'fixed: added a parity test', when review_summary runs, then it is not in leftovers |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 3.00 | |
| Priority | 4.00 | |
| Feasibility | 5.00 | |

**Acceptance Criteria:**
1. AC1: TEST stage-complete, when latest-regression evidence exists, sets the test artifact's tests_total to the measured result count, failed to the measured failures NOT classified pre-existing, and pre_existing_failures to the pre-existing count.
2. AC2: the chat quality line renders passed/total from those numbers and appends 'K 个既有失败' when K>0 (run_930b3a62's evidence would render 测试 1217/1220 通过 · 3 个既有失败).
3. AC3: the REPORT test metrics carry pre_existing_failures and its tests phrase names them, so the two doors agree.
4. AC4: a meta_review risk / attention flag / finding whose mitigation or disposition starts with fixed/resolved/accepted/closed/done followed by a separator (: ; — -) counts as done and leaves the 遗留问题 list.
5. AC5: a mitigation that only proposes a fix ('add a parity test', 'fixed in follow-up', 'accepted risk; owner TBD' without a separator after the keyword) stays a leftover.
6. AC6: push-ready.md documents how to mark a risk addressed in-run (status or mitigation 'fixed: <how>' / 'accepted: <why>').


## 3. Design & Approach
**Research Findings (THINK):**
- TEST stage-complete (artifact_cli.py:8165-8177) reads latest-regression evidence and overwrites artifact.layers + regressions; tests_total/failed stay author-written. Every reader (summary via _delivery_summary_for:2145, REPORT via _report_test_metrics:5891, METRICS :6715) reads the artifact, so deriving at stage-complete fixes all doors at once.
- Evidence counts = {passed, failed, errors, timeouts, skipped}; failures[].classification in {regression, pre-existing, ...}. run_930b3a62: 1217 passed + 3 failed, all pre-existing.
- METRICS (:6715-6720) turns failed into test_regressions, so failed must exclude pre-existing failures; a separate pre_existing_failures field carries them.
- _item_done (run_delivery_summary.py:881) is the single done-predicate for findings, attention_flags and meta_review risks; it reads resolved, status, resolution. Writers mark addressed risks in mitigation (~15) and disposition (1).

**Alternatives Evaluated:**
- QUALITY — effort: S
- SIMPLICITY — effort: S
- DELETION — effort: S

**Chosen Approach:** New pure helper artifact_cli._test_counts_from_evidence(ev) used in the TEST derive block next to layers/regressions; _report_test_metrics/_report_tests_phrase/_delivery_summary_for carry pre_existing_failures; run_delivery_summary._quality_line subtracts and names it; _item_done gains a separator-anchored regex over mitigation/disposition. Doc line in push-ready.md.

**Success Criteria:**
- run_930b3a62-shaped evidence renders 1217/1220 通过 · 3 个既有失败
- a 'fixed:' risk leaves 遗留问题

**Always:**
- failed keeps meaning new failures

**Never:**
- treat a bare keyword without a separator as done


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | failed keeps meaning 'new failures'; pre-existing counted se | judgment | Gate-0: METRICS writes test_regressions from faile |
| evaluate | Reader accepts the observed mitigation/disposition conventio | judgment | matches ~15 real writers; separator keeps proposal |
| think | QUALITY approach | judgment | one derive point feeds summary, REPORT and METRICS |
| plan | Extract _test_counts_from_evidence as a pure helper | taste | unit-testable without the stage-complete harness;  |
| plan | Gate-1 WARN folded: dash needs surrounding space, flaky coun | judgment | each verified: flaky is non-blocking in pipeline_t |
| build | Derive at TEST stage-complete, the single point every reader | mechanical | summary, REPORT and METRICS all read the stored ar |
| review | Accept spec-compliance WARNINGs | judgment | flaky is the AC1 class; the legacy-passed path has |
| test | Non-regression failures not fixed here | mechanical | runner classified them flaky (test_run_report_writ |
| deliver | Gates keep reading resolved only | judgment | loosening a HIGH block to free text would weaken a |

**Summary:** 2 mechanical, 1 taste, 6 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**1 found · 1 fixed · 0 open**
- [LOW] backend/scripts/artifact_cli.py _test_counts_from_evidence: a pre-existing/flaky

### 7.6 Completion Audit
**Requirements met:** 2/2
**All green:** yes


## 8. Files Changed
- `backend/scripts/artifact_cli.py`
- `backend/core/run_delivery_summary.py`
- `backend/skills/s_autonomous-pipeline/stages/ref/deliver/push-ready.md`
- `backend/tests/test_summary_counts_and_done_marks.py`

## 9. Lessons
- [pitfall] A number the summary shows must come from the instrument that measured it, not from the author's transcription of it: the TEST artifact's tests_total/failed were hand-written while the runner's evidence already sat in the run dir, so '1218/1218 通过' hid three failures. When a stage already overwrites some fields from evidence (layers, regressions), audit every sibling field the readers show and derive those too.
- [pitfall] When widening what a display predicate counts as 'done', check every GATE that answers the same question: the summary started accepting status/mitigation text while the push-ready HIGH check reads only resolved, so the fix is to make writers set the mark every reader honors (resolved:true), not to loosen the gate to free text.
- [guideline] Free-text fields that can hold either a proposal or a disposition ('fixed in follow-up' vs 'fixed: added a test') need a separator-anchored match, pinned by negative tests over real proposal phrasings.


## 10. Known Gaps & Attention Flags
- Non-regression failures in the full run: 2 pre-existing in test_artifact_cli_run_ownership.py (run_created_at kwarg mismatch already in HEAD, another session) and 5 flaky (test_run_report_writer_fields passes 49/49 alone).
- Local commit only; nothing pushed.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 08:21 UTC*
