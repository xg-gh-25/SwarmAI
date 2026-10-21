---
title: Pipeline gate 与 expert review 简化
date: 2026-10-21
created: 2026-10-21
updated: 2026-10-21
project: SwarmAI
status: DRAFT — 待 XG 确认 §9 的待决问题
related: pipeline-review-convergence.md（N1–N5，本文收窄其轮数）；pipeline-run-worktree.md（run_owner_gate 的 token 机制）；pipeline-run-budget.md（轮数预算）
---
# Pipeline gate 与 expert review 简化

> **目标**：守住安全底线和真 bug 的拦截率，把每个 run 的 expert 派出次数从平均 27 次降到 4–6 次，把 gate 的流程摩擦和误伤类 deny 降掉 80% 以上。
>
> **不在范围**：危险命令审批、外部副作用审批、AskUserQuestion、worktree 隔离、防挂起三件套。这些不动（§2.1）。

## 0. 一页摘要

| 编号 | 改什么 | 现在 | 改成 | 风险 |
|---|---|---|---|---|
| G1 | `pipeline_test_gate` | 在 run 内直接跑 pytest/vitest/tsc 一律 deny，改道到 runner；heredoc 里出现 `pytest` 字样也 deny | 只保留"runner 不许放进循环、不许一次调用跑两遍"的 deny。直接跑测试改为放行并附一句提示 | 低：落地只认 runner 的证据，直接跑的结果本来就不算数 |
| G2 | `run_owner_gate` 的"必须是第一条命令" | 发 token 的 CLI 调用前面不能有任何命令 | CLI 调用可以在 `&&`/`;` 链的任意位置，前面的段不能有函数定义、alias、`source`/`.`、`eval`、对 CLI 脚本的写入 | 中：需要 security expert 复核 §5.2 的论证 |
| G3 | `audit_marker_write_gate` | `cd` 进审计目录或只读的 glob 也 deny | 只读命令放行 | 低 |
| G4 | gate 治理 | 加 gate 不需要证据 | 新 gate 要引用真实事故；deny 分支标类别，`routing`/`format` 类每月按误伤复审 | 无 |
| E0 | 两套 Gate-2 并存 | legacy（3 轮 + meta-review + root-cause）和 deterministic（finding ledger，2 轮）同时在用 | 只留 finding ledger 这一套 | 中：迁移量最大 |
| E1 | Gate-2 名单 | 最多 7 个 specialist，按文件模式触发 | 默认只派 correctness；有明显安全风险时才加 security + red-team。其他 lens 并进 correctness 的检查清单 | 中：部分 MEDIUM 可能漏 |
| E2 | gate0 / gate1 / meta-review | 三个都常态派出 | 删 gate1 和 meta-review；gate0 只在 bugfix 或需求有两种读法时派 | 低 |
| E3 | REVIEW 阶段 | 再派 spec-compliance / code-quality / ux-test / security | 不派 sub-agent，只做确定性的 AC→测试检查 | 中 |
| E4 | 轮次与去重 | legacy 3 轮；同一 bug 被多个 expert 各报一次 | R1 全员，R2 只派"有发现被修过"的 expert、只看修复 diff；修之前按位置去重 | 低 |
| E5 | `push_ready` | 每条 finding 都要被 break test 证明才算 ready；expert 没输出也压低 | 只有未解决的 HIGH 才压低；没输出、untestable 的 MEDIUM/LOW 记为剩余风险 | 中：需新增 `severity` 字段 |

预计效果：每个 run 的 expert 派出 27 → 普通改动 1–2、有安全风险的改动 3–5，子 agent 时间和 token 减少约 75–80%；gate deny 从 2 天 124 条降到约 30 条，剩下的都是真拦截或 worktree 保护。

## 1. 证据

数据源：`~/.swarm-ai/logs/gate_decisions.jsonl`（只有 10-20、10-21 两天，124 条 deny/ask）、10 月 218 个 run 的 `run.json` 和 REVIEW.md、72 个有 transcript 的 run 的 sub-agent 记录。

### 1.1 复杂度

| 项 | 量 |
|---|---|
| pipeline/gate 代码 | 约 4 万行：`artifact_cli.py` 1.56 万、`security_hooks.py` 7426、`pipeline_validator.py` 6023、`pipeline_test.py` 5437、gate2_*/finding_ledger/review_round_gate 约 1575 |
| 相关测试 | 96 个文件，约 4 万行 |
| 阶段文档 | 83 个 ref 文档，200KB |
| hook/gate | 32 个 |
| 10 月提交 | 445 个里 209 个改的是 pipeline/gate 本身 |
| Gate-2 流程 | 两套并存。10-18 以来 66 个 run 走 legacy，15 个走 deterministic（direct profile 默认 deterministic，其他 profile 默认 legacy） |

### 1.2 Gate deny 分类（2 天）

| gate | deny | 拆分 |
|---|---|---|
| `run_owner_gate` | 36 | 31 次"CLI 必须是第一条命令"。拒绝的命令几乎都是 `python3 /tmp/<run>/fix_plan.py && artifact_cli.py stage-start ...`，即先改 plan.json 再调 CLI。5 次 claim 被拒（真规则） |
| `pipeline_test_gate` | 46 | loop 11、repeat 1（真规则）；pytest 16、tsc 3、vitest 2（改道）；python stdin 13（几乎都是用 `python3 -I - <<'EOF'` 改文件，正文里含 `pytest` 字样，不是在跑测试） |
| `worktree_write_gate` | 22 | 主 checkout 写入 9、绝对路径写入 4、`git stash` 3、`git restore`/`git apply` 4、ref/worktree 管理 2。都是真保护 |
| `background_command_guard` | 7 | 真规则（防挂起） |
| `audit_marker_write_gate` | 4 | 全是只读：`cd` 进目录、glob 列文件 |
| `bash_syntax_guard` | 2 | 真拦截 |
| `active_run_commit_gate` | 2 | 真拦截 |
| `pytest_command_guard` | 2 | 真拦截 |
| `dangerous_command_gate` | 1 | `git push --force origin main`，真拦截 |
| `expert_budget_gate` / `review_round_gate` | 各 1 | 真规则 |

### 1.3 Expert 派出与产出（10 月）

| expert | 派出 | 发现 | HIGH | 每次派出的 HIGH 率 |
|---|---|---|---|---|
| correctness | 338 | 135 | 27 | 8% |
| red-team | 176 | 97 | 12 | 7% |
| security | 308 | 61 | 10 | 3% |
| integration | 123 | 39 | 6 | 5% |
| meta-review | 194 | 68 | 4 | 2% |
| gate1-skeptic | 167 | 10 WARN，0 BLOCK | — | 0 |
| gate0-skeptic | 121 | 1 WRONG-FRAME | — | — |
| spec-compliance / code-quality / ux-test | 262 | 多为注释残留类 LOW | — | 低 |

- 能归属到 expert 的 HIGH 共 71 个，69% 来自 correctness、red-team、security。另有 37 个 HIGH 没记 expert。
- 在 12 个 gate2-v2 run 里，同一个问题常被 2–3 个 expert 各报一遍。`run_ef9eacfb`：5 个 expert 交回 6 条，实际 2 个问题。
- 41 份 REVIEW.md 有 38 份 push-ready=no。主要原因是流程状态："expert gave no usable output"（`finding_ledger.record` 在重派 1 次仍无输出时写入一条 advisory finding，而 `push_ready()` 要求所有 finding 都是 `proven`）、"no review round left"、"untestable"。
- 10-20、10-21 有 4 个 run 在 REPORT 里写了"pipeline 坏了，手工落地"。

### 1.4 局限

- gate 日志只有 2 天，类别比例可能偏。
- legacy 格式的 `resolved` 几乎全是 true，分不清修复和驳回。
- 重复率只来自 12 个 run。
- 一半 run 在改 pipeline 自己，finding 偏向 pipeline 代码。

## 2. 原则

### 2.1 不动的底线

下面这些 gate 便宜、误伤少、守的是不可逆或跨 session 的损害，本文不改：

| gate | 守什么 |
|---|---|
| `dangerous_command_gate` / `external_approval_gate` | 危险命令和外部副作用需要人批准 |
| `ask_question_gate` | 需求读不懂时问人 |
| `session_link_gate` / `audit_marker_write_gate` 的写入拦截 | 其他 gate 依赖的身份和审计记录 |
| `worktree_write_gate` | 共享 checkout 不被别的 session 的改动污染 |
| `active_run_commit_gate` / `run_amend_gate` / `direct_commit_gate` | run 拥有的文件只经 run-commit 落地 |
| `background_command_guard` / `bash_syntax_guard` / `pytest_command_guard` | 防挂起 |
| `expert_type_gate` / `expert_budget_gate` | expert 只读、有调用上限 |

### 2.2 判断一个 gate 或 expert 该不该留

1. **它拦住过真东西吗？** 用 `gate_decisions.jsonl` 和 finding 记录说话，不用"理论上可能"。
2. **它和别的机制重复吗？** 落地检查只认 runner 证据，那在执行前再拦一道就是重复。
3. **它的误伤成本是谁付的？** 每次误伤是一个模型回合（约 20–60 秒）。误伤多到人绕开 pipeline，等于这个 gate 让质量变差了。
4. **它的结论能被机器判定吗？** 能判定的（AC 有没有对应测试、计划字段齐不齐）交给 validator，不派模型。

## 3. Gate 改动

### G1 `pipeline_test_gate`：只拦循环和连跑

**现在**（`backend/core/security_hooks.py` `create_pipeline_test_gate`，约 4505 行起）：
1. `_runner_loop_kind` 命中（runner 在 for/while/until 里或一次调用跑两遍）→ deny，与 run 无关。
2. `_classify_pipeline_test_command` 命中 pytest / vitest / tsc / make test / hang triage / `python stdin pytest` → 本 session 有活跃 run 时 deny，给出 runner 命令。

**改成**：
1. 第 1 条不变。
2. 第 2 条从 deny 改为 approve，带 `additionalContext`："本 run 的落地只认 `pipeline_test.py` 的结果，这次直接运行不计入证据：<runner 命令>"。
3. 删掉 `python stdin pytest` 这一族（`_py_stdin`、`_mentions_test_runner`、`_code_can_execute`、`_test_gate_hint`）。它唯一的产出就是误伤。
4. hang triage（`ps | grep pytest`、`pkill -f pytest`）也改为放行带提示：`pkill` 杀错进程的风险由 `dangerous_command_gate` 管。

**为什么安全**：`pipeline_validator.landing_test_errors` 和 `_deliver_push_ready_bound` 只读 runner 写的 `latest-<mode>.json`（带 `tree_digest`）。直接跑的 pytest 不产生这个文件，不能让 run 落地，也不能让 push_ready 变真。直接跑最多浪费时间，而单次调用已被 `pytest_command_guard` 限时。

**放弃的东西**：runner 会在调用前后对比仓库状态，恢复测试写坏的 tracked 文件；直接跑的测试没有这层保护，写出的改动会留在 worktree 的 diff 里，可能被当成本 run 的改动提交。现有防线是 conftest 里对已知副作用调用的 autouse stub。G1 另加一条：放行直接测试命令时，gate 记下当时 `git status --porcelain` 的指纹；runner 下一次调用时对比，期间新出现且不在计划路径里的改动列为 `repo_mutations.unexplained`，进 REPORT。

**删除的测试**：`tests/test_pipeline_test_gate*.py` 里断言 pytest/vitest/tsc/stdin deny 的用例改为断言 approve + 提示；loop/repeat 用例保留。

### G2 `run_owner_gate`：CLI 调用不必是第一条命令

**现在**（`backend/core/run_owner_gate.py` `_token_binding`）：需要 token 的 CLI 调用（stage-start、deliver 的 stage-complete、run-update 到终态）必须是整行第一条简单命令，前面只允许 `cat > FILE <<'EOF'` 写文件。原因是 token 通过 `updatedInput` 插成 `SWARM_HOOK_TOKEN=<nonce>` 前缀，必须保证同一行里先执行的命令没法改写 CLI 脚本或劫持调用。

**改成**：CLI 调用可以出现在顶层 `&&` / `;` 链的任意位置（仍然只能有一个需要 token 的调用），前面的段满足下列全部条件：
- 不含命令替换、子 shell、进程替换（沿用 `_SUBST`）；
- 不是函数定义、`alias`、`source`/`.`、`eval`、`exec`、`export`/`set`/`unset`、`enable`、`builtin`、`command`、`hash`；
- 没有对 `_SCRIPTS`（`artifact_cli.py`、`pipeline_test.py`）或 CLI 所在目录的重定向写入；
- 前面的段不设置 `PATH` 或其他影响命令查找的变量，CLI 调用本身用绝对路径的解释器和脚本（`_single_form` 打印的就是这种形式）。

token 前缀仍然只加在 CLI 那一条简单命令上，其他段拿不到。

**安全论证**见 §5.2：这个限制想挡的"同一行里先执行的程序改写 CLI 脚本"，在两次 Bash 调用之间同样能做到（上一次调用里跑 `python3 /tmp/x.py` 改 `artifact_cli.py`，gate 只看命令文本，看不到脚本内部）。所以"第一条命令"挡不住这个威胁，只多了 31 次往返。真要防，应该在 CLI 侧校验自身完整性（§5.2 的 G2b）。

**测试**：`tests/test_run_owner_gate*.py` 增加"前置 python 准备脚本 + CLI"放行、"前置函数定义/alias/eval"拒绝、"前置重定向写 CLI 脚本"拒绝。

### G3 `audit_marker_write_gate`：只读放行

**现在**（`security_hooks.py` `create_audit_marker_write_gate`，约 6152 行起）：`cd` 进 `state/pipeline_agent_audit`、或 python 代码里出现该目录名就 deny。4 次 deny 全是只读。

**改成**：
- `cd` 不再算写入；只有之后的段里出现写操作（重定向、`rm`/`mv`/`cp`/`touch`/`tee`/`sed -i`/`truncate`、解释器的写调用）才 deny。
- python 代码里出现目录名时，只有同时出现 `open(...,'w'|'a'|'x')`、`write_text`、`write_bytes`、`unlink`、`rename`、`replace`、`rmtree`、`shutil.` 才 deny。
- 无法解析的命令仍 fail-closed（不变）。

### G4 gate 治理

1. 新 gate 的 PR/run 必须在设计里引用至少一个真实事故（日志行、run id 或 commit）。
2. 每个 deny 分支在原因文本里带一个类别标签：`safety`（挡危险或不可逆操作）、`isolation`（挡跨 session 污染）、`routing`（改道到另一个入口）、`format`（命令写法不符）。`gate_audit.py` 增加 `--by-kind`，按分支和类别统计。
3. `routing` 和 `format` 类分支每月复审一次：看 deny 样本，误伤占多数的降为 warn；连续两个月仍以误伤为主就删除。`safety` 和 `isolation` 类不走自动降级。
4. `gate_decisions.jsonl` 目前只有 2 天，先保留 60 天再做第一次复审。

## 4. Expert 改动

### E0 只留一套 Gate-2：finding ledger

**现在**：
- legacy：`core/gate2_convergence.py`（3 轮 + 根因复盘 + 退回 PLAN 重设计一次）、`core/review_round_gate.py`（`MAX_ROUNDS = 3`）、meta-review、`gate2-recheck`。
- deterministic：`core/finding_ledger.py`（`BUDGETS["gate2_rounds"] = 2`，finding 靠 runner 密封的 break test 关闭）、`gate2-next`。
- 选择逻辑：`artifact_cli.py` run-create 里 `_convergence_for(args)`，direct 默认 deterministic，其他默认 legacy；run 落到共享 checkout 时退回 legacy。`pipeline_validator.model_verdicts_decide` 是唯一的模式判定。

两套共存意味着每个 gate2 相关改动都要写两遍、测两遍，阶段文档也要分两路讲（`deliver-convergence.md`、`gate2-findings.md`、`gate2-deterministic.md`）。

**改成**：所有 profile 都走 finding ledger。难点是共享 checkout：run 落到共享 checkout 时现在退回 legacy，因为 break test 要在 R1 时的代码上跑旧侧，而共享 checkout 没有 R1 快照。`worktree_mode` 已经默认开启（`core/run_worktree.py` `mode_enabled`），共享 checkout 只在建 worktree 失败时出现（`pipeline_worktree._shared`）。两个办法，选一个（§9 Q1）：
- (a) 去掉共享 checkout 回退：建 worktree 失败时 stage-start 报错，给出原因和重试命令；
- (b) 保留回退，R1 时由 runner 在 `$KIROCREW_SCRATCH` 下 `git worktree add --detach <R1 commit>` 建一次性快照目录，旧侧在那里跑。

(a) 更简单，并且删掉一整条很少走到的分支。（顺带：AGENTS.md 里"worktree 模式默认关闭"的说法已过时，实施时一起改。）

**删除**：`gate2_convergence.py`、`gate2_fixspec.py` 的 legacy 分支、`review_round_gate.py` 的多轮计数（保留并行派出检查）、`artifact_cli.py` 的 `gate2-recheck` 和 `_refuse_legacy_gate2`、`--convergence legacy` 选项。进行中的 legacy run 按原流程跑完，新 run 一律 deterministic。

### E1 Gate-2 名单：两个固定 + 一个按需

**现在**（`artifact_cli.py` `_dispatch_experts`、`_EXPERT_FILE_TRIGGERS`）：correctness 必派；strict profile 上 security 必派；performance / api-contract / operational 按文件模式；integration 按新文件；red-team 在 >200 行或有 HIGH 时加；refactor 用 correctness + api-contract + integration + red-team；PLAN 的 `required_experts` 取并集。

**改成**（非 small change、源码改动 ≥ 50 行时）：

| expert | 何时派 |
|---|---|
| correctness | 总是 |
| security | 有明显安全风险（见下），任何行数 |
| red-team | 有明显安全风险，且源码改动 ≥ 50 行 |

**明显安全风险**用一个新的 `_SECURITY_RISK_RE` 判定，比现在的 `_REVIEW_SENSITIVE_RE` 窄（后者还包含 config、settings、schema、handler、migration 等，大多不是安全问题）：
- 认证和凭据：`auth`、`login`、`token`、`secret`、`credential`、`permission`；
- 权限边界：`security_hooks.py`、`hook_builder.py`、`*_gate.py`、`permission_manager.py`、`file_access`；
- 命令执行和外联：新增或修改 `subprocess`、`shell=True`、`os.system`、出站 HTTP 请求的代码（按 diff 内容判定，不按文件名）；
- 每个 session 的系统提示词来源：`_GOVERNED_CONTEXT_RE`（`backend/context/`）；
- CORS、监听地址、端口。

不再按"> 200 行"或"有 specialist 报了 HIGH"加 red-team。

- `correctness.md` 的检查清单并入 integration（新公共符号有没有调用方、调用方有没有一起改）、api-contract（前后端字段映射、Pydantic 字段）、state-machine、concurrency、operational（后台任务、调度、daemon）、performance（循环里的 IO、N+1）和 spec-compliance（每条 AC 是否真的实现）这几项。并进来的每项写成一行可检查的问题，不搬整份文档。
- 删除 `_EXPERT_FILE_TRIGGERS` 中 performance、api-contract、operational 三项。对应的 `backend/experts/code/*.md` 保留文件，标为"已并入 correctness"，以便 `required_experts` 引用旧 id 时映射。
- `required_experts` 只接受 `correctness`、`security`、`red-team`；声明了 security 或 red-team 但改动没有安全风险时，按声明派（PLAN 的显式判断优先）。旧 id 映射到 correctness，并在 stage-start 输出里说明映射。
- small change 规则（≤ 20 行、无新代码文件、无敏感路径 → 不派）和 docs-only 规则不变。

**为什么这样选**：能归属的 HIGH 里 69% 来自这三个，其中 correctness 一个就占 27/71。被并掉的 integration 派了 123 次出 6 个 HIGH，它的检查点本来就是 correctness 应该看的"调用方"。red-team 和 security 是攻击者视角，只在改动碰到安全边界时才有用武之地，普通改动不派。

**放弃的东西**：red-team 现在报的 HIGH 里，有一部分不在安全边界上（例如边界输入导致崩溃）。这类问题改由 correctness 清单里的"异常输入和边界值"一项覆盖。落地后按 §7 的 HIGH 指标观察，掉得多再放宽触发条件。

### E2 删 gate1-skeptic 和 meta-review，gate0 按需

| 项 | 数据 | 处理 |
|---|---|---|
| gate1-skeptic（BUILD 前审计划） | 167 次，0 次 BLOCK，10 次 WARN | 删除。计划的结构检查（AC 每条有测试、`implementation_constraints` 有读者）已经在 `pipeline_validator` 里，只把 WARN 里能机械判定的两三条补成 validator 检查 |
| meta-review（Gate-2 之后审 finding 和修复） | 194 次，68 个发现，4 个 HIGH，和其他 expert 重叠 | 删除。它查的"修复有没有引入新问题"由 E4 的 R2 复查承担 |
| gate0-skeptic（EVALUATE 后审需求理解） | 121 次，1 次 WRONG-FRAME | 只在 profile=bugfix，或 EVALUATE 的 understanding 记录了 ≥ 2 种读法时派。其他情况跳过 |

**删除**：`_write_gate1_prompt`、`_gate1_skip`、`_gate1_points*`、`_recorded_gate1`、`_is_relaxed_for_gate1`、`stages/ref/build/gate1.md`、meta-review 的 spawn 和 `adversarial_review.meta_review` 字段检查、`_NOT_IN_WAVE` 里的 `meta-review`。

### E3 REVIEW 阶段不再派 sub-agent

**现在**（`artifact_cli.py` `_review_dispatch`）：总派 spec-compliance；fan-out 时（>3 个源文件、>100 行或敏感路径）再派 code-quality、前端文件加 ux-test、Gate-2 不派 security 时加 security。REVIEW 之后 DELIVER 的 Gate-2 再审一遍同一份代码。

**改成**：REVIEW 阶段只做确定性检查：
1. AC→测试映射：每条 AC 在 `ac_coverage` 里有测试，测试文件存在，测试名在 runner 的收集结果里（`pipeline_validator` 已有前两步，补第三步）。
2. 改动文件和计划声明的范围是否一致（`files_touched` vs 计划的 `paths`），超出范围的列出来。
3. 原先由 REVIEW 负责的"小改动敏感路径的 security 审查"（Gate-2 在 < 50 行时不派人）移到 Gate-2：敏感路径的改动无论行数都派 security（E1 表里已写）。

spec-compliance 的"AC 是否真的实现"由 correctness 的检查清单承担（E1）。code-quality 和 ux-test 的产出大多是注释残留类 LOW，不再单独派。

**涉及**：`stages/review.md`、`stages/ref/review/*.md`（7 个文件，约 48KB）缩成一张卡片；`_review_dispatch`、`_write_review_prompts`、`_review_fanout_applies` 删除或只留给 small-change 判定用。

### E4 轮次与去重

**现在**：ledger 是 R1 → 修 → R2 → 修 → prove；R2 的名单默认等于 R1。legacy 是 3 轮。同一问题被多个 expert 报，台账按 id 去重，不同 expert 的 id 不同，所以重复留在台账里，各修各的。

**改成**：
1. **finding 带位置**：expert 输出的每条 finding 必须有 `file` 和 `line`（`MODEL_FIELDS` 加 `line`、`severity`）。
2. **修之前聚类**：ledger 在一轮所有 expert 都交回后，把同一文件、行号相差 ≤ 10 的 finding 合成一个 cluster。修复按 cluster 写一份 fix spec（沿用"一轮发现统一评估、按原因分组、一次修完"的规则）。
3. **R2 只派相关的人、只看修复 diff**：R2 名单 = 在 R1 有 finding 被修的 expert；输入 = R1 之后的 diff 加它自己在 R1 的 finding。R1 没有发现的 expert 不进 R2。R1 没有任何发现就没有 R2（已有行为）。
4. 上限不变：R2 之后不再派 expert。

### E5 `push_ready` 只看未解决的 HIGH

**现在**（`finding_ledger.push_ready`）：已落地，且每条 finding 都是 `proven` 才 ready。expert 无输出时写入的 advisory finding、`untestable`、`not_reproduced` 都会让它为假。所以 38/41 是 no，这个信号已经没人看。

**改成**：
- finding 增加 `severity`（HIGH / MEDIUM / LOW），由报告它的 expert 给出，作者不能改。cluster 的 severity 取最高。
- push_ready 为假的条件只有：
  1. 没落地；
  2. 有 HIGH cluster 的状态是 `open` 或 `invalid_test`；
  3. 有 HIGH cluster 被声明为 `untestable`，且没有人工确认（REPORT 里列出，等人看）；
  4. correctness 在重派后仍无输出（没人审过等于没审）。
- 其他情况都 ready，MEDIUM/LOW 的未证明 finding、`not_reproduced`、其他 expert 的无输出列在 REPORT §10"剩余风险"里。
- `_deliver_push_ready_bound` 里的测试证据条件（最终代码上有全量 verify/regression、证据不早于最后一次修改）不变。

**风险**：expert 可能把 HIGH 报低。缓解：correctness 和 red-team 的提示词里给出 HIGH 的判定标准（数据丢失、安全绕过、崩溃或挂起、需求未实现），并在 REPORT 里按 severity 列全部 finding，人可以复查。

## 5. 安全论证

### 5.1 每项改动可能漏掉什么

| 改动 | 可能漏掉 | 谁兜底 |
|---|---|---|
| G1 | 模型直接跑 pytest 却以为测试过了 | 落地和 push_ready 只认 runner 证据（`landing_test_errors`、`_deliver_push_ready_bound`）；放行时的提示明确说"不计入" |
| G2 | 同一行里前置命令改写 CLI 脚本后再拿 token | 见 5.2；跨调用的同类攻击本来就存在，本改动不扩大攻击面 |
| G3 | 只读识别有漏洞，把写操作当只读 | 写操作关键词列表；无法解析的命令仍 fail-closed |
| E1 | performance、api-contract、operational 类 MEDIUM | 并入 correctness 清单；api-contract 的前后端字段问题由 TS 编译和 `toCamelCase` 测试兜一部分 |
| E2 gate1 | 计划层面的错误（方向错、漏需求） | gate0 在 bugfix 和多读法时仍派；PLAN 的 AC↔测试由 validator 检查；correctness 检查"每条 AC 是否实现" |
| E2 meta-review | 修复引入的新问题 | E4 的 R2 只看修复 diff，正对这个问题 |
| E3 | spec-compliance 抓的"需求没实现" | 同样的问题在 12 个 v2 run 里被 correctness/red-team 也报过（"需求 3 没实现"报了 3 遍） |
| E5 | 被报低的 HIGH | 提示词里的 HIGH 标准；REPORT 列出所有 severity；`push_ready` 只是"建议 push"，push 仍是人决定 |

### 5.2 G2 的 token 论证

token 机制要证明的是"这次 CLI 调用前，PreToolUse hook 真的跑过"，用来挡没有 hook 的子进程（例如 `claude --print --bare`）冒充 owner 改 run 状态。"必须是第一条命令"额外挡的是"同一行里先运行的程序改写 `artifact_cli.py`，让拿到 token 的是被改过的脚本"。

这个额外限制挡不住同类攻击：在上一次 Bash 调用里运行 `python3 /tmp/x.py`，脚本内部改 `artifact_cli.py`，gate 只看命令文本看不到；下一次调用再以第一条命令的形式调 CLI，照样拿到 token。所以第一条命令的限制只在"同一行"这个窄场景生效，代价是 2 天 31 次往返。

G2 放宽后，前置段仍禁止函数定义、alias、`source`、`eval`、`PATH` 改写和对 CLI 脚本的重定向写入，即命令文本层面能看到的劫持方式都还挡着。

**G2b（建议一起做，单独评估）**：run-create 时把 CLI 脚本的 sha256 记进 `run.json["cli"]`；CLI 每次烧 token 时把自身文件的 sha256 发给 daemon，daemon 对不上就拒绝。这样不管改写发生在哪一次调用，都能挡住。需要先确认：pipeline 在改 `artifact_cli.py` 自己的 run（10 月约一半）里，`run.json["cli"]` 指向主 checkout 还是 worktree 的脚本。指向 worktree 的话，sha 要在每次 run-commit 后更新，否则会误伤。

## 6. 实施分期

每期是一个独立 run（或 direct 改动），各自落地、各自验收。按顺序做，后一期依赖前一期的数据。

| 期 | 内容 | 主要文件 | 预计改动 |
|---|---|---|---|
| P1 | G1、G3、E5 的"无输出不压低"部分、G4 的类别标签 | `security_hooks.py`、`finding_ledger.py`、`gate_audit.py`、对应测试 | 约 300 行（删多于加） |
| P2 | G2（+ G2b）| `run_owner_gate.py`、`hook_token.py`、`artifact_cli.py` 的 token 烧录 | 约 200 行；需要 security expert |
| P3 | E0、E1、E2、E4、E5 余下部分 | `artifact_cli.py`、`finding_ledger.py`、`gate2_*`、`review_round_gate.py`、`pipeline_validator.py`、`backend/experts/code/*.md`、`stages/deliver.md`、`stages/ref/deliver/*.md`、`stages/build.md` | 大：预计删除 3000+ 行代码和 40KB+ 文档 |
| P4 | E3 | `stages/review.md`、`stages/ref/review/*`、`_review_dispatch` | 中 |

**测试**：P3、P4 会碰到很多写死卡片措辞的测试（gate1 相关 17 个文件、meta-review 45 个、gate2_convergence 16 个）。每期开始前先 `grep` 列出所有引用，按"删除对应功能的测试 / 改断言 / 保留"分类写进该期的计划，不边改边修。

**怎么做 P3**：P3 是在改 pipeline 自己的 review 机制，用现有 pipeline 跑它会出现"旧 review 机制审查删除旧 review 机制的代码"的循环。建议 P3 用 direct profile 分 3–4 个 worktree 提交完成，每个提交独立可测，并且在 P1 落地后再开始（P1 减少的摩擦让 P3 本身更容易跑完）。

## 7. 验收指标

落地后 30 天对比落地前 30 天（P1 之前先跑一次 `gate_audit.py` 和 run 统计脚本，存一份基线）：

| 指标 | 基线 | 目标 |
|---|---|---|
| 每个 run 的 expert 派出（中位数） | 27（均值） | ≤ 3 |
| REVIEW + DELIVER 占阶段时间 | 33% | ≤ 15% |
| gate deny / 天 | 约 62 | ≤ 15 |
| `routing` + `format` 类和误伤 deny 占比 | 约 55%（69/124） | ≤ 20% |
| 每个 run 抓到的 HIGH（有 finding 的 run 里） | 基线待测 | 不低于基线的 80% |
| push_ready=yes 的完成 run 比例 | 3/41 | ≥ 50% |
| REPORT 里写"手工落地"的 run | 2 天 4 个 | 0 |
| 逃逸率：落地后 7 天内改同一文件的 `fix:` 提交占比 | 基线待测 | 不高于基线 |

"抓到的 HIGH"和"逃逸率"是质量底线。任何一期落地后两周内这两项变差超过目标，就回滚那一期（§8）。

## 8. 回滚

- G1、G3：deny 分支改为放行是一个函数内的改动，回滚即 revert 对应提交。
- G2：保留 `_token_binding` 的严格模式，用 `config.json["pipeline"]["owner_gate_strict"]` 切换，P2 落地后观察两周再删开关。
- E0–E5：P3 期间 `--convergence legacy` 先保留为隐藏选项，落地两周、指标达标后再删除 legacy 代码。删除提交单独做，方便 revert。
- 删除的 expert 文档文件保留在 `backend/experts/code/`，只改为"已并入"说明，恢复时改回 `_dispatch_experts` 即可。

## 9. 待决问题

- **Q1**：E0 的共享 checkout 选 (a) 去掉回退，还是 (b) 建一次性快照？建议 (a)。
- **Q2**（已定，XG 2026-10-21）：red-team 只在有明显安全风险时派。
- **Q3**：E5 里 HIGH 的 `untestable` 是否允许由作者写理由后放行？建议不允许，留给人看（REPORT 列出）。
- **Q4**：G2b 是否和 G2 一起做？建议一起做，否则 G2 只是把一个已知漏洞的窄版本放宽。
- **Q5**：P3 用 direct 分批提交，还是走一次完整 pipeline run？建议 direct 分批。
