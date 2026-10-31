---
title: Pipeline 评审到点就收、落地冲突有路可走
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: r2 — 零上下文审查一轮已并入；未实施
---

# Pipeline：评审到点就收、落地冲突有路可走

r2 · 2026-10-31 · 起因：`run_8437ff7e`（Swarm Out 单路收尾）

## 0. 一句话

这个 run 跑了近两个小时，最后 40 分钟一直在修 LOW 级发现，评审一共跑了 5 轮。到 DELIVER，rebase 冲突改变了补丁，引擎要求重新评审，却没有回 REVIEW 的路，run 只能放弃、改走手动落地。之后 Needs You 又把这个已经落地的 run 报成"没落地就丢了"。

这些不是 agent 不守规矩，是引擎的几条规则互相矛盾。本文按原因修 4 处，另外改一处 skill 文字。

## 1. 实测（事件日志 `pipeline_agent_audit/runs/run_8437ff7e/events.jsonl`）

| 时间 (UTC) | 发生了什么 |
|---|---|
| 10:46 | R1：correctness + security，共 9 条发现（1 HIGH、3 MEDIUM、5 LOW） |
| 11:01 | 9 条全部 address，派 R2 |
| 11:04 | R2 回答：HIGH `CF-97084f77` 判 RESOLVED；`CF-bb8b8a54`（LOW）判 TEST_INADEQUATE；另有 3 条新发现，全是 LOW。这时 `next` 的 action 已经是 "stage-complete, every review check passes"（`next_step.py:139-142`），REVIEW 的完成检查只有 RV-4、RV-5（`checks.py:141-145`）。但同一份 `next` 附带的 findings 列表给每条没关的发现都写了 "fix it…" |
| 11:09 | agent 照 findings 列表去修 → 代码变了 → RV-4 要求增量评审 → 第 3 轮 |
| 11:11 | 增量评审：security 4 条（1 MEDIUM）。correctness 的回答**整份没解析**（§2.3），里面 3 条发现丢失，其中 1 条 MEDIUM 是这次修改引入的回归。`next` 没有安排重问 |
| 11:15 | address 5 条 → 第 4 轮 R2 → 1 条 OPEN、2 条 TEST_INADEQUATE |
| 11:19 | 再修 → 第 5 轮增量评审 → 2 条新发现 |
| 11:24 | REVIEW 完成 |
| 11:35 | 落地：main 被另一个会话推进，rebase 冲突（LG-4 `merge_conflict`）；LG-1 先说 "test again and report" |
| 11:39 | 冲突解决、重测后再落地：LG-1 "this change was never reviewed"，两次 |
| 11:44 | 放弃。XG 批准手动落地为 `827a1e29b`（就是 run 分支 `run/run_8437ff7e` 的 tip） |

总计 10 次专家派发，147.6M billed tokens。专家自己只占约 6M，其余是主会话 410 次调用、每次约 36 万 token 的上下文（cache read）。多出来的轮次贵在主会话多跑了几十次调用，不在专家身上。

全部 6 个 v1 run 里，28 个评审回答有 2 个整份没解析，都是"一行格式不对，整份作废"。

## 2. 根因

### 2.1 同一份 `next` 里两条指令矛盾，findings 列表让 agent 修每一条

`review.close_with`（`review.py:142-176`）给每一条没关的发现都写 "fix it…"，不管严重度，也不管 R2 是否已经回答。`next` 的 action 说可以完成了，附带的 findings 列表却列了一串待修项。skill 里写的是 "MEDIUM/LOW are residual risks. No extra experts, no third round"（`stages/review.md:14`），10-26 设计 §9 也写"不加第三轮审查"。agent 选了更具体的那份：findings 列表。

### 2.2 评审之后改代码，就是一轮新的、可以无限接续的评审

RV-4（`checks.py:417-425`）在评审之后代码一变就要求增量评审；增量评审的 handout 是 round 1（`engine.py:2973`），它的发现可以 address，address 了又要 R2（RV-5）。增量评审的上限是每个专家整个 run 3 次（`engine.py:2910`），所以"两轮"只对第一组 R1/R2 成立。

### 2.3 回答没解析时，重问机制被挡住了

`answers.parse_answer` 一行出错整份就是 `unparsed`（`answers.py:186-224`）。这本身是对的：不合格式的一行可能正是一条 HIGH，部分接受等于替专家删发现。问题在后面：

- 已有"没解析就重派一次"的机制（`next_step.py:148-163`，`engine.py:2711-2723`），但 `next` 在它前面先判断"没有挡住的检查 → stage-complete"（`next_step.py:139-142`），所以只要检查都过了，重问永远走不到。11:11 那次丢失就是这样漏掉的。
- 它按"这个专家有几个 R1 handout"计数（`len(mine) >= 2`），增量评审的 handout 也算进去。所以一次 R1 加一次增量评审，就已经被当成"两次都没给出能用的回答"。
- 重问不告诉专家上次哪里错了，同样的错可能再犯。

### 2.4 DELIVER 里补丁变了，只有死路

LG-1（`checks.py:340-358`）要求落地的补丁（patch id，`--verbatim`，`run_worktree.py:634-644`）是某次 REVIEW 完成时记下的那个。冲突解决之后补丁变了，但：

- `review-start` 只在 REVIEW 里能用（`engine.py:2639`）；`review_reopen` 只能从 TEST 发出（`lifecycle.py:404-407`）。
- 同步时已经能发现"rebase 改了 run 自己的补丁"，LG-4 报 `patch_changed`（`engine.py:3295-3298`），但它只是一个失败，没有下一步。
- LG-1 先查 TEST 报告是不是新的，是新的才查有没有评审过（`checks.py:340-341`），所以 agent 先被叫去重测，重测完才知道要重审，重审完又要重测。
- 这些失败计入落地的 3 次（`lifecycle.py:330-333`），而落地次数整个 run 不清零。

另外，就算能回 REVIEW，今天的增量评审给专家看"上次评审的 tree → 现在的 tree"（`engine.py:2931`），rebase 之后这段 diff 包含 main 上别人的提交。`run_worktree.range_diff`（`run_worktree.py:836`）正是为"同步前后 run 自己的改动"做的复审输入，但没有地方调用它。

### 2.5 放弃记录不知道改动已经落地

`abandon` 只记 `reason`（`engine.py:1829-1831`）。`pipeline_metrics.needs_you`（`pipeline_metrics.py:68-73`）对除用户取消之外的所有 abandoned run 都返回"需要你"；前端 `PipelineOverlay.tsx:374` 也直接把 abandoned 写成 "needs you"，不读后端算好的 `needs_you`。

## 3. 设计

### 改动 1：每次评审访问最多两轮，第三轮只给 HIGH

**访问**：run 进入 REVIEW 到离开 REVIEW 的一段。边界是 `review_reopen` 事件在日志里的位置：第一次访问从头开始，之后每个 `review_reopen` 开始一次新的访问。（不能用 `attempt_base`：它数的是 stage attempt，人回答问题时也会改它，`lifecycle.py:422,455`。）R2 ledger 和 uncovered 的判断（`review.r2_ledger`、`uncovered_addresses`）加一个"只看本次访问的事件"的过滤，判断规则本身不变。

**每次访问的顺序**：

1. 第一轮评审：第一次访问是完整的 R1；TEST 或 DELIVER 退回后的访问是增量评审（改动 3），它就是这次访问的第一轮。
2. 修复 ①：address 第一轮的发现，派 R2。
3. R2 回答后：
   - 没有还开着的 HIGH/CRITICAL → 评审关闭。
   - 还有开着的 HIGH/CRITICAL（R2 判 OPEN、判 TEST_INADEQUATE、或者 R2 新报的 HIGH）→ 允许一次修复 ②，**只能 address 这些 HIGH/CRITICAL**，再派一次 R2。这次 R2 回答后评审关闭。

**关闭之后**：

- `close_with` 对 MEDIUM/LOW 返回 "residual: listed in the REPORT, no code change"。HIGH/CRITICAL 返回它今天的关闭方式，并说明"本次访问的修复已用完；在 REPORT 和审计里列为未关闭的 HIGH"。
- `review-address` 带 items 时拒绝，并说明原因。不带 items 的补派（RV-5 的补派、R2 用错词的重问）照旧。
- 不需要改代码的记账照旧：`review-proof diff_assertion`、`review-proof refuted`。
- 关闭之后又改了代码：`review-start` 不再给增量评审，`next` 说"本次访问的评审已关闭；把代码恢复到上次评审过的 tree `<tree>`"，并给出恢复命令。RV-4 不通过，REVIEW 的检查失败满 3 次走已有的 `checks_exhausted`（`lifecycle.py:337-342`）。要在评审之后改代码，正规的路是完成 REVIEW、在 TEST 里改：TEST 改代码会退回 REVIEW 开始新的访问，这有自己的上限（`REVIEW_REOPEN_CAP = 2`）。
- 删掉 `_INCREMENTAL_CAP`，上限由访问来定。

**上限**：每次访问最多 3 轮（第三轮只在有 HIGH 时出现）。整个 run 最多 1 + 2（TEST 退回）+ 1（DELIVER 退回）= 4 次访问。常见情况是 2 轮。

对 `run_8437ff7e`：11:04 R2 回答后没有开着的 HIGH（`CF-97084f77` 是 RESOLVED），评审关闭。findings 列表把剩下的 LOW 列为剩余风险，REVIEW 在 11:05 左右完成，省掉第 3-5 轮。

### 改动 2：让已有的重问机制真的能触发，并把错误告诉专家

不改解析规则（一行出错仍然整份 `unparsed`）。改三处：

- `next` 里先判断"有没有专家最新的回答没解析"，再判断 stage-complete（调换 `next_step.py:139-163` 的顺序）。
- 计数按"同一份评审"：同一个专家、本次访问、同样的 `code_trees` 的 handout 才算同一份评审的重派。增量评审是另一份评审，不计入。最多重派一次。
- 重派的 handout 开头写上解析器的错误原文，例如"你上一次的回答第 18 行是 R2 status 行，第一轮评审只接受 finding 行；如果你想说某条旧发现又坏了，把它写成一条新的 finding"。

重派之后仍然不能用：不再重派，审计的 Quality 写 WARN，open 里列出"<专家> 第 N 轮评审没有可用回答"（需要确认审计今天是否已经列出；没有就加上）。不挡完成：模型没给出能用的回答，不能让 run 卡住，但要让人看到（P5）。

对 `run_8437ff7e`：11:11 那次会被重问一次，专家会把 3 条发现（包括那条回归）重新写成 finding。

### 改动 3：DELIVER 里补丁和评审过的不同，回 REVIEW 只审这部分差异

**判断依据是事实，不是检查的文字**：落地同步之后，引擎直接比较 run 现在的补丁（patch id）和所有 REVIEW 完成时记下的补丁（`_review_facts` 已经算好 `patch_ids` 和 `reviewed_patches`）。条件：

- 补丁不在评审过的里面；
- 没有未解决的冲突（rebase 不在进行中）；
- 不论原因：冲突解决改了补丁（同步报 `patch_changed`），还是 agent 在 DELIVER 里改了代码。

满足时，不管 TEST 报告新不新，引擎把这次落地尝试的守卫都记完（LG-1、LG-4 的 check 写 `cause: "review_reopened"`），然后走和 TEST 退回**同一个**函数（把 `engine.py:2228-2252` 里 TEST 的退回抽成 `_reopen_review(from_stage)`，两处共用）：

- 只改了文档类文件：引擎自己完成那次 REVIEW（已有的 `_doc_paths_since_review` / `_complete_review_without_experts`），回到 TEST。
- 否则写 `review_reopen`（payload `{"reason": "...", "from": "deliver"}`），run 回到 REVIEW，返回"运行 review-start"。

**lifecycle**：

- `_review_reopen` 接受从 DELIVER（没有打开的 attempt、没有 `pending_landing`）发出；REVIEW 打开，TEST、DELIVER 变回 pending，`last_land_intent`、`last_report_render` 清空（这次落地尝试已经关闭）。
- 单独计数 `deliver_reopens`，上限 1（`DELIVER_REOPEN_CAP`）。第二次补丁变化时不再退回，LG-1 按今天的规则失败，计入落地次数，最终走 `land_conflict`。原因是 main 在 run 落地期间第二次冲突，说明这块代码有人同时在改，该由人来看。
- `CHECK_CAUSES` 加 `review_reopened`。`_failed_stage` 里，cause 是 `review_reopened` 的失败另计（`review_reopened_failures`），和 `base_moved` 一样从落地次数里减掉。

**增量评审只看 run 自己补丁的变化**：

- handout 和 REVIEW 完成事件多记一项 `bases`（每个 repo 的 run base）。
- "没审过的文件"：逐个文件比较"上次评审时 run 的补丁"（上次 base → 上次 tree）和"现在的补丁"（现在 base → 现在 tree），用和 LG-1 相同的 `git patch-id --verbatim`（缩进、空白都算）。不同的文件、只在一边出现的文件都算没审过（全程 `--no-renames`，改名算一删一加）。
- 专家看到的是 `run_worktree.range_diff`（同步前后 run 自己的改动），加上这些文件现在的补丁。
- 旧事件没有 `bases`：退回今天的 tree 对 tree 比较（范围更大，不会漏）。
- `_review_facts.unreviewed` 用同一个函数，RV-4、增量评审、TEST 和 DELIVER 的退回判断一致。

**之后照旧**：REVIEW（新的访问，按改动 1）→ TEST（tree 变了，LG-1 要求新 tree 上的 TEST 报告）→ DELIVER → 落地。

对 `run_8437ff7e`：11:35 冲突解决后的那次落地就会把 run 送回 REVIEW，专家只看冲突解决改到的文件，然后重测、落地，不需要放弃。

### 改动 4：记下"改动已经在 pipeline 外落地"，Needs You 不再报它

- `pipeline-v1 abandon` 加 `--landed-as <repo>=<commit>`（单 repo 可以只写 commit）。已经放弃的 run 可以补记一次：`pipeline-v1 landed-as --run-id <R> <repo>=<commit>`，只对 abandoned、没记过的 run 有效。
- 检查（都通过才记）：
  - commit 在该 repo 的集成分支历史里（`git merge-base --is-ancestor <commit> <integration branch>`，分支用 run 记录的 `integration_branch`，不用 checkout 的 HEAD）；
  - commit 带的改动就是这个 run 的改动：commit 的 patch id（`<commit>^ → <commit>`，`--verbatim`）等于 run 最后一份补丁的 patch id（run 的 base → run 分支或保留快照的 tip）。不相等就拒绝，并说明差在哪些文件。这样任何不相干的 commit 都记不上。
- 新事件 `landed_as`：在 `EVENT_SPECS` 登记，lifecycle 加处理函数，只写 `run["landed_as"]`，不改 status（run 仍然是 abandoned，这是事实）。
- 读者：
  - `needs_you` 增加 `landed_as` 参数：abandoned 且有 `landed_as` → 不需要你。`routers/pipelines.py:329`、`attention_authority.py:276` 传入它。
  - 前端 `PipelineOverlay.tsx:374` 改读后端的 `needs_you` 字段，不再自己按 status 判断；有 `landed_as` 时写"在 pipeline 外落地为 `<sha>`"。
  - `brainMapModel.ts:60` 有 `landed_as` 的 abandoned run 不标成失败。
  - `routers/pipelines.py:290` 的计数不变（它数的是 status，abandoned 是事实）。
- 新 binary 上线后，用补记命令给 `run_8437ff7e` 记上 `827a1e29b`（它就是 run 分支的 tip，应当通过检查）。

### 改动 5：skill 文字和 `next` 一致

`stages/review.md` 写改动 1、2 的规则（访问、两轮、HIGH 的第三轮、关闭之后不改代码、恢复命令）；`stages/deliver.md` 写改动 3（补丁变了会退回 REVIEW）；`INSTRUCTIONS.md` 放弃那段写 `--landed-as`。以 `next` 为准，skill 照着写。

## 4. 不做的

- **不放松 LG-1**：落地的补丁必须是评审看过的那份。改动 3 给出回去评审的路，不跳过评审。
- **不改解析规则**：不部分接受、不把 R1 里的 R2 status 行当成 finding。改动 2 让重问能触发，由专家自己把格式改对。
- **不改 Quality 规则**：MEDIUM/LOW 没关时 Quality 仍是 WARN，这是如实的信息。循环来自 `close_with` 的指令，不来自 Quality。
- **不改 `--skip-tests` 也会跳过 LG-1 这件事**（`engine.py:1951`）：这是另一个问题（"跳过测试"顺带跳过了"评审过"），记为 ToDo，不在本文改。
- **不给 token 或花费设上限**（STEERING #2）。
- **PLAN 的 DS-9a 拒绝**：一次，14 秒后改好，错误信息已经指出具体字段，不加机制。
- **TEST 的 4 个 not_run**：按 10-26 §4.3 如实记录。
- **Hive 手动更新**：不是 pipeline 的问题，另作处理。
- **不加合并队列**（10-26 §9）。

## 5. 验证计划

**先写测试，在当前 main 上都是红的：**

| # | 测试 | 证明 |
|---|---|---|
| T1 | R2 回答后没有开着的 HIGH：MEDIUM/LOW 的 `close_with` 是 residual；`review-address` 带 items 被拒；不带 items 的补派照常 | 改动 1 |
| T2 | R2 后 HIGH 判 OPEN：允许一次只含 HIGH 的 address 和 R2；带上 LOW 的 address 被拒；第二次 R2 后再 address 被拒 | 改动 1，HIGH 的第三轮 |
| T3 | 关闭后改代码：`review-start` 不出 handout，`next` 给出恢复到上次评审 tree 的命令 | 改动 1，没有兜底增量 |
| T4 | TEST 退回开始新的访问：新访问的增量评审算第一轮，又能 address 一次、R2 一次 | 访问边界 |
| T5 | 检查都过、但 correctness 最新回答没解析：`next` 先安排重问（不是 stage-complete），重问的 handout 带错误原文；一次 R1 加一次增量评审不算两次 | 改动 2 |
| T6 | 重问后仍没解析：不再重问，stage-complete 可以走，审计 Quality WARN 并列出 | 改动 2 |
| T7 | DELIVER：冲突解决改了补丁、TEST 报告是旧的 → `land` 直接退回 REVIEW（不先要求重测）；这次失败不计入落地次数；第二次补丁变化不再退回 | 改动 3 |
| T8 | 只改了文档类文件的 DELIVER 补丁变化：引擎自己完成 REVIEW，回到 TEST | 改动 3，复用 TEST 的路径 |
| T9 | main 前进、冲突解决改了文件 A、main 还改了不冲突的文件 B：没审过的只有 A；A 只改了缩进也算没审过；没有 `bases` 的旧事件退回 tree 比较 | 改动 3 的 diff |
| T10 | 端到端：DELIVER 冲突 → REVIEW 增量 → TEST → `land` 通过 LG-1 | 改动 3 |
| T11 | `landed-as`：不在集成分支上、或 patch id 不同的 commit 被拒；正确的被记下，`needs_you` 为 False，Needs You 来源不再列它；只能记一次、只对 abandoned | 改动 4 |
| T12 | 前端：有 `landed_as` 的 abandoned run 不显示 "needs you"、不标失败 | 改动 4 |

每个改动做 mutation check（`scripts.mutation_check`）：去掉修复，对应测试变红。

**提交前跑**：改到的函数和它们直接调用方的测试：`run_protocol` 的 engine、lifecycle、next_step、review、checks、cli，`pipeline_metrics`、`attention_authority`、`routers/pipelines`，前端 `PipelineOverlay`、`brainMapModel` 的测试。不按目录全跑。

**评审**：correctness + state-machine 两位专家（lifecycle 加了一条转换和两个计数），一轮。

**上线后**：下一个真实 run 里看每次访问的轮数 ≤ 3；用补记命令给 `run_8437ff7e` 记上落地 commit，看它从 Needs You 消失。

## 修订记录

- r1 2026-10-31：初稿。
- r2 2026-10-31：零上下文审查后按原因一次修改。前提改准：11:04 时 `next` 的 action 已经是 stage-complete，矛盾在同一份 `next` 里。改动 1 允许只给 HIGH 的第三轮，删掉"兜底增量评审"，访问边界改用 `review_reopen` 的位置。改动 2 不再改解析规则，改为修好已有的重问（顺序、计数、错误原文）。改动 3 用事实触发、不看 TEST 是否新鲜，复用 TEST 的退回路径和已有的 `range_diff`，patch id 用 `--verbatim`，失败另计、`CHECK_CAUSES` 登记，DELIVER 退回上限改为 1。改动 4 要求 commit 的补丁和 run 的补丁相同，并列出所有读者。
