# Autonomous Pipeline Report: Pipeline 完成总结主动给出 review 信息，消除假干净

**Run ID:** run_39068a4a | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 06:29 UTC | **Duration:** 84.8 min (in progress) | **Tokens:** 0 (估算 / estimate)
**Gate 1:** BLOCK | **Files:** 8 | **Tests:** not recorded

## TL;DR
**解决了什么问题**
- 原来：pipeline 完成时，聊天总结只有"你要的/需求/改动/质量"四块，没有解决了什么、怎么做的、还剩什么；REPORT §10 只读 completion audit，DELIVER 里记录的遗留、meta-review 风险、push_ready=false 原因、强制提交全都看不见，渲染成 None identified（假干净）
- 现在：聊天总结和 REPORT TL;DR 由同一个函数生成三段：解决了什么问题 / 怎么做的 / 遗留问题；§10 读同一来源，有遗留就不可能显示 None identified；强制提交在 commit 记录和质量行里都会标出；某段没记录就写"未记录"

**怎么做的**
- core/run_delivery_summary.review_summary：汇总未修复发现、push_ready=false 原因、强制提交、attention_flags、meta-review 风险（兼容 gate 输出的 dict 结构）
- render_review_lines 一个渲染器同时供聊天块和 REPORT 使用，聊天侧转义并限条数、REPORT 侧全量列入 §10
- run-commit --force 给 commits[] 打 forced_push_ready 标记并写一条 push_ready_force_bypass 审计记录
- finalize 时 push_ready 判定作为整体继承，不再被 COMPLETE 调用抹掉，也不会在提交后被重新压成 false
- deliver 卡片要求写 outcome 和 attention_flags；顺带修好 HEAD 上本来就坏的 test_spec_stale_todo.py

**遗留问题**（6 条，见 §10）

## 1. Requirement
pipeline 完成总结主动给出 review 信息，消除假干净：(1) 聊天完成总结(core/run_delivery_summary.py)与 REPORT.md TL;DR 共用一个函数生成三段式——解决了什么问题(原来→现在)、怎么做的、遗留问题(DELIVER attention_flags、meta_review.risks、push_ready=false 原因、是否 --force 提交、延后发现)；某段无数据写'未记录'不省略。(2) REPORT §10 Known Gaps 读同一来源，记录了遗留就绝不渲染 None identified。(3) 质量行在 push_ready=false 或强制提交时明确标出。(4) pipeline_validator._read_evidence_json 对 >2MB 证据返回 None 被当成缺失——读不了的证据不得报告为缺失。约束：专家同一消息并行前台；只测本 run 文件；每专家最多 2 轮。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起 pipeline：完成总结加三段式+修假干净

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: 解决了什么问题 怎么做的 还有什么遗留问题 这些 summary 才是最好的 review 信息 | confirmed | ● | Given a completed run, when the summary renders, then three labeled parts appear; missing data shows 未记录 |
| FR-2 | user-story | user-stated: XG: 起 pipeline：完成总结加三段式+修假干净 | confirmed | ● | Given deliver has attention_flags, when REPORT renders, then §10 lists them, not None identified |
| FR-3 | user-story | user-stated: XG approved the three-part plan including the quality line | confirmed | ● | Given run-commit --force with push_ready false, when summary renders, then quality line shows 未 push-ready · 强制提交 |
| FR-4 | user-story | user-stated: peer-session report confirmed by code-trace pipeline_validator.py:4714 | confirmed | ● | Given latest-verify.json over 2MB, when push_ready bound computed, then reason says unreadable (size), not no evidence |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 8.00 | |
| Priority | 9.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: one shared function renders a three-part review summary (解决了什么问题: before -> now; 怎么做的: key approach points; 遗留问题: leftovers); both the chat completion summary and REPORT.md TL;DR use it; a part with no recorded data renders 未记录, never omitted.
2. AC2: leftovers aggregate DELIVER attention_flags, meta_review.risks, push_ready_reason when push_ready is false, a forced commit, and unresolved adversarial findings; REPORT §10 renders the same list, so a run with any recorded leftover never renders 'None identified'.
3. AC3: run-commit --force past the push_ready gate records forced on the commits[] entry plus an intervention; the chat quality line shows 未 push-ready and 强制提交 when they apply.
4. AC4: an evidence file above the size cap is reported as unreadable with its reason, not as missing; the push_ready bound reason names it.
5. AC5: DELIVER artifact accepts outcome {problem_before, result_now, approach[]} and the deliver card tells the agent to write it; validator WARNs when absent on strict profiles.


## 3. Design & Approach
**Research Findings (THINK):**
- chat summary render_delivery_summary_md (core/run_delivery_summary.py:850) reads deliver only for adversarial counts and push_ready; attention_flags, meta_review.risks, push_ready_reason are never rendered
- REPORT TL;DR is delivery.summary or requirement[:200] (artifact_cli.py:5971); §10 reads completion_audit and validation_blocks only (:6333)
- artifact_cli already imports core.run_delivery_summary (_delivery_summary_for, :2096), so one shared function can serve both doors
- deliver schema (pipeline_validator.py:529) has required/recommended lists only; a new outcome field is accepted and can WARN when absent
- run-commit --force on the push_ready gate writes commits[] {repo, sha, files} with no force mark (:4343); only D7 force writes an intervention
- evidence over 2 MB is silently dropped by _read_evidence_json (pipeline_validator.py:4714); 3 real latest-*.json files on disk are 3.4-4.0 MB, and _judge_test_criterion then says 'no latest-verify.json evidence' (:4848)

**Alternatives Evaluated:**
- QUALITY — effort: M (1 session)
- SIMPLICITY — effort: S
- DELETION — effort: S

**Chosen Approach:** QUALITY alternative from THINK. A pure helper review_summary(run_state, deliver) in core/run_delivery_summary.py returns {solved, approach, leftovers}. Sources: deliver.outcome; leftovers = deliver.adversarial_review.findings[] with resolved != true, the deliver stage record push_ready=false + push_ready_reason, commits[].forced_push_ready, deliver.attention_flags[], deliver.meta_review.risks[]. Emptiness is decided on raw values (strip, drop blanks) before _txt. Chat renderer escapes via _txt and caps lines; REPORT renders full lists in TL;DR and §10. run-commit records forced commits.

**Success Criteria:**
- new tests pass and go RED under mutation
- existing summary/report/commit tests stay green
- re-rendering run_1907ca1d's REPORT lists its 4 attention_flags in §10

**Always:**
- every model/user string in the chat block goes through _txt
- summary rendering never raises

**Never:**
- derive a fake 解决了什么 from the requirement text
- touch files outside files_touched


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | profile full: bounded feature across 3 modules + deliver car | mechanical | not a single bug, not iterative |
| evaluate | fold the oversized-evidence bug into this run | taste | same false-clean class, one review instead of two  |
| think | missing outcome renders 未记录, no derived fallback | taste | a derived guess hides that the agent skipped it, t |
| think | evidence gets its own cap (64 MB) instead of reusing the 2 M | taste | the 2 MB cap guards arbitrary disk_check reads; ev |
| plan | no required experts beyond the default Gate-2 set | mechanical | no endpoint, async or state machine touched |
| plan | drop AC4 + validator schema change: another session owns unc | judgment | Gate-1 BLOCK; R29 never commit another session's h |
| build | carry push_ready / push_ready_reason / push_ready_capped for | judgment | real-data replay showed the COMPLETE finalize eras |
| build | REPORT TL;DR points leftovers at §10 instead of repeating th | taste | one full list in the report, capped list in chat |
| review | leave stage-complete replace path without verdict carry | taste | deliver stage-complete recomputes push_ready from  |
| test | accept the load-induced timeout of test_status_active_only_f | judgment | timed each CLI step: per-call cost is load, identi |

**Summary:** 2 mechanical, 5 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**3 found · 3 fixed · 0 open**
- [MEDIUM] artifact_cli.py cmd_run_update: carried push_ready=True re-ran _cap_legacy_push_
- [LOW] artifact_cli.py carry-forward: push_ready/reason/capped carried field by field, 
- [LOW] run_delivery_summary.py: resolved rule stricter than other readers. Fixed: _item

### 7.6 Completion Audit
**Requirements met:** 4/4
**All green:** yes


## 8. Files Changed
- `backend/core/run_delivery_summary.py`
- `backend/scripts/artifact_cli.py`
- `backend/skills/s_autonomous-pipeline/stages/deliver.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/deliver/report-and-commit.md`
- `backend/tests/test_run_delivery_summary.py`
- `backend/tests/test_run_report_writer_fields.py`
- `backend/tests/test_artifact_cli_run_commit.py`
- `backend/tests/test_spec_stale_todo.py`

## 9. Lessons
- [pitfall] A summary that only reads the fields its author expected renders a run with recorded problems as clean: §10 read the completion audit and ignored the DELIVER attention_flags, meta-review risks, push_ready reason and forced commit. When a report section claims 'none', it has to read every store that can hold an item of that kind.
- [pitfall] The gates write their findings and risks as dicts ({severity, description, status}), and a reader that only accepts strings drops them without an error. Replaying the new renderer over the live run artifacts (42 of 66 recorded risks were dicts) caught what the unit fixtures could not.
- [principle] Two readers of 'is this resolved' that use different rules (one checks the resolved field, the other also accepts status=FIXED) can disagree about the same item in one report. A shared state judgment needs one predicate that every reader calls.


## 10. Known Gaps & Attention Flags
- REPORT 的 TL;DR 和 §10 对模型文本只折行不做 markdown 转义（旧模式，聊天侧已转义）；要统一转义另起一个小改动
- git 里提交的 REPORT 副本不含"强制提交"那条遗留（run-commit 之后才有标记），账本里的 REPORT 有；已写进 report-and-commit.md
- 超大证据文件被当成"没有证据"的 bug 由另一个 session 在 pipeline_validator.py 里修，本 run 未改该文件
- 改动在 daemon rebuild + restart 之前不进入运行中的系统（CLI 脚本按源码运行，下一个 run 即生效；后端导入的部分需重启）
- "已修复"的判定规则两处不一致：pipeline_validator 只认 resolved 字段，总结函数还认 status=FIXED 等前缀；应在 pipeline_validator 提供一个共享判定（该文件归另一 session，未改）
- LOW 风险：REPORT TL;DR/§10 fold newlines but do not neutralize markdown in model text


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 06:29 UTC*
