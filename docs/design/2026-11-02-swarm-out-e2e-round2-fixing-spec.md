---
title: Swarm Out E2E 第二轮 fixing-spec（真实任务测试测出的问题）
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r1，待 XG 确认后实施
---

# Swarm Out E2E 第二轮 fixing-spec

一句话：2026-11-02 派了四件真实任务（一件编码、三件调研）走完整条 Swarm Out 流程，测出的问题按原因分成五组，每组一个方案、一组先红后绿的测试，一次修完。

## 0. 来源和范围

**测试本身。** 四单都派到两台 Hive（demo、explorer），全部跑完：

| 单 | 类型 | 结果 |
|---|---|---|
| `so_efd0d6989e4f` 修 AI 文档刷新脚本 | 编码，native，返回 commits | 改到受保护路径，agent 接手在本机落地（`73555184a` `7852330b6` `e9c1b37a1`） |
| `so_6024d9705fc4` i18n 后包体积上限 | 调研，返回回复 | 回复被截，重跑一次让 Hive 写成文件后验收 |
| `so_9eb2946188f9` 丢弃单子的 pipeline 决定 | 调研，返回回复 | 同上；Hive 复现出一个 Hive 端 bug（本 spec 的 G4） |
| `so_cb13960bdad3` Agent 评测报告 | 调研，返回文档 | 一次验收 |

**纳入本 spec：** G1–G5（下文）。

**不纳入，及原因：**

- **决策归谁、怎么留痕、受保护路径谁能落地、agent 接手后怎么结单：** 在 `docs/design/2026-11-02-hive-run-decision-record.md`（r3）。E2E 里"批准落地后报冲突"那一条也归它：那次的冲突是真的（`docs/CODEBASE_METRICS.md` 的代码图那一行，Hive 写"not measured"，本机补成真实数字）。按 r3，agent 落不了地（冲突）时，就在本机用自己的提交接手改动，再丢弃这一单；commits 已经取回到本机的 `refs/hive/`，所以丢弃不会丢东西。本 spec 不另做检查。
- **Hive 上没有代码图**：这次唯一的后果是指标那一行写成"not measured"，本机重跑就补上了；Hive 上缺 `code_intel.impact` 时，查调用方还有 grep 可用。为每次派单建一张代码图要花时间，眼下没有测到它挡住了什么（P9），不做。
- **Hive 没有 git 身份**：实测不成立。`core/hive_workspace.py:340-341` 已经给可写仓库设了 `user.name` / `user.email`（`SWARM_IDENTITY`），Hive 回复里的"git 没有身份"说的不对。

## 1. 分组总览

| 组 | 原因（一句话） | 现象 | 严重度 |
|---|---|---|---|
| G1 | Host 只存回复的前 4000 字 | agent 和人都看不到回复后半段，只能重跑，白白用掉一次重发 | 高 |
| G2 | 检查状态靠在 Bash 文本里找命令来猜 | 卡片说 5 项没看到、2 项出错；Hive 说 11 项全过；两边都不可信 | 高 |
| G3 | 重跑后，上一轮的结果仍被当作"最终" | 约 20 秒内卡片提供"验收"，验收的是旧结果 | 中 |
| G4 | Hive 释放工作区时不结束这单的 pipeline run | Hive 上永久留一条阻塞的 Needs You；决定若在 PLAN 之后，run 再也 abandon 不了 | 高 |
| G5 | 交回的改动文件在本机没有可打开的东西 | 结果卡上改动文件点不开，人要批准落地却看不到 diff | 中 |

## 2. G1 回复截断

**现象。** `so_9eb2946188f9` 回复 8,535 字、`so_6024d9705fc4` 回复 6,700 字，`read` 和卡片都只有前 4,000 字，修法和方案 C 的工作量、风险都在被截掉的那半。两单各用掉一次重发（每单上限 2 次）去要全文。

**原因。** `core/swarm_out_results.py:54` `REPORT_CHARS = 4000`；`:361` 取最后一段文字为 `report_full`；`:510` 只存 `report_full[:REPORT_CHARS]` 和长度。存进去的就是截断的，`read`（`scripts/swarm_out.py:427-428`，`GET /runs/{id}/result`）和卡片（`desktop/src/components/swarm-out/RunOverlay.tsx:188`，`:256` 显示"完整回复 N 字"提示）都只能拿到这一截。

**同因的其他位置。** 只有这一处截。`runs` 列表本来就不带回复正文（`core/swarm_out_results.py:1118` 只投影 `report_chars`），所以存全文不会让列表变大。

**方案。**
1. `summarize` 存 `report_full` 全文，删 `REPORT_CHARS`；`report_chars` 保留。`SUMMARY_VERSION` 加一，已存的结果在下次检查时重新汇总（`:56-59` 的现有机制）。
2. 卡片按全文渲染，删 `RunOverlay.tsx:256` 的"完整回复 N 字"提示。回复区已有滚动。
3. 这是 AGENT R23"单元不截断"：一条回复是一个单元，要么整条给，要么不给。

**测试（先红后绿）。**
- 后端：一条 9,000 字的回复经 `summarize` 后，`result["report"]` 等于原文，`report_chars == 9000`。改回截断时变红。
- 后端：旧版本 `summary_version` 的结果再检查时会重新汇总（已有机制，补一条覆盖断言）。
- 前端：`report_chars > report.length` 的提示不再出现；一条长回复整条渲染。

## 3. G2 检查状态靠猜

**现象。** `so_efd0d6989e4f` 的卡片：`backend-import-smoke` 等 5 项"没看到"，`desktop-typecheck`、`desktop-contract-lint`"出错"。Hive 的回复说"11 项全过"。

**实测原因（从 Hive 会话读到的命令，经 `/api/swarm-out/remote/{sid}/chat/sessions/{sid}/messages`）：**
- 5 项"没看到"：Hive 用一个循环跑了它们：`P=backend/.venv/bin/python; for s in smoke_import ...; do ... $P scripts/$s.py ...`。卡片的匹配（`core/swarm_out_results.py:195-204` `_runs`，`:244`）在 Bash 文本里找字面的 `python scripts/smoke_import.py`，变量和循环它看不出来。这几项其实跑了。
- 2 项"出错"：跑 `npx tsc --noEmit` 和 `npm run lint:contract` 的那一次 Bash 调用本身出错（`is_error: true`，没有结果内容），之后会话里没有再跑或读日志的记录。所以卡片的"出错"更接近事实，Hive 回复里的"全过"没有依据。
- Setup 也有同类问题：Project Agent 的 setup 是从 `.github/workflows/ci.yml` 原样搬来的（`core/project_agent_draft.py:18`，`core/task_spec.py:371-374` 当作文字交给 Hive），里面有 `echo "$VIRTUAL_ENV/bin" >> $GITHUB_PATH`，只在 GitHub Actions 里有意义。Hive 只好自己装 uv（`/tmp/uvboot/bin/uv`）、手动拼 PATH 来跑 BVT。

**根本原因。** 判断"这项检查跑了没有、结果如何"靠的是内容（命令文本长得像不像），不是由执行者给出的身份（AGENT R3 第 8 条）。agent 怎么写命令是它的自由，文本匹配永远会漏；而 agent 自报的"全过"又不能当证据。

**方案：检查由一个按 id 执行的命令来跑，结果由它记录。**
1. **Host 把结构化的 setup 和检查送给 Hive。** 派单时 order 带上 `setup` 和 `validations`（`id`、`command`、`repo`、`cwd`），今天只有渲染进任务文字的版本（`core/swarm_out.py:799` 只给结果检查用）。
2. **Hive 新增 `scripts.task_checks`：**
   - `setup <work_id>`：在声明的仓库和目录里，把 setup 逐条交给 `bash -e` 执行。仿照 GitHub Actions 的约定，提供 `GITHUB_PATH`、`GITHUB_ENV` 两个临时文件，执行后把其中的路径和变量带进后续的检查。这样从 CI 搬来的 setup 不用改写就能跑，而且对所有用 GitHub Actions 的仓库都成立（P7）。不认识的 CI 写法照常执行，失败就记为失败。
   - `run <work_id> [<id> ...]`：按 id 跑检查（不给 id 就全部跑），每项记一行 `{id, exit, started_at, ended_at, log}` 到 `Outputs/<work_id>/checks.jsonl`，日志放在旁边。
3. **任务文字改为"跑 `task_checks run <work_id>`"**，不再列出原始命令让 agent 自己跑（`core/task_spec.py:375-378`，"Before you finish, run:" 一段）。
4. **Host 读 `checks.jsonl` 给出状态：** `exit 0` 为"跑过，退出 0"，非零为"出错"，没有记录为"没跑"。仍然不写"通过"：退出码说的是命令有没有出错，不是它查到了什么（`_checks` 文档字符串的原则不变）。旧版 Hive 没有这个文件时，退回到今天的文本匹配，卡片标注"按命令文本推断"。
5. **Hive 版本门槛：** 只有新版 Hive 会收到结构化的检查；`spec` 选 Hive 时把它作为一项能力（沿用"没有运行中的 Hive 有这个版本"的现有机制）。

**安全。** `task_checks` 只执行 order 里 Host 声明的命令，在该单的工作目录下运行，和 agent 今天自己用 Bash 跑这些命令的权限完全一样。命令来自仓库自己的 CI，由 Host 的 Project Agent 定义传过去，Hive 的 agent 改不了。`bash -e` 跑的是常量字符串，不拼接任何外部输入。

**这份记录能证明什么。** 记录由 `task_checks` 写，所以循环写法看不出来、agent 回复里自称全过这类误报不再决定状态。但它是 Hive 的记录，不是证明：Hive 上 agent 和 daemon 是同一个 OS 用户，agent 自己也能写这个文件（`core/hive_workspace` 模块文档：Hive 上没有 agent 碰不到的文件，run-spec r4.2 的选择 A）。所以它防的是无意的误报，防不了刻意伪造；要防伪造，得让 agent 换一个 OS 用户运行，那是另一份设计。验收不由这个状态决定，由 agent 或人看结果判断。

**测试（先红后绿）。**
- `task_checks setup`：一条写 `$GITHUB_PATH` 的 setup 之后，`run` 里的命令能在 PATH 上找到那个目录里的程序。去掉 GITHUB_PATH 支持时变红。
- `task_checks run`：一项 `exit 1` 的检查记为出错，`exit 0` 记为跑过；记录里有 id 和退出码。
- Host `_checks`：有 `checks.jsonl` 时按它给状态，Bash 文本里的循环写法不影响结果；没有这个文件时退回文本匹配，并带上"推断"标记。
- 本次的真实会话作为一个用例：同一份消息加一份 `checks.jsonl`，5 项"没看到"变为"跑过"，tsc 那项按记录给出。

## 4. G3 重跑后旧结果被当作最终

**现象。** 两单重跑后，那一轮结束时 `watch` 先报"已停、可验收"，带的却是上一轮的 `ended_at`；大约 20 秒后才刷新成新一轮的结果。期间按卡上的 `ended_at` 验收，验收的是旧结果。另外，重跑后状态出现过 `running · waiting`，但等待列表为空：这是 Hive 会话的 `activity` 值原样当成了原因（`core/swarm_out.py:1376-1377`），不是有等待。

**原因。** 判断结果是否最终的 `_final`（`core/swarm_out_settle.py:267-269`）只看结果本身是否结束、是否 provisional，不知道这单在结果结束之后又发过一次重跑。重跑记录在 settle 记录的 `commands` 里（带 `at`），`_final` 没有读。

**同因的其他位置。** `swarm_out_actor.py:128-132` 校验 `accept` 时也用 `_final` 和结果的 `ended_at`，所以 agent 在这段时间里验收会被放行；之后 `derive` 发现新一轮替换了旧结果，会让这次验收作废（`swarm_out_settle.py:296-303` 的说明），不会丢东西，但卡片会来回变，也白做了一次。

**方案。**
1. `_final(res, rec)`：只要 settle 记录里有一条重跑，时间晚于 `res.ended_at`，这个结果就不是最终的。`derive` 和 actor 都走这一个判断（MEMORY："共享的状态判断只能有一个谓词"）。
2. 这段时间里，阶段显示为"重跑已发出"，不提供验收。
3. `running` 的原因只用已知的取值：Hive 的 `activity` 不在已知集合里时，原因写成 `streaming`，原始值放在一个单独的字段里。这样"waiting"不会出现在 `running` 下面。

**测试（先红后绿）。**
- 结果 `ended_at=T`，settle 记录里有一条 `T+5s` 的重跑：`_final` 为假，`actions` 里没有 accept，actor 拒绝 `accept --ended-at T`。删掉新条件时变红。
- 新一轮结果 `ended_at=T+60s` 到达后：`_final` 为真，提供验收。
- Hive 的 `activity="waiting"`、`streaming=false`：状态是 `running`，原因不是 `waiting`。

## 5. G4 Hive 释放工作区时不结束 pipeline run

**现象（Hive 复现，本机核对源码属实）。** 一单停在 pipeline 决定上时被丢弃：
- `run.json` 一直是 `awaiting_human`；`pending_decisions` 还在列出它，`decisions.json` 照样推送给 Host；Hive 的 Needs You 留着一条 BLOCKING，直到 `run.json` 满 30 天。
- 决定若出现在 PLAN 之后（已有 run worktree）：释放删掉了 main checkout，worktree 再也清不掉，`pipeline-v1 abandon` 也失败（`git rev-parse --git-common-dir` 返回 128），这个 run 永远结束不了。

复现脚本和完整分析：`Projects/SwarmAI/deliverables/hive/2026-11-02-so_9eb2946188f9/Outputs/so_9eb2946188f9/`（`answer.md`、`repro/repro_discard_release.py`）。

**原因。**
- 丢弃时 Host 发的 stop，到了 `RunEngine.record_stop`（`core/run_protocol/engine.py:652-663`），只处理 `active` 的 run，`awaiting_human` 原样保留（这是对的：人可能还会回答）。
- 释放 `hive_workspace.release`（`core/hive_workspace.py:551-566`）只在会话忙时拒绝，然后 `_remove`（`:379-387`）删掉工作目录、outputs 和 order 文件，不管这单会话拥有的 pipeline run。
- `pending_decisions`（`core/pipeline_human.py:170-178`）读所有未结束的 run，没有期限。

**方案。**
1. `release` 在 `_remove` 之前，找出这单会话（order 的 `session_id`）拥有的每个未结束的 v1 run，逐个 `RunEngine.abandon(reason=USER_CANCEL_REASON)`（`engine.py:1899`；`USER_CANCEL_REASON` 在 `core/pipeline_metrics.py:65`）。必须在删 checkout 之前做，worktree 才能被保存并移除。
2. 有一个 abandon 失败：抛 `WorkError`（422），不删任何东西；Host 的 `release_due` 对非 200/404 会退避重试（`core/swarm_out_settle.py:596` `mark_released`、`:611` `release_due`）。
3. "找出这单会话拥有的 run"和 `pending_decisions` 用同一个来源（`run_index.runs` 加 run 的 owner），不新造索引。

**不做：** Host 侧再拒绝对已丢弃单子的回答。`closed_state`（`swarm_out_settle.py:296-310`）已经让 Host 代理和人的命令拒绝已关闭的单子，丢弃到释放之间的窗口很短。

**测试（先红后绿）。** 把 Hive 的复现脚本改成两条测试：
- 决定在 EVALUATE（没有 worktree）：释放后 run 已结束，`pending_decisions` 里没有它，Needs You 为空。
- 决定在 PLAN 之后（有 worktree）：释放后 worktree 已保存并移除，run 已结束。
- abandon 失败（mock 抛异常）：释放返回 422，工作目录和 order 都还在（R28：恢复路径要有执行测试）。

## 6. G5 改动文件看不到

**现象。** 结果卡的"What came back"里，仓库改动文件点了没反应；受保护文件的勾选框写着"我已读过这些改动"，卡片却不提供阅读的途径，只能把卡片底部的 `git diff` 命令拿到终端去跑。

**原因。**
- 改动文件行用的是 `ItemRow`，没有 `onOpen`（`RunOverlay.tsx:243-246`）；带回的普通文件用 `FileItemRow`，可以打开（`:222`）。
- 这些改动在本机不是文件：`return-prepare` 之后它们才出现在 `refs/hive/<id>/<key>` 这个 ref 上（`core/hive_return.py:379-419`）；源码仓库也在 workspace 之外。
- FileViewer 已有 diff 视图（`autoDiff`，可以指定对比的 git ref，`desktop/src/components/file-viewer/FileViewer.tsx:67-70`），但它的内容来自磁盘文件加上 `GET /workspace/file/committed`（`backend/routers/workspace_api.py:14`），读不到一个仓库 ref 里的文件。

**方案。**
1. **只读接口** `GET /api/swarm-out/runs/{id}/return/file?url=<repo>&path=<path>`：要求这单已经 prepare，`path` 必须在 prepare 列出的文件里（不接受任意路径）。用 `git show <base>:<path>` 和 `git show <ref>:<path>` 只读对象，不碰工作区，返回 `{base, tip, binary, too_large}`。新增或删除的文件，对应一侧为 null。
2. **FileViewer 增加只读的"两个版本"来源**：直接拿到 base 和 tip 的文本，用现有 diff 视图显示，不能编辑、不能保存。
3. **改动文件行接上 `onOpen`**：单子还没 prepare 时，先 prepare 再打开。受保护文件同样能看，勾选框旁边加一句"点文件名看改动"。

**安全。** 只读，只接受 prepare 时列出的路径，只读 git 对象，不在本机写出 Hive 的文件，也不执行任何东西（`hive_return.land` 文档字符串里"没有工作区装着 Hive 的文件"的原则保持不变）。

**测试（先红后绿）。**
- 后端：已 prepare 的单子，列出的路径返回 base 和 tip 两个版本；不在列表里的路径、`..`、绝对路径一律 400；没 prepare 时 409。
- 后端：新文件 base 为 null，删除的文件 tip 为 null，二进制文件 `binary: true`、不带内容。
- 前端：点击改动文件行会打开 FileViewer 的 diff 视图；没 prepare 时先调用 prepare。

## 7. 实施顺序和提交

每组一个提交，按 R4（抽取和扩展分开）：

1. G1（最小，后端加前端各一处）
2. G3（只改 `_final` 及其两个调用方）
3. G4（Hive 端，需要发新版 Hive）
4. G2（Host、Hive、任务文字三处，需要发新版 Hive）
5. G5（后端接口、FileViewer 新来源、卡片接点击）

G2 和 G4 都要发新版 Hive：合在一次发版里，发版之后更新两台 Hive。

## 8. 验证计划（现在定下，不在收尾时再加）

**每组的测试：** 第 2–6 节列出的那些，每条先在修改前跑一次确认是红的，修完后变绿；用 `scripts.mutation_check` 对每组的关键改动做一次反向检查。

**提交前：** 改动涉及的函数及其直接调用方的现有测试：
- `test_swarm_out_results*`、`test_swarm_out_settle*`、`test_swarm_out_actor*`
- `test_hive_workspace*`、`test_hive_return*`
- 前端 `runResult.test.tsx`、`runReview.test.tsx`、FileViewer 测试

**全部完成后，真实复测一次：** 两台 Hive 更新到新版，重派这次的四件任务（同样的任务文字），逐行核对：
- 长回复在 `read` 和卡片上都是完整的；不需要为了要全文而重跑。
- 编码单的卡片上，检查状态来自 `checks.jsonl`，与实际执行一致。
- 发一次重跑，在新结果到达之前卡片不提供验收。
- 派一单停在 pipeline 决定上，然后丢弃：Hive 上没有遗留的 Needs You。
- 结果卡上每个改动文件都能点开看 diff。

**专家审查（`s_expert-review`，最多两轮）：**
- G2、G5：security（执行命令、读文件的接口）和 correctness。
- G3、G4：state-machine 和 correctness。
- G1：不审（一处删截断加一处删提示）。

## 9. 待 XG 确认

1. G2 的方案改动最大（Host、Hive、任务文字三处）。另一个更小的做法是只修匹配：把变量和循环展开后再匹配。我不建议这么做：agent 写命令的方式是无穷的，这样做永远修不完，而且"Hive 自报全过但其实出错"这个问题它解决不了。
2. G2、G4 要发新版 Hive。发版和更新 Hive 我来做，你看结果即可，除非你另有安排。
