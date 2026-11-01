---
title: Autonomous Pipeline 提速 — 系统化修复方案
date: 2026-10-17
created: 2026-10-17
updated: 2026-11-01
project: SwarmAI
status: 已实施后退役 — A–G 于 2026-10-17/18 在旧 pipeline 落地（§6）；stage-start/stage-complete、pipeline_test.py、pipeline_run_profile.py 等随 v1 切换和 S6 删除（f5b8b455e、6ba4726f6、20129f043），仍在的只有执行卡大小 lint 和 CI BVT 清单；并行 session 提交隔离已拆到 parallel-session-commit-isolation.md 单独讨论
related: Opus 5.5 后 pipeline run 变慢（e4fc6ad4 / 2cb579ce / b3dd7ed9 / bfd8b9d3 / 48d377ec / ba1c8a22）
author: Kiro（CLI transcript 取证 + 代码调研）
---
# Autonomous Pipeline 提速：系统化修复方案

> 依据：2026-10-17 切到 Opus 5.5 之后的 5 个 run（`e4fc6ad4`、`2cb579ce`、`b3dd7ed9`、`bfd8b9d3`、`48d377ec`），数据来自 CLI transcript；当前 run `ba1c8a22` 和一次没走 pipeline 的直接修复作为补充。
> 范围外：autocompact 阈值（另一个 session 在做）、模型选择、validator 规则本身的松紧（一条都不放宽）。

> 2026-11-01：本文的命令和工具属于已退役的旧 pipeline；v1 不选、不跑测试（usable-by-anyone §3.3.3，20129f043）。

## 0. 一页摘要

| 工作流 | 解决什么 | 怎么解决 |
|---|---|---|
| A. Stage 边界 | 每个 stage 收尾要 3 到 5 次调用；报错看不懂，就反复读 `artifact_cli.py` 源码 | `stage-start` 一次拿齐进入所需的信息；`stage-complete` 一次完成校验、写盘和 advance；报错带错误码、字段路径和期望值片段 |
| B. 测试 | 碎片化（同一文件最多重跑 35 次）和范围过大（一次跑 583 秒被杀）；现场追 hang | 测试准则 8 条；`pipeline_test.py` 作为唯一入口，4 种模式，按内容摘要缓存；HEAD 基线判断失败是否之前就存在；gate 拦截直接调用测试命令 |
| C. 文档 | stage 文档整篇读入，上下文涨到 80 到 90 万 token | 执行卡（最多 5K）、参考、历史三层；规则 ID 保全检查；大小 lint |
| D. 度量 | 提速与否没有数字 | `pipeline_run_profile.py`，每个 run 结束自动记录指标 |
| E. 交付摘要 | 完成摘要靠模型手写，放在代码块里点不开；token 是估算的；看不到原始意图和新增文件 | CLI 用实测数据渲染，模型原样转发；文件和 REPORT.md 可以点开在 Canvas 查看，修改过的文件直接打开 diff |
| F. 全流程机械工作 | 每个 run 有 28 到 159 次 Bash 调用是模型手工做的机械工作 | 每类机械工作指定一个工具接手；派生字段由工具计算；lint 防止 shell 配方回到文档里 |
| G. 完成条件先行 | 没有事先定好的结束条件，验证停不下来：fuzz 跑了近 10 轮、40 多分钟，mutation 重复 4 遍 | 动手前先写机器可检查的退出契约；heavy 验证走 probe 模式，一轮没有新发现即收敛，目标和探针都没变时直接返回上次结果；直接修也用轻量的 `direct` profile |
实施顺序：D → 两次并行的 run（A+E+G 的契约部分；B+G 的 probe 部分）→ C。gate 一条不放宽，旧命令全部保留。

并行 session 的提交隔离不在本方案范围内，草稿已拆到 `docs/design/parallel-session-commit-isolation.md`，单独讨论。

## 1. 问题一句话

pipeline 把本该由工具做的机械工作交给了模型：选测试、判断失败是不是之前就有、拼 stage 记录、计算改动范围、生成专家 prompt、记住 validator 规则、写完成摘要。模型每做一步机械工作，就要一次约 10 秒的串行往返，还要往上下文里加内容。

## 2. 实测证据

| 现象 | 数据 | 根因 |
|---|---|---|
| 测试和构建占总时长最多 | 每个 run 占 31% 到 65%。`b3dd7ed9` 的 L2 一次跑全量依赖，583 秒撞上 alarm 被杀，再拆 3 片重跑。`48d377ec` 在 BUILD 里现场追 hang 约 45 分钟。`ba1c8a22` 先 `git archive` 导出 backend 再跑基线。 | L2 范围靠模型手写 grep 挑（test.md:69-94），没有上限，也没有工具。"这个失败是不是之前就有"没有任何机制，test.md 只写了定义，没写怎么判断。 |
| 反复读 `artifact_cli.py` | 7610 行的文件，每个 run 被 `sed`、`grep` 读 13 到 54 次 | validator 报错是自由文本，不附示例；stage 记录的字段（stage_doc_consumed、token_cost、decisions、consumed_artifacts、push_ready 等）只写在 prose 里，没有 schema。 |
| 每个 stage 收尾 3 到 5 次调用 | 写 `/tmp/*.py` 拼 JSON，再 publish、run-update、advance，失败了再改再提交 | 收尾被拆成 3 条命令，每条做一部分校验；`advance` 校验的是"最后一个已完成的 stage"，不是刚做完的那个；stage 文档写 publish→advance，INSTRUCTIONS 又说必须先 run-update，两边互相矛盾。 |
| 上下文一个 run 内涨到 80 到 90 万 token | stage 文档被整篇读进来：deliver 73K 字符，evaluate、review、build 各约 55K，INSTRUCTIONS 95K，REVIEW_PATTERNS 112K | INSTRUCTIONS §3c 要求"BLOCKING: Read ALL files"；`advance` 的 next_action 写的是"execute IN FULL"；规则、事故史和示例都写在同一个文件里。 |
| 测试碎片化 | pytest 114 次、前端测试和构建 80 次，一半以上只跑 1 个文件；`turnView.test.ts` 重跑 35 次 | 每次小修改后都单独跑一次测试；没有统一入口，也没有缓存。详见 B。 |
| 机械工作由模型手做 | 每个 run 28 到 159 次 Bash 调用，占 Bash 调用的 59% 到 67% | 文档里写的是 shell 配方和公式，没有对应的工具。详见 F。 |
| 完成摘要不可用 | 文件和 REPORT 点不开，token 是估算值，没有原始意图 | 摘要由模型手写，放在代码块里；run 和 session 没有关联。详见 E。 |
| 验证没有终点 | 一次直接修复里，fuzz 每轮 6 万条命令、4 到 5 分钟，跑了近 10 轮，超过 40 分钟，其中 fuzz 早已找不到新问题；一轮因为 macOS 上 `sed` 转义没生效，跑的还是旧字母表；mutation 测试重复 4 遍 | 事先没有定好"做到什么程度算完"，"尝试打破它但失败"变成了"必须证明完美"（STEERING #17）。heavy 验证是临时脚本，没有记录、没有收敛判断，改没改成功也看不出来。详见 G。 |
| 并行 session 互相干扰 | 今天提交本方案时，其他 session 已暂存 `artifact_cli.py` 等 4 个文件；`48d377ec` 自己写了 `commit_mine.py` 用临时 index 只提交自己的改动，提交相关调用 12 次；5 个 run 里 `git add`/暂存检查共 42 次；`48d377ec` 还专门绕开 `bfd8b9d3` 暂存的文件 | 所有 session 共用一个工作区和一个 git index，没有文件归属，也没有提交隔离，只能靠模型自己避让。不在本方案范围内，见 `parallel-session-commit-isolation.md`。 |
| 调用串行 | 超过 90% 的响应只带一个 tool call，每步 p50 8 到 11 秒 | 模型行为，没法直接改。能做的是减少需要的步数。 |

## 3. 设计原则

1. **工具管机械，模型管判断。** 能用确定性代码算出来的东西（测试范围、失败归类、记录格式、下一个 stage），一律由工具给结果，模型只消费结果。
2. **每个边界只需一次调用。** 进入 stage 一次（`stage-start`），离开 stage 一次（`stage-complete`），跑测试一次（`pipeline_test.py`）。
3. **契约要机器可读，并且只有一份。** schema、示例和报错来自同一个定义；文档只引用它，不再复述。
4. **文档分层。** 执行卡（每次都读，最多 5K）、参考（按需读）、历史（运行时不读）。大小由 lint 卡住。
5. **先定结束条件，再动手。** 做完的定义是"试图打破它但没成功"，不是"证明它完美"。结束条件满足就停。
6. **派生字段由工具算，不由模型声明。** 计数、耗时、token、文件清单、push_ready 都从 git、runner 和数据库算出。
7. **交付结果由工具渲染。** 模型只补充判断类内容（next steps），不手写格式。
8. **gate 一个不放宽。** 新命令内部调用现有的 validator 和 gate，旧命令保留。

## 4. 七个工作流

### A. Stage 边界：`stage-start` 和 `stage-complete`

**A1. `artifact_cli.py stage-complete`，每个 stage 唯一的出口**

```bash
python backend/scripts/artifact_cli.py stage-complete \
  --project <P> --run-id <R> --stage <S> --data @/tmp/<R>/<S>.json [--check]
```

`--data` 文件包含两部分：

```json
{"artifact": {...}, "record": {"decisions": [{"description": "...", "classification": "taste", "reasoning": "..."}]}}
```

模型只写判断类内容。`stage_doc_consumed`、`status=completed`、`artifact_id`、`token_cost`、`consumed_artifacts` 以及 F2 列出的其他派生字段都由命令自己填。

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
- 这个 stage 需要消费的上游 artifact 摘要（按 STAGE_ROUTING，带 id）；
- PLAN 的 AC 列表和退出契约（G1）；
- 当前的 `files_touched` 和 `required_experts`；
- artifact 和 record 的**可直接通过校验的示例**；
- review 和 deliver 阶段额外返回：改动文件（区分新增和修改）、增删行数、是否触发大 diff 升级、本次要派的专家清单和每位专家已填好的 prompt（见 F1）。

它替代现在进入 stage 时的 discover、run-get、schema、git diff、spawn_prompt 和读文档这几类调用。

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

> **已部分废弃（2026-10-19）**：本节里 L2 的 40 个文件上限、按耗时截断、`skipped_for_budget` 溢出都已不再适用，以 §6 末尾「B 的修订」为准。每次调用仍是 `alarm 110` + 95 秒截止，但选中的测试一个不砍：一次跑不完，输出 `continue: true`，再跑同一条命令只补跑没跑完的。当时 L3 = 本地跑 CI 的 BVT 清单 + import smoke（runner 已删，20129f043）。

**实测：测试问题有两种，碎片化比范围过大更普遍**

| run | 测试调用 | 测试耗时 | 重复最多的目标 |
|---|---|---|---|
| `e4fc6ad4` | pytest 23 次 | 10.8 分钟 | `test_app_config_manager.py` 跑了 16 次 |
| `2cb579ce` | pytest 10 次 | 4.0 分钟 | `test_job_cli_result.py` 8 次 |
| `b3dd7ed9` | pytest 17 次 + vitest 6 次 | 26.4 分钟 | `test_prompt_builder_thinking_guard.py` 11 次；一次全量依赖 583 秒被杀 |
| `bfd8b9d3` | vitest 57 次 + tsc/build 12 次 + pytest 6 次 | 43.1 分钟 | `turnView.test.ts` 35 次 |
| `48d377ec` | pytest 58 次 | 58.3 分钟 | `test_output_degeneration.py` 34 次；追 hang 的 ps/kill/faulthandler 31 次，48 分钟 |

5 个 run 合计：pytest 114 次（98 分钟），前端测试和构建 80 次（45 分钟），一半以上的调用只针对 1 个文件。范围过大只是少数几次，但每次很长；碎片化是几十次小调用，每次都要付约 10 秒的模型往返，再加 pytest 启动开销。

**测试准则（由 runner 和 gate 强制执行，不靠文档提醒）**

1. **只有一个入口。** pipeline 运行期间，所有测试类命令（pytest、vitest、tsc、eslint、vite build）都只能通过 `pipeline_test.py` 执行。
2. **范围由模式决定，不由模型列文件。** 一共 4 种测试模式，没有全量模式（另有 G2 的 probe 和 mutation 两种验证模式）：

   | 模式 | 用在哪 | 范围 |
   |---|---|---|
   | `tdd` | BUILD 的 RED/GREEN 循环 | 显式列出的测试文件，最多 5 个 |
   | `verify` | BUILD 结束 | 本次改动的测试 + 直接 import 了改动模块的测试 |
   | `regression` | TEST 阶段 | L1（ac_coverage 声明的测试）+ L2（全部直接依赖）+ L3（CI BVT 清单 + import smoke），跑不完就续跑 |
   | `recheck` | 修复之后（TEST、DELIVER） | 上次失败的测试 + 受本次修改影响的测试 |

3. **没变就不重跑。** runner 以"测试文件 + 它 import 的源文件"的内容摘要作为缓存键。输入没变时直接返回上次结果，标记为 `cached`，耗时为 0。
4. **一个模式一次调用跑完。** 内部按文件分片并行，不需要也不允许拆成多次调用。
5. **与本次改动无关的失败不在现场排查。** 基线（B2）判定为 pre-existing 的失败，记入 Known Issues 后继续，不修也不调试。
6. **hang 由工具报告。** 超时的测试自动附带栈信息，模型不需要也不应该手工 `ps`、`kill`、`faulthandler`、`sleep` 轮询。
7. **迭代次数可见，但不阻断。** `tdd` 模式下同一文件超过 8 次时，输出里给出提醒（"先把修改攒齐再跑"），并把次数记入 METRICS。不用硬性上限，因为 TDD 迭代本身是正当的，而 XG 的原则是不要无端停下 run。
8. **结果直接进 artifact。** TEST 阶段的 `layers`、`regressions`、`ac_driven.pass` 由 runner 的 evidence 自动填，不由模型手抄。

**B1. `backend/scripts/pipeline_test.py`**

```bash
perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py \
  --project <P> --run-id <R> --mode regression
perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py \
  --project <P> --run-id <R> --mode tdd --files backend/tests/test_x.py desktop/src/x.test.ts
```

调用形状：pytest 不出现在命令词的位置，同时保留 perl-alarm 外层上限；runner 前台运行，不使用 `&`。这样两个现有 gate 都不会误拦。前端和后端由同一个 runner 处理：后端走 pytest，前端走 `vitest related` 和 tsc，tsc 和 build 同样按前端源码摘要缓存。多个并行 run 共用一个全局 flock 信号量，避免同时跑测试把内存打满。

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
- 紧凑 JSON：通过、失败、超时的计数，每个失败的 nodeid 附 10 行摘要。完整日志落盘到 `runs/<R>/evidence/tests-<mode>-<ts>/`。
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

**B3. 结构性 gate：`pipeline_test_gate`（PreToolUse，Bash）**
- **生效条件**：当前 session 有一个 `status=running` 的 run（包括 `direct` profile 的 run，用 E1 记录在 run.json 里的 `session_id` 判断）。不在 pipeline 里的普通对话完全不受影响。
- **拦截内容**：
  - 直接调用 pytest、vitest、tsc、eslint、vite build、`npm run build`、`npm test`；
  - 对 backend 做 `git archive`、`rsync`、`cp -r`、`git worktree add` 导出副本；
  - 手工 hang 排查（对 pytest 进程的 `ps`/`kill`，`python -X faulthandler`）。
- **deny 消息**：给出对应模式下可以直接复制的 `pipeline_test.py` 命令，不让模型自己去找替代办法。
- **兜底**：设置 `SWARM_TEST_GATE_FORCE=1` 可以放行，并记日志，与 `SWARM_ADVERSARIAL_GATE_FORCE` 的做法一致，防止 runner 自身的 bug 卡死 run。
- 按 AGENTS.md 的规定，新 gate 要在 `hook_builder.py` 注册，并更新 Security Architecture 列表。
- 这条 gate 取代原方案里"超过 8 个文件才拦"的设计。原设计只能防范围过大，防不了碎片化；碎片化的根因是每次修改都单独跑一次测试，只有统一入口加缓存才能消掉。

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
- 流程类调用数、`artifact_cli.py` 源码读取次数、机械工作调用数（F1 各类）；
- 测试类调用数和同一文件的最大重复次数；
- 结束条件满足的时间点，以及满足之后的验证类调用数（G3）；
- 峰值上下文、测试执行时长、gate 拒绝次数。

每个 run 结束时由 `run-report` 调用它，结果写进 METRICS.json。这样提速有数字为证，以后变慢也能马上看到。

**Phase 0 状态（2026-10-17 已完成）**：`backend/scripts/pipeline_run_profile.py` 和 `tests/test_pipeline_run_profile.py` 已提交。对 5 个基线 run 跑出的数字和 §2 一致（例如 `48d377ec`：309 次调用、单调用响应 0.976、峰值上下文 829K、机械工作 159/265，模型 62.3 分钟 + 工具 84.7 分钟 = 总时长 147.0 分钟）。`--write-metrics` 以合并方式写入 `METRICS.json` 的 `speed_profile`。原先剩下的两件事已随 Phase 1 完成：`run-update --status completed` 会自动写入 speed profile；`run-metrics` 改成合并写入，不再冲掉 `speed_profile`。

取证方法（已验证）：transcript 在 `~/.claude/projects/**/<sdk-session-id>.jsonl`；按 `message.id` 归并 assistant 记录，跳过 `isSidechain` 和 `agent-*` 文件；tool_use id 与 tool_result 时间戳配对得到工具耗时；用 run.json 的 `created_at`/`completed_at`（UTC）切时间窗；按命令内容分类（流程类、测试/构建、读/探查、编辑、git、sub-agent）。

### E. 交付摘要：渲染好的结构化结果，点击在 Canvas 打开

**现状问题**
- 完成摘要由模型按 `stages/complete.md` 的模板手写，整块放在 ``` 代码块里。代码块里的路径不能点击（MarkdownRenderer 只对行内 code 和 markdown 链接做路径识别），`Report:` 一行写的还是项目相对路径。
- 摘要里没有初始意图、没有真实 token 花费、没有运行时长，文件只有"N changed"，不区分新增和修改。
- token 数是模型按公式估的（例如 `48d377ec` 的 635,000），不是实测值。真实用量其实已经写进 `token_usage` 表（按 session_id），但 run 和 session 之间没有关联。
- `run.json.requirement` 是模型改写过的需求，不是用户原话。

**E1. 数据：全部由工具取，不由模型抄**

| 字段 | 来源 |
|---|---|
| 初始意图 | `run-create` 时记录 `session_id`，并从 data.db 取这个 session 在 `created_at` 之前的最后一条真实用户消息（跳过 UI State、task-notification、"继续"这类短消息，往前找到有内容的那条），写入 `run.json.intent` 和 `intent_source`。取不到时回退到 `--intent` 参数，再回退到 `requirement`，并标注来源。 |
| 最终需求 | evaluate artifact 的 `requirements[]`（每条的 statement 和达成状态）。旧格式 run 用 `acceptance_criteria`。另外列出"AI 替你拍板"的条目（self-answered clarification、assumption），与 REPORT 的需求溯源审计同源。 |
| token 花费 | `token_usage` 表中该 session 在 `[created_at, completed_at]` 窗口内的行，汇总 cost_usd、新输入、输出、缓存读。注意该表时间戳是本地时间，run.json 是 UTC。没有 session 关联的旧 run 显示估算值，并明确标注"估算"。 |
| 运行时长 | `completed_at − created_at`，另附耗时最长的阶段（来自 `derived_stage_gaps`）。 |
| 文件 | 对 `run.json.commits` 的每个 sha 执行 `git show --name-status` 和 `--numstat`，得到新增、修改、删除、重命名，以及每个文件的增删行数。没有 commit 的 run（被 gitignore 的项目）回退到 `files_touched` 加工作区状态。 |

run 和 session 的关联：backend 在构建 `ClaudeAgentOptions` 时通过 SDK 的 `env` 参数给 CLI 子进程注入 `SWARM_APP_SESSION_ID`。这是每个子进程各自的 env，不改全局 `os.environ`，符合 session 隔离规则。`run-create` 读取这个变量写入 run.json。B3 的 test gate 也用这个关联判断当前 session 是否在 pipeline 里。

**E2. 渲染：一个纯函数，一个出口**
- 新增纯函数 `render_delivery_summary_md(run_state, eval_artifact, usage, file_changes)`。`run-update --status completed` 的返回里带上 `_display_block`，沿用 evaluate publish 已有的做法，单一 JSON 对象，不破坏解析。另加 `artifact_cli.py run-summary --run-id <R>`，可以随时重新渲染。
- 执行卡要求模型**原样输出** `_display_block`，自己只补 2 到 3 条 next steps（放在现有的 ```next-steps 块里，前端已经会渲染成可点击的 chip）。①到⑩的过程框和 Executive Summary 移进 REPORT，不再在 chat 里手写。
- 格式约束：
  - 不用代码块。文件路径写成 markdown 链接：显示文字用仓库相对路径，链接目标用绝对路径，这样能通过 `isWorkspaceFilePath` 识别，点击后派发 `swarm:open-file` 在 Canvas 打开。
  - 只保留首尾两条 `━` 分隔线：`LANDMARK_RE` 靠它识别 landmark，同时不会触发 `48d377ec` 新加的重复输出检测。
  - 文件超过 15 行时折叠为"另 N 个文件见报告 §8"；需求超过 7 条时同样处理；意图摘录最多 200 字。
  - 所有用户或模型写的文本都经过 `_md_cell` 中和，与需求溯源审计的处理一致，防止换行注入假标题。

**E3. 修改过的文件点击后直接看 diff**
- chat 链接目前只传 `{path}`，Canvas 打开的是文件全文。OUTPUTS rail 的行带了 `baseRef=<sha>^`，所以能看到本次 run 之前到之后的 diff。
- 前端小改动：MarkdownRenderer 的链接处理支持 `#base=<ref>` 片段，先剥离片段再做路径识别，然后把 `baseRef` 一起放进 `swarm:open-file` 的参数。渲染器给修改过的文件生成 `…/x.py#base=<sha>^`，新增的文件不带片段。
- `baseRef` 只接受与 `ui_actions.build_surface_events` 相同的 sha 正则，否则丢弃。

**E 的 chat 显示样例**（以 `48d377ec` 为例；链接目标实际是绝对路径，这里省略；数字是示意）

> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
> **✅ Pipeline 完成** · `run_48d377ec` · bugfix · ⏱ 147 分钟（BUILD 最长，77 分钟）· 💰 $23.40（输出 283K tokens · 缓存读 165M）
>
> **你要的**
> > 清理 127K 退化污染，再起 full run：流式输出退化 / 无进展检测（model-agnostic）…
>
> **最终需求**（7 条，7 条达成）
> - ✅ FR-1 按 content block 维护 2000 字符窗口，zlib 压缩比 ≥15 判退化
> - ✅ FR-2 判退化后走用户 Stop 的中断路径，对话内提示，不触发 leak continuation
> - …另 5 条见报告
>
> ⚠️ **AI 替你拍板 3 处**：Q1 提示文案用中文加英文括注 · Q2 提示走 assistant 事件通道 · Q3 不检测 thinking 内容
>
> **改动** · 7 个文件 · +612 / −48 · commit `abc1234`
>
> | | 文件 | 行数 |
> |---|---|---|
> | 🆕 | [backend/core/output_degeneration.py](#) | +210 |
> | 🆕 | [backend/tests/test_output_degeneration.py](#) | +260 |
> | ✏️ | [backend/core/streaming_orchestrator.py](#) | +88 −12 |
> | ✏️ | [backend/core/leak_transcript.py](#) | +31 −4 |
>
> **质量** 测试 34/34 通过 · Gate-2 13 条发现全部修复 · 结束条件 3/3 满足 · push-ready
>
> 📄 **[REPORT.md](#)**：完整报告，点击在 Canvas 打开
> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

设计取舍：
- 顺序按用户关心的先后排：结果和代价、我要的、做成了什么、AI 替我决定了什么、改了哪些文件、能不能放心用、看详情。
- 过程信息（①到⑩各阶段、决策分类、token 分阶段）只放 REPORT，不在 chat 里占位置。
- 文件表的图标：🆕 新增、✏️ 修改、🗑️ 删除、↪️ 重命名。排序是新增在前，然后按改动行数从多到少。
- 实际 chat 里，文件名和 REPORT.md 都是可点击的链接。

**E4. REPORT 同步**
- REPORT 的 `原始意图` 改用 `run.json.intent`（用户原话）。`requirement` 作为"最终需求描述"另列。
- REPORT 头部的 Tokens 改用 E1 的实测值；旧 run 仍显示估算值并标注"估算"。

### F. 全流程：机械工作一律由工具做

原则：凡是能写成确定步骤的事，都由代码执行；模型只做判断和写内容。

**F1. 实测：模型手工做的机械工作（5 个 run 合计，Bash 调用）**

| 机械工作 | 调用次数 | 执行耗时 | 改由谁做 |
|---|---|---|---|
| 跑 pytest | 114 | 97.9 分钟 | B：`pipeline_test.py` 四种模式 + 缓存 |
| 拼 stage JSON（写 `/tmp/*.py`、`--stage-json`） | 113 | 17.6 分钟 | A：`stage-complete`，派生字段由工具填（见 F2） |
| publish / run-update / advance | 91 | 5.8 分钟 | A：合成 `stage-complete` 一条 |
| 计算改动范围（`git diff`、`ls-files`、`--stat`、BASE_REF、DIFF_LINES） | 80 | 13.6 分钟 | `stage-start` 直接返回：改动文件（区分新增和修改）、增删行数、是否触发大 diff 升级为 full tier |
| 前端测试和构建（vitest、tsc、eslint、build） | 80 | 45.5 分钟 | B：同一个 runner，按前端源码摘要缓存 |
| 读 `artifact_cli` / validator 源码 | 47 | 3.0 分钟 | A3：结构化报错 |
| `git add`、暂存检查、清掉别的 session 暂存的文件 | 42 | 3.7 分钟 | `stage-start --stage deliver` 自动暂存本 run 的 `files_touched`（只动 index），并列出不属于本 run 的已暂存文件。彻底的提交隔离见 `parallel-session-commit-isolation.md`，不在本方案范围内 |
| ps / kill / faulthandler / sleep 轮询 | 33 | 48.5 分钟 | B：runner 报告 hang 栈，gate 拦手工排查 |
| schema / run-get / discover 查询 | 26 | 1.3 分钟 | A2：`stage-start` 一次返回 |
| git commit / 自写 commit 脚本 | 19 | 0.8 分钟 | `run-commit` 作为唯一提交通道；commit gate 的 deny 消息直接给出 `run-commit` 命令 |
| run-report / run-cultivate / learn | 15 | 2.1 分钟 | reflect 阶段的 `stage-complete` 自动执行 |
| 导出代码副本做基线 | 10 | 3.3 分钟 | B2：HEAD 基线缓存 |
| run-observe 遥测 | 8 | 0.7 分钟 | `stage-start` / `stage-complete` 自动记录阶段起止时间 |
| spawn_prompt 生成专家 prompt | 8 | 0.1 分钟 | `stage-start --stage review/deliver` 直接返回本次要派的专家清单，以及每位专家已填好的 prompt（EXPERT_ID、文件列表、项目知识） |
| wtf_gate | 2 | 0.9 分钟 | `stage-complete --stage test` 从 `files_touched` 和修复次数自动计算 |

**F2. 派生字段一律由工具计算，模型不再声明**

| 字段 | 计算依据 |
|---|---|
| `token_cost` | 该阶段起止时间窗内 `token_usage` 的实测值。删掉 INSTRUCTIONS 里让模型估算的公式。 |
| `stage_doc_consumed`、`consumed_artifacts`、`artifact_id`、`outputs_surfaced` | `stage-complete` 自动填。`consumed_artifacts` 按 STAGE_ROUTING 取上游 artifact id。 |
| `files_changed`、`diff_summary`、`commits` | git |
| `tdd.*` 计数、`layers.*`、`regressions`、`ac_driven.pass` | runner 的 evidence |
| `findings_total`、`findings_fixed`、`findings_remaining` | 由模型给出的 `findings[]` 计数得出 |
| `exit_criteria_met` | 按退出契约逐条对照 runner 的 evidence（G1） |
| 专家派遣清单（tier 默认 ∪ `required_experts` ∪ 按文件规则触发的专家，大 diff 自动升级） | `stage-start` 按 deliver.md 里现有的派遣表计算 |
| `push_ready` | 由各项 gate 结果计算：测试通过、没有未解决的 HIGH 和高置信度 MEDIUM、completion audit 全绿、没有回归、meta-review 没有 HIGH、退出契约全部满足。模型不再自己声明 push-ready。 |

**F3. 留给模型的只有判断**

understanding 和 claim、方案选择、AC 和退出契约的写法、写代码、决策分类、review 发现的取舍和修复、meta-review、lessons、next steps。

**F4. 防止机械工作回流到模型**
- C4 的 lint 再加一条：执行卡里的代码块只允许出现 pipeline 自己的命令（`artifact_cli.py`、`pipeline_test.py`、`spawn_prompt.py`）。出现多行 shell 配方（例如现在 deliver.md 里计算 BASE_REF 和 DIFF_LINES 的那段），就说明有机械工作还留在文档里，必须先改成工具。
- D 的度量工具把"机械工作调用数"作为一项固定指标。现在 5 个 run 里这几类调用分别是 51、28、42、123、159 次，占 Bash 调用的 59% 到 67%。改完后每个 run 应该只剩：每个 stage 的 `stage-start` 和 `stage-complete`（8 个 stage 共 16 次）、`verify`/`regression`/`recheck` 约 4 次、`tdd` 约 10 次，合计不超过 30 次。

### G. 完成条件先行：验证要有终点

**现状问题**（来自一次没走 pipeline 的直接修复）
- 第一版被 Red Team 打穿，重写一版，这一步是对的。
- 之后开始用 fuzz"证明它完美"：每轮 6 万条随机命令、每条对照真实 bash，一次调用 4 到 5 分钟，前后跑了将近 10 轮（bash fuzz 7 个 seed，路径 fuzz 3 个 seed），超过 40 分钟。
- 该停的信号出现了没停：重写后的 fuzz 已经找不到新问题，这本来就是结束条件，却又往字母表里加片段再跑。
- 浪费了一轮：加字母表用的 `sed` 在 macOS 上转义没生效，那一轮跑的还是旧字母表，没被发现。
- mutation 测试重复 4 遍：脚本一开始没有隔离出要验证的改动，后来又被 `--maxfail` 提前截断。
- 正确做法是先定结束条件：Red Team 找到的例子都写成回归测试，gate 测试全绿，fuzz 只跑一轮且没有新发现，然后提交，大约 15 分钟。

根因：结束条件不是事先定的，而 heavy 验证是临时脚本，没有记录、没有收敛判断。工具看不出"这一轮和上一轮一模一样"，也看不出"改动没生效"。

**G1. 退出契约：动手前写好，满足就停**
- PLAN artifact 新增必填字段 `exit_criteria[]`，每一条都能由工具检查：

  ```json
  {"exit_criteria": [
    {"id": "X1", "kind": "test", "ref": "backend/tests/test_x.py::test_red_team_case_1", "expect": "pass"},
    {"id": "X2", "kind": "test", "ref": "backend/tests/test_security_hooks.py", "expect": "pass"},
    {"id": "X3", "kind": "probe", "ref": "bash-fuzz", "expect": "no_new_findings"}]}
  ```

- 按 work type 给默认模板。例如 gate 或 parser 类的修复，默认就是"每个反例一条回归测试 + gate 测试全绿 + probe 一轮无新发现"。
- `stage-complete`（build、test、deliver）根据 runner 的 evidence 逐条判定。全部满足时返回 `exit_criteria_met: true`，`next_action` 写明"结束条件已满足，不要再加验证，进入下一步"。
- 契约只能因为新发现而扩展：一个新反例对应新增一条回归测试，并记录原因。不允许无缘无故挪球门。
- **直接修复也用契约。** 新增轻量 profile `direct`，只有 plan、build、deliver 三步（契约写在 PLAN 的 `exit_criteria[]` 里，没有另起一个 contract stage 名），不读 evaluate、think 的文档。模型自己写契约，不需要问 XG。这样直接修复也能用上同一套 runner、gate、`run-commit` 和交付摘要（E）。
  - 什么时候必须用：会提交且改动超过 20 行的源码修改。由 commit gate 检查：当前 session 没有 run 就提交这类改动时 deny，并给出可以直接复制的 `run-create --profile direct` 命令。文档和 20 行以内的小改动不受影响。

**G2. probe 模式：heavy 验证一轮无新发现即收敛**
- fuzz、数据集回放、e2e 探针这类重验证，都通过 `pipeline_test.py --mode probe --name <名字> -- <命令>` 执行。约定 probe 输出 JSON，`findings[]` 里每条带稳定的 id，runner 跨轮去重。
- runner 每轮记录：被测目标的内容摘要、probe 脚本和输入的摘要、耗时、新发现数。
- 规则：
  1. 一轮新发现为 0，这个 probe 就判定为**已收敛**。
  2. 已收敛的 probe，在目标和 probe 都没变的情况下再跑，直接返回上次结果，耗时为 0，并提示"目标和探针都没变"。macOS 上 `sed` 没生效的那一轮，会在这里马上暴露出来，而不是白跑 5 分钟。
  3. 目标没变、只扩展了 probe（例如加字母表），允许再跑一轮。这一轮仍然没有新发现，就是最终收敛；之后再扩展，必须先有新发现。也就是说，收敛后最多多跑一轮。
  4. 在 pipeline 里，契约中的 probe 条目以收敛结果为准，收敛后 `stage-complete` 不再接受这个 probe 的新轮次作为证据。
- **mutation 模式**：`pipeline_test.py --mode mutation --mutations <spec.json>`。在隔离的 worktree 里（HEAD + 本 session 改动的文件 + 变异）执行，一次调用批量跑完所有变异，固定 `--maxfail=0`，报告每个变异让哪些测试变红。不碰共享工作区，并行 session 不受影响，也不会出现"没隔离出要验证的改动"或被 `--maxfail` 截断的问题。

**G3. 度量**
- D 的工具记录"结束条件满足的时间点"和"满足之后的验证类调用数"。目标是 0。

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
| 模型手工做的机械工作（F1 各类合计） | 每个 run 28 到 159 次 | 不超过 30 次，且基本都是 pipeline 自己的命令 |
| 每个 run 的测试类调用 | 10 到 75 次；同一文件最多重复 35 次 | 不超过 15 次；输入没变的重跑命中缓存 |
| 手工排查 hang | `48d377ec` 31 次调用、48 分钟 | 0（runner 报告栈信息，gate 拦手工排查） |
| 完成摘要里的 token 花费 | 模型估算（例如 635,000） | 来自 `token_usage` 的实测值，含美元 |
| 完成摘要可点击的文件 | 0（整块在代码块里） | 所有改动文件和 REPORT.md 都能点开；修改过的文件打开即是 diff |
| 完成摘要的生成方式 | 模型按模板手写 | CLI 渲染，模型原样转发，只补 next steps |
| 结束条件满足后的验证类调用 | 一次直接修复里 fuzz 收敛后又跑了多轮，超过 40 分钟 | 0；同一 probe 收敛后最多多跑一轮 |
| mutation 测试的重复执行 | 同一验证重复 4 遍 | 一次调用批量跑完 |

"峰值上下文"和"总时长"两行的目标是估算，需要用 D 的工具在改完后的前 5 个 run 上验证。

## 6. 实施顺序

```
Phase 0   D 度量工具（小，先做，拿到基线）
Phase 1   run ①：A + E + G1（退出契约、direct profile）                         ┐ 可以并行，
          主要改 artifact_cli.py、pipeline_validator.py、prompt_builder.py、       │ 改的文件
          MarkdownRenderer.tsx                                                     │ 不重叠
Phase 1'  run ②：B + G2（probe、mutation 模式）                                   ┘
          主要改新的 pipeline_test.py、security_hooks.py、hook_builder.py
Phase 2   C 文档三层化 + 规则保全检查 + 大小 lint（依赖前两次 run 的新命令）
```

**Phase 1 / 1' 状态（2026-10-17 已完成，未走 pipeline，由一个 session 直接实现）**：A、B、E、G1、G2 和 F 的派生字段已提交。经过一轮对抗评审（4 个并行 reviewer），修了 8 个 HIGH、约 15 个 MEDIUM，主要是：profile 降级绕过 review/test、push_ready 用过期证据、缓存漏掉传递依赖、没跑到测试也报绿、order 依赖被判成 flaky、mutation 路径穿越、gate 被无 session 的 run 整体关掉、`python -c`/`make test`/`coverage run` 绕过、token 窗口漏掉最后一轮。与原设计的差异：
- `direct` = plan → build → deliver（见 G）。
- B2 的 flaky 判定比原文严格：重跑整个测试文件（保留原顺序）通过，且 HEAD 上也通过，才算 flaky。只重跑单个测试会把 order 依赖的回归误判成 flaky。
- run 判定为"活跃"要求 3 小时内有活动，避免遗留的 running run 一直卡住测试；deny 文本给出 `run-update --status abandoned`。
- 交付摘要的 token 数如果在完成那一轮内渲染，标注"已结算（不含当前一轮）"。
- 已知残留：故意清掉 `SWARM_APP_SESSION_ID` 创建的 run 没有归属，对创建者不生效（run-create 会告警）；`cd X && make`、pnpm/yarn/bun 不识别；跨文件的 order 依赖不复现。
- 上线需要 `./prod.sh build` 重启 daemon 后，`SWARM_APP_SESSION_ID` 注入和两个新 gate 才会生效。

**Phase 2 状态（2026-10-18 已完成）**：C 全部落地。INSTRUCTIONS 从约 96K 降到 9.6K 的编排卡，11 个 stage 文档都改成 ≤5K 的执行卡，条件细节放进 `stages/ref/<stage>/`，事故和理由放进 `stages/history/`。旧文档里 127 条 `[GATE…]`/`[MUST]`/BLOCKING/⛔ 规则全部打了标签，`check_rule_preservation.py check` 127/127 通过，并由测试在 CI 里重跑。size lint 的 ratchet 表已清空，以后所有卡片都按真实上限检查。Meta-Review prompt 移到 `data/gate-prompts/meta-review.md`，由 `spawn_prompt.py --gate meta-review` 输出。`advance` 的 `next_action` 不再写 "IN FULL"，改成"读执行卡，满足条件才读 ref"。
- 与原设计的差异：REVIEW_PATTERNS 按需输出后仍有约 80K（45 行是 `*` 全量适用的规则），收益比 C1 预计的小，后续可以再收紧这些行的 `match`。
- 下一步：上线后用 profiler 测 5 个 run，对照 §5 的峰值上下文和总时长。

F 不单独成一个 phase，它的各项已经落在 A、B、E 里：派生字段、`stage-start` 返回的改动范围和专家清单、自动遥测随 A 一起做；测试和基线随 B；token 实测随 E。F4 的执行卡 lint 随 C。G 按改动的文件拆进上面两次 run。

E 的改动面：`artifact_cli.py`（run-create 记录 session 和意图、完成分支返回 `_display_block`、新增 `run-summary`）、`prompt_builder.py`（注入 `SWARM_APP_SESSION_ID`）、`MarkdownRenderer.tsx`（`#base=` 片段）、`stages/complete.md`（改为原样转发）。

每个 phase 走一次 pipeline run（full profile）。run ① 和 run ② 可以在两个 tab 里并行，改的文件不重叠。C 最后做，同时把执行卡里的命令换成新命令。run ① 要等 `ba1c8a22` 把它在 `artifact_cli.py` 上的改动提交之后再开始；run ② 改 `security_hooks.py`，开始前也要确认没有其他 session 正在改这个文件（目前只能人工确认，见 `parallel-session-commit-isolation.md`）。

**B 的修订（2026-10-19，XG 确认）**：上文 B 节里 L2 的「40 个文件上限、溢出进 `skipped_for_budget`」和固定约 95 秒的截止时间已经废弃，当时的规则如下（runner 2026-10-24 已删除，20129f043）：

- L2 选**全部直接 import 改动模块的测试**，加上提到被改 `backend/scripts/*.py` 的测试，不设上限，也不按耗时裁剪。
- 每次调用不超过 Bash 默认的 120 秒（`alarm 110`，95 秒截止）。一次跑不完就输出 `continue: true` 和没跑完的数量，再跑同一条命令只补跑那部分（`_auto_recheck`），直到不再出现 `continue`。曾短暂试过 `--deadline auto` 把单次调用拉到 590 秒，但这要靠一个解析 shell 命令的 Bash timeout 门禁兜底，三轮评审都没收敛，已经撤掉。
- 只经过其他模块间接依赖改动的测试不跑，由 runner 列在 `transitive_unrun`，DELIVER 写进 REPORT §10。
- 曾经试过按函数名缩小测试范围，四个 expert 评审后撤掉了：改名、import 重新绑定、多行 import、`getattr`、多跳调用、拼接出来的子命令都会漏选，造成假绿。
- 完整的 import 闭包会覆盖 559 到 565 个测试文件（约 39 分钟），等于全量，所以不用。
- 第 3 层是 CI 的 BVT。pipeline 只做本地提交、看不到 CI，所以 `regression` 在本地跑同一份清单（`backend/tests/bvt_manifest.txt`，约 42 个文件），和 L1、L2 同一次调用、同样续跑。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| stage-complete 变成绕过 gate 的通道 | 内部只调用现有的 `validate_artifact_data` 和 `validate_stage_state`。现有每个 gate 的测试参数化一份，经 stage-complete 再跑一遍，必须同样拦住。 |
| 拆文档时规则丢失 | C3 的规则 ID 保全检查不全绿就不合入。 |
| 测试跑不完漏掉回归 | 不截断：没跑完的测试标为 `timeout`（不算通过），同一条命令续跑；只经间接依赖的测试全部列进 `transitive_unrun` 和 REPORT §10；BVT 在本地 `regression` 里跑。 |
| 基线 worktree 和真实环境有差异 | 只用来判断失败测试的归属，不用来宣告通过；复用同一个 venv；缓存按 sha 作键。 |
| test gate 拖慢 TDD 循环 | TDD 通过 runner 的 `tdd` 模式执行，不做选择也不分片，开销与直接跑 pytest 相当；gate 只在当前 session 有运行中的 run 时生效；deny 消息给出可以直接执行的 runner 命令。 |
| 兄弟 skill 依赖旧命令 | 旧命令全部保留；`test_advance_continuation` 的兄弟 skill 契约不变。 |
| 退出契约写得太松，验证不够就停了 | 按 work type 给默认模板（例如 gate 类修复必须有反例回归测试和一轮 probe）；Gate-2 的专家仍然会审查契约是否覆盖了需求。 |
| probe 的"无新发现"依赖 probe 自己去重 | 约定 findings 带稳定 id，由 runner 跨轮去重，不信任 probe 自报的计数。 |
| `direct` profile 给小修复增加负担 | 只有会提交且超过 20 行的源码改动才需要；创建由模型自己完成，一条命令，不问 XG。 |
| 测试 gate 在 runner 出 bug 时卡死整个 run | `SWARM_TEST_GATE_FORCE=1` 可以放行并记日志；runner 自身有一套独立测试，作为 B 的验收条件之一。 |
| 工具计算的 `push_ready` 与模型判断不一致 | 工具算出的是上限：工具算出 false 时模型不能改成 true；工具算出 true 时，模型仍然可以带着理由改成 false。 |
| 缓存命中掩盖了环境变化（依赖升级、conftest 改动） | 缓存键里加入 `conftest.py`、`pyproject.toml`、`package-lock.json` 和 venv 的摘要。 |

## 8. 受影响的测试（需要同步更新）

- `test_advance_continuation.py`：continuation 短语和 700 字符窗口的断言要移到执行卡。
- `test_advance_continuation_e2e.py`：断言下一个文档名是 `evaluate.md`。
- `test_deliver_template_drift.py`：Sub-agent configuration 块、不得指定 model。
- `test_expert_id_contract.py`：`<EXPERT_ID>` 和 "registry id"。
- `test_spawn_prompt_emitter.py`、`test_pipeline_validator.py:4994/5064`、`test_publish_display_block.py:256`：这些读了 evaluate.md 的具体内容。
- `test_lint_skills_body.py`：REVIEW_PATTERNS 至少 80 行。
- `test_security_hooks.py:601`：governance 层级。
- `test_artifact_cli_schema.py`：扩展到所有 stage 的 artifact 和 record。
- E 新增：`render_delivery_summary_md` 的纯函数测试（含注入中和、截断、无 session 时标注估算）；前端 `MarkdownRenderer` 的 `#base=` 片段测试（非法 ref 被丢弃）；`run-create` 读取 `SWARM_APP_SESSION_ID` 的测试。注意 conftest 会剥掉 `SWARM_*` 变量，测试里要用 `monkeypatch.setenv` 设置。
- G 新增：`exit_criteria` 的 schema 和逐条判定测试；probe 收敛规则测试（无新发现即收敛、目标和探针都没变时返回缓存、扩展后最多多一轮）；mutation 模式不修改共享工作区的测试。

## 9. 明确不做

- 不放宽任何 validator 规则或 gate。
- 不动 autocompact（另一个 session 在做）。
- 不靠 prose 劝模型"并行一点"：减少需要的步数比劝说可靠。
- 不删旧 CLI 命令。
- 不做并行 session 的提交隔离（文件认领、私有 index、worktree），草稿见 `parallel-session-commit-isolation.md`，单独讨论。
