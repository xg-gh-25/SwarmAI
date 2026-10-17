---
title: Autonomous Pipeline 提速 — 系统化修复方案
date: 2026-10-17
created: 2026-10-17
updated: 2026-10-17
project: SwarmAI
status: DESIGN — XG 已确认方向，按 §6 顺序实施
related: Opus 5.5 后 pipeline run 变慢（e4fc6ad4 / 2cb579ce / b3dd7ed9 / bfd8b9d3 / 48d377ec / ba1c8a22）
author: Kiro（CLI transcript 取证 + 代码调研）
---
# Autonomous Pipeline 提速：系统化修复方案

> 依据：2026-10-17 切到 Opus 5.5 之后的 5 个 run（`e4fc6ad4`、`2cb579ce`、`b3dd7ed9`、`bfd8b9d3`、`48d377ec`），数据来自 CLI transcript；当前 run `ba1c8a22` 作为补充。
> 范围外：autocompact 阈值（另一个 session 在做）、模型选择、validator 规则本身的松紧（一条都不放宽）。

## 1. 问题一句话

pipeline 把本该由工具做的机械工作交给了模型：选测试、判断失败是不是之前就有、拼 stage 记录、记住 validator 规则。模型每做一步机械工作，就要一次约 10 秒的串行往返，还要往上下文里加内容。

## 2. 实测证据

| 现象 | 数据 | 根因 |
|---|---|---|
| 测试和构建占总时长最多 | 每个 run 占 31% 到 65%。`b3dd7ed9` 的 L2 一次跑全量依赖，583 秒撞上 alarm 被杀，再拆 3 片重跑。`48d377ec` 在 BUILD 里现场追 hang 约 45 分钟。`ba1c8a22` 先 `git archive` 导出 backend 再跑基线。 | L2 范围靠模型手写 grep 挑（test.md:69-94），没有上限，也没有工具。"这个失败是不是之前就有"没有任何机制，test.md 只写了定义，没写怎么判断。 |
| 反复读 `artifact_cli.py` | 7610 行的文件，每个 run 被 `sed`、`grep` 读 13 到 54 次 | validator 报错是自由文本，不附示例；stage 记录的字段（stage_doc_consumed、token_cost、decisions、consumed_artifacts、push_ready 等）只写在 prose 里，没有 schema。 |
| 每个 stage 收尾 3 到 5 次调用 | 写 `/tmp/*.py` 拼 JSON，再 publish、run-update、advance，失败了再改再提交 | 收尾被拆成 3 条命令，每条做一部分校验；`advance` 校验的是"最后一个已完成的 stage"，不是刚做完的那个；stage 文档写 publish→advance，INSTRUCTIONS 又说必须先 run-update，两边互相矛盾。 |
| 上下文一个 run 内涨到 80 到 90 万 token | stage 文档被整篇读进来：deliver 73K 字符，evaluate、review、build 各约 55K，INSTRUCTIONS 95K，REVIEW_PATTERNS 112K | INSTRUCTIONS §3c 要求"BLOCKING: Read ALL files"；`advance` 的 next_action 写的是"execute IN FULL"；规则、事故史和示例都写在同一个文件里。 |
| 调用串行 | 超过 90% 的响应只带一个 tool call，每步 p50 8 到 11 秒 | 模型行为，没法直接改。能做的是减少需要的步数。 |

## 3. 设计原则

1. **工具管机械，模型管判断。** 能用确定性代码算出来的东西（测试范围、失败归类、记录格式、下一个 stage），一律由工具给结果，模型只消费结果。
2. **每个边界只需一次调用。** 进入 stage 一次（`stage-start`），离开 stage 一次（`stage-complete`），跑测试一次（`pipeline_test.py`）。
3. **契约要机器可读，并且只有一份。** schema、示例和报错来自同一个定义；文档只引用它，不再复述。
4. **文档分层。** 执行卡（每次都读，最多 5K）、参考（按需读）、历史（运行时不读）。大小由 lint 卡住。
5. **gate 一个不放宽。** 新命令内部调用现有的 validator 和 gate，旧命令保留。

## 4. 四个工作流

### A. Stage 边界：`stage-start` 和 `stage-complete`

**A1. `artifact_cli.py stage-complete`，每个 stage 唯一的出口**

```bash
python backend/scripts/artifact_cli.py stage-complete \
  --project <P> --run-id <R> --stage <S> --data @/tmp/<R>/<S>.json [--check]
```

`--data` 文件包含两部分：

```json
{"artifact": {...}, "record": {"token_cost": 0, "decisions": [], "consumed_artifacts": []}}
```

`stage_doc_consumed`、`status=completed`、`artifact_id` 由命令自己填，不需要模型写。

执行顺序：先全部校验，校验全过才写盘。

1. 一次性收集所有错误：`validate_artifact_data`（artifact）+ 新增的 `STAGE_RECORD_SCHEMAS`（record）+ `validate()` 里针对**当前 stage** 的检查（在内存里的 run_state 上跑）。
2. 全部通过才写：publish artifact，写 stage 记录为 completed，然后 advance 到 profile 里的下一个 stage。下一个 stage 由命令计算，不由调用方提供。
3. 返回 `{ok, artifact_id, next_stage, next_card, next_action}`，并保留 `_CONTINUATION_PHRASE`。
4. `--check`：只校验不写盘，用来预检。

它顺带修掉两个现有缺陷：`advance` 校验的是上一个 stage 而不是刚完成的 stage；publish 先写 artifact、后拒绝。

需要的重构：把 `validate(project, run_id, stage)` 拆成"读盘 + `validate_stage_state(run_state, stage, artifact_data)`"，保持单一实现，不复制一份校验逻辑。

**A2. `artifact_cli.py stage-start`，每个 stage 唯一的入口**

```bash
python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage <S>
```

一次返回：
- 执行卡路径；
- 这个 stage 需要消费的上游 artifact 摘要（按 STAGE_ROUTING，带 id，可以直接填进 `consumed_artifacts`）；
- PLAN 的 AC 列表；
- 当前的 `files_touched` 和 `required_experts`；
- artifact 和 record 的**可直接通过校验的示例**。

它替代现在进入 stage 时的 discover、run-get、schema 和读文档这几次调用。

**A3. 统一的结构化错误（所有命令共用一个格式）**

```json
{"ok": false, "stage": "review", "errors": [
  {"code": "E_DEPTH_MISSING", "path": "litmus_gate.evidence",
   "message": "litmus_gate.evidence 至少引用 HF1-HF4 中的 2 个",
   "got": "looks fine", "expected": "HF1: ... HF2: ..."}]}
```

- 每个检查有固定的 `code`。`expected` 从模板按 `path` 截取对应片段，所以报错本身就告诉模型怎么改，不必再读源码。
- 现在 publish、advance、run-update 各有一套错误格式（`validation_failed`、`validation_blocked`、`{"error"}`、`{"blocked"}`），统一成上面这一种。旧字段保留一个版本做兼容。

**A4. schema 补全**
- 新增 `STAGE_RECORD_SCHEMAS`：把 INSTRUCTIONS.md:1583-1599 那张 prose 表变成代码。
- 模板里的枚举改为合法的字面值（例如不再写 `"PASS|BORDERLINE|FAIL"`），另给一个 `enums` 字段。
- 补上 reflect 和 goal_cycle 的模板。
- 把现有的 `TestTemplatePassesItsOwnValidator` 扩到**所有 stage 的 artifact 和 record**，保证示例一定能通过校验。

**A5. 兼容**
- `publish`、`run-update`、`advance` 作为底层命令保留，s_evaluate、s_qa、s_deliver 这些兄弟 skill 不受影响。
- 文档只教 `stage-start` 和 `stage-complete`。

### B. 测试：范围由工具算，归类由工具判

**B1. `backend/scripts/pipeline_test.py`，一次调用完成一层测试**

```bash
perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py \
  --project <P> --run-id <R> --layer l2
```

调用形状：pytest 不出现在命令词的位置，同时保留 perl-alarm 外层上限；runner 前台运行，不使用 `&`。这样两个现有 gate 都不会误拦。

**选测试**
- 改动文件 = run.json 的 `files_touched` ∪ `git diff HEAD` ∪ 未跟踪文件。
- 后端：对 `backend/tests/*.py` 做 AST import 扫描，建立反向依赖图（按 tests 目录的 digest 缓存），选出直接 import 了改动模块的测试，加上被改动的测试文件本身。
- 前端：`npx vitest related <files> --run`。
- 上限：后端默认 40 个文件，并按历史单文件耗时（缓存）估算总时长。超出上限时，按 import 距离和近期失败排序截断，被截掉的测试写进结果的 `skipped_for_budget`，不静默丢弃。

**执行**
- 按文件分片，每片一个子进程（`-n 0 --maxfail=0 --timeout=30 --timeout-method=thread`），按内存决定并行度（复用 conftest 的算法）。
- 已知在 xdist 下会死锁的套件（session_unit、lifecycle、healing、recovery、context_health）单独串行成片。
- 全局截止时间约 100 秒，到点按进程组杀掉，结果记为 `timeout`。不会挂住，也不会转到后台。
- 每片开启 faulthandler：超时的测试自动附带卡住位置的栈。`48d377ec` 里手工做的 faulthandler 排查由工具完成。

**输出**
- 紧凑 JSON：通过、失败、超时的计数，每个失败的 nodeid 附 10 行摘要。完整日志落盘到 `runs/<R>/evidence/tests-<layer>-<ts>/`。
- `stage-complete --stage test` 直接读这份 evidence 填 `layers`，模型不再手抄测试数字。

**B2. 判断失败是不是之前就存在：HEAD 基线缓存**
- 只对**失败的 nodeid** 跑基线：在 `git worktree add ~/.swarm-ai/cache/test-baseline/<HEAD-sha>`（与主仓共享对象，几秒建好；复用同一个 venv）里只重跑这些 nodeid，结果缓存到 `<sha>.json`。
- 归类规则：
  - HEAD 上也失败 → pre-existing
  - HEAD 上通过、现在失败 → regression
  - 新增的测试失败 → new failure
  - 在当前树里重跑一次就通过 → flaky
- 同一个 HEAD 的基线缓存可以跨 run 复用。
- conftest 的 venv 守卫在 worktree 里不会误触发，因为 worktree 里没有 `backend/.venv` 目录，守卫的 `isdir` 判断为假直接跳过。实现时要实测确认。

**B3. 结构性 gate，防止旧习惯回潮**
- 新 gate：`pipeline_scope_guard`（PreToolUse，Bash），只在有活跃 pipeline run 时生效。
  - 一条 pytest 命令带超过 8 个测试文件，或用 `$(cat 列表文件)` 展开，就 deny，并在消息里给出可以直接复制的 `pipeline_test.py` 命令。
  - 活跃 run 期间对 backend 做 `git archive`、`rsync`、`cp -r` 导出，deny 并指向 B2。
- BUILD 里 TDD 的单文件、少量文件 pytest 不受影响。
- 按 AGENTS.md 的规定，新 gate 要在 `hook_builder.py` 注册，并更新 Security Architecture 列表。

**B4. 顺手消除文档与代码的矛盾**
- INSTRUCTIONS.md:1276 写"pytest `| tail -5`"，与 R9 guard 冲突。
- `pyproject.toml` 写"conftest SIGALRM 30s"和 `make test-changed`，都不存在。
- `conftest.py` 和 `Makefile` 写 `-n 4`，实际默认是 `-n 0`。

### C. 文档：执行卡 + 按需参考 + 历史

**C1. 三层结构（stage 文档和 INSTRUCTIONS 都按这个改）**

| 层 | 位置 | 内容 | 什么时候读 |
|---|---|---|---|
| 执行卡 | `stages/<stage>.md`（最多 5K） | 目的（1 行）、编号步骤（每步一条确切命令）、gate 清单（每条一行，标注 `[GATE·x]`）、"产出见 `stage-start` 示例"、continuation 段 | 每次进入 stage 时读，`stage_doc_consumed` 指的就是读执行卡 |
| 参考 | `stages/ref/<stage>/<topic>.md` | 条件步骤的细节（greenfield、cross-boundary、跨平台）、命令配方、边界情况 | 执行卡里写明"如果 X，读 ref/…"时才读 |
| 历史 | `stages/history/<stage>.md` | 事故记录、rationalization 表、"为什么有这条规则" | 运行时不读，给人看，也给 evolution 用 |

- INSTRUCTIONS.md 改成最多 10K 的编排卡：core loop、profile 表、escalation、stage-start 和 stage-complete 的用法、legal hand-back 条件。其余内容移到 `ref/`。
- deliver 的 Meta-Review 内联 prompt（约 4.7K）移到 `data/gate-prompts/meta-review.md`，与 gate0、gate1、gate2 的做法一致。
- REVIEW_PATTERNS：给 Trigger 列加一个机器可读的 `match`（glob 或关键字）。新增 `spawn_prompt.py --patterns-for <files>`，只输出触发了的行。review 执行卡写"运行这条命令"，不再整篇读 112K。原表保留，`test_lint_skills_body` 要求至少 80 行，这一条不受影响。

**C2. 指针**
- `advance` 和 `stage-complete` 的 `next_action` 指向执行卡，去掉 "IN FULL"。
- INSTRUCTIONS §3c 的 "Read ALL files" 改成"读执行卡，按卡上的条件读 ref"。

**C3. 防止规则在拆分中丢失（拆文档最大的风险）**
- 迁移脚本 `scripts/check_rule_preservation.py`：从旧文档提取每条带 `[GATE]`、`[MUST]` 标签的语句以及每个 `BLOCKING` 段，生成规则清单，给每条分配一个规则 ID。
- 断言每个 ID 都能在新的执行卡或 ref 中找到。拆分在这个检查全绿之前不能合入。
- 这份规则清单以后也作为执行卡的索引。

**C4. 大小由 lint 卡住**
- 在 `lint_skills.py`（已经接入 CI）里加检查：`stages/*.md` 不超过 5,000 字符，`INSTRUCTIONS.md` 不超过 10,000 字符，`ref/` 下单个文件不超过 15,000 字符。
- `security_hooks.py:2927` 的 governance 匹配 `*/s_autonomous-pipeline/stages/*.md` 不覆盖子目录，需要补上 `stages/**`，让 ref 和 history 同样受 governance gate 管。

### D. 度量：先有基线，再谈提速

把这次的取证脚本固化为 `backend/scripts/pipeline_run_profile.py`：

```bash
python backend/scripts/pipeline_run_profile.py --run-id <R>
```

它对单个 run 输出：
- 总时长、模型时间和工具时间的拆分；
- 工具调用数、单调用响应占比；
- 流程类调用数、`artifact_cli.py` 源码读取次数；
- 峰值上下文、测试执行时长、gate 拒绝次数。

每个 run 结束时由 `run-report` 调用它，结果写进 METRICS.json。这样提速有数字为证，以后变慢也能马上看到。

取证方法（已验证）：transcript 在 `~/.claude/projects/**/<sdk-session-id>.jsonl`；按 `message.id` 归并 assistant 记录，跳过 `isSidechain` 和 `agent-*` 文件；tool_use id 与 tool_result 时间戳配对得到工具耗时；用 run.json 的 `created_at`/`completed_at`（UTC）切时间窗；按命令内容分类（流程类、测试/构建、读/探查、编辑、git、sub-agent）。

## 5. 验收指标

| 指标 | 现在（实测） | 目标 |
|---|---|---|
| 每个 stage 的流程类调用 | 3 到 5 次，加失败重试 | 不超过 2 次（start + complete） |
| 每个 run 读 `artifact_cli.py` 源码的次数 | 13 到 54 | 0 |
| TEST 阶段时长 | `b3dd7ed9` 26 分钟 | 不超过 8 分钟 |
| 判断失败是否之前就存在 | 现场导出代码再跑，5 到 45 分钟 | 一次调用，基线已缓存时不超过 2 分钟 |
| 单个 run 读进的 pipeline 文档 | 29 万到 46 万字符 | 不超过 6 万字符 |
| 峰值上下文 | 63 万到 90 万 token | 不超过 45 万 token |
| bugfix / full 总时长 | 21 到 147 / 54 到 110 分钟 | bugfix 不超过 40 分钟，full 不超过 70 分钟 |

最后两行是估算，需要用 D 的工具在改完后的前 5 个 run 上验证。

## 6. 实施顺序

```
Phase 0  D 度量工具（小，先做，拿到基线）
Phase 1  A stage-start / stage-complete / 结构化错误 / record schema   ┐ 可以并行，
Phase 1' B pipeline_test.py / 基线缓存 / scope gate                    ┘ 改的文件不重叠
Phase 2  C 文档三层化 + 规则保全检查 + 大小 lint（依赖 A、B 的新命令）
```

每个 phase 走一次 pipeline run（full profile）。A 和 B 可以在两个 tab 里并行：A 改的是 `artifact_cli.py`、`pipeline_validator.py`，B 改的是新脚本、`security_hooks.py`、`hook_builder.py`，文件不重叠。C 最后做，同时把执行卡里的命令换成新命令。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| stage-complete 变成绕过 gate 的通道 | 内部只调用现有的 `validate_artifact_data` 和 `validate_stage_state`。现有每个 gate 的测试参数化一份，经 stage-complete 再跑一遍，必须同样拦住。 |
| 拆文档时规则丢失 | C3 的规则 ID 保全检查不全绿就不合入。 |
| 测试截断后漏掉回归 | 截断列表显式写进 artifact；DELIVER 的完成审计必须看到 `skipped_for_budget`；CI 的 BVT 仍是兜底。 |
| 基线 worktree 和真实环境有差异 | 只用来判断失败测试的归属，不用来宣告通过；复用同一个 venv；缓存按 sha 作键。 |
| 新的 scope gate 误拦 TDD | 只在有活跃 run、且超过 8 个文件时拦；deny 消息给出可直接执行的替代命令。 |
| 兄弟 skill 依赖旧命令 | 旧命令全部保留；`test_advance_continuation` 的兄弟 skill 契约不变。 |

## 8. 受影响的测试（需要同步更新）

- `test_advance_continuation.py`：continuation 短语和 700 字符窗口的断言要移到执行卡。
- `test_advance_continuation_e2e.py`：断言下一个文档名是 `evaluate.md`。
- `test_deliver_template_drift.py`：Sub-agent configuration 块、不得指定 model。
- `test_expert_id_contract.py`：`<EXPERT_ID>` 和 "registry id"。
- `test_spawn_prompt_emitter.py`、`test_pipeline_validator.py:4994/5064`、`test_publish_display_block.py:256`：这些读了 evaluate.md 的具体内容。
- `test_lint_skills_body.py`：REVIEW_PATTERNS 至少 80 行。
- `test_security_hooks.py:601`：governance 层级。
- `test_artifact_cli_schema.py`：扩展到所有 stage 的 artifact 和 record。

## 9. 明确不做

- 不放宽任何 validator 规则或 gate。
- 不动 autocompact（另一个 session 在做）。
- 不靠 prose 劝模型"并行一点"：减少需要的步数比劝说可靠。
- 不删旧 CLI 命令。
