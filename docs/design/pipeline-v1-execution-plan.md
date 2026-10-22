---
title: Pipeline v1 修复执行计划（第二版：并入 direct 去 PLAN、纯文档放行）
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
status: DRAFT — §5 的问题待 XG 回答后开工
related: v1 修复方案（~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-22-pipeline-v1-fixing-spec.md）；v13 设计（同目录 2026-10-21-pipeline-structural-decisions.md）；修复记录 docs/design/pipeline-v1-fixing-log.md
---

# Pipeline v1 修复执行计划（第二版）

## 0. 范围和判定方法

本文把三件事合成一份执行计划：

1. v1 修复方案里还没做的部分（G、A、B1/B2/B4 已在 main：`d6b4b948`、`c927cf53`、`806927c0`）。
2. 第 4 路审查（方案 §8）报出、已归组但未修的项（修复记录 §2）。
3. XG 2026-10-22 新提的两处设计改动：**N1** direct 去掉 PLAN 和 design spec；**N2** 纯文档改动不必开 run。

不确定的地方按 v13 §0.2 的五个维度推断：Run Isolation、Quality 是硬约束，任一 BLOCK 即 BLOCK；Autonomy 是主要目标；Speed、Token 是效率指标；安全、权限、生产操作的人工审批是系统边界。推断不出的列在 §5，等 XG 决定。

## 1. N1：direct 去掉 PLAN 和 design spec

### 1.1 行为

| 项 | 规定 | 依据 |
|---|---|---|
| 阶段 | direct 改为 `build → review → test → deliver → reflect`；其他 profile 一律不变，trivial 仍有 PLAN 和 lite design | XG 原文 |
| 需求 | `create --requirement <原话>`：direct 必填，缺了就报错，不默认空串；其他 profile 可选。写进 `contract.json` 的 `requirement` 字段（不可变、绑定 run），REPORT 和 run.json 从这里读 | Isolation：需求是 run 的输入，必须不可变、只属于这个 run。只放 contract，不改事件 schema |
| 检查清单 | direct 不登记任何读 REQUIREMENTS / DESIGN_SPEC 的检查：RQ-\*、DS-\*、BX-1、TS-2、TS-3；DC-1、DC-2 也不登记（direct 没有可以提 decision 的阶段，见 §5 Q4）。做法是从 registry 的 `profiles` 里去掉 direct，并在 `EXEMPTIONS` 写明理由，不是跑了再放行 | XG 原文"根本不在清单里"；按 registry 的 input 列逐条判定"依赖 design/requirements"，不靠猜 |
| BUILD 通过条件 | 新检查 **BX-2**（stage 效果，只登记给 direct）：有改动（changed_paths 非空）；且 runner 现有选测规则对 changed_paths 选中的每个测试文件（改动的测试 + 改动模块的直接 importer，冻结的 `select_backend`，不改它）在当前 code_tree 上都有 `--mode tdd` 结果，并且全部通过（`node_results`）。选不出任何测试（纯文档）时，tdd 不作为条件 | Quality：tdd 失败不能过；"改动涉及的测试"用已冻结的选测函数计算，不新造一份规则 |
| TEST | 不变：一次 regression（TS-1），加上 base 版本验证清单（LG-5）。TS-2 / TS-3 对 direct 不登记，测试红由 LG-2 拦 | Quality 由落地守卫保证，不依赖 exit criteria |
| REVIEW、DELIVER、REFLECT | 不变 | — |
| REPORT | "需求"一栏显示 contract 里的原话；追溯矩阵显示"direct：无 exit criteria"，不留空 | Quality E2 要求可追溯；没有就明说 |

### 1.2 要改的地方（R27：生产和测试都 grep 全）

- 生产：`lifecycle.py`（PROFILE_STAGES）；`checks.py`（profiles 列、`EXEMPTIONS`、新 BX-2、`_stages_for` 里 RQ 对 direct 的特例删除）；`engine.py`（`create` 的 requirement、`check_context` 的 variant、`stage_complete` 里 direct 的 PLAN 分支）；`documents.py`；`audit.py`（REPORT 的需求和追溯）；`pipeline_v1.py`（`--requirement`）。
- 卡片和文档：`stages/plan.md`、`stages/build.md`、`INSTRUCTIONS.md` 的 profile 描述、`AGENTS.md` 里 direct run 的说明（"direct run always goes plan -> build -> …"那段要改）。
- 测试：`tests/pipeline_v1_rig.py`（`documents("direct")`、`complete_stage`）；`test_pipeline_v1_direct_e2e.py`、`test_pipeline_v1_api.py`、`test_pipeline_v1_human_loop.py`、`test_pipeline_v1_real_e2e.py`、`test_pipeline_v1_isolation_e2e.py`、`test_pipeline_v1_recovery_review_landing.py`、`test_pipeline_v1_rehearsal.py`、`test_run_protocol_lifecycle.py`、`test_run_protocol_checks.py`；以及 `run_owner_gate` / `worktree_write_gate` 里打印 `create --profile direct` 命令的测试（命令要带 `--requirement`）。

### 1.3 必须有的测试

- direct 的 create 结果里 stages 不含 plan；缺 `--requirement` 时 create 报错。
- 不交任何 design，只改一个 `.md` 的 direct run 用真 CLI 走到 land，main 上出现 commit。
- 改代码的 direct run，tdd 失败时 BUILD 不过；没跑选中的测试时也不过。
- trivial 的阶段和每个阶段的检查清单与改之前逐项相等（把改前的清单冻结成常量，diff 断言）。

## 2. N2：纯文档改动不必开 run

### 2.1 行为

| 项 | 规定 | 依据 |
|---|---|---|
| 纯文档路径 | `is_doc_only_path(rel)`：后缀 `.md`，或在 `backend/context/` 下。只有这一个定义，放在一个共享模块里，rule 4 和 direct_commit_gate 都调它 | XG 原文 |
| 现有 `_is_doc_path` | 删除，直接改用 `is_doc_only_path`。原来额外豁免的 lockfile、`docs/`、`Knowledge/`、`.kiro/`、`.context/` 下的非 `.md` 文件不再豁免 | XG 要求"不能各写一份"；变严只影响 Autonomy（这几类改动要开 run），Isolation 不受损。硬约束优先，所以选变严 |
| rule 4 | 写入目标是纯文档路径时，允许在 main checkout 直接写 | XG 原文 |
| direct_commit_gate | 一次 commit 的所有路径都是纯文档时，没有 run 的 session 也能提交；混进任何一个非文档路径，照旧拒绝 | XG 原文 |
| adversarial_commit_gate | 不变：纯文档 commit 仍需要覆盖全部提交路径的审查 marker | XG 原文；这也是 Quality 对"改判断依据的文档"的唯一把关 |

### 2.2 残余风险（写进 commit message 和本文，不另加机制）

- **会被别的 session 的提交卷走：** main checkout 里未提交的文档改动，可能被另一个 session 的 `git commit -a` 一起提交。2026-10-19 的事故就是这一类。这不属于 run 的隔离问题（E1 管的是 run），但会让 commit 的归属错乱。
- **`.md` 里有会改变系统判断的文件：** stage 卡片、`backend/experts/code/*.md`（审查 prompt）、`SKILL.md` / `INSTRUCTIONS.md`、`backend/context/` 下的系统 prompt。改它们相当于改 OS（AGENTS.md"OS 和 disk 两层"）。v13 §C3 规定卡片只是模板、规则以代码为准，所以落地守卫的结论不受影响；审查质量可能受影响，把关只剩 adversarial marker。XG 原文已明确 `.md` 一律放行，所以这里只记风险，不改范围。

### 2.3 必须有的测试

- main checkout 里 Edit `backend/context/AGENT.md`：放行。同一个 session 里 Edit `backend/core/x.py`：照旧拒绝，并给出开 run 的命令（带 `--requirement`）。
- 只含 `.md` 的 commit：direct_commit_gate 放行；`.md` + `.py`：拒绝；只含 lockfile 的 commit（worktree mode 下）：拒绝（变严的钉子）。
- 只含 `.md`、没有 adversarial marker 的 commit：仍被 adversarial_commit_gate 拒绝。

## 3. 剩余项（来自修复方案和第 4 路审查）

| 组 | 条目 | 状态 / 处理 |
|---|---|---|
| A | A7：hookless 执行器 `land` 得到 `not_landed(needs_hooked_session)`；`infrastructure_failed` 的生产者（审计区不可写、拿不到 token） | 待做，hook 批次 |
| A | A9：awaiting_human 时 session_lost 是否进 paused | **按五维已定**：保持等人。v13 §A1.2 表里"崩溃 / 用户停止 → paused"只从 `active` 出发，修复方案"按设计"的说法与设计原文不符；Autonomy 上，继续等能保住问题，不需要先 resume 再重问。记为修复方案的偏离 |
| B | B6 验证清单读 run base；B7 缓存 key 加 runner 代码哈希和规则版本、不存在才创建；B8 DS-2b/DS-4b 真实 collect；F-5 Gate-0 多个 VERDICT 拒绝；F-6 无输出重派一次；F-7 答案复用接线、RV 认 `answer_reuse`；F-8 注释改动按 docs 判定；F-9 ±10 行聚类强制；F-10 FIXING_SPEC 由事件生成；F-11 来源记录补规则版本 | 待做 |
| C | C1 只按 `land_request` 事件执行（JSON 只用于显示），审批卡显示树；C2 已由 `fc6d2ab2` 完成（补一个回归测试）；C3 进程只能拿到自己的 writer（`test_a_cli_process_cannot_sign_as_the_hook` 是 strict xfail）；C4 保留路径写保护、land 在 run 锁内、去掉 `--mode`、shared 也能签 token；C5 skip-tests 无 final 时 LG-4 不要求 final、CLI 捕获 `LandingError`、`--prepare` 先跑测试；C6 `auth_consumed` 去重在锁内并记 integrity；F-2 `answer` 发生键改为 `tool_use_id`，唯一性由 bridge 在锁内按 handout 检查 | 待做，hook 批次 |
| D | D1 REFLECT 后台 job；D2 按 profile p95 的基线文件；D3 Token 统计去重、替换不累加；D4 `required_approval` / `manual_rescue` 生产者；D5 REPORT 和卡片对齐 §B8、§E9；F-4 handout 的 prompt 全文进 blob | 待做（D4 属 hook 批次） |
| E | 拒绝提示里的退役命令、SKILL.md 流程图、不再派发的专家文件、过时 docstring、卡片 lint 校验命令和字段、风险扩展的共享配置匹配 | 待做 |

**XG 原文"已知问题，不在本次范围"里的两条已经修掉了：** probe exit 没有执行器、`_stage_data` 把 v1 stages 当列表读，都在 `806927c0`（B1、B4）。对应的 strict xfail 已转为必须通过。所以 N1、N2 的 commit message 不再列这两条。

## 4. 执行顺序和批次

| 顺序 | commit | 说明 |
|---|---|---|
| 1 | N2 | 只改 gate，最独立；先落地，之后的文档改动就不用再开 run |
| 2 | N1 | 改 lifecycle 和检查清单，会和 B、C 组的同一批文件冲突，所以先做 |
| 3 | C 组（含 A7、F-2） | 安全面 |
| 4 | B 组剩余（B6–B8、F-5–F-11） | |
| 5 | D 组（含 F-4） | |
| 6 | E 组 | |

- **每组一个 commit**，N1、N2 各一个。都在本 session 自己的 worktree 里做，`merge --ff-only` 进 main，不 push。worktree 需要一个本地分支，这是 git 的要求；落地后删除，main 上看到的就是直接提交（AGENTS.md"Code changes outside a pipeline run"）。
- **一次重启：** N1、N2、C、D4 和已经落地的 A、B 都会改 daemon 加载的代码（hook、lifecycle 折叠、store 校验）。daemon 和 CLI 版本不一致时，daemon 侧写入的事件（`human_answer`、`user_stop`、`owner_transfer`）会用旧规则校验新日志。所以**在 XG 重建之前，不要用 v1 跑真实的 run**；全部做完后统一交 XG build 和重启一次。
- 每个 commit 前：受影响的测试文件全部跑绿（`perl -e 'alarm N; exec @ARGV'` 包 pytest）；adversarial 审查覆盖全部改动路径；build 见 §5 Q2。

## 5. 需要 XG 决定的问题

**Q1 commit trailer。** XG 原文要求 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`。AGENTS.md 规定本仓库的 commit 不得使用 Claude / Anthropic 身份，只能用 `Co-Authored-By: Swarm <swarm@swarmai.dev>`；而且本机的 `prepare-commit-msg` hook 会把 Claude trailer 改写成 Swarm 那行。这不是五维能推断的问题，是两条规则冲突。建议：照旧用 Swarm trailer。

**Q2 每个 commit 前的 `./prod.sh build`。** 它会部署并重启 daemon（`prod.sh` 第 4 行），与"不重启生产 daemon、hook 改动攒成一批交 XG"冲突。建议：每个 commit 前只跑受影响的测试和 `./prod.sh verify`（不部署）；全部做完后由 XG 跑一次 `./prod.sh build`。

**Q3 direct 的 exit criteria"可选"。** 去掉 PLAN 之后，direct 没有任何阶段可以提交 design，"可选"实际上永远是"没有"。两种做法：
- A：direct 永远没有 exit criteria；需要 exit criteria 的改动用 trivial。规则最简单。
- B：`create` 时可以附带一份 lite design；附带了就登记 DS-\* / BX-1 / TS-2，不附带就走 §1.1。
- 五维上：A 的 Speed、Token 更好；B 保留了 Quality 的可追溯性，但同一个 profile 有两套检查清单。Quality 的 BLOCK 条件里没有"缺追溯"，所以 A 不触发 BLOCK。建议 A。

**Q4 direct 不能再问人。** decision 只能在 EVALUATE / THINK / PLAN 提出，去掉 PLAN 后 direct 一个都没有。E3 要求"需求有两种以上合理解释时问人"，并且不能为了自主跳过这一步。两种做法：
- A：允许 direct 在 BUILD 开始前（还没有任何 stage_attempt 时）提一次 decision。这要改 lifecycle 的转移表，是协议改动。
- B：direct 的需求被认定为没有歧义；agent 遇到歧义时 abandon，改用 trivial 重开 run 并在那里问。
- 建议 B：direct 的定位就是"小而明确"，有歧义本身就说明选错了 profile；B 不改协议。但这会让 E3 的"可避免问题"统计里多出一类"选错 profile 后重开"的情况，需要 XG 确认能接受。

## 6. 交付标准

- 每个 commit 的说明列出：修了哪些条目、证明它的测试名、留到后面的条目和原因。
- `tests/test_pipeline_v1_real_e2e.py` 全部通过，不再有 strict xfail；没有任何测试手写 `land`、`render`、`human_answer`、`auth` 等生产事件。
- 按 v13 §E7 填一张审核表，附在 `docs/design/pipeline-v1-fixing-log.md` 末尾。
- 与本文或设计冲突、能由代码和测试决定的，自己决定并写进修复记录；涉及协议的，问 XG。
