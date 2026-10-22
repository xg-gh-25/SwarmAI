---
title: Pipeline v1 实施计划（执行 v13 设计）
date: 2026-10-21
created: 2026-10-21
updated: 2026-10-21
project: SwarmAI
status: IN PROGRESS — cycle 1 未开始
related: ~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-21-pipeline-structural-decisions.md（v13 FINAL，下称"设计"）；pipeline-gate-expert-simplification.md；pipeline-v1-deviations.md
---
# Pipeline v1 实施计划

设计以 v13 FINAL 为准，本文只记怎么做、做到哪了。偏离设计的地方写进 `pipeline-v1-deviations.md`，不在代码里悄悄改。

## 1. 工作方式

- **一个 worktree 做到底**：当前目录 `$KIROCREW_SCRATCH/wt-v1`，对应本地分支 `kiro/pipeline-v1`（git worktree 必须有分支）。所有 cycle 都在这里连续完成，不为每个 cycle 再建 worktree，不 push。切换时合成一个落地 commit（设计 §D1）。
- 如果 session 结束导致 scratch 被回收，只用同一分支重建这一个 worktree；这只是恢复，不是并行开第二个。
- **main 前进**：每个 cycle 开始前 `git rebase main`。`artifact_cli.py` 是热点文件，冲突当场解决，不累积。
- **数据隔离**：任何会碰应用数据目录的进程都设 `SWARM_DATA_DIR` 到临时目录；不写 `~/.swarm-ai/`（设计 F2）。
- **不重启生产 daemon**，直到 cycle 10 切换，并且要先经 XG 批准。

## 2. 进度

| cycle | 内容（设计 §D2） | 状态 | 提交 |
|---|---|---|---|
| 1 | `core/run_protocol/`：lifecycle、schemas、checks 注册表、events、tree、probe、auth、decision；纯函数 + §D4 不变量测试 | **DONE** — 三轮独立复核完成；最终 4 HIGH/4 MEDIUM 已修复；rebase `eb9331b9` 后 334 tests green；等待 XG 审查 | |
| — | **XG 审 cycle 1** | | |
| 2 | 审计区按 run 分目录、events.jsonl、blobs；hook 按 §A3.2 解析 | **DONE** — `AuditStore` 锁内追加/损坏 fail-closed；strict answer parser；Agent tool_use 与 SubagentStop 精确绑定；275 tests green | 待提交 |
| 4 | TEST_REPORT、验证清单、final、树身份、缓存 key、依赖范围复用 | **DONE** — base-pinned `validations.json`；真实 code/delivery tree；sealed per-call/per-repo final；完整缓存身份；dependency-scope carry；`pipeline_test --mode validation` 接线；201 tests green | 待提交 |
| 3 | 人工落地、保留、worktree 失败改 paused | **DONE（接线待 cycle 7）** — prepare→auth→consume→commit-tree；tree 变化拒绝且不消费；未集成 branch+bundle 永久保留；仅授权 discard；150 tests green | 待提交 |
| 4b | 风险扩展表 + `pipeline-test-speed.md` §11 记录 | **DONE** — 7 类路径风险确定性扩展；contract cap；overflow→incomplete；冻结摘要同步；17 tests green | 待提交 |
| 5 | 审查协议、canonical finding、R2 状态行、push 规则；名单沿用 P3a 的 `_dispatch_experts` 规则 | **DONE** — lightweight roster；统一 prompt lint；exact answer reuse identity；新 canonical occurrence；cluster/fixing spec；最小 R2；REVIEW/FIXING_SPEC 事件生成；312 tests green | 待提交 |
| 6 | 文档契约、decision、卡片改为"模板 + 检查 ID" | **DONE** — validated JSON SSOT；REQUIREMENTS/DESIGN renderer；exact stage roster；DC-1 event；9 张 <2KB v1 卡片；116 tests + skill lint green | 待提交 |
| — | **竖切里程碑**：只接 direct，dev backend 预演出 `run_audit.json` | **DONE** — direct real E2E；localhost dev backend healthy；direct×2 + 其余 profile 各1 real rehearsal；五维无 BLOCK | `0d03941a` + Cycle 9 |
| 7 | 阶段重排、删除清单 §C2（含 gate1-skeptic、meta-review、legacy Gate-2） | **DONE** — active skill/cards only use v1；legacy mutations refuse；round hook/verdict parser removed；Gate-2/finding-ledger/review-round source + 12 legacy suites deleted；proof/classifier migrated | final deletion commit |
| 8 | REFLECT 拆分 | **DONE** — DELIVER 前 prepare 验证/evidence/relation/去向；落地后 bounded judge+writer；每条 outcome；失败 degraded；40 tests green | `d6a4b04d` |
| 9 | `run_audit.json`、指标、REPORT 两版、desktop 卡片、端到端测试 | **DONE** — 五维 audit+共享 renderer；expert token attribution；backend/API/frontend card；profile×mode + isolation + full rehearsal；E7 PASS | `948a8370` |
| — | **终审修复** | **DONE** — 三路 fresh-context 终审 BLOCK 的五组根因全部修复：Gate-0/R2/proof/resume/restore/abandon/decision/run-land/accept/discard 可执行；nonce 绑定答案；runner 签名 call/proof；冲突 poison 缓存；多 repo 落地可恢复；v1 owner/HITL/API/UI 迁移。分批 targeted ~880 tests、前端 22 tests + tsc + build、358 模块 import、lint、eval gate 全绿；security_scan 与 main 基线相比无新增类别 | 单一 ready commit |
| 10 | 切换：预演 → 落地 commit → build + 重启（XG 批准）→ direct 冒烟 | **READY COMMIT** — 已 squash 为单一可 revert commit（未合并 main）。merge main、生产 build/restart、active-run drain、post-restart smoke 需 XG Cycle-10 批准 | |

## 3. cycle 1 的拆法

纯函数、不接线，每个模块一个文件、一个测试文件，按依赖顺序写：

1. `events.py`：信封、`event_id`（发生键）、4 KB 行限、blob 外置、去重和 integrity（§A3.1）。
2. `tree.py`：code_tree / delivery_tree 的计算接口和 LG-3 判定（§A3.7）。git 调用由调用方注入，单测里用假实现。
3. `lifecycle.py`：§A1.1 状态、§A1.2 转移表、`transition(run, event)`。
4. `checks.py`：注册表 `{id, doc, effects, at, variants, fn}`，§A2.3 全表登记，§A2.4 豁免表。
5. `schemas.py`：§B1、§B2 的 full / lite 变体，EARS 结构字段。
6. `findings.py`：canonical ID、R2 状态行、§A3.4 改分类和 `docs` 下限、§A3.5 证据与 push 规则。
7. `probe.py`：§A2.5 的 `validate(argv)`。
8. `auth.py`、`decision.py`：§A4.2 绑定和消费顺序（oracle 注入）、§B5.2 DC-1。

验收：§D4 第一段列出的每条不变量至少一个测试；全部测试在干净 checkout、无 `~/.swarm-ai` 的情况下能跑。
