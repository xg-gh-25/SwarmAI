---
title: Pipeline 测试提速 — 选得准、跑得快、续跑一定有进展
date: 2026-10-20
created: 2026-10-20
updated: 2026-10-20
project: SwarmAI
status: DESIGN v2 — XG 2026-10-20：每次 verify 只跑自己的测试 + BVT（T2 两层），已通过的不再跑（T6）；T2-T6 等 run_600db8ac 落地后实施
related: pipeline-speed-systematic-fix.md, Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md
author: Kiro（回放 10 个 run 的 verify 证据 + pipeline_test.py 调研）
---
# Pipeline 测试提速

## 1. 问题

一次 verify 经常要续跑好几次，有时越续越多，从不收敛。原因有三层：

1. **选得太宽**：runner 选所有 import 了改动文件的测试（`REVERSE_DEPTH = 1`）。改到
   `session_unit.py` 就是 94 个文件，而用到改动函数 `_send_generation` 的只有 9 个。
2. **少数文件太慢**：时间集中在几个每个用例都建 git 仓库、起 artifact_cli 子进程的文件上。
   run_d8762bc8 的 318 秒里，最慢 5 个文件占 65%（`test_artifact_cli_run_commit.py` 61s、
   `test_artifact_cli_lifecycle.py` 52s、`test_pipeline_run.py` 44s）；run_6e826e80 的 1016 秒里，
   `test_pipeline_worktree.py` 一个就 181 秒。
3. **续跑不保证有进展**：每次调用最多 95 秒，跑不完就续跑，而续跑的状态处理有三处缺陷（§4）。

前两层决定要不要续跑，第三层决定续跑会不会卡死。三层都要修。

## 2. 不变的规则

- 不按时间或文件数砍测试。选出来的每个文件都要跑完，结果才算绿。
- 每次调用仍然在 Bash 120 秒以内（`alarm 110`、deadline 95），不重新引入长调用。

## 3. 选得准、跑得快

### T1 慢测试文件本身提速（先剖析，再改）

对最慢的 3 个文件（`test_artifact_cli_run_commit.py`、`test_artifact_cli_lifecycle.py`、
`test_pipeline_worktree.py`）用 `--durations=0` 加 profile 找出时间花在哪。预计的改法：一个文件只建
一次 git 仓库，用例之间复位；能直接调 `main()` 的不再起子进程。

- 验收：这 3 个文件各自耗时降到原来的一半以下，用例数和断言不变。

### T2 分两层：每次 verify 只跑"自己的测试 + BVT"，全部 importer 只在 DELIVER 跑一次

**改什么**：现在每次 verify 都跑所有 import 了改动文件的测试。改成两层：

| 层 | 内容 | 什么时候跑 |
|---|---|---|
| A 层（快） | 本 run 改过的测试；改动模块自己的测试 `test_<module>*.py`；最近失败过的测试；CI 的 BVT 清单（`tests/bvt_manifest.txt`，42 个文件，跨 run 按 key 缓存） | 每次 verify、每次修完重跑 |
| B 层（宽） | A 层之外、直接 import 了改动模块的测试（现在的全部选择减去 A 层） | 只在 DELIVER 的 regression 跑一次；有失败就进正常修复，修完只重跑失败的和受影响的 |

**为什么不按符号名筛**：2026-10-19 的专家评审已经否决过按名字缩小范围（改名、import 重绑定、getattr
分发、多跳调用都会漏，漏了就是假绿）。两层方案不缩小最终的范围，落地前每个 importer 仍然都跑；
省掉的是"每次修改都把 importer 全跑一遍"。

**回放**（8 个落地 run 的 verify 证据；耗时是各文件用时之和，没有除以 worker 数）：

| run | 现在 | A 层（不含 BVT） | 新失败落在哪 |
|---|---|---|---|
| run_6e826e80 | 54 个 / 1016s | 23 个 / 503s | `test_worktree_gates.py`，在 A 层 |
| run_df73d970 | 40 / 343s | 20 / 208s | 无 |
| run_d8762bc8 | 40 / 318s | 13 / 147s | 无 |
| run_1a309783 | 40 / 251s | 22 / 104s | 无 |
| run_a87bfe3f | 40 / 220s | 15 / 129s | 无 |
| run_376299fa | 40 / 326s | 8 / 95s | 无 |
| run_045b96b2 | 40 / 297s | 9 / 94s | 无 |

A 层的耗时是现在的 30%-50%。按 4 个 worker 并行，大部分 run 一次调用就能跑完，不需要续跑。
唯一一个后端新失败落在 A 层，B 层在 DELIVER 兜底。

- 验收：A 层由 runner 算，模型不能自己挑；B 层在 DELIVER 必须跑完才能落地；回放最近 10 个落地 run，
  每个新失败要么在 A 层，要么在 B 层。

### T6 已通过的不再跑：按文件缓存，不按整次选择缓存

**现在的问题**：缓存 key 是整次选择的，改动文件集合里多一个、少一个文件，key 就变，所有文件从头再跑
（`docs/CODEBASE_METRICS.md` 那次 42 -> 43，就是这样从 388 个没跑完变成 771 个）。

**改法**：每个测试文件单独一个 key = 该测试文件的内容摘要 + 它 import 闭包里所有模块的内容摘要
（`sgraph` 已经有 import 图）+ 解释器和 pytest 配置。一个文件通过之后，key 不变就不再跑：同一次 verify
的续跑里不跑，下一次 verify 不跑，regression 也不跑。改了某个模块，只有能 import 到它的测试 key 会变。

- break test：A、B 两个测试文件，A import `m1`，B import `m2`；都通过后只改 `m2`，下一次 verify 只跑 B。

## 4. 续跑一定有进展（`pipeline_test.py`）

| | 问题 | 修法 | break test |
|---|---|---|---|
| T3 | 每次续跑排序都一样，大文件总排在前面，balance、workers、retention 永远轮不到 | 续跑时先跑从没开始过的文件，再跑做到一半的文件 | 两个慢文件加三个快文件，第 2 次调用时快文件必须已经开始跑 |
| T4 | 每次都重放已通过的前缀（约 45 秒） | 一个文件的前缀只在第一次续跑时重放，结果记下来，之后不再重放 | 连续 3 次续跑，前缀只执行一次 |
| T5 | 进展按通过数算，每次只多 3 个也算有进展 | 改成按没跑完的数量算：连续 2 次减少不到 10%，就判为不收敛，改成按文件拆开，一个文件一次调用，输出里给出拆开后的命令 | 构造每次只多 3 个的情况，第 3 次必须给出拆分命令 |

已经修掉的同类问题（不再重复）：测试改写仓库文件导致缓存 key 漂移（`493963b2`、`c81db2fa`），
在一条 Bash 里循环续跑（`pipeline_test_gate`）。

## 5. 顺序和依赖

1. **T1 剖析**：只读，现在就可以做。
2. **T2 + T6**：选择分层和按文件缓存，效果最大。
3. **T3-T5**：续跑规则。T2 + T6 之后续跑会很少发生，但仍要保证有进展。

T2-T6 都在 `pipeline_test.py` 里，而 run_600db8ac（break test 两头跑）正在改这个文件，所以 T2-T5
等它落地后在它的代码上做，避免合并冲突。T1 只改测试文件，不受影响。

## 6. 上线后怎么看效果

用 profiler 看接下来 5 个 run：每次 verify 的调用次数中位数 <= 2，没有不收敛的 verify，
测试总耗时比本文 §1 的基线降一半。
