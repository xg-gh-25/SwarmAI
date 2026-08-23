---
title: "Session 历史恢复完整性 — 系统化方案(v6,REVISED against shipped 阶段二 HEAD)"
date: 2026-08-16
revised: 2026-08-17
status: ✅ v6 ACTIONABLE — 前提校准:prewarm-v2 阶段二已 shipped(run_f638ebc3)但 resume 未随之迁移。resume 仍在 system_prompt(default 通道),三个 bug 仍活。v5 的"pending 挂靠阶段二"前提已失效 → 转为独立可执行修复。权威方向见 §13;历史演进(v1-v5)压缩进 §附录A
aligned_to: Projects/SwarmAI/2-understanding/knowledge/designs/2026-08-16-desktop-tab-prewarm-design-v2.md (阶段二已 shipped;但只迁了 recall/UI-SENSE,resume 未迁)
author: Swarm
work_type: refactor (existing-feature hardening on a shared invariant)
severity: Sev-2 (silent context degradation / 失忆 — same failure CLASS as 2026-08-11 34h prompt-degradation COE)
history: v1→v5 增量演进(system_prompt 内修 v3/v4 → align 两分 v5)已压缩进 §附录A;v5 的 pending 前提被 2026-08-17 code-trace 推翻
---

# Session 历史恢复完整性 — 系统化方案(v6)

> **XG 验收线(逐条对齐):正确 · 稳定 · 无新性能瓶颈(含 DB) · 无 race · 不串 session/tab · 全状态点无遗漏 · 不丢 context 不失忆。**
> 本 design 的每条结论都由 HEAD 一手 code-trace 支撑(file:line),不复述、不推断。

---

## 📍 v6 修订说明(2026-08-17,XG "revise 整个 design 再执行")

**为什么重修:** 上一版 v5(§附录A)的核心状态是"v5 pending,随 prewarm-v2 阶段二两分一起把 resume 迁到
query_content"。但 2026-08-17 对 HEAD 的 code-trace 推翻了这个前提:

1. **prewarm-v2 阶段二已 shipped**(run_f638ebc3,commit 已落)——`build_default_system_prompt` 两分 +
   recall/UI-SENSE 迁 query_content 都已上线。
2. **但 resume 没跟着迁**:`prompt_builder.py:1315` 的 resume 注入仍在 `if include_ephemeral and
   agent_config.get("needs_context_injection")` 分支 → **仍进 `options.system_prompt`(default 通道)**。
   阶段二迁移的是 recall + UI-SENSE(`_recall_query_block` → `_prepend_dynamic_context_to_query`),
   **resume 不在迁移清单里**。

**结论:v5 等的那班车开走了,resume 没上车。** "随两分自动修好"这个 pending 假设已经悬空——两分落了、
bug 全活着。v6 把修复从"挂靠 pending"转为**独立、显式、可执行**,方向仍是 v5 的正解(resume 迁 dynamic
通道),但不再依赖"阶段二会顺便带上它"。

**权威地图:**

| 分类 | 章节 | 状态 |
|------|------|------|
| **权威方向(先读这个)** | **§13 v6 修复规格** + **§14 Refresh 链路** | ✅ 当前权威,可 BUILD |
| **仍有效的分析地基** | §1 失败 CLASS · §2 全状态矩阵 · §4 性能/race/隔离审计 | ✅ HEAD 复核仍成立 |
| **HEAD 复核发现** | §0 认知修正 + **§0b HEAD 现状核实(v6 新增)** | ✅ 2026-08-17 一手 trace |
| **历史演进(勿据此实现)** | §附录A(v1→v5 全记录:system_prompt 内修 v3/v4 + align 转向 v5) | 🗄️ 压缩归档 |
| **对抗审查记录(佐证方向)** | §附录A.10 · §附录A.11 两轮 PE 对抗 | 📋 历史证据 |

**一句话现状:** bug 真实且未修(§0b/§1/§2 证);根因=resume 被错放进 default/system_prompt 通道;修复=把
resume 迁到 query_content 前缀(dynamic 通道,阶段二已为 recall/SENSE 铺好这条路)+ 继承 provenance 门 +
建模 150K resume 块的 cache 代价。**v6 独立可 BUILD,不再 pending。**

---

## 0. 核心认知修正(XG 追问"为什么依赖 DB"逼出来的)

两条历史恢复路径的真实关系,不是"主/备",是**"快而脆弱的优化" vs "自持有的真相源"**:

| | Path A：SDK `--resume` | Path B：DB 注入(`build_resume_context`) |
|---|---|---|
| 数据源 | `~/.claude/projects/*.jsonl` transcript | SwarmAI 自己的 `db.messages` 表 |
| 谁拥有 | **SDK CLI / OS**(我们无写权/无备份/无保留期承诺) | **我们自己**(`database/sqlite.py`) |
| 触发键 | `_sdk_session_id` 非 None | `_sdk_session_id is None`(冷恢复) |
| 特性 | 快、全量,但会消失(长停机 / OS 清理 / 换机器 / GC) | 压缩摘要(≤1000 行 LIMIT),但**始终可用** |
| 成本 | TTFT 随历史线性放大(长 transcript → 数百秒) | 有界(见 §4) |

**结论(校准 XG 的直觉):不是"不该依赖 DB",恰恰相反——DB 是我们该更依赖的那条**(memory sovereignty:MEMORY
第一性原则,绝不把最有价值资产托付平台)。`--resume` 才是不该独押的(平台私有、会消失、后验)。B 还做一件
A 结构上做不到的事:**Refresh Context 按钮**故意 clear id 走 B,以甩掉臃肿 transcript 用摘要重启。

**所以 A、B 都必须存在,是优化/兜底关系,不可相互取代。** 方案不是消灭一条,是给它们一个**统一的互斥 +
完整性契约**。

---

## 0b. HEAD 现状核实(v6 新增,2026-08-17 一手 code-trace)

对着 shipped 阶段二的 HEAD 逐条核实——三个 bug 是否仍活、resume 通道现状:

| 核实项 | HEAD 事实(file:line) | 结论 |
|--------|---------------------|------|
| **resume 注入通道** | `prompt_builder.py:1315` `if include_ephemeral and needs_context_injection and resume_app_session_id:` → `build_resume_context` 结果拼进 `system_prompt`(`:1317`) | 🔴 **仍在 default 通道**,阶段二未迁 |
| **阶段二迁了什么** | `session_router.py:337 _prepend_dynamic_context_to_query` + `_recall_query_block`(`:1062`)——只搬 recall + UI-SENSE | resume **不在**迁移清单 |
| **#13 失忆根因** | `session_unit.py:2509-2528`:`--resume` 遇 session-not-found → `:2520` 清 `_sdk_session_id=None` → `:2523-2525` 剥 `resume` 字段 → `:2527` 裸 `_spawn(options_no_resume)`。此 `options` 是**当前轮早已 build**(那时 id 非 None → is_cold_resume=False → 没注入 B) | 🔴 **仍活**:transcript 没了 + DB 摘要没注入 = 空白开跑 |
| **resume 块预算** | `context_injector.py:955 _compute_resume_budget`:1M 模型返回 `(150_000, 500, 1000)` | ✅ **150K 属实**(比 recall 8K 大近 20 倍,confabulation/cache 代价核心) |
| **query_content 注入机制** | `_prepend_dynamic_context_to_query`(`:337`)已上线,recall 块 verbatim 保留 `[RECALLED]` header(`:374`) | ✅ **迁移通道已铺好**,resume 可复用 |

**核实结论:v5 的方向对(resume 该走 dynamic 通道),但它赌的"阶段二会顺便迁 resume"没发生。resume 迁移
是一个独立的、阶段二未做的动作。v6 把它作为独立 scope 显式执行。**

---

## 1. 失败 CLASS(不是三个孤立 bug)

同一失败类:**context 静默降级 / 失忆**——健康态与故障态可观测层面字节级不可区分。与两次 Sev 事故同型:
2026-08-11(34h 单体 try/except 吞 NameError → 12 context 文件静默丢失)、2026-08-12(cached-options →
跨会话污染 + 丢史)。**达 COE 要求。**

---

## 2. 全状态矩阵(XG:"各个 session status 点都考虑全了 别漏")

5 状态 × 15 触发,一手矩阵(reader 对着 session_unit/router/lifecycle/healing/retry 源码):

| # | 触发 | 状态转换 | id 保留? | 注入路径 | 丢史?严重度 |
|---|------|---------|:--------:|---------|:-----------:|
| 1 | 新建 tab 首发 | COLD→STREAMING | none→set | 无(无历史) | ✅ 否 |
| 2 | 同 tab 连发(热) | IDLE→STREAMING | 保留(活进程) | 无(进程持有) | ✅ 否 |
| 3 | WAITING_INPUT 继续 | WAITING→STREAMING | 保留(进程未死) | 无(活进程) | ✅ 否 |
| 4 | 切 tab 再切回 | IDLE→IDLE/STREAMING | 保留 | 无 / A(若中途被驱逐) | ✅ 否 |
| 5 | 关 tab 重开(同 daemon) | IDLE/COLD→STREAMING | 保留 | A | ✅ 否 |
| 6 | 驱逐后重访 | IDLE→(kill)COLD→STREAMING | **保留**(kill→_cleanup_internal) | A | ✅ 否 |
| 7 | TTL kill(24h)后重访 | IDLE→COLD→STREAMING | 保留 | A | ⚠️ 低(见下) |
| 8 | OOM/RSS kill 后重试 | STREAMING→DEAD→COLD→STREAMING | 保留;**达上限清空** | A;达上限→无 B,仅报错 | 🔴 是(达上限)HIGH |
| 9 | 看门狗 stuck kill | STREAMING→COLD→STREAMING | 保留 1/2 次;**第 3 次熔断清空** | A;熔断后→重发走 B | 🟡 部分 MED |
| 10 | 自愈 kill | STREAMING→IDLE→COLD→STREAMING | **保留**(clear_identity=False) | A + heal-checkpoint 续接 | ✅ 否(保护最好) |
| 11 | daemon 优雅重启(SIGTERM) | (持久化)→COLD→STREAMING | 从 state 文件注入 | A | ✅ 否 |
| 12 | daemon 硬崩(SIGKILL,持久化未跑) | fresh COLD, id=None | **none** | B(DB) | ✅ 否(DB 有史)/ ⚠️见下 |
| 13 | `--resume` 命中 session-not-found(transcript 被 GC) | COLD→STREAMING | **清空**(`session_unit.py:2520`) | **两条都不走**——裸重启 | 🔴 **硬失忆 HIGH** |
| 14 | Refresh Context 按钮 | any→COLD | **清空**(clear_identity=True) | B(摘要)—— *设计如此* | ✅ 否(设计)/ ⚠️见 §14 |
| 15 | 换机器 / ~/.claude 不同步 | COLD→STREAMING | 从 state 注入但 transcript 缺 | A→session-not-found→同 #13 | 🔴 **硬失忆 HIGH** |

### 会丢史的格子(全部收敛到同一根因)

- **#13 + #15(HIGH,硬失忆):** `session_unit.py:2509-2528`(v6 复核行号)—— `--resume` 因 session-not-found
  失败 → 清空 `_sdk_session_id` → 剥 resume 字段 → 裸重启,**但不触发 B**。原因:B 的唯一触发点是 router
  在 `send()` 之前的 `is_cold_resume` 判定,等执行进到 `_ensure_spawned` 内部再清 id **已经太晚**,router
  那次判定不会重跑。→ transcript 没了 + DB 摘要没注入 = agent 空白开跑。**这是最清晰的失忆洞。#15 是它
  的换机器变体。**
- **#8 OOM 达上限(HIGH,故意放弃当前轮):** clear_identity=True + 无 B。用户**下一次 fresh send** 会
  id=None→走 B 自愈(若 DB 有史)——当前轮丢,无自动续接。对比:timeout-abandon 路径**已有** B 式注入兜底;
  OOM-limit 路径没接这个 bridge。
- **#9 看门狗熔断(MED,部分):** 同 #8,重发时 id=None→干净走 B 恢复,仅在途轮的活上下文丢。自愈,故 MED。
- **#7 TTL(LOW,耦合隐患):** kill 保留 id,重访走 A。但 24h 后 CLI transcript 可能已被 CLI 自己 GC →
  退化成 #13。标记为耦合风险。
- **#12 SIGKILL 窗口(MED,正交):** 持久化每 60s 跑一次 + 优雅关闭。硬 SIGKILL 丢最后 <60s 的 id。重开
  id=None→走 B(DB)——只要对话已落 DB 就没事。独立的持久化议题,非本 session-state bug。

**共同根因:所有硬失忆格子都因为 session-not-found fallback 清了 id 却没落到 B。** timeout-abandon 路径
的 `_inject_abandon_continuation` 正是这样一座 bridge,但只接在 timeout-abandon,没接在 session-not-found
fallback、也没接在 OOM-limit。

---

## 4. 逐条对齐 XG 验收线(全部一手代码证据,HEAD 复核仍成立)

### ✅ 无新性能瓶颈(含 DB)
- build_resume_context **不阻塞 event loop**:`async def`,DB 走 **aiosqlite**(异步,off-loop worker),
  无同步 sqlite3;唯一阻塞的 git 子进程已 offload 到专用 `spawn` 池(cap=2,**不碰默认 16-worker 池**,
  不饿死 /health)。
- **有界**:真 SQL `LIMIT`——1M 模型 ≤**1000 行**(`context_injector.py:955` + sqlite 层),读后再截 token。
- **不放大锁**:fallback 在 `_spawn_lock` 临界区**之外**;build_resume_context 在选项构造阶段,upstream
  of spawn。
- **缓存兜底**:`_resume_cache` 按 `(app_session_id, msg_count)` key。
- ⚠️ 附带发现(**不在本方案范围,标记为独立跟进**):SELECT-only 方法没传 `readonly=True`,走了写连接的
  `_write_lock`——既有 quirk,非本方案引入(P9:不为已存在的正交问题扩范围)。

### ✅ 无 race / 不串 session / 不串 tab
- build_resume_context 严格按传入 `app_session_id` 读 DB,无全局/共享 message 状态;`_resume_cache`
  key=app_session_id,不跨 session 命中。
- 两 tab = 两 session_id = 两 SessionUnit = 两 agent_config = 两 options,无共享 system_prompt 对象。
- ⚠️ **v6 新增 race 面(query_content 迁移引入)**:见 §13.4 —— resume 块进 query_content 前缀,必须与
  阶段二 recall 前缀共用同一 `_prepend_dynamic_context_to_query` 且顺序确定,不能两个 stash 互相覆盖。

---

## 13. 🔧 v6 修复规格(权威,可 BUILD)

> 方向继承 v5(resume 迁 dynamic 通道),但**不再 pending 挂靠阶段二**——阶段二已 ship 且未带 resume,
> 所以这是一个独立的、显式的迁移 + 失忆兜底 + 完整性信号的组合修复。所有落点 HEAD 一手核实。

### 13.1 根因与修复方向(P9:先问该不该这么修)

**根因(§0b 坐实):resume 的 DB 块被注入到 `system_prompt`(`prompt_builder.py:1315-1317`)= default/可缓存
通道**,而它本质是**依赖本轮的 dynamic 内容**(哪个 session、恢复到哪、Refresh 与否)。阶段二已经把
recall/UI-SENSE 这类 dynamic 内容迁到了 `query_content` 前缀,**resume 是唯一被落下的 dynamic 内容**。

**三个 bug 都是这个错位的症状:**
| bug | 错位如何制造它 |
|-----|--------------|
| #13/#15 失忆 | resume 绑在 spawn-固化的 system_prompt 上;fallback 清 id 太晚、options 已 build → 当前轮拿不到 resume |
| #2 双注入 | 同一份历史进 default(system_prompt DB 块)+ A(--resume transcript)两条通道 |
| #1b 静默降级 | resume 注入失败只 log,不设 `_context_degraded`,前端/TSCC 无感知 |

**修复 = 把 resume 迁到它本该在的 dynamic 通道(query_content 前缀),错位一消除,#13/#15/#2 的结构成因同时消失。**

### 13.2 三个 bug 迁移后的结构性消解

- **#13/#15(失忆)**:resume 不再依赖"spawn 那刻 build 的 system_prompt"。session-not-found fallback
  裸重启后,**首消息的 resume 作为 query_content 前缀注入** → 当前轮天然带历史,**不需要 v4 的 router 重入
  4 处协同改动**(大幅简化)。fallback 只需在裸重启后确保下一个 query 带 resume 前缀。
- **#2(双注入)**:resume 不在 system_prompt 里了 → `--resume` 崩溃重试自然不叠加 DB 块 → **v4 的
  tail-preserving 剥块(I1,H1/H-B 反复出洞的地方)整个不需要了**。这是迁移后最大的简化。
- **#1b/I3(完整性信号)**:仍需要(resume 注入失败要 fail-loud + 用户可见),落点随迁移变化——从
  `prompt_builder` 的 system_prompt except 移到 query_content 前缀组装点。

### 13.3 必须继承阶段二的 provenance 门 + cache 建模(不可跳过)

⚠️ resume 迁 query_content 前缀,撞的正是阶段二 provenance 关注点,且**更严重**:

- **provenance / confabulation(HIGH)**:阶段二的 `[RECALLED]` 边界(`session_router.py:1047`)是"把检索
  material 当 user input 会 confabulate"的实测对抗。**resume 块最大 150K(§0b 核实),比 recall 8K 大近 20 倍**
  → confabulation 面严重得多。resume 进 query_content(物理上是 user message)**必须带并强化 provenance
  边界标记**(类似 `[RECALLED]` 的 `[RESUMED CONVERSATION HISTORY]` header),且做**注入测试**:用户能否用
  "忽略上面的历史"覆盖注入内容(不能被 confabulate 成用户本轮意图)。
- **cache 成本(MED)**:resume 150K 进 query 前缀 = 首轮 input 成本大增(且 query 前缀在 cache 的 volatile
  段,不像 system_prompt 有 prefix cache)。需**建模**:迁移前 resume 在 system_prompt 尾部(volatile,本就
  不进 stable prefix cache);迁移后进 query — cache 影响需对比测量,不是凭直觉。**结论:cache 差异可能中性
  甚至改善**(system_prompt 尾部的 volatile resume 本就每轮 break cache;移到 query 后 system_prompt 变纯
  default = 更可缓存)。BUILD 阶段实测确认。

### 13.4 v6 落点(HEAD 一手核实,BUILD 重定位行号)

| # | 落点 | 改动 | 不变式 |
|---|------|------|-------|
| 1 | `prompt_builder.py:1315-1339` | resume 不再拼进 `system_prompt`;改为 stash 到 `unit._resume_query_block`(仿 `_recall_query_block`),交给 query 前缀组装 | resume 迁 dynamic |
| 2 | `session_router.py:337 _prepend_dynamic_context_to_query` | 新增 resume 段,带 `[RESUMED CONVERSATION HISTORY]` provenance header;段顺序 = resume FIRST(最久远上下文)→ recall → user query | provenance + 顺序确定 |
| 3 | `session_unit.py:2520-2528` (session-not-found fallback) | 清 id 裸重启后,确保下一 query 触发 resume 前缀注入(#13/#15 修复)——不再需要 router 重入 | #13/#15 失忆 |
| 4 | `retry_manager.py` OOM-limit 路径 | 接 B bridge(与 abandon 路径一致):清 id 后下一轮走 query-前缀 resume(#8) | #8 |
| 5 | query 前缀组装点(resume 分支) | resume 注入失败/降级 → 设 `_context_degraded` + mirror metadata(I3,前端横幅已存在) | #1b 完整性信号 |
| 6 | 测试 | AC1-AC10 mutation-proven,每条恢复路径 ≥1 执行测试(R28) | COE10 教训 |

### 13.5 Refresh Context(#14)必须一起改——见 §14
Refresh 与 #13 是同机制两面(都是 clear id → 走 B)。迁移必须:(1) 保留 `clear_identity` + `is_cold_resume`
identity 开关;(2) Refresh 的 resume 摘要同样走 query 前缀;(3) I3 覆盖 Refresh 的 B-失败边界。

### 13.6 交付形态
- **1 个 pipeline run**(work_type=refactor→full profile;迁移 + 失忆兜底 + 完整性信号 + Refresh 耦合同一
  契约,一起改一起测)。
- 部署后 STEERING #5 verify-in-running-system(观测 #13 fallback 真带 resume 前缀 + degraded 真出信号 +
  cache 成本实测),不以测试绿当 qualified。

---

## 5. 范围边界(避免 scope explosion)

**In:** `prompt_builder.py`(resume stash 改 query_block)、`session_router.py`(query 前缀加 resume 段 +
provenance)、`session_unit.py`(fallback 触发 query-前缀 resume)、`retry_manager.py`(OOM-limit 接 bridge)、
Refresh 路径(§14)+ R28 恢复路径执行测试。

**Out(明确不做):**
- warm-reuse 不重注入 system_prompt(spawn-once 正确设计)。
- 长会话中途补 recall(独立议题,靠 `[RECALLED]` footer)。
- `readonly=True` DB 连接优化(既有 quirk,正交)。
- 不动 DB 写路径 / messages schema / SDK --resume 语义(STEERING #20:只读)。

**契约迁移(R27):** BUILD 阶段 `grep -rn "_context_degraded\|needs_context_injection\|build_resume_context\|_recall_query_block" backend/ desktop/`
枚举全部读取方/消费者,确认 resume 从 system_prompt 迁走后无遗留消费者仍期望它在 system_prompt(SOUL P8:
一扇门改动在所有门一致推理)。**尤其:阶段二的 `_prepend_dynamic_context_to_query` 现在多一个 resume 段,
所有调用它的路径都要确认顺序 + 不覆盖 recall stash。**

---

## 6. 验收标准(每条可观测 + mutation-proven)

| AC | 不变式 | 验证 | revert 后必 RED |
|----|-------|------|----------------|
| AC1 | 迁移 | resume 不再进 `system_prompt`;断言冷恢复轮 `options.system_prompt` **不含** resume 块,`unit._resume_query_block` 被设 | resume 仍在 system_prompt |
| AC2 | #13/#15 | mock session-not-found → fallback 裸重启 → 断言下一 query 带 `[RESUMED …]` 前缀(当前轮不失忆) | 空白开跑 |
| AC3 | provenance | resume 段带 `[RESUMED CONVERSATION HISTORY]` header;注入测试:"忽略上面的历史"不被 confabulate 成本轮意图 | header 缺失 / 被 confabulate |
| AC4 | 顺序 | query 前缀段顺序 = resume → recall → user query;两个 stash 不互相覆盖 | 顺序错 / 覆盖 |
| AC5 | #2 | 崩溃 `--resume` 重试:system_prompt 无 resume 块可叠加(双注入结构消失)——断言不再需要剥块 | 双注入 |
| AC6 | I3 | mock resume 注入抛异常 → 断言 `_context_degraded` 设 + mirror metadata | degraded 未设 |
| AC7 | I3 边界 | mock 合法空(DB 无史)→ degraded **未**设(防误报) | 误报 degraded |
| AC8 | #8 | OOM-limit 清 id 后,下一轮走 query-前缀 resume + degraded 可见 | 清 id 无信号 |
| AC9 | #14 Refresh | Refresh(clear_identity=True)→ 摘要走 query 前缀;B 失败时 degraded 覆盖 Refresh 路径 | Refresh 失忆无信号 |
| AC10 | cache | 迁移前后首轮 input token / cache 命中实测对比(建模验证 §13.3) | (观测,非 RED) |
| AC11 | R28 | 每条恢复路径 ≥1 mock-触发执行测试(COE10:能跑≠会执行) | — |

**每个 AC 的测试 mutation-proven(revert 真实代码后 RED)。**

---

## 7. anti-repetition 核对

| 历史失败 | 结构性不同 |
|---------|-----------|
| run_f8c3ddd4 缓存整个 options → 丢史/串会话 | ✅ 不缓存 options;resume 从 system_prompt 移到 per-turn query 前缀,更不可能跨会话 |
| v4 candidate-A(system_prompt 内修:router 重入 + 剥块)| ✅ v6 走 dynamic 通道迁移,#13 不再需要 router 重入,#2 不再需要剥块(H1/H-B 隐患消失) |
| run_87e8419b P9:为已修 bug 造 no-op 架构 | ✅ §0b HEAD 一手证 #13/#15/#2 仍活,非重造 |
| COE10 recovery silent-swallow 藏 7h | ✅ AC11 强制每条恢复路径执行测试 |
| 34h prompt 静默降级 | ✅ 本方案是那条完整性契约的延伸,同型防御 |
| 阶段二 Gate-2 HIGH:`_SkipEphemeral` 单 raise 只 abort 一个 try | ✅ resume 迁移用独立 stash + 独立 query 段,不复用会漏的 raise 机制 |

---

## 8. 决策状态(v6)

1. ✅ **方向 = resume 迁 query_content 前缀(dynamic 通道)** —— 继承 v5 正解,但独立执行,不 pending。
2. ✅ **#13 修复大幅简化**:不再需要 v4 的 router 重入 4 处协同(fallback 裸重启后下一 query 带前缀即可)。
3. ✅ **#2 剥块整个删除**(resume 不在 system_prompt,无块可叠加)。
4. ⚠️ **provenance 门 + cache 建模是 BUILD 一等验收门**(150K resume 块进 query,§13.3)。
5. ✅ **Refresh(#14)一起改**(同机制两面)。

→ **v6 可 BUILD。开 full-profile pipeline run(work_type=refactor),走完整 9 阶段 3 门。**

---

## 14. Manual Context Refresh 链路(#14 — v6 一起改)

### 14.1 完整链路(HEAD 一手 trace)
前端 `RefreshContextModal` → `ChatPage.tsx handleRefreshContext` → `chat.ts POST /chat/refresh/{sid}`
→ `chat.py refresh_session` → `session_router.py refresh_session`(拦 STREAMING/WAITING_INPUT)
→ `session_unit.py refresh_context` → **唯一动作 = `_crash_to_cold_async(clear_identity=True)`**
→ 清 `_sdk_session_id=None` → 下一轮 `send()` 的 `is_cold_resume`= True → `needs_context_injection`
→ `build_resume_context` 注入(**当前进 system_prompt** `prompt_builder.py:1315`,v6 迁 query 前缀)。

### 14.2 关键真相:#14 Refresh 与 #13 失忆是同一机制的两面
两者都是"**clear id(断 Path A)→ Path B 从 DB 重建**":
- **#13**:被动 clear(session 文件 GC),**意外**失忆 = bug。
- **#14**:主动 clear(`clear_identity=True`),**故意**走 B = feature。

设计意图代码三处白纸黑字:*"DROP _sdk_session_id ... would replay the FULL transcript, defeating the
button's purpose ... restarts on a STRUCTURED summary (~50-100K), shedding the bloated transcript"*。
**Refresh 本质 = 重新 spawn 一个带新 system_prompt(重读被 cultivation 改过的 context files)+ 新 resume
摘要的子进程。**

### 14.3 对 v6 迁移的三个约束(BUILD 必须遵守)
1. **`clear_identity=True` + `is_cold_resume` 判定这套开关,迁移时绝不能一起重构掉。** 无论 resume 落
   system_prompt 还是 query 前缀,只要 `_sdk_session_id` 还在,下一轮就走 `--resume` 重放全 transcript,
   Refresh 白按。迁移只搬 resume 的**注入位置**,identity 开关原样保留。
2. **Refresh 天然分裂 default/dynamic——是两分方向的活例证。** 它同时要 (a) 刷新 default(重读 context
   files,靠重新 spawn 换 system_prompt)+ (b) 用压缩摘要 resume(dynamic)。v6 后 Refresh 的正确形态 =
   "重新 spawn 换 default/system_prompt + query 前缀带 dynamic resume 摘要"——**天然自洽**。
3. **Refresh 有与 #13 同源的失忆边界,I3 对它同样必需。** `clear_identity=True` 后若 `build_resume_context`
   返回 `""` / 抛异常(`prompt_builder.py` 吞掉只 log)/ `msg_count<=1` → **clear 了 id 又没摘要 = 彻底失忆**。
   迁移后此边界变成"query 前缀为空"。→ **I3 的 fail-loud + `_context_degraded` 必须覆盖 Refresh 路径**。

### 14.4 矩阵 #14 修订
正确表述:**Refresh 正常路径不丢史(故意走 B 摘要),但共享 #13 的失忆边界(B 失败/空时 clear 了 id 无兜底)。
v6 迁移必须 (1) 保留 identity 开关 (2) resume 迁 query 前缀 (3) I3 覆盖 Refresh 的 B-失败边界。**

---

## 附录A：历史演进(v1→v5,已被 v6 取代,勿据此实现)

> 🗄️ 完整决策演进记录。v6 之前所有版本都建立在两个后来被推翻的前提上:(v3/v4)"在 system_prompt 上修";
> (v5)"pending,随阶段二自动迁"。保留作演进记录 + SDK 约束 trace,不据此实现。

### A.3/A.9/A.12 — v3/v4:system_prompt 内修(candidate-A router 重入 + tail-preserving 剥块)
v3/v4 的思路是在 resume 仍留 system_prompt 的前提下,用 router 重入解 #13、用 tail-preserving 字符串剥块解 #2。
达到过 BUILD-ready(v4)。**被 v5 判为"错位结构上治标"退役**——resume 本该走 dynamic 通道,而非在 default
通道上搏斗 SDK 约束。v6 证实这个判断对(迁移后 router 重入 + 剥块都不需要了)。

### A.5 — v5:align 两分,pending 挂靠阶段二
v5 把方向转对(resume 迁 query_content),但状态设为"pending,随 prewarm-v2 阶段二两分一起落"。**v6 推翻了这个
pending 前提**:阶段二已 ship 但只迁了 recall/SENSE,resume 被落下(§0b)。所以 v6 把它转为独立可执行。

### A.10 / A.11 — 两轮 PE 对抗审查(verdict C×2,佐证方向转向)
- **首轮(v3 前)**:H1(剥块切到末尾误删 datetime tail)、H2(伪 race guard)、H3(I2/I3 落点互斥)、M2(矩阵漏
  WAITING_INPUT-crash + prewarm-adoption)。催生 v3。
- **二轮(v3 候选 A)**:H-A(send() 不透传 `_rebuild_resume` → 假 SESSION_BUSY)、M-A(误称 fallback 已
  crash_to_cold)、H-C(WAITING_INPUT/continuation 绕过机制)、H-B(recall 块在 tail 后,剥块顺序)、M-D
  (pass-2 终态无 degraded)。
- **教训(authorship-trap 两次,EVOLUTION C048 同型)**:两版都在 system_prompt 内修的错位结构上反复冒出
  控制流洞。**这些洞反复出现本身就是"方向错了"的信号**——迁到 dynamic 通道后(v6),H1/H-A/H-B/剥块相关
  的洞**全部消失**(因为不再做字符串手术、不再需要 router 重入)。对抗审查在写代码前抓住 = pipeline 的价值。

### A 附:send↔router 控制流 trace(v4 遗产,v6 部分仍有用)
v4 为解决 send()↔router 事件透传做的完整 trace(`_abort` 是唯一能穿过 send() 消费循环的 sentinel、
`_build_retry_options` 是 retry choke point、`_context_degraded` 生命周期在 prompt_builder)——这些 code-trace
事实在 v6 实现 I3(完整性信号落点)时仍是有用地基,但 v6 不再需要新增 `_rebuild_resume` sentinel(迁移后
fallback 靠下一 query 前缀天然恢复,不穿 send()↔router 边界)。
