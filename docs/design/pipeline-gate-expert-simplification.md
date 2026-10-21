---
title: Pipeline gate 与 expert review 简化
date: 2026-10-21
created: 2026-10-21
updated: 2026-10-21
project: SwarmAI
status: DONE — P1、P3a 已落地 main；P2 安全复核失败后回退；其余由 pipeline v13 设计一次切换实现（§6）；验收基线见 §7
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
| G2 | `run_owner_gate` 的"必须是第一条命令" | 发 token 的 CLI 调用前面不能有任何命令 | **不改**：允许前置命令的实现安全复核失败，已回退（见 G2） | — |
| G3 | `audit_marker_write_gate` | `cd` 进审计目录或只读的 glob 也 deny | **不改**（实施时撤回，见 G3） | — |
| G4 | gate 治理 | 加 gate 不需要证据 | 新 gate 要引用真实事故 | 无 |
| E0 | 两套 Gate-2 并存 | legacy（3 轮 + meta-review + root-cause）和 deterministic（finding ledger，2 轮）同时在用 | 只留 finding ledger 这一套 | 中：迁移量最大 |
| E1 | Gate-2 名单 | 最多 7 个 specialist，按文件模式触发 | 默认只派 correctness；有明显安全风险时才加 security + red-team。其他 lens 并进 correctness 的检查清单 | 中：部分 MEDIUM 可能漏 |
| E2 | gate0 / gate1 / meta-review | 三个都常态派出 | 删 gate1 和 meta-review；gate0 只在 bugfix 或需求有两种读法时派 | 低 |
| E3 | REVIEW 阶段 | 再派 spec-compliance / code-quality / ux-test / security | 不派 sub-agent，只做确定性的 AC→测试检查 | 中 |
| E4 | 轮次与去重 | legacy 3 轮；同一 bug 被多个 expert 各报一次 | R1 全员，R2 只派"有发现被修过"的 expert、只看修复 diff；修之前按位置去重 | 低 |
| E5 | `push_ready` | 每条 finding 都要被 break test 证明才算 ready；expert 没输出也压低 | 只有未解决的 HIGH 才压低；没输出、untestable 的 MEDIUM/LOW 记为剩余风险 | 中：需新增 `severity` 字段 |

预计效果：每个 run 的 expert 派出 27 → 普通改动 1–2、有安全风险的改动 3–5，子 agent 时间和 token 减少约 75–80%；G1 预计把 2 天 124 条 gate deny 降到约 90 条。P2 未保留，因此 `run_owner_gate` 的 31 次格式 deny 仍在。

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

**放弃的东西**：runner 会在调用前后对比仓库状态，恢复测试写坏的 tracked 文件；直接跑的测试没有这层保护，写出的改动会留在 worktree 的 diff 里，可能被当成本 run 的改动提交。现有防线是 conftest 里对已知副作用调用的 autouse stub。不另加机制：这类测试是少数，已知的都被 stub 了。

**删除的测试**：`tests/test_pipeline_test_gate*.py` 里断言 pytest/vitest/tsc/stdin deny 的用例改为断言 approve + 提示；loop/repeat 用例保留。

### G2 `run_owner_gate`：撤回，不改

P2 曾让 token CLI 前面可以运行 python 或简单文件工具（`db08c31f`），以消除 2 天里的 31 次命令格式 deny。独立安全复核发现两个 HIGH、两个 MEDIUM：

- 前置 `cp` 可以替换随后接收 token 的 Python 解释器；脚本目录检查没有保护解释器、导入路径及符号链接别名。
- `xargs ... artifact_cli.py` 被 `_unwrap_program` 识别成一个 CLI 调用，但 token 实际先给了 `xargs`；它能在运行时追加未审参数或启动多个子进程。
- Bash 内建 `printf -v PYTHONPATH ...` 会改掉 CLI 继承的环境。
- shell 注释后的换行 CLI 被 `_top_spans` 识别，但 `parse_cli_calls` 没识别，导致真实 CLI 拿不到 token。

继续补这些分支会把一个 49 行的绑定函数变成自建 shell parser（P2 一度增加 212 行），而且每轮复核都出现新的解析和执行边界。按本项目规则，这是错误方案信号，不再逐个补丁：完整回退 P2，恢复“CLI 是第一条简单命令；前面只允许原有的 quoted heredoc 写入”。需要准备 JSON 或改计划时，分成两次 Bash 调用。

31 次 deny 是可见的流程摩擦，但没有安全地消除；v13 如要重新设计 token 传递，必须不依赖解析任意 shell 前缀。

### G3 `audit_marker_write_gate`：撤回，不改

实施 P1 时读了这个 gate 的设计注释（`security_hooks.py` 里 `_AUDIT_WRITE_VERBS` 上方），两处"只读也拒绝"都是有意的：
- `cd` 进审计目录被拒，是因为 Bash 工具在调用之间保留 cwd，下一次调用里一个相对路径的 `> m.marker` 就看不出写的是审计目录；
- 解释器代码只要提到目录名就拒，是因为在程序文本里区分读和写是一场正则的军备竞赛。

这个 gate 守的是 D7、Red Team、Gate-0/1 和 `adversarial_commit_gate` 共同依赖的证据，不能 force。2 天只有 4 次误伤，放宽换来的很少，所以撤回。读审计目录用绝对路径（`ls <abs>`、`cat <abs>`）本来就放行。

### G4 gate 治理

新 gate 必须在设计里引用至少一个真实事故（日志行、run id 或 commit）。没有新流程、新标签或定期复审；要看 gate 效果时用现有的 `gate_audit.py --samples`。

## 4. Expert 改动

### E0 只留一套 Gate-2：finding ledger

**现在**：
- legacy：`core/gate2_convergence.py`（3 轮 + 根因复盘 + 退回 PLAN 重设计一次）、`core/review_round_gate.py`（`MAX_ROUNDS = 3`）、meta-review、`gate2-recheck`。
- deterministic：`core/finding_ledger.py`（`BUDGETS["gate2_rounds"] = 2`，finding 靠 runner 密封的 break test 关闭）、`gate2-next`。
- 选择逻辑：`artifact_cli.py` run-create 里 `_convergence_for(args)`，direct 默认 deterministic，其他默认 legacy；run 落到共享 checkout 时退回 legacy。`pipeline_validator.model_verdicts_decide` 是唯一的模式判定。

两套共存意味着每个 gate2 相关改动都要写两遍、测两遍，阶段文档也要分两路讲（`deliver-convergence.md`、`gate2-findings.md`、`gate2-deterministic.md`）。

**改成**：所有 profile 都走 finding ledger。难点是共享 checkout：run 落到共享 checkout 时现在退回 legacy，因为 break test 要在 R1 时的代码上跑旧侧，而共享 checkout 没有 R1 快照。`worktree_mode` 已经默认开启（`core/run_worktree.py` `mode_enabled`），共享 checkout 只在建 worktree 失败时出现（`pipeline_worktree._shared`）。两个办法，定为 (a)（§9 Q1）：
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
| G2 | 允许前置命令使 token 的接收者和继承环境不再固定 | 独立复核发现两个 HIGH，完整回退；不靠继续扩展 shell parser 修补 |
| E1 | performance、api-contract、operational 类 MEDIUM | 并入 correctness 清单；api-contract 的前后端字段问题由 TS 编译和 `toCamelCase` 测试兜一部分 |
| E2 gate1 | 计划层面的错误（方向错、漏需求） | gate0 在 bugfix 和多读法时仍派；PLAN 的 AC↔测试由 validator 检查；correctness 检查"每条 AC 是否实现" |
| E2 meta-review | 修复引入的新问题 | E4 的 R2 只看修复 diff，正对这个问题 |
| E3 | spec-compliance 抓的"需求没实现" | 同样的问题在 12 个 v2 run 里被 correctness/red-team 也报过（"需求 3 没实现"报了 3 遍） |
| E5 | 被报低的 HIGH | 提示词里的 HIGH 标准；REPORT 列出所有 severity；`push_ready` 只是"建议 push"，push 仍是人决定 |

### 5.2 G2 复核结论

“前置命令跨调用本来就能改脚本”不能证明同一条 tokenized shell command 安全：后者在 hook 发出 token 之后、CLI 接收之前改变了解释器、环境或实际子进程，直接破坏 token 对调用身份的绑定。完整发现见 G2。该实现已回退。

## 6. 实施分期

P1、P3a 已经在 main 上落地；P2 在独立安全复核发现两个 HIGH 后回退。其余部分不再在 main 上分期实施，由 pipeline 统一设计 v13（`~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-21-pipeline-structural-decisions.md`）的 cycle 5、7 实现，并在 v13 的切换 commit 里与旧代码的删除一起落地（v13 §D1、§D3.4；XG 2026-10-21 确认：一次切换，不留隐藏开关）。

| 期 | 内容 | 状态 |
|---|---|---|
| P1 | G1、E5 的"无输出不压低"部分（G3 撤回） | 已落地 `dee27dd7` |
| P2 | G2 | `db08c31f` 实现、`8948540a` 测试修正；独立安全复核发现两个 HIGH，随后完整回退 |
| P3a | E1：Gate-2 名单 | 已落地 `8fd9335c`。规则与 v13 §A1.4 相同，v1 的 cycle 5 重写这段代码时沿用 |
| P3 其余 | E0、E2、E4、E5 余下部分 | 由 v13 实现：E0、E2 在 cycle 7 的删除清单（§C2）里，E4 在 cycle 5（§B4 聚类），E5 在 cycle 5（§A3.5 push 规则） |
| P4 | E3 | 由 v13 实现（§A1.4：REVIEW 不再派 spec-compliance / code-quality / ux-test） |

为什么不在 main 上继续分期：这些代码会先在 main 上改一遍，再被 v1 整体替换；而且分期需要的"保留 legacy 两周"会让 main 上新旧并存，与 v13 冲突。

## 7. 验收指标

落地后 30 天对比落地前的基线。基线取 10-01 到 P1 落地（10-21 20:50 CST）之间的数据，2026-10-21 计算：

| 指标 | 基线 | 目标 |
|---|---|---|
| 每个 run 的 expert 派出（中位数） | 27（均值） | ≤ 3 |
| REVIEW + DELIVER 占阶段时间 | 33% | ≤ 15% |
| gate deny / 天 | 约 62 | ≤ 45 |
| 改道、格式和误伤类 deny 占比（按 §1.2 的分法） | 约 55%（69/124） | ≤ 40% |
| 至少有一个 HIGH 的 run 占有 finding 的 run 的比例 | 43%（55/127） | 不低于 34%（基线的 80%） |
| push_ready=yes 的完成 run 比例 | 3/41 | ≥ 50% |
| REPORT 里写"手工落地"的 run | 2 天 4 个 | 0 |
| 逃逸率：run 的提交之后 3 天内，有 `fix` / `revert` 提交点名该 run 的 id 或 commit sha | 5%（4/85） | 不高于 5% |

"HIGH 比例"和"逃逸率"是质量底线。逃逸率用 3 天窗口：run-commit 是 10 月中旬才有的，7 天窗口完整的 run 只有 7 个。按"7 天内改同一文件的 fix 提交"计算会得到 85%，因为这个仓库几乎每个文件每周都会被 fix 提交改到，这个口径测不出逃逸，没有采用。P1、P3a 落地后两周内这两项变差超过目标，就回滚对应提交；v13 切换后用 v13 §E6 的指标对比（§8）。

## 8. 回滚

- G1：deny 分支改为放行是一个函数内的改动，回滚即 revert 对应提交。
- G2：已回退；不要恢复 `db08c31f`。
- P3a：revert `8fd9335c`。
- 其余：按 v13 §D1，revert 切换 commit，再 build 并重启。不保留隐藏开关。

## 9. 已定的决定（XG 2026-10-21）

- **Q1**：E0 去掉共享 checkout 回退，建不了 worktree 时 stage-start 报错。
- **Q2**：red-team 只在有明显安全风险时派。
- **Q3**：HIGH 的 `untestable` 不能由作者放行，留给人看（REPORT 列出）。
- **Q4**：G1 不加 git 状态指纹，G4 不加类别标签和定期复审；P2/G2 的 shell-prefix 方案安全复核失败，已完整回退。
- **Q5**：~~P3 用 direct 分批提交~~ 改为：P3 其余部分和 P4 由 v13 一次切换实现（XG 2026-10-21）。
