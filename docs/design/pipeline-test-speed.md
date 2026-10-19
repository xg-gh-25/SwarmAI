---
title: Pipeline 测试提速 — 选得准、跑得快、续跑一定有进展
date: 2026-10-20
created: 2026-10-20
updated: 2026-10-20
project: SwarmAI
status: IMPLEMENTED — T1 66431d66；T2 由 f88dce48、T6 由 42a4aae8 + d8cbcddd 覆盖（key 不收窄到 import 闭包，见 §3）；T3-T5 d8cbcddd（§8）；§9 worker 调查、§10 熔断待定
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

### T2 两层范围 — 已由 `f88dce48` 覆盖（2026-10-20）

不再单独实施。`f88dce48`（"Test the whole scope once per run"）用更严的方式实现了同一个目标：BUILD 里只能跑
`--mode tdd`（最多 5 个明确的测试文件，按改动挑），全部 importer + BVT 的全范围测试每个 run 只在 DELIVER
跑一次，最多 2 轮（§7）。和原方案的区别只在 A 层：原方案由 runner 自动挑"自己的测试 + BVT"，现在由模型
按改动挑最多 5 个文件。按名字缩小范围仍然不做（2026-10-19 专家评审：改名、import 重绑定、getattr、多跳
调用会漏，漏了就是假绿）。

下面的回放数据留作背景（8 个落地 run，耗时是各文件用时之和）：A 层是现在的 30%-50%，唯一一个后端新失败
（run_6e826e80 的 `test_worktree_gates.py`）落在 A 层。

### T6 已通过的不再跑 — 已由 `42a4aae8` 覆盖，key 不收窄

按文件复用 2026-10-18 就有了（`42a4aae8`，`_l2_file_keys` / `_reusable`）；`d8cbcddd` 又把 HEAD 从 key 里去掉（§8）：一个文件在本 run 最近的 verify / regression
里全部通过、per-file key 没变，就复用结果、不再跑。

原方案想把 key 缩小成"这个测试文件自己的 import 闭包"，这样改了一个模块只让能 import 到它的测试重跑。
**不这样做**：key 里刻意包含**每一个改动过的文件**，因为一个模块可以不经过 import 边就读到改动的文件
（`importlib.import_module` 按计算出的名字、`spec_from_file_location` 按路径、`open`、遍历目录）。
2026-10-18 的 red-team 用 `importlib.import_module` 实际复现过一次缩窄 key 造成的 stale pass。缩窄 key
就是重新引入假绿。现在的复用覆盖的是"树没变"的重跑：换 mode、上一次不可缓存、选择不同。

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

现状：T1 `66431d66`；T2 由 `f88dce48`、T6 由 `42a4aae8` + `d8cbcddd` 覆盖；T3-T5 由 `d8cbcddd` 实施（§8）。

## 6. 上线后怎么看效果

用 profiler 看接下来 5 个 run：每次 verify 的调用次数中位数 <= 2，没有不收敛的 verify，
测试总耗时比本文 §1 的基线降一半。

## 7. 2026-10-20 追加：全范围测试每个 run 只跑一次（已实施）

XG 定的规则，代码已落地（`pipeline_test.py` 的 `_full_scope_refusal`、`artifact_cli._deliver_push_ready_bound`）：

- verify / regression / recheck 只在 DELIVER 跑：在最后一次改动之后跑一次 `--mode regression`。BUILD、REVIEW、TEST
  里 runner 直接拒绝，并提示改用 `--mode tdd --files` 或 `--mode prove`。默认退出条件不再自动加 `mode:verify`；
  已声明的 `mode:` 条件只在 DELIVER 判。
- 一个 run 最多 2 轮全范围（第一轮红了、修完再跑一次）。deadline 没跑完的续跑、缓存命中都不算一轮。goal run 不受限。
- push_ready 必须有一份 verify / regression 报告，而且在最后一次改动之后；只有 tdd 是绿的不算。纯文档改动不需要。
- 不再偷偷重跑：输入没变、测试都跑完了但结果不能缓存（有失败、单测超时），结果照原样报告，不再执行。续跑只补
  deadline 留下没跑的测试，跑过并失败的不重跑。
- rebase：`pipeline_worktree._tests_carry` 已经是"本 run 的文件没变、main 的新提交没碰到它们就沿用测试结果"，不变。

对本文的影响：T2 的 A 层原本是"每次 verify 都跑"，现在 verify 每个 run 只跑一次，A/B 分层的收益主要剩在那一次上；
T6（按文件缓存）和 T3-T5（续跑有进展）仍然有用，因为那一次 regression 可能要续跑。

## 8. 2026-10-20 追加：续跑有进展、按文件缓存、审查按比例（已实施）

- T3：续跑先排从没开始过的文件，做到一半的（要重放前缀的）排在后面。
- T4：一个文件的前缀只在第一次续跑时重放（证据里记 `context_replayed`），之后剩下的单独跑，列进
  `context_unverified`。
- T5：进展按没跑完的数量算（run index 每行记 `unfinished`）。连续 2 次减少不到 10%：下一次按文件拆开、
  从最便宜的开始、不重放；连续 4 次：熔断，按未达标收尾。
- T6：按测试文件复用的 key 去掉了 HEAD。main 的提交只有进了某个测试文件的 import 闭包，才让它重跑；
  本 run 自己改的文件仍然对所有测试文件生效（防 importlib 这类动态加载）。剩余风险：main 的提交被
  动态加载、又不在任何闭包里，交给 CI 的 BVT。
- 审查按比例：≤ 20 行非测试、无新代码文件、不碰敏感路径的改动，不派 Gate-2、不跑 meta-review、
  不复核（声明过的 required_experts 也豁免）；审过之后又改了 ≤ 20 行且没有新文件，原审查继续有效。
- 测试分层已由 f88dce48 做到：开发时只跑 tdd / prove，落地前跑一次完整 regression。

## 9. 多个 run 抢 worker（2026-10-20 调查，未改代码）

runner 里记录的耗时是单独串行跑的 2 到 3 倍（`test_artifact_cli_run_commit.py` 61s 对 33s，
`test_artifact_cli_lifecycle.py` 52s 对 17s）。查下来，runner 自己的调度已经有约束，没有改代码：

- 全机共用一个 worker 令牌池：12 核机器上 8 个（`cpu - WORKER_BUDGET_RESERVE`），所有 runner 共享；拿不到
  令牌的只跑 1 个 worker。只有没有别的 runner 持有令牌时才扩容（`_scale_up`）。
- vitest 用 `--maxWorkers` 限制 fork 数，并按 fork 数计入令牌权重。
- HEAD baseline 只重跑**这次失败的测试**，按 sha 缓存，不会把整个选择再跑一遍。

所以放大主要来自 runner 之外：同时在跑的模型进程、daemon、前端构建，以及 macOS 报的内存压力。每个用例
起的 artifact_cli 子进程和 git 也不在令牌里，但它们是串行等待的，大约各占一个核。要进一步降低，方向是让
令牌池按当前空闲核数发放（现在只按固定池大小），这需要先在负载高的时候实测，确认减少 worker 不会反而更慢。

## 10. 待定：熔断把"一直在进展的大范围"也当成卡住

回放（30 个各 1 秒的测试文件、1 个 worker、deadline 4 秒）：每次续跑的未完成数是 29 -> 27 -> 25 -> 23，
每次都在稳定前进，但相对减少都不到 10%。按 §8 的 T5 规则，第 2 次判为卡住、按文件拆开（从最便宜的开始、
不重放）——这对等长文件没有帮助；连续 4 次就熔断、按未达标收尾，而这个范围其实再跑十几次就会跑完。

真实 run 的全范围一般 3 次调用左右就跑完，所以只在"要十次以上调用"的超大范围才会撞上。可选的改法：进展
判断改成"本次调用至少跑完了一个文件、且没有重新变多"，而不是相对比例；或者熔断只在拆分之后仍然没有文件
跑完时才触发。
