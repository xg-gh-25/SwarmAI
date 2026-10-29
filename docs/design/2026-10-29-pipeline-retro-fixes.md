---
title: Pipeline retro 修复 — 代码改动卡、一个需求一个 run、review 证明挂不上
created: 2026-10-29
updated: 2026-10-29
project: SwarmAI
status: fixing spec — 一次修完（XG 2026-10-29）
---

# Pipeline retro 修复

来源：复盘 Hive 会话 tab 的四个 run（run_edd6fe17 / run_e579851e / run_b3dcb4e8 / run_cddd5362）
和 run_8a1af4d4。下面每组写清原因、所有同因的位置、一个解法、证明它的测试。

## F1 pipeline 落地没有代码改动卡

**原因。** 改动卡的"这轮落地了什么"只读 git reflog 里 `swarm-land <branch> session=<key>
turn=<id> onto=<sha>` 这条标记（`core/session_landings._STAMP`，只认 `session/` 分支），
只有 `scripts/session_worktree.py land` 写它。pipeline 落地的 `git merge --ff-only`
（`core/run_worktree._merge_carrying`，人工落地 `core/run_protocol/landing._fast_forward`）
写的是 git 默认的 `merge <sha>: Fast-forward`。同一轮里 run worktree 在落地后被删掉，
`TurnChanges.finish()` 按文件 diff 也拿不到。另外 `run_owner_gate` 只在命令里出现
`scripts.session_worktree` 时给它 turn id，并设 `_turn_sw`（这轮读落地记录的开关）。

**同因的位置。** `_merge_carrying`、`landing._fast_forward`、`session_worktree.land`
（三个写方，一个格式）；`_STAMP`（读方）；`run_owner_gate._gate`（turn id 的来源）。

**解法。**
- 格式只定义一次：`session_landings.stamp(branch, onto)` 从环境读
  `SWARM_APP_SESSION_ID` / `SWARM_TURN_ID`，生成标记（无效的写 `none`）。三个写方都用它，
  merge 都带 `core.logAllRefUpdates=always`。pipeline 的分支名是 `run/<run_id>`，
  onto 是 merge 前 main 的 HEAD。
- `_STAMP` 接受 `session/` 和 `run/` 两种分支。
- `run_owner_gate`：一条 pipeline 落地命令（`pipeline_command.is_land`）和
  session_worktree 命令一样，前面加 `export SWARM_APP_SESSION_ID=… SWARM_TURN_ID=…;`
  并设 `_turn_sw`；sub-agent 的命令不带 turn id（原规则）。
- 卡片本身不改：落地行、文件 +/-、diff 都复用 ChangesCard 已有的 landing 渲染。

**测试。** 读方：`run/` 分支的标记被 `read_turn` 读出（onto..new 的文件）。写方：
`run_worktree.land` 后 main 的 reflog 最新一条是该格式，带环境里的 session 和 turn。
gate：`pipeline-v1 land` 命令得到 export 前缀并设 `_turn_sw`；不是落地的 pipeline
命令不变。

## F2 一个需求被拆成多个 run

**原因。** 引擎不要求拆：一个 `full` run 的 BUILD 输入就是 DESIGN_SPEC 的全部任务，
做法不限。拆是说明文字要求的：`stages/evaluate.md`（"能各自提交、各自验证的部分，每块
一个 run"）、`INSTRUCTIONS.md`（"EVALUATE says how to split a task into runs"）、
`backend/context/AGENT.md` § Pipeline Profile（"Decompose a big effort into an ordered list
of runs"）、`lifecycle.REMOVED_PROFILES["goal"]`（"split a large task into a sequence of
runs"）。代价（四个 run 实测）：约 125 分钟、210 多万 token，EVALUATE / Gate-0 / REVIEW /
TEST / DELIVER / REFLECT 各跑四遍；第 2 步审的是没有调用方的一层，问题被挪到下一个 run，
落地次数多，撞上 main 移动的次数也多。

**解法。** 四处改成同一句话：一个需求一个 run，不管它有几部分。PLAN 把每一部分列成任务，
BUILD 按任务原生做完全部，REVIEW 审整个改动一次，DELIVER 落地一次。只在部分之间是互不相关
的需求（各自有用户、各自可回滚），或用户明确要分开落地时，才拆成多个 run。
`REMOVED_PROFILES["goal"]` 改成指向 full。

**测试。** 已有的 `test_pipeline_v1_goal_removed` / `test_pipeline_profiles` 匹配新提示；
`scripts/lint_skills.py` 通过。

## F3 review 证明挂不上

**F3a 原因：finding 的路径不是仓库根路径。** expert 写 `core/x.py` 而仓库里是
`backend/core/x.py`。三处解析（`engine._repo_of_finding`、`_finding_repo`、
`_resolve_reported_tests` 里的候选）都找不到文件，证明报"resolves to 0 repos"。
**解法：** 在 finding 进入的唯一入口（`hook_bridge._write` 的 `_occ`，hook 和 relayed
两种来源都经过它）把路径规范成仓库根路径：handout 的 `code_trees` 里该路径不存在、而这些
tree 里恰好一个文件以 `/<路径>` 结尾时，用那个文件；零个或多个时原样保留。另外输出契约写明
`file` 是从仓库根开始的路径，和 diff 文件头一致。

**F3b 原因：修复写在另一个文件。** `design` / `security_policy` / `docs` 类 finding 靠
diff_assertion 关闭，引擎只看 finding 那个文件"R1 之后改过、R2 之后没改"
（`_file_assertion`），R2 拿到的 diff 也只有那个文件（`_fix_diffs`）。修复是另一个文件
（比如把一个边界写进设计文档）时，R2 判了 RESOLVED，证明仍被拒。
**解法：** review-address 的条目可带 `fix_files`（仓库根路径，1–20 个），记在 address
事件上。R2 的 diff 和 diff_assertion 都按"最新一次 address 的 `fix_files`，没有就用
finding 的文件"来算；每个文件都必须 R1 之后改过、R2 之后没改。`fix_files` 里的路径必须在
当前树里恰好属于一个仓库、而且是文件（不是目录），否则 review-address 什么都不写并说明。
本次 address 的 finding，R2 的 diff 只按本次 address 给的 `fix_files` 算（没给就是 finding
自己的文件），不沿用更早一次 address 的，这样 R2 看的和证明判的始终是同一批文件。一个证明
只绑一个仓库：`fix_files` 跨仓库时拒绝。证明事件记下每个 fix 文件的 blob
（`fix_files: [{path, blob}]`），`stale_closures` 对这类关闭检查这些文件，而不是 finding
自己的文件：修复被 rebase 撤掉时关闭会失效。

**测试。** F3a：一条写了缺前缀路径的 finding 落成仓库根路径；有歧义时原样。F3b：修复在
另一个文件时，带 `fix_files` 的 diff_assertion 成立、R2 diff 含该文件；不带时照旧被拒；
一个不存在的 `fix_files` 路径被 review-address 拒绝。

## Review（correctness + security，一轮）

两条 MED、六条 LOW，全部核对过源码。改了的：R2 和证明看的文件不一致（MED）；`fix_files`
关闭不随修复文件失效（MED）；`fix_files` 接受目录；`core.logAllRefUpdates=false` 且分支还
没有 reflog 时不写记录（落地 merge 现在总带 `logAllRefUpdates=always`）；`_repo_paths` 对
异常形状的 contract 抛错；审批阶段的 run-land 改写缺测试。每处修复都做了 mutation 检查。

## 不改的

- 模型自己在命令前写 `export SWARM_TURN_ID=…` 可以伪造落地记录里的 turn（只影响改动卡显示
  在哪一轮，不涉及权限）。session_worktree 本来就是这样，不是这次引入的；要堵需要把
  `SWARM_TURN_ID` 加进 session_link_gate 的保护，单独做。
- 被拒绝的落地命令也会让这一轮去读一次落地记录（`_turn_sw` 在判定之前设置），只多一次读，
  和 session_worktree 现有行为一致。
- `_fix_diffs` / `_file_assertion` 的 `git diff -- <file>` 没用 `:(literal)`（加固项，非缺陷）。
- `tests/test_session_landed_path.py` 有 4 个用例在未改动的 main 上就失败，不在这次范围。

- answer 检查找不到提示板时放行（CF-81c433b6）：之前有意保留，不在这次范围。
- 已结束的 run 的事件不重写：已有 finding 的路径、已有落地的 reflog 都保持原样。

## 验证计划

| 组 | 写方 | 读方 | 最终测试集 |
|---|---|---|---|
| F1 | stamp（三处） | session_landings.read_turn | test_session_landings、test_run_worktree 落地、test_run_owner_gate、新测试 |
| F2 | 说明文字 | 下一个 run 的 agent | goal_removed、profiles、lint_skills |
| F3 | hook_bridge `_occ`、address `fix_files` | engine 三处解析、`_fix_diffs`、`_file_assertion` | review / findings / closure 相关已有测试 + 新测试 |

每处修复做 mutation 检查（撤掉修复，对应测试变红）。
