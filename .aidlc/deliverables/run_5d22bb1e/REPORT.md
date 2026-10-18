# Autonomous Pipeline Report: PLAN rejects directory and glob test refs in exit criteria

**Run ID:** run_5d22bb1e | **Project:** SwarmAI | **Profile:** bugfix
**Date:** 2026-10-18 06:55 UTC | **Duration:** 33.2 min | **Tokens:** 0 (估算 / estimate)
**Gate 1:** WARN | **Files:** 4 | **Tests:** 619 run, 0 failed

## TL;DR
**解决了什么问题**
- 原来：PLAN 接受 kind=test 的退出标准引用一个目录或 glob（如 desktop/src/components/file-viewer/__tests__/），但 TEST 判定只按文件路径精确匹配，这条标准永远无法满足，run_2e1a4548 和 run_cb0e98ad 都只能 --force 提交
- 现在：PLAN stage-complete 直接拒绝目录/glob 引用，报错告诉你改成逐个测试文件或 mode:regression；所有能被满足的引用形状（文件、nodeid、参数化、vitest 文件、带方括号的路由目录、runner 合成 nodeid、mode:<m>）照常通过

**怎么做的**
- pipeline_validator.validate_exit_criteria 新增引用形状检查：结尾 /、无扩展名、glob 字符、或在源码根下实际是目录 → E_EXIT_CRITERIA
- mode:<m> 和 runner 命名空间（desktop::tsc、import:: 等）走同一个豁免判断，不误杀
- plan.md / exit-contract.md 写明只接受文件级引用
- 33 个新测试 + 9 个 mutation 全部被杀；用真实 PLAN 入口 --check 验证过两条历史坏引用都被拒

**遗留问题**（7 条，见 §10）

## 1. Requirement
PLAN 阶段直接拒绝目录引用：exit_criteria / acceptance_criteria 里引用的测试路径如果是目录（而不是具体测试文件），PLAN 落盘时就拒绝并给出清晰报错。背景：run_2e1a4548 的一条退出标准引用了 desktop/src/components/file-viewer/__tests__/ 目录，validator 只按文件路径精确匹配，这条标准永远无法满足，最后只能 --force 提交。

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起pipeline 让 PLAN 阶段直接拒绝目录引用

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | user-story | user-stated: XG: 起pipeline 让 PLAN 阶段直接拒绝目录引用 | confirmed | ● | Given a strict-profile PLAN artifact with exit_criteria [{kind:test, ref:'desktop/src/components/file-viewer/__tests__'}], when stage-complete --stage plan runs, then it exits with an Exit criteria error naming exit_criteria[0].ref and nothing is published |
| FR-2 | constraint | derived: survey: 24 of 26 historical distinct test refs are .py/.ts/.tsx files or mode:verify | confirmed | ● | Given refs file.py, file.py::t, desktop/src/a.test.tsx, mode:regression, when validate_exit_criteria runs, then it returns no error for them |

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
1. AC1: PLAN stage-complete on a strict profile fails with an Exit criteria error naming exit_criteria[i].ref when a kind=test ref names a directory: a trailing slash, or a path part (before ::) whose last segment has no file extension (e.g. desktop/src/components/file-viewer/__tests__).
2. AC2: the same check rejects a kind=test ref whose path part contains a glob metacharacter (* ? [), since one result file can never equal a pattern.
3. AC3: every ref shape the judge can satisfy still passes: file.py, file.py::test_fn, file.py::Class::test[param], desktop/src/x.test.tsx, ./relative paths, and mode:<m> refs.
4. AC4: the error message tells the planner what to write instead (name each test file, or use mode:regression for whole-scope coverage).
5. AC5: the PLAN card (stages/plan.md exit-contract step) states that kind=test refs are file-level or nodeid-level, never a directory or glob.


## 3. Design & Approach
**Research Findings (THINK):**
- validate_exit_criteria (pipeline_validator.py:4612) is the single PLAN-time check of exit_criteria, called only from artifact_cli._stage_complete_errors at :8298; it checks a test ref for non-empty and no <...> placeholder only.
- The judge matches a non-mode test ref against one result file or nodeid (_evidence_matches/_path_eq, :4807-4817): equality or /-suffix. A directory or glob ref can never be satisfied, so the only correct moment to catch it is PLAN.
- Survey of every design_doc exit_criteria in the workspace: 26 distinct test refs; 22 are .py/.ts/.tsx files or nodeids, 2 are mode:verify, 2 are directories (run_2e1a4548 X2, run_cb0e98ad X2), and both directory runs ended with the criterion unmet and a forced run-commit.
- artifact_cli already has _evidence_roots() (:7970) = SWARM_TEST_REPO_ROOT, the source repo, the workspace — the same roots judge_exit_criteria uses — so the PLAN check can also ask the filesystem whether a ref is an existing directory.

**Alternatives Evaluated:**
- QUALITY — effort: S (1 session)
- SIMPLICITY — effort: S (under 1 session)
- FLEXIBILITY — effort: M (1 session)

**Chosen Approach:** Add _is_mode_ref(ref) (raw ref.startswith('mode:'), the judge's exact predicate) and use it in BOTH _judge_test_criterion and the new _test_ref_shape_error(ref, roots). validate_exit_criteria gains keyword-only roots=None; None -> _exit_criteria_roots() = SWARM_TEST_REPO_ROOT, the repo containing pipeline_validator.py, and _get_workspace() (the same list as artifact_cli._evidence_roots, which the validator cannot import). artifact_cli.py is not touched (another session has uncommitted edits).

**Success Criteria:**
- the repro ref 'desktop/src/components/file-viewer/__tests__' returns an Exit criteria error from validate_exit_criteria
- test_exit_criteria_ref_shape.py, test_stage_boundary_commands.py and test_artifact_cli_schema.py pass through pipeline_test.py
- mutation of the suffix check and of the is_dir check is killed

**Always:**
- only kind=test refs are checked; probe/mutation/manual unchanged
- stage only this run's hunks of the files it touches

**Never:**
- loosen the judge (_path_eq / _judge_test_criterion)
- touch another session's uncommitted changes


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Reject at PLAN instead of teaching the judge prefix matching | mechanical | user directed; prefix matching would let one passi |
| evaluate | Profile bugfix | mechanical | clear defect with a reproduced root cause |
| evaluate | Also reject glob refs | taste | same class as a directory: a pattern can never equ |
| think | QUALITY approach with filesystem check | taste | closes the dotted-directory hole for a few stat ca |
| think | No replan repair path for legacy directory refs | taste | no in-flight run carries one; PLAN now prevents ne |
| plan | Default roots computed inside pipeline_validator instead of  | taste | artifact_cli.py carries another session's uncommit |
| plan | Mutation instead of probe for X4 | taste | the risk is a silently weakened check; mutation pr |
| plan | Gate-1 WARN folded in: drop [ from glob set, shared _is_mode | mechanical | each item verified against pipeline_validator.py;  |
| build | Glob set is * and ? only | taste | Gate-1: [ appears in real route folders (app/[id]/ |
| build | artifact_cli.py left untouched | mechanical | default roots computed in the validator; another s |
| review | Fix R1 in REVIEW rather than defer | mechanical | two-line rule plus a test; same class as the run's |
| test | 3 pre-existing test_review_completeness_check failures logge | mechanical | runner classified them pre-existing (fail on the b |

**Summary:** 6 mechanical, 6 taste, 0 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**2 found · 1 fixed · 1 open**
- [MEDIUM] backend/scripts/pipeline_validator.py _test_ref_shape_error: the runner's synthe
- [LOW] A legacy run already holding a directory ref that replans after BUILD/TEST canno

### 7.6 Completion Audit
**Requirements met:** 6/6
**All green:** yes


## 8. Files Changed
- `backend/scripts/pipeline_validator.py`
- `backend/tests/test_exit_criteria_ref_shape.py`
- `backend/skills/s_autonomous-pipeline/stages/plan.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/plan/exit-contract.md`

## 9. Lessons
- [pitfall] A PLAN-time shape check for refs the judge consumes must be derived from EVERY producer of satisfiable refs, not from the planner's usual spelling: the runner's synthetic nodeids (desktop::tsc, import::<module>) have an extensionless, directory-named path part, and only a correctness reviewer reading pipeline_test._res caught that the new check rejected them. Grep the producers of the evidence the judge reads before writing the reject rule.
- [pitfall] Tightening an input validator that also runs on replan creates a deadlock for legacy state when a replan lock forbids changing old entries: the old value fails the new check and cannot be edited. Before adding a reject rule to a validator, check whether any lock freezes previously-accepted values, and survey live state for values the new rule rejects.
- derive a PLAN-time ref check from every producer of satisfiable refs
- survey live state before tightening a validator that a replan lock freezes


## 10. Known Gaps & Attention Flags
- 未修复的 LOW 发现：A legacy run already holding a directory ref that replans after BUILD/TEST cannot keep or change it (replan lock in artifact_cli._replan_errors). Accepted: zero running/paused runs carry one (survey + meta-review), and PLAN now prevents new ones; the clean fix lives in artifact_cli.py, which has another session's uncommitted edits.
- Legacy runs only: a run whose PLAN already holds a directory/glob test ref and must replan after BUILD/TEST is deadlocked by the replan lock; none exist today.
- 3 pre-existing failures in test_review_completeness_check.py (fail on the base tree) logged in IMPROVEMENT.md Known Issues.
- Local commit only; nothing pushed.
- LOW 风险：replan deadlock for a legacy run holding a directory ref
- LOW 风险：_exit_criteria_roots mirrors artifact_cli._evidence_roots
- LOW 风险：a dotted directory in a bound repo outside the roots is not caught by the filesystem probe


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 06:55 UTC*
