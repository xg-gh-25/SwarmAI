---
title: Pipeline v1 收尾与旧 pipeline 退役：设计
created: 2026-10-23
updated: 2026-10-23
status: draft r6（加入实测摩擦、旧 pipeline 退役、衔接；待 XG 审）
---

# Pipeline v1 收尾与旧 pipeline 退役：设计

**依据：**
- 符合性修复 spec（2026-10-23）；
- NE（2026-10-22 native-execution）；
- skeptic 对 r4 的证伪（1ff171d4）；
- 实测摩擦：`$KIROCREW_SCRATCH/friction/report.md`；
- 旧 pipeline 清单：`$KIROCREW_SCRATCH/legacy-inventory.md`。

**已合入本地 main：** K1 `91f558dfe`、K2 `fe4e7a8e6`、K5 `9b9d7e7e1`、K3 `e69f54d40`。

**目标（XG）：** 打造一条高效率的 autonomous pipeline。rebuild 之后，新 run 跑得又快又好。旧 pipeline 不做任何兼容，只给旧 pipeline 用的代码一律删除。

## 1. 实测：一个 happy-path run 的摩擦

测法：用 rig 跑真实 run，冷启动，每个配置测一次。

| | direct | full |
|---|---:|---:|
| agent 发出的 CLI 调用 | 17 | 23 |
| 其中 `status` | 6 | 9 |
| agent 手写的 JSON 文件 | 1 | 3 |
| 事件数 | 34 | 56 |
| CLI 累计耗时 | 10.5 s | 12.1 s |
| git 子进程 | 293 | 389 |
| `read_events` / `fold` 次数 | 181 / 112 | 327 / 181 |

**热点：**
- **`land`：** 2.0 s。一次 land 读了 39–45 遍 `events.jsonl`，起了 70 个 git 子进程。
- **`status`：** 400–500 ms，16 个 git 子进程。这些来自 `check_context → _current_code` 里的 write-tree、ls-files、diff。
- **`stage-complete`：** 450–870 ms，一次命令读 17–28 遍日志。
- **`pipeline_test` tdd/regression：** 1.3–1.7 s，每次起 32 个 git 子进程。这还是测试很小的情况，pytest 本身占不了多少。
- **`measure-usage`：** 拿不到 transcript 时以堆栈错误退出，agent 只能自己判断这是失败还是可以忽略。

**agent 侧的机械步骤（都可以由 CLI 自己完成）：**
- 每跨一个阶段边界都要调一次 `status`。
- `stage-start` 唯一的作用是建 worktree。
- 跑完测试后还要单独调一次 `stage-complete`。
- JSON 要先写成文件，再把路径传给 CLI。
- full 的 THINK 是一次空的 `stage-complete`。
- `measure-usage` 要 agent 自己去调。

## 2. 效率设计

### 2.1 每个命令只读一次：EventSnapshot 和 CodeFacts

- **EventSnapshot：** 命令开始时读日志一次、验签一次，lifecycle 和 finding 各 fold 一次，得到不可变的快照。
  - 命令内所有读取都用它：projection、pending、push、check_context、audit、渲染。
  - 本命令自己追加事件之后，快照作废并重建一次。
  - 不跨命令、不落盘、不做增量 fold，所以不受 skeptic 那两个反例的影响。
- **CodeFacts：** 每个 repo 的 code_tree、base、changed paths、existing files、diff，一个命令只算一次。
  - 现在 `status` 要起 16 个 git 子进程，原因是 `_current_code` 被重复调用。
  - 先算 tree，按 tree 缓存 diff 和 ls-files，只在本命令内有效。
- **批量 append：** 一次锁内写入一组事件，例如 stage_attempt + check×N + stage_complete + transition，或者 hook 的 answer + findings + usage。整组只验一次链、跑一次 validator、fsync 一次。
- **去掉重复验链：** 现在一次 append 会在 read、validator、add_event 和 finding fold 里各验一遍整链，改成只验一次。

**预算（实施后实测，数字写进 commit message）：**

| 命令 | 现在 | 目标 |
|---|---:|---:|
| `status` | 400–500 ms，16 个 git | ≤ 150 ms，≤ 4 个 git |
| `stage-complete` | 450–870 ms | ≤ 300 ms |
| `land` | 2.0 s，读 45 遍日志 | ≤ 1.0 s，读日志 ≤ 3 遍 |
| `pipeline_test`（不算 pytest） | 32 个 git | ≤ 8 个 git |

### 2.2 并发：每个 run 一把操作锁

- **罩住的：** 同一个 run 的变更类事务串行执行，包括 `pipeline_v1.py` 的变更命令和 daemon 里 hook 的 answer 事务。
- **不罩的：** `pipeline_test` 跑测试的过程。它只在每次 append 时拿 store 锁。
- 拿到操作锁之后，重新读快照再判断。
- **ordinal：**
  - finding 和 R2 状态行保持答案里的行序号，因为重放时要稳定；
  - `render_failed` 用 `{document, source_tail}`；
  - 冲突类 integrity 用 `{type, conflict_id}`；
  - 其余事件由 store 在锁内分配（`NEXT`）。
- **投影头：** 写 `{source_sha256, event_count, tail}`，只在写入层加，REPORT 不加。同一个 count 但内容不同时，按失败处理并记 integrity。
- **测量：** hook 等操作锁的最长时间。

### 2.3 衔接：每个命令都告诉 agent 下一步

- **把 NE-2 提前做：** 每个命令的返回里都带 `next`。`next` 是纯函数，只读事件、CodeFacts、spec 和 finding ledger，不读 agent 自报的任何东西。这样就不用每次跨阶段都单独调 `status`（现在每个 run 要调 6–9 次）。`status` 命令保留，用于接手 run，它返回的 `next` 和各命令返回的是同一个。
- **去掉单独的 stage-start：** 第一个需要 worktree 的命令幂等地创建它，并记录现有的 stage-start 事件，事件协议不变。删除 `stage-start` 子命令。
- **测试结果进 final 后自动尝试完成阶段：** `pipeline_test` 在 BUILD/TEST 跑出结果、写进 final 之后，在同一个进程里调 `stage_complete`。
  - 检查没过时，原因放进返回的 `next`。
  - 这个尝试要拿操作锁，测试跑完才拿，不会持锁跑测试。
  - agent 仍然可以手动调 `stage-complete`。
- **artifact 直接从 stdin 传：** `--artifact -`，agent 不用管文件路径。CLI 把它存进 run 目录，作为 source。
- **usage 由 daemon 自动记：** turn 结束和 SubagentStop 时，daemon 从 transcript 记 usage，agent 不再调 `measure-usage`。拿不到 transcript 时记一条结构化的 `not_available` 事件，Token 判 WARN，不报堆栈错误。
- **THINK 不变：** 它是 full 的一个阶段，有检查，本轮不改阶段表。

**效果：** happy path 下 agent 的调用次数，direct 预计从 17 降到大约 8，full 从 23 降到大约 12。NE-0 时实测。

## 3. K4 收尾（第 1 轮审查的修复）

| # | 问题 | 修法 |
|---|---|---|
| 1 | 旧 `address` 事件没有 hunk | 不兼容旧 run，`hunk` 保持必填 |
| 2 | hunk blob 丢失时 FIXING_SPEC 静默少一段 | 记 `render_failed`，文档里标出缺失。cause、proof 保持可选 |
| 3 | 追溯矩阵缺 task | 新建 `core/run_protocol/trace.py::matrix(design, final)`，矩阵为 R/P/F → task → exit/test → result。audit 和以后的 NE-4 共用 |
| 4 | critical_path 混入人工等待 | `active_seconds` 扣除人工等待；`dispatch_seconds` 取并集；`test_call` 没有开始时间，写 null 并注明原因 |
| 5 | REPORT 标题可被注入 | audit 渲染层统一做纯文本编码 |

## 4. K6 和设计文字

- **K6：**
  - L1：词表照 §A2.3 逐字写，加测试钉住；
  - L2：full DESIGN_SPEC 必须有 `properties` 和 `failure_modes`；
  - L4：`.py` 用 tokenize 判断，失败时按"有代码改动"处理。
- **设计文字修订：**
  - §B3.4、§C4.1、§A3.1、§A3.7；
  - §A3.6 和 §A4.2：auth 时间就是事件的 `at`；
  - §C6.2：删除跨 run 复用；
  - 加一条：v13 里所有描述旧 pipeline 兼容的段落，标注已退役。

## 5. 旧 pipeline 退役（依据清单，估计删除 14.5k–17.5k 行）

**原则：**
- 只有旧 pipeline 用的，直接删（D）。
- v1 在用的，保留并清理文案（K）。
- 两边都用的，先迁到中立模块再删旧的（M）。
- 安全门要先改到不依赖旧字段，才能删旧的 matcher。
- 用户磁盘上的旧 run 数据不在本轮，删除要另外授权。

**必须先修的安全问题：**
- `active_run_commit_gate` 依赖 `files_touched`，但 v1 的 projection 不写这个字段。所以对 v1 run，这个 gate 现在是静默放行的：手工 `git commit` 可以绕过 landing。
- 改成读 contract 的 repo roots、run worktree 和 reserved delivery tree，并补上 v1 的拦截测试。
- 这一条放在退役的第一个 commit 里，不等后面。

**保留并重写的 gate：**
- `audit_marker_write_gate`：不动。
- `session_link_gate`：覆盖 `pipeline_v1.py` 和 `pipeline_test.py`。
- `run_owner_gate`、`worktree_write_gate`：去掉 artifact_cli、claim、undecided 的旧分支。
- `direct_commit_gate`、`run_amend_gate`：只清理文案。

**删除的 gate：** `session_link_stamper`、`_is_main_claim` 及其 approval cache。

**v1 的识别：** router 和各处一律以受保护的 `contract.json`（`is_v1_run`）为准，不再用"stages 是 dict + decision_cap"这种启发式判断。

**提交（每个审 1 轮；安全门那个审 2 轮）：**

| # | 内容 |
|---|---|
| R1 | v1 读模型和安全门：新建 `core/pipeline_metrics.py` 和 v1 文档元数据，router 只列有 contract 的 run，四个 gate 改写，`active_run_commit_gate` 修复，安全回归测试 |
| R2 | 旧执行入口：<br>· 删除 artifact_cli 里 run/stage/report/metrics/purge 的部分，保留 artifact registry、DDD、release-gate；<br>· 删除 `completion_gate.py`、`contract_readers.py`；<br>· `pipeline_test.py` 在任何测试执行前要求 v1 contract，删掉 13 处 `v1 is None` 分支 |
| R3 | 旧渲染链：删除 `run_delivery_summary.py`、`run_surface_changes.py`、`ui_actions.surface_run_outputs`、orchestrator 的 surface batch；`run_docs.py` 里仍需要的文档名、摘录、是否存在迁到 `run_protocol.documents` 后，删整个文件；`pipeline_stage_events` 改成只认 v1 |
| R4 | 旧维护和调度：删除 analytics、cleanup、purge、foreign-stub、reconcile 这些脚本，删除 `system_jobs` 的 `pipeline-retention`，删除 `job_manager pipeline` |
| R5 | backend schema 和 desktop 同批切换：删除 `crash_residue`、checkpoint、currentStage 的回退逻辑，同步 Pydantic 模型、`toCamelCase` 和 fixture；未知值显示 `—` |
| R6 | 旧测试和文档：先把仍有用的 helper 迁到 `pipeline_v1_rig.py`，再删 23 个整文件测试和需要逐条改的测试；更新 AGENTS.md 和 skill 注释 |
| R7 | 验证：静态零命中（迁移说明和否定测试除外）、`pytest --collect-only`、受影响套件、前端 build 和 test |

## 6. 总顺序

| # | commit | 审查轮次 |
|---|---|---|
| 1 | K4 + Q3 + `trace.py` | 第 2 轮（共 2 轮） |
| 2 | 效率：EventSnapshot、CodeFacts、批量 append、去掉重复验链 | 1 |
| 3 | 并发：操作锁、ordinal 分类、投影头 | 1 |
| 4 | 衔接：`next`、去掉 stage-start、测试后自动完成、stdin artifact、usage 由 daemon 记 | 1 |
| 5 | K6 | 1 |
| 6 | 设计文字 | 1 |
| 7–13 | 退役 R1–R7 | R1 两轮，其余各 1 轮 |

之后：
1. 填 E7 表；
2. 三路复审；
3. 交给 XG 一次 rebuild；
4. NE-0：direct 和 full 各一个真实 run，核对 §1 的数字和 §2.1 的预算。

本轮之后，NE 剩下的是 NE-1、NE-3、NE-4、NE-5、NE-6、NE-7。NE-2 已经在 commit 4 里做了。

## 7. 五维评估（v13 §E7：没有证据就判 PARTIAL）

| 维度 | 本轮改了什么 | 主要风险 | 怎么防 | 判定 |
|---|---|---|---|---|
| Run Isolation | 操作锁；快照；v1 识别改为认 contract；四个 gate 改写；修复 `active_run_commit_gate` | 改写 gate 时留下漏洞 | 安全门那个 commit 先写拦截测试，再删旧 matcher；审 2 轮 | **PARTIAL**。现在已知一处静默放行，修复后才能升级 |
| Quality | 批量 append；自动完成阶段；`next`；trace 矩阵；K6 | 自动完成绕过了检查；`next` 给出错误的下一步 | 自动完成调用的就是 `stage_complete` 本身，检查集不变；`next` 是纯函数，只读事件和 CodeFacts，有单测；K1–K5 的验收测试全部重跑 | **PARTIAL** |
| Autonomy | `next`；不再需要 stage-start 和 measure-usage；结构化的 not_available | 自动完成失败时 agent 不知道下一步 | 失败原因放进 `next` | **PARTIAL**。NE-0 时数 agent 的调用次数和 idle 次数 |
| Speed | §2.1 的预算 | 优化没达到预算 | 每个 commit 附改前改后的数字；没达到就不合并 | **PARTIAL**。NE-0 实测 |
| Token | 调用次数从 17/23 降到约 8/12；不再有 status 轮询 | REVIEW.md 变长 | 决定规则固定约 10 行 | **PARTIAL**。NE-0 实测 |

没有 BLOCK 项。`active_run_commit_gate` 的静默放行是一个已存在的 Isolation 缺陷，放在 R1 修。

## 8. 需要 XG 定的事

1. NE-2（`next`）提前到本轮，在 commit 4 里做。
2. 删除 `stage-start` 子命令，worktree 由第一个需要它的命令幂等创建。
3. `pipeline_test` 跑完 BUILD/TEST 的测试后，自动尝试 `stage_complete`。
4. `artifact_cli.py` 保留它的非 pipeline 部分（artifact registry、DDD、release-gate），只删 run/stage/report/metrics/purge。
5. 用户磁盘上的旧 run 数据另外处理，不在本轮。
