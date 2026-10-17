---
title: 并行 session 的提交隔离（待讨论）
date: 2026-10-17
created: 2026-10-17
updated: 2026-10-17
project: SwarmAI
status: DRAFT — 从 pipeline-speed-systematic-fix.md 拆出，待与 XG 单独讨论，未排期
related: docs/design/pipeline-speed-systematic-fix.md
author: Kiro
---
# 并行 session 的提交隔离（待讨论）

> 原为提速方案里的工作流 H。XG 2026-10-17 决定从提速方案的范围里拿出来单独讨论，这里保留草稿。

## 现状问题
- 所有 session 共用 `swarmai` 这一个工作区和一个 git index，直接在 main 上工作。
- 2026-10-17 提交提速方案时，其他 session 已经暂存了 `artifact_cli.py` 和 `test_artifact_cli_run_commit.py`，还改了 `security_hooks.py` 和 `test_reviewed_paths_multirepo.py`，只能用 pathspec 只提交自己的文件。
- `48d377ec` 自己写了 `commit_mine.py`，用临时 index 只提交本 run 的改动，提交相关调用 12 次，其中 R1 commit gate 拒绝 4 次。5 个 run 里 `git add` 和暂存检查共 42 次。
- `48d377ec` 在澄清问题 Q2 里专门选了一个方案来绕开 `bfd8b9d3` 暂存的 `TabView`、`turnView`，靠模型自己协调。
- `adversarial_commit_gate` 把整个 index 当作待提交集合，别的 session 暂存的文件也要求被 review 覆盖（deliver.md 里有一大段专门解释这个问题）。

除了慢，还有正确性风险：
1. `git commit -- <file>` 提交的是工作区里整个文件，两个 session 改了同一个文件时，别人改了一半的代码会混进你的 commit。
2. 测试跑在别的 session 改了一半的代码上，出现的失败既不是回归也不是 pre-existing，会被误判。
3. 共享 index 被反复写，模型要花调用去检查和清理。

## 方案一（符合"只在 main 上直接提交、不建分支"的现有规则）
- **H1. 文件认领。** 一个 session 第一次写某个文件时认领它，登记在 `~/.swarm-ai/state/file_claims.json`（路径 → session_id、run_id、认领时间）。另一个 session 再写这个文件时，PreToolUse 拦下（Write、Edit，以及已经能识别的 Bash 写入），deny 消息写明谁在改、从什么时候开始。认领在 run-commit 后释放；session 结束或 COLD 超过 1 小时自动过期。
- **H2. 私有 index 提交。** `run-commit` 用私有的 `GIT_INDEX_FILE`：以 HEAD 为基础，只加入本 run 的文件，在这个 index 上提交；提交后只刷新共享 index 里这些路径的条目，别的 session 暂存的内容原样保留。即把 `48d377ec` 临时写的 `commit_mine.py` 做成正式工具。
- **H3. gate 只看本次提交的路径。** `adversarial_commit_gate` 的待提交集合改为"本次提交的 pathspec ∩ 私有 index"，不再是整个共享 index。
- **H4. 测试归因。** runner 发现失败的测试涉及被其他 session 认领的文件时，标注为"其他 session 正在改"，不计入回归。
- 剩下的限制：测试仍然会看到其他 session 在别的文件里尚未提交的改动。H4 能正确标注这类失败，但不能完全隔离。
- 依赖：提速方案 E1 的 run↔session 关联（`SWARM_APP_SESSION_ID`）。

## 方案二（彻底隔离，需要 XG 决定是否放宽规则）
- 每个 run 在自己的 git worktree 里工作（本地临时分支，不 push），run-commit 时 rebase 到最新的 main 并 fast-forward 落地，最终 commit 仍然直接在 main 上。
- 好处：每个 run 有独立的工作区和 index，测试只跑自己的代码；两个 run 改了同一处时，冲突在落地时显式出现。
- 代价：AGENTS.md 写的是"Commit directly to main — never auto-branch"，需要 XG 同意"本地临时分支、不 push"不算违反；`desktop/node_modules` 要软链到主工作区；`Projects/SwarmAI` symlink 指向主工作区，`files_touched`、Canvas 链接和 DDD 路径都要能识别 worktree 路径。

## 风险
| 风险 | 对策 |
|---|---|
| 文件认领造成死等 | session 结束或 COLD 超过 1 小时自动释放；认领只限制写，不限制读和测试。 |
| 私有 index 提交后共享 index 不一致 | 提交后只刷新本次提交路径的条目，并用测试覆盖"别的 session 已暂存的内容在提交后保持不变"。 |

## 待讨论
- 先做方案一还是直接方案二？
- 认领的粒度（文件还是 hunk）和过期时间。
- 是否需要 `direct` profile 之外的手工提交也走私有 index。
