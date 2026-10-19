---
title: Pipeline 测试提速 — 选得准、跑得快、续跑一定有进展
date: 2026-10-20
created: 2026-10-20
updated: 2026-10-20
project: SwarmAI
status: DESIGN — XG 2026-10-20 确认这三类问题都要做；T3-T5 等 run_600db8ac 落地后实施
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

### T2 按改动的符号选测试

从 diff 里取改动的函数、类、模块级常量，加上同一文件里直接调用它们的函数（一层）。在 import 了改动文件
的测试里，只留正文提到这些名字的文件。以下情况保守处理，按 import 全选：改动的是 import 以外的模块顶层
语句，或者提取不到任何符号。改动文件自己的 `test_<module>*.py` 和本 run 改过的测试始终都跑。

- 回放（`backend/scripts/replay_test_selection.py`，10 个落地 run）：后端测试文件 374 -> 254（少 32%），真正新出现的失败
  一个没漏。耗时多数只少 0-20%，所以 T2 必须和 T1 一起做。
- 验收：对最近 10 个落地 run 回放，没有任何新失败落在新规则选择之外。

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
2. **T2**：改的是 `pipeline_test.py` 的选择函数。
3. **T3-T5**：改的是续跑状态。

T2-T5 都在 `pipeline_test.py` 里，而 run_600db8ac（break test 两头跑）正在改这个文件，所以 T2-T5
等它落地后在它的代码上做，避免合并冲突。T1 只改测试文件，不受影响。

## 6. 上线后怎么看效果

用 profiler 看接下来 5 个 run：每次 verify 的调用次数中位数 <= 2，没有不收敛的 verify，
测试总耗时比本文 §1 的基线降一半。
