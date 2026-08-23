# 改进提案:「模型可见 = 必被记录」升级为运行时不变量

> **Type:** `[decision]`(设计沉淀)· **Status:** ✅ **已实现(ALREADY-SATISFIED)** — 无需新建
> **Proposed:** 2026-08-15 · **来源:** DeepSeek Harness `docs/architecture.md` 借鉴
> (研究报告:`Knowledge/Reports/2026-08-15-deepseek-harness-cordis-research.html`)
> **对应我们的事故:** MEMORY § COE Registry — "system prompt 静默降级 ~34h" COE
>
> ⚠️ **2026-08-15 复核结论(pipeline run_026449e5,EVALUATE DEFER,零代码):** 起 pipeline
> 落地时,EVALUATE 的 Understanding Gate 判定为 **ALREADY-SATISFIED**——§3.1 的三项**全部已由
> COE 修复自身(commit chain run_e47c1cfb)实现,并已测试覆盖**:
> - completeness gate → `prompt_builder.py:49 assert_core_sections()` + `required_prompt_sections()` SSOT,调用点 `:1372`;
> - 故障隔离 → `_core_committed` 核心先提交(`:949-954`),ephemeral 段各自独立 try;
> - fail-loud → ERROR 日志 + `_context_degraded` 标记,且 mirror 进 `_system_prompt_metadata`(`:1397`,下游可消费);
> - R28 恢复路径执行测试 → `test_prompt_builder_properties.py::test_ac4_core_failure_is_loud`(mock load_all 抛异常、断言降级路径**真的执行**)+ `test_ac4_degraded_signal_reaches_metadata` + Layer-4 真实 E2E + `test_ac5_core_committed_exactly_once`;**实测 6/6 绿**。
>
> **本文档保留为设计记录 + 外部借鉴溯源**,不再是待办。DeepSeek 的不变量思路验证了我们 COE
> 修复方向的正确性(独立团队、同一解药),但我们**已经在那条路上了**。
> **归属说明:** 这是设计沉淀,不是 earned 治理规则,不进 SOUL/AGENT。

---

## 0. 一句话

dsh 强制一条不变量:**凡是模型请求能看到的内容,必须能从持久化日志重建**,由运行时断言守护。
我们有同一个思路(resume-context / DailyActivity),但**靠约定,不靠断言**——而约定
会 fail-soft(静默),断言会 fail-loud(响)。本提案把这条从约定升级为结构性不变量。

---

## 1. 问题:约定为什么不够(证据,非推断)

我们已经真实付出过代价。COE 记录(MEMORY):commit `039c4f32`(2026-08-10,一个把
blocking I/O 移出 event loop 的性能修复)在 `prompt_builder.build_system_prompt`
引入 `daily_activity_dir` NameError → 被**单体 try/except 整体吞掉** → 整个 system
prompt 组装抛异常 → **全部核心 context 文件(SOUL/AGENT/SELF/rules)一起被丢** →
agent 带近乎空 prompt 运行,**静默 ~34 小时,触发 ≥316 次,零告警**。

根因类别 = **fail-soft**:组装失败时,「丢了核心认知」与「组装成功」在字节层面
不可区分。这正是 dsh 用不变量要消灭的失败模式。

> dsh 的原话(`docs/architecture.md` § Session log):
> "**Model-visible means logged.** Anything that reaches a model request must be
> reconstructable from the log, and a runtime invariant asserts it."

## 2. dsh 怎么做的(参考,不照抄)

- 会话日志是 **append-only 事件流**,是模型上下文的**唯一来源**。
- `deriveMessages()` 从日志投影出模型历史;fork / resume / 转录 / 遥测 / 持久化
  **全部从这一条流派生**。
- 要给模型加任何新的可见输入 → **必须新增一个 session event 类型**,再从日志渲染。
- 一条**运行时不变量**断言:进入模型请求的内容,都能从日志重建。

**注意差异:** dsh 是「日志即上下文唯一来源」的强模型;我们的 system prompt 是从
**12 个治理文件 + 若干 ephemeral 层**组装的,不是单一日志流。所以我们**不照搬「日志
即唯一源」**,只借**「可见即可重建 + 组装完整性由断言守护」**这个更小、更贴合我们的核。

## 3. 提案:我们的最小落地形态

分两个层次,后者是核心:

### 3.1 组装完整性断言(核心,直接封 34h 那类事故)
在 `prompt_builder.build_system_prompt` 的组装收尾处,加一条 **completeness gate**:

- **核心段必达:** 断言核心 context 段(SWARMAI/IDENTITY/SOUL/SELF/AGENT + 必需 rules)
  在最终 prompt 中**实际存在且非空**——用一个 SSOT 的 `required_prompt_sections()`
  清单校验,不是靠"组装没抛异常"推断。
- **故障隔离,核心不连坐:** 核心段先独立提交;ephemeral 段(briefing/digest/
  suggestions/resume 等)各自独立 try,失败只丢自己,**绝不让一个 ephemeral bug 炸掉
  核心**(这是 34h COE 的直接教训,COE 修复里已部分实现 `_core_committed`——本提案
  把它固化为不变量 + 断言,而非一次性修复)。
- **fail-loud:** 任何核心段缺失 → ERROR 日志 + `_context_degraded` 标记 + 升级到
  Need-You / 当前会话可见(绝不静默降级)。

### 3.2 可重建性断言(进阶,可作第二阶段)
对**注入模型的每一段可见内容**,要求它有一个**可重建来源**(文件路径 / DB 行 /
DailyActivity 条目 / resume-context 源)。断言:prompt 里出现的核心认知内容,都能
指回一个持久源。这条更接近 dsh 的完整不变量,但成本更高,建议**先做 3.1,观察后再定**。

## 4. 为什么现在做 / 值不值(P9 前置自问)

- **该存在吗?** system prompt 是 agent 全部判断力的来源;它静默缺失 = agent 静默变傻。
  这是最高阶不变量之一(AGENT §0)。✅ 值得。
- **在要紧路径吗?** 是——每个新 session / resume 都走这条组装。✅
- **最小动作是不是删?** 不是删,是**把已有的一次性修复固化成结构性断言**——正好符合
  "prose/一次性修复 < 结构性 gate"(SOUL P7)。这条 COE 的修复目前是散在代码里的
  `_core_committed` + per-段 try,**没有一条 SSOT 断言守护它不回退**。

## 5. 与我们既有设计的关系(P8 四门一致性)

- 这不改 MEMORY/EVOLUTION/KNOWLEDGE 的**摄取**门,只改 **system-prompt 组装**这道
  「读侧」——所以不触发跨门一致性风险(它不是知识 admission,是 prompt assembly)。
- 与 § System Prompt Assembly(KNOWLEDGE.md)的现状一致:读线「WARN-never-truncate」
  已是既定方向;本提案在同一条读线上**再加一条 completeness 断言**,方向同源。

## 6. 明确不做的(避免范围蔓延)

- ❌ 不把我们的 system prompt 改成「单一 append-only 日志流」(dsh 强模型)——我们是
  多文件治理组装,强行日志化是大爆炸重构(R26),且无必要。
- ❌ 不引入 cordis / dsh 任何代码或依赖——**借思路,不借码**。

## 7. 下一步

待 XG 决策:批准则开 `s_autonomous-pipeline`(profile 由 pipeline 在 EVALUATE 定;
预判 bugfix→full 之间,因触及热路径 + 需 R28 恢复路径执行测试)。
本提案本身零代码改动,仅设计沉淀。
