---
title: Pipeline v1 与 v13 设计的偏离记录
date: 2026-10-21
created: 2026-10-21
updated: 2026-10-22
project: SwarmAI
status: CLOSED — 已并入 v13 设计 §D3.4、§D3.5（2026-10-22）
related: pipeline-v1-plan.md；v13 设计（~/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-21-pipeline-structural-decisions.md）
---
# Pipeline v1 偏离记录

> 已关闭：全部条目于 2026-10-22 并入 v13 设计（§D3.4 实施分工、§D3.5 实施中接受的偏离）。本文件只作历史记录，后续偏离直接写进设计文档。

每条：设计章节、问题、实际做法、理由。

| # | 章节 | 问题 | 实际做法 | 理由 |
|---|---|---|---|---|
| 1 | §D3.4 | 写的是"P1（G1、G3、E5 无输出不压低）"在 main 上落地 | G3 撤回，未落地 | `audit_marker_write_gate` 对 `cd` 和解释器文本的拒绝是有意的（Bash 工具在调用之间保留 cwd；程序文本里难以区分读写），2 天只有 4 次误伤。见 `pipeline-gate-expert-simplification.md` G3 |
| 2 | §D3.4 | 写的是 P3、P4 不单独实施 | P3a（E1 Gate-2 名单）已在 §D3.4 确认前落地 main（`8fd9335c`），XG 2026-10-21 决定保留 | 规则与设计 §A1.4 相同，只改名单、不删流程；cycle 5 沿用 `_dispatch_experts` / `_security_risk` 的规则 |
| 3 | §D3.4 | "待确认" | XG 2026-10-21 确认：一次切换、不留隐藏开关；P1、P3a 留在 main，其余并入 v1 | — |
| 4 | §D3.4 | 写的是 P2 留在 main | P2/G2 的 shell-prefix 实现（`db08c31f`）独立复核发现两个 HIGH、两个 MEDIUM，已完整回退（`332b0227`） | 允许任意前置命令破坏 token 对接收者和继承环境的绑定；继续扩展自建 shell parser 是错误方案信号 |
| 5 | §A3.6、§C2（受信任的写入者） | 事件签名密钥 `authority.key` 是审计区里的文件；同一 OS 用户下的任何进程都能读。agent 在 Bash 里跑的 Python 可以用拼出来的路径读它，或直接实例化 `V1Runner`（例如用手写的 proof 收据调 `record_proof_result`），从而以 runner/cli 身份签 `test_call`、`proof` 等事件。gate 只做字面检查，拦不住运行时拼出的路径 | XG 2026-10-22 接受为已知残余风险后切换。人工授权（run-land / accept-finding / discard-snapshot）仍由 daemon 内存里的一次性 token 判定，不受影响；final 的树/base 绑定不受影响 | 根治需要把签名密钥移进 daemon 内存、只经 owner-only socket 提供按事件类型和执行上下文校验的签名，属于 Part A 变更；单独立项，不阻塞切换 |
