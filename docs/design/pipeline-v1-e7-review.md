---
title: Pipeline v1 实施 — v13 §E7 审核表
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: current
---

# Pipeline v1 实施 — v13 §E7 审核表

**审的是什么：** 从 K1（`91f558dfe`）到 R7 的全部 commit，即符合性修复（K1–K6）、r6 设计的 commit 2–6，以及旧 pipeline 的退役（R1–R7）。

**依据：**
- v13 §E7 的维度和判定规则；
- 符合性修复方案（`2026-10-23-pipeline-v1-conformance-fixing-spec.md`）§7 的完成标准：Run Isolation 和 Quality 两行都不能是 BLOCK；
- r6 设计 `pipeline-v1-remaining-design.md` §7 的五维预评。

**证据：** 每个 commit 都有 fixing log（`pipeline-v1-fixing-log.md` 的各节）和一轮独立审查（R1a 两轮），以及受影响测试的回归结果。本表没有任何真实 run 的数据：NE-0 留给 XG rebuild 之后做，所以需要实测的两行（Speed、Token Cost）只能判 PARTIAL。

| 维度 | 当前基线 | 本次变化 | 证据 | 判定 | 处理 |
|---|---|---|---|---|---|
| **Run Isolation** | 改动前：worktree 创建失败时静默退回共享 checkout；`active_run_commit_gate` 按 run.json 的 `files_touched` 判断，旧 run 能静默放行；owner 取自 run.json，agent 可以改写；`own_isolated_run` 只认旧状态 `running`，v1 隔离 run 还没建 worktree 时，不占本 session 的写入 | 多仓库原子落地（K2）；操作锁和 `append_batch`（commit 3）；commit gate 改读签名的 code facts（`RunEngine.owned_paths`），读不出相关 run 时拒绝，`-C` / `--git-dir` 跨仓库时拒绝（R1a）；owner 取自签名事件，接管必须先确认旧 owner 不在线（R1a）；活跃状态只认 v1 lifecycle 的非终态，隔离 run 改为认 `active` / `reflecting`（R6） | §D4 的并发验收：两个真实隔离 run 共用一个仓库，第 1、4、5、6 条都在两种模式下跑过，第 6 条还做了真实竞争（`test_pipeline_v1_conformance_k5`、`test_pipeline_v1_isolation_e2e`）；commit gate 的拦截测试先写、后删旧 matcher（`test_pipeline_v1_commit_gate`）；`test_worktree_gates`、`test_run_worktree` 全部通过；R1a 两轮审查的 HIGH 都已修复 | **PASS** | 已知遗留：commit gate 每次 commit 都会扫描所有 contract（影响速度，不影响隔离），见 Speed 行 |
| **Quality** | 改动前：落地读所有 `latest-*`；finding 由模型判断是否关闭；测试选择会把前端文件交给 pytest，挡住落地；不能截断的 nodeid 列表对 v1 永远为空 | final 加验证清单、按证据关闭 finding（K1、K3）；trace 矩阵（K4）；K6 的 L1/L2/L4；自动完成调用的就是 `stage_complete` 本身，检查集不变（commit 4）；知识写入的信任改为读 v1 的 `run_audit.json`，fail-closed（R6）；风险扩展选出的前端测试改由 vitest 跑，recheck 和续跑都能覆盖（R7，XG 批准修改冻结函数） | K1–K5 的验收测试在每个后续 commit 都重跑过；R7：后端 58 个受影响测试文件、前端 2245 个测试、`tsc -b` 全部通过，`pytest --collect-only` 没有错误；冻结守卫的摘要随 §11 一起更新 | **PASS** | 已知遗留：`test_audit_marker_write_gate` 中一个测试在机器高负载时会超过 5 s，单独跑和在 main 上跑都通过 |
| **Autonomy** | 改动前：3 个 run 因 DELIVER 被拒 3 次而放弃，落地被拒 5 次；agent 要自己调用 `stage-start` 和 `measure-usage`，靠轮询 `status` 才知道下一步 | 每个响应都带 `next`；删除 `stage-start`，worktree 由第一个需要它的命令创建；测试通过后自动完成阶段；`not_available` 结构化（commit 4）；人的决策只通过 decision 卡片；Need You 按状态写清楚该做什么（R5） | `next` 是纯函数，有单测（`test_pipeline_v1_handoff`、`test_pipeline_v1_cards`）；真实 run 里 agent 的调用次数和 idle 次数还没有测量 | **PARTIAL** | NE-0：direct 和 full 各跑一个真实 run，数调用次数和 idle 次数 |
| **Speed** | Gate-2 多派了 189 次；判定时扫描约 1.3 万个文件 | 判定只读本 run，快速失败、并行、复用（commit 2）；删除 status 轮询 | 每个 commit 附了改前改后的局部数字；没有真实 run 的 p50 / p95 | **PARTIAL** | 已知 WARN：`status` 有 11 次 git 调用，达不到 ≤150 ms 的预算；commit gate 每次 commit 扫描所有 contract（要等 active-run 索引）。NE-0 实测 |
| **Token Cost** | 有阶段级测量，没有按专家归属 | 调用次数从 17 / 23 降到约 8 / 12（设计预估）；删除重派和抄写；usage 由 daemon 记录，没测到时为 None 并显示 `—`（R1b、R5） | `audit.token_totals` 和测试都已到位；没有真实 run 的数字 | **PARTIAL** | NE-0 实测 |

## 总体判定：PARTIAL，没有 BLOCK

- Run Isolation 和 Quality 都是 PASS，满足符合性方案 §7 的完成标准（这两行不能是 BLOCK）。
- Autonomy、Speed、Token Cost 缺真实 run 的证据。按 E7 规则，缺证据就是 PARTIAL，不能声称已经验证。这三行都没有发现无界增长，也没有新增机械性的人工依赖。
- 下一步是 XG rebuild 之后跑 NE-0：direct 和 full 各一个真实 run，用 `run_audit.json` 的五行结论把这三行补上实测值。

## 交给 XG 的遗留项

1. **active-run 索引：** commit gate 每次 commit 都扫描所有 contract。
2. **`_source_repo_root` 的 contract 回退：** 根本修法在安装 / 启动配置。
3. **`status` 的 ≤150 ms 预算没达到：** 有 11 次 git 调用。
4. **K5 的 dev-backend 预演：** 留给 XG 做。
5. **前端死代码：** `source-final` 这种 canvas 类型和 `needs_human_review` Literal 里的对应项已经没有发出方，但保留着。
6. **高负载下偶发超时：** `test_audit_marker_write_gate::test_classification_is_bounded_on_pathological_input` 在机器高负载时会超过 5 s。
7. **TODO（XG 2026-10-24 决定先不做）：agent 和 daemon 是同一个系统用户。** 这是一个架构偏离，包含两处缺口，根因相同：
   - **签名证据可伪造：** B-a/b/c 之后只有 daemon 加载 `authority.key`，但同用户进程自己读那个 0600 文件仍读得到，拿到就能签任意事件（如"测试全绿""人已批准落地"）。Keychain 拦不住同一用户。
   - **人工决策可代答：** 后端 HTTP 没有鉴权，`PATCH /api/pipelines/{id}/cancel`、`/resume`、`POST /{id}/decision`、`POST /chat/answer_question` 本机任何进程都能调。这就是没做的 B-d。
   - **现状：** `audit_marker_write_gate` 拒绝读密钥，`run_owner_gate` 拒绝 Bash 调 `/decision`，只拦普通写法；agent 在 Python 里拼路径或 URL 就能绕过。后端只绑 127.0.0.1、CORS 是允许列表，对外不暴露。伪造的落地只进本地 main，push / build 都是 XG 手动。代价是：被故意绕过时，伪造事件和真事件分不出来，审计结论只能当"大概率成立"。
   - **修法：** 另立"agent 进程隔离"设计：agent 跑在单独的系统用户下，或放进 macOS sandbox profile，让它读不到密钥文件、连不上 daemon 的私有 socket、不能 ptrace daemon。两处缺口一起关。B-d 原方案（Tauri 经 Unix socket 取启动 secret）不单独做：同用户 agent 也能连那个 socket，只抬门槛，还要改 Rust 和 rebuild。
