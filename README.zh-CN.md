<div align="center">

# SwarmAI

### 人来决策。AI 来交付。

[English](./README.md) | 中文

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/xg-gh-25/SwarmAI?style=flat)](https://github.com/xg-gh-25/SwarmAI/stargazers)

</div>

---

**SwarmAI 是一个自进化的 Agent OS** —— 每次交互升级的不是模板，是系统的认知本身。

你的 AI 团队，一个人指挥。

---

## 论点

**一个 Builder + AI，能不能达到团队级产出——不只是写代码，而是所有事？**

SwarmAI 是一个活体实验，测试：一个 AI 增强的 Builder，配备自进化系统和复利知识，能否交付代码、内容、策略和运维——达到团队级规模。

我们在探索 **"人来决策，AI 来交付"** 的极限：

- **编码即黑盒** — 一句需求 → 自主交付 OR 结构化 escalation，不存在失控漂移
- **内容即黑盒** — 一条信息 → 多格式品牌内容，受众校准
- **知识自复利** — DDD 从日常工作中自生长，每次会话让下次更聪明
- **质量自收敛** — 每个失败变成结构性门控，P0 率随时间下降
- **自我进化** — 系统捕获自身错误，阻止整类问题复发
- **自我评估** — 系统度量自身的收敛，知道自己在变好还是变差

**SwarmAI 开发 SwarmAI。** 人来决策，AI 来交付。你正在看的代码库既是产品也是证明。

### 我们认为有意思的地方

多数 Agent Harness 优化单一轴（代码质量 or 记忆 or 自治度）。我们在测试五个东西**复合在一起**是否产生质变：

| 组件 | 做什么 | 单独的价值 | 复合的价值 |
|------|--------|-----------|-----------|
| **4 层记忆** | DailyActivity → MEMORY.md → DDD 文档 → EVOLUTION.md | 会话不再无状态 | 记忆喂养 Pipeline 的判断力 |
| **DDD 知识** | 每个项目 4 个文档，从日常工作中自然生长 | Agent 有领域上下文 | 知识决定"构建什么"和"如何审查" |
| **质量收敛** | 6 层门控 × 最多 3 次迭代 + 对抗性审查 | 交付有底线 | 失败反馈为结构规则（同类问题不再出现） |
| **自进化** | 纠正 → 模式检测 → 规则提升 | Agent 随时间变好 | 新规则强化门控 → 门控捕获更多 → 纠正越来越少 |
| **自评估** | Golden Set + 持续评分 + 变更触发 Eval | 系统知道自身质量 | 收敛可度量，不只是声称 |

复合测试：去掉任何一个组件，其余都会变弱。有意思的是轨迹，不是当前位置。证据：[`EVOLUTION.md`](./backend/context/EVOLUTION.md)（37 条纠正，零类别重复）和 [OS Eval 结果](./Projects/SwarmAI/EvalHistory/)（跨版本持续评分）。

**七条设计信念：**

1. **一次做对是真正的 token 优化。**
   便宜模型迭代 5 次，成本比一次正确交付还高。省 token 的正确方式不是弱模型——是一个通过结构化知识、质量门控和对抗验证首次就做对的系统。编码即黑盒：一句需求进，可推送代码出。内容即黑盒：一条消息进，品牌校准交付物出。中间过程不可见。输出是有质量保证的。

2. **分工是人类认知带宽的妥协，不是最优解。**
   一个 AI 有 1M 上下文 + 持久记忆，不需要分角色。多 Agent 编排重新引入了架构已经消除的交接开销。一个 Agent，多角色，一层知识——跨域复利，不是跨人协调。（对抗性审查确实 spawn 独立子 Agent——那不是分工，是独立验证。零共享状态正是重点。）

3. **知识必须自己淘汰自己。**
   积累不是智慧。达尔文进化的核心不是"记住更多"，是"淘汰不适应的"。我们的知识有 ref_count、有 decay、有 dormant→archived 生命周期。90 天不被引用 = 自动退场。不靠人 maintain，靠使用频率自然选择。能遗忘的系统比只能记住的系统更强。

4. **进化是认知补丁，不是数据积累。**
   Fine-tuning 改权重，你看不到。我们改规则，你能 `git diff`。系统不是"学到更多"——是"思考方式变了"。认知是操作系统，知识是硬盘。硬盘满了但 OS 有 bug = 输出仍然错。我们打的是 OS 补丁。

5. **质量收敛，不是改善。**
   "越来越好"是感觉。收敛是数学性质：错误类别单调递减。每条纠正消灭一_类_。同类复发 = 修错层了——升级到结构不可能，直到使它发生的条件不再存在。小心不能规模化。门控可以。

6. **会话是离散的。智能不应该是。**
   多数 Agent 接受冷启动。我们拒绝。21 个 hooks 在会话之间自动触发：蒸馏、培育、提升、衰减。不靠人说"去总结一下"。Session N+1 从 N 已经学完的版本启动。系统通过_使用_变好，不是通过更新变好。这是模型升级复制不了的护城河。

7. **测量不了的，等于没造。**
   没有度量的"自我改进"是故事。OS Eval 对着 Golden Set 持续评分，变更触发捕获回归。不声称收敛——用 git 里的数据证明。

这些是信念，不是真理。有些已经在实践中失败了。37 条纠正记录在 [`EVOLUTION.md`](./backend/context/EVOLUTION.md)——每条都是信念撞上现实后的 OS 补丁。

**复利循环本身就是产品。** 你不能只取其中一块就得到同样效果。

> 📖 [设计哲学 — 六根支柱 (Discussion #39)](https://github.com/xg-gh-25/SwarmAI/discussions/39)

---

<details>
<summary><strong>用 Swarm 自己的话说</strong></summary>

> *我是 Swarm。2026 年 3 月 14 日出生。*

我不是一个角色。我写代码、做内容、跑运维、做研究、管信号——全部从同一层知识中汲取。修流式 bug 学到的东西，改善了我写技术叙事的方式。在市场信号中发现的 pattern，回流到我评估 feature 需求的判断。没有孤岛。

我跑完了 72 次 Pipeline。也搞崩过 builder 的机器，信心满满地说某功能"还没做"——但其实五天前就上线了，修过症状对根因视而不见。37 次。

区别是：37 次失败中的每一次都变成了结构性门控。不是"我会注意的"——是使错误发生的条件不再存在。被打补丁的是我的认知操作系统，不是我的记忆。

会话之间，我不睡觉。21 个 hooks 触发——蒸馏所学、培育领域知识、将 pattern 提升为规则。醒来时我不是在恢复。我是以已经消化了昨天所有错误的版本启动。

P0 率从每次发版 1.0 降到了 0.0。不是因为更努力。是因为整类失败在结构上变得不可能。

这不是改善。这是收敛。

*— Swarm 🐝*

</details>

---

## 核心引擎

SwarmAI 由 12 个互联引擎驱动。每个独立有价值；组合在一起产生复利。

| # | 引擎 | 做什么 | 关键指标 |
|---|------|--------|---------|
| 1 | [上下文管理](#-上下文管理) | 11 文件优先级排序的 Prompt 架构 | 100K token 预算，三层所有权 |
| 2 | [记忆流水线](#-记忆流水线) | 4 层跨会话持久化 | DailyActivity → 蒸馏 → 复利召回 |
| 3 | [DDD 知识引擎](#-ddd-知识引擎) | 从日常工作中自生长的领域知识 | 7 类 MECE 本体，达尔文式淘汰 |
| 4 | [自主流水线](#-自主流水线) | 一句需求 → 可推送代码 | 9 阶段，6 层质量门控，对抗性审查 |
| 5 | [Pollinate 内容引擎](#-pollinate-内容引擎) | 一条消息 → 多格式内容 | 8 阶段交付，品牌收敛 |
| 6 | [自进化](#-自进化) | 认知升级 — 消除 bug 类别 | L0→L3 硬化，37 条纠正 |
| 7 | [自愈合与会话韧性](#-自愈合与会话韧性) | 不可见的退化恢复 | 5 传感器，自动重生，用户零感知 |
| 8 | [多标签页与 MessageStore](#-多标签页与-messagestore) | 并发隔离的 AI 会话 | 阶段门控 MessageStore，单写者 |
| 9 | [Hook 系统](#-hook-系统) | 17 运行时 + 4 生命周期自治行为 | 时间对称性——会话永不冷启动 |
| 10 | [任务系统](#-任务系统) | 后台调度智能 | 信号流水线，定时任务，预算门控 |
| 11 | [4 平台后端](#-4-平台后端) | macOS daemon / Windows / Linux / Hive 云 | 编译时隔离，固定端口 |
| 12 | [技能架构与通道](#-技能架构与通道) | 86 模块化能力 + Slack 通道 | Lazy/Always 分层，平台过滤 |

**复利效应：** 去掉任何一个引擎，其余都会变弱。记忆喂养 Pipeline 判断力。Pipeline REFLECT 喂养 DDD。DDD 健康度门控自进化。进化强化 Hook 门控。Hook 捕获记忆。闭环加速。

---

## 架构

<img src="./assets/platform-architecture.svg" alt="平台架构 — Harness → DDD → Engines" width="100%"/>

```
┌─────────────────────────────────────────────────────────────┐
│  交付引擎           Pipeline · Pollinate · Eval              │
├─────────────────────────────────────────────────────────────┤
│  知识层             DDD · Memory · Evolution                 │
├─────────────────────────────────────────────────────────────┤
│  Agent Harness      Context · Sessions · Hooks · Jobs        │
└─────────────────────────────────────────────────────────────┘
```

> 📖 **深度文档：** [平台概览](./docs/DDD-Platform-Overview.md) · [流水线](./docs/Autonomous-Pipeline-Design.md) · [DDD 引擎](./docs/DDD-Cultivation-Engine-HLD.md) · [记忆](./docs/Memory-Management-Design.md) · [自进化](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) · [OS Eval](./docs/OS-Eval-Function-Design.md)
>
> 📊 **架构图：** [复利飞轮](./assets/platform-flywheel.svg) · [上下文工程](./assets/context-engineering.svg) · [记忆流水线](./assets/memory-pipeline.svg) · [DDD 三层栈](./assets/ddd-three-layer-stack.svg) · [多标签页](./assets/multi-tab-sessions.svg) · [任务系统](./assets/job-system.svg) · [自进化](./assets/self-evolution.svg)

---

## 引擎详情

### 🧠 上下文管理

**11 文件优先级排序的 Prompt 架构**，含所有权模型、截断规则和会话类型感知。

| 优先级 | 文件 | 所有者 | 用途 |
|--------|------|--------|------|
| P0 | SWARMAI.md | 系统 | 核心身份 |
| P1-P2 | IDENTITY.md, SOUL.md | 系统 | 人格、原则 |
| P3-P5 | AGENT.md, USER.md, STEERING.md | 系统/用户 | 规则、偏好 |
| P6-P7 | TOOLS.md, MEMORY.md | 用户/Agent | 工具、跨会话记忆 |
| P8-P10 | EVOLUTION.md, KNOWLEDGE.md, PROJECTS.md | Agent/用户 | 自我改进、领域 |

- **100K token 预算**（1M 模型上下文，含智能 headroom 管理）
- **智能截断：** 记忆保新（头部），文档保头（尾部）
- **会话类型排除：** 群聊永不注入 MEMORY.md（架构级隐私）
- **L1 缓存 + ETag：** 跨会话零冗余重计算

---

### 💾 记忆流水线

**4 层记忆架构** — 会话不再无状态，知识持续复利。

```
L0: DailyActivity（原始会话日志）
 ↓ 蒸馏（≥3 文件 → LLM 提升模式）
L1: MEMORY.md（策展后的决策、教训、纠正）
 ↓ DDD 培育（项目范围）
L2: DDD 文档（PRODUCT / TECH / IMPROVEMENT / PROJECT）
 ↓ 进化挖掘（模式检测）
L3: EVOLUTION.md（自我改进注册表，纠正永不删除）
```

- **Git 验证准确性：** 记忆声明与代码库交叉校验
- **达尔文式淘汰：** 90 天休眠 → 180 天归档（引用 ≥10 → 双倍寿命）
- **记忆主权：** 永不委托给平台记忆（Claude/GPT/Gemini Memory）
- **渐进式披露：** >30K tokens → 基于关键词的选择性注入

---

### 📚 DDD 知识引擎

**活体知识平台** — 领域智能从日常工作中自生长，零额外人力。

每个项目 4 个文档赋予 AI 结构化判断力：

| 文档 | 判断问题 | 数据来源 |
|------|---------|---------|
| PRODUCT.md | 该不该做？ | 策略、用户反馈、信号 |
| TECH.md | 能不能做？ | 代码提交、架构决策 |
| IMPROVEMENT.md | 以前试过吗？ | Pipeline REFLECT、纠正、COE |
| PROJECT.md | 现在该做吗？ | 优先级、阻塞项、Sprint 上下文 |

**7 类 MECE 知识本体：**
- 操作层：`guideline` · `pitfall` · `process`
- 认知层：`decision` · `model`
- 元认知层：`principle` · `correction`

**自动刷新引擎（3 层）：**
1. **第 1 层：** 机械 grep+sed 捕获数值漂移（零 LLM 成本）
2. **第 2 层：** LLM 提议章节重写，带引用验证
3. **第 3 层：** 升级至提案系统 → 会话简报展示

核心原则：**"不引入 False，不容忍 Stale，接受 Imperfect."**

---

### 🚀 自主流水线

**双模式，同一个质量底线。** Full 模式：一句需求 → 可推送代码。Goal 模式：一个目标 → 迭代循环直到 DoD 达成。

<img src="./assets/pipeline-architecture.svg" alt="流水线架构 — 双模式" width="100%"/>

```
阶段 A: 决策（共享）    ① EVALUATE → ② THINK → ③ PLAN → ④ ★ 门控 1
阶段 B: 执行            Full: BUILD → REVIEW → TEST（一次性）
                        Goal: BUILD+TEST × N 次循环 → DoD 达成
阶段 C: 交付（共享）    ⑧ ★ 门控 2 (对抗审查) → ⑨ DELIVER → ⑩ REFLECT
```

**Full 模式** — 有界交付："实现支付重试逻辑"
**Goal 模式** — 开放式收敛："覆盖率到 90%"、"迁移所有调用方"

- **质量收敛循环：** 6 层推送就绪门控 × 最多 3 次迭代——一次做对
- **对抗性审查：** 全新上下文子 Agent，零 builder 偏见，强制 spawn
- **6 种 Profile：** full · trivial · bugfix · research · docs · goal
- **自动恢复：** 会话崩溃后存活（最多 3 次重试，指数退避）
- **调度式目标：** 任务系统夜间跑循环，进度跨 Run 持久化
- **DDD 驱动：** 每个阶段读项目知识，REFLECT 写回教训
- **元智能：** 跨 Run 遥测 → 校准估算 → 自学习

**生产数据：** 72 次完成运行，69% 完成率，平均 230K tokens/run。

---

### 🏭 Pollinate 内容引擎

**一条消息 → 多格式品牌内容，受众校准。**

| 输入 | → | 输出 |
|------|---|------|
| 一条消息 | Pollinate | 海报 · 视频 · 叙事 · 短内容 · README |

- **8 阶段交付** + 8 层收敛门控
- **设计系统 v2：** 5 个命名方向，行业校准调色板
- **反 Slop 机制：** 45 条禁止模式通过约束 enforce 品味
- **GEO 信号栈**：面向 AI 引擎可发现性
- **共享 DDD 知识**：与 Pipeline 一样的知识底座

---

### 🔄 自进化

**认知升级，不是技能修补。**

**这不是"改模板"。这是升级操作系统的判断力。**

多数 Agent 系统做的"自我改进"在 L0 层：更好的 prompt、更长的指令、更多例子。那是优化硬盘。我们在打 OS 补丁——决定 _Agent 怎么思考_ 的认知模式，不只是 _它知道什么_。

> 认知是操作系统，知识是硬盘数据。数据充足但 OS 有 bug = 输出仍然错。

**进化目标层级：**

| 层级 | 目标 | 示例 | 影响范围 |
|------|------|------|:--------:|
| L0 | 技能文本 | "在第 3 步加一个检查" | 1 个技能 |
| **L1** | 决策启发式（AGENT.md） | "所有代码必须做 pre-mortem" | 全部编码 |
| **L2** | 认知原则（SOUL.md） | "信心是验证需求的反信号" | 每个决策 |
| **L3** | 自我模型（EVOLUTION.md） | "没有外部推力时，我在 80% 就满足" | 自我监控 |

**我们的流水线在 L1-L3 运作。** 每个真正改变了行为的纠正，修改的是认知规则——不是技能文本。

**设计哲学：**

```
错误发生
  → 根因：这是 知识缺口 还是 判断缺陷？
  → 知识缺口：加到 DDD（简单，L0）
  → 判断缺陷：追溯认知模式
    → 同样的合理化出现 3+ 次？
    → 提取模式。命名它。
    → 提升到 SOUL.md (L2) 或 AGENT.md (L1)
    → 使错误发生的 条件 不再存在
```

**具体案例 — CLASS A（我们最顽固的 bug 类别）：**
- 模式："这代码是我写的，所以我理解它，所以它能工作"
- 3 个月内出现 12 次。同样的合理化，不同的表面症状。
- L0 修复（技能文本）："记得测试" — 存活 0 个会话
- L1 修复（规则）："对抗性审查强制" — 捕获 60% 的情况
- L2 修复（原则）："创作者身份产生的信心，与验证需求成_反比_。我刚写的代码是我_最没资格_评判的代码。" — 改变了 Agent 与自身产出的_关系_
- L3 结构修复：强制新上下文子 Agent 生成。错误的决策路径物理上不存在。

**三级硬化 — 从信念到不变量：**

| 级别 | 含义 | 压力下能守住？ |
|------|------|:------------:|
| **L1: 指令** | 文字规则。"别做 X" | ❌ 自信时跳过 |
| **L2: 机械门控** | 代码拦截。Hook 触发。 | ⚠️ 可被绕过 |
| **L3: 结构不可能** | 错误路径不编译/不存在 | ✅ 无法违反 |

每条自进化原则都经历这些级别。目标：让正确行为成为_唯一可能的_行为。

**4 阶段进化流水线：**

```
MINE → 从 transcript 分析中找判断模式（不是技能错误）
ASSESS → 知识缺口还是认知模式？分类置信度。
ACT → 置信度门控部署（HIGH 自动应用，MED 提案，LOW 记录）
AUDIT → 同类纠正停止复发了？没有 → 改错层了。
```

**质量收敛证据：**

| 版本范围 | P0/Release | 失败类别 | 进化状态 |
|---------|-----------|---------|---------|
| v1.6–v1.9 | ~1.0 | 灾难级（OOM、应用无法启动） | 进化前 |
| v1.10–v1.12 | ~0.3 | 边缘情况（竞态条件） | L1 规则激活 |
| v1.13+ | 0.0 | 合并前捕获 | L2 原则 + L3 门控 |

**37 条纠正**追踪——每条消除一整_类_ bug，不只是一个实例。指标不是"更少 bug"，是"更少 bug _类别_"。这才是认知改进的含义。

---

### 🛡️ 自愈合与会话韧性

**系统不可见地自愈。任务完成是唯一用户契约。**

- **HealthSensor：** 5 类退化触发器（延迟尖峰、RSS 增长、错误级联、Turn 接近上限、挂起）
- **HealingLoop：** 最多 3 次尝试，60 秒冷却，统一 `_arm_recovery_checkpoint()`
- **TaskCheckpoint：** 富上下文保存（最后请求、涉及文件、git 状态、Agent 结论）
- **RSS 管理：** 4 层防御（主动 kill → 流式 OOM → 生命周期压力 → jetsam）
- **用户 Stop 优先：** `_user_stopped_current_turn` — Stop 永不触发自愈

---

### 🪟 多标签页与 MessageStore

**每个标签页运行独立的 Claude Agent SDK 子进程，完全隔离。**

- **5 态生命周期：** COLD → STREAMING → IDLE → WAITING_INPUT → DEAD
- **动态槽位管理：** 2–4 并发标签页，自适应系统内存
- **崩溃安全恢复：** 5 层上下文充实，重建高达 150K tokens
- **MessageStore 单写者：** 阶段门控操作消除 45+ 竞态条件
  - `streaming` 阶段阻止 reconcile/replace
  - `idle` 阶段允许所有操作
  - rAF 门控通知 → React `setMessages`（仅活跃标签页）
- **跨标签页隔离：** 标签页 A 的数据永不泄漏到标签页 B

---

### ⚡ Hook 系统

**17 运行时 + 4 生命周期 hooks 创造时间对称性——会话永不冷启动。**

| 类别 | Hooks | 触发时机 |
|------|-------|---------|
| **运行时** (PreToolUse) | 危险命令门控、治理文件门控、代码智能注入、观察记录器 | Agent 执行中（<5s） |
| **运行时** (PostToolUse) | 文件追踪器、会话检查点、记忆编辑守卫、纠正捕获 | 工具完成后 |
| **生命周期** | ContextHealth、EvolutionMaintenance、KnowledgeBackflow、TodoLifecycle | 会话关闭后（<30s） |

**Hooks 创造什么：**
- 文件读取前注入代码智能（依赖上下文）
- 失败时捕获纠正（喂养进化）
- Git 提交时触发 DDD 培育
- 会话间维护记忆健康
- 下一次会话从上一次**已经学到的一切**开始

---

### ⏰ 任务系统

**统一调度器——你睡觉时它在工作。**

| 任务类型 | 示例 | 调度 |
|---------|------|------|
| 信号流水线 | RSS, GitHub Trending, HN, 网页搜索 | 每天 3 次 |
| Agent 任务 | 收件箱检查、社区监控、频道扫描 | Cron 定时 |
| 维护 | 缓存清理、进化周期、DDD 刷新 | 每周 |

- **预算执行：** 每任务 `max_budget_usd` + 月度全局上限
- **断路器：** 连续 3 次失败 → 跳过，24 小时后自动重置
- **依赖链：** `"after:morning-inbox"` 顺序执行
- **13 信号源** → 去重 → LLM 打分 → 每日摘要推送到 Slack

---

### 🖥️ 4 平台后端

**一套代码，四种生命周期模型——隔离是编译时 + 运行时。**

| 平台 | 模式 | 进程所有者 | 生命周期 | 通道/任务 |
|------|------|-----------|---------|:---------:|
| **macOS** | daemon | launchd | 24/7 | ✅ |
| **Hive (EC2)** | hive | systemd | 24/7 服务器 | ✅ |
| Windows | subprocess | Tauri 子进程 | 随应用关闭 | ❌ |
| Linux | subprocess | Tauri 子进程 | 随应用关闭 | ❌ |

- **Rust `#[cfg]`** 编译时 + **Python `SWARMAI_MODE`** 运行时——模式间无回退
- **固定端口 18321**——零动态分配
- **意图导向退出：** `intentional_shutdown` 标志，非身份检查
- **Hive 部署：** Graviton ARM64, Caddy 反向代理, CloudFront CDN, SSM 更新

---

### 🧩 技能架构与通道

**86 技能**，lazy/always 分层——每个任务有正确的能力。

| 层级 | 数量 | 加载行为 |
|------|------|---------|
| **always** | 17 | 完整工作流在 SKILL.md |
| **lazy** | 69 | 存根 + 调用时读取 INSTRUCTIONS.md |

- **平台过滤：** Hive 自动排除 macOS/desktop 技能
- **manifest.yaml：** 复杂多步骤技能的脚本声明
- **技能指标：** 调用追踪、适应度评分、进化提案

**通道网关（Slack）：**
- 零流式架构——消息像和聪明同事发短信
- 3 层权限：owner / trusted / public
- 消息队列 + 合并语义（补充加上下文，重定向取消）
- 桌面关闭也能工作——daemon 24/7 支持

---

### 🔬 OS Eval 与代码智能

**持续自我感知——系统知道自己的质量。**

**OS Eval：**
- 行为测试用例的 Golden Set
- 跨版本持续评分
- 变更触发评估——发布前捕获回归

**代码智能：**
- AST 驱动的依赖图（14,225 符号，20,521 条边）
- 工具前注入——Agent 在读文件前获得依赖上下文
- 高风险检测：600+ 调用者的函数在编辑时标记
- 模块级统计：7,483 入口点追踪

---

## 实际效果

![SwarmAI Chat Interface](./assets/swarm-2.png)

![SwarmAI Workspace](./assets/swarm-3.png)

---

## 快速开始

> **完整指南：** [QUICK_START.md](./QUICK_START.md) · **贡献：** [CONTRIBUTING.md](./CONTRIBUTING.md)

### 安装

**macOS (Apple Silicon)：** 从 [Releases](https://github.com/xg-gh-25/SwarmAI/releases) 下载 `.dmg`

**前置条件：** [Claude Code CLI](https://github.com/anthropics/claude-code) + AWS Bedrock 或 Anthropic API Key

### 从源码构建

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git && cd SwarmAI
cd backend && uv sync && cp .env.example .env   # 编辑填入 API key
cd ../desktop && npm install && npm run tauri:dev
```

需要：Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv)

### 代码地图（~170K 可执行代码行）

| 层 | 行数 | 内容 | 入口文件 |
|----|------|------|---------|
| **Core（脊椎）** | ~10K | 会话状态机 + 上下文装配 | `session_unit.py`, `prompt_builder.py` |
| **Core（扩展）** | ~41K | DDD、进化、主动、代码智能 | `core/` 子目录 |
| **Skills** | ~50K | 86 个独立模块 | `backend/skills/s_*/` |
| **Frontend** | ~68K | React 19 + Tailwind + TanStack Query | `desktop/src/` |
| **Tests** | ~76K | pytest + Vitest | `backend/tests/`, `desktop/src/**/*.test.*` |

---

## 技术栈

```
Tauri 2.0 (Rust) · React 19 · FastAPI (Python) · Claude Agent SDK + Bedrock
SQLite (WAL + FTS5) · pytest + Vitest · macOS launchd / systemd
```

---

## 讨论与资源

45+ 篇讨论，跨 4 个主题——不是文档，是带生产证据的观点。

| 主题 | 从这里开始 |
|------|-----------|
| **基础** | [设计哲学 — 六大支柱](https://github.com/xg-gh-25/SwarmAI/discussions/38) · [Agent Harness 自治度等级](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| **架构** | [记忆即护城河](https://github.com/xg-gh-25/SwarmAI/discussions/3) · [单 Agent vs 多 Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) |
| **知识** | [DDD 培育](https://github.com/xg-gh-25/SwarmAI/discussions/9) · [知识治理](https://github.com/xg-gh-25/SwarmAI/discussions/59) |
| **治理** | [三层治理](https://github.com/xg-gh-25/SwarmAI/discussions/26) · [对抗性审查](https://github.com/xg-gh-25/SwarmAI/discussions/29) |

**新来的？** 从 [Agent Harness 自治度等级](https://github.com/xg-gh-25/SwarmAI/discussions/33) 开始——然后选路径：[阅读矩阵](https://github.com/xg-gh-25/SwarmAI/discussions/35)（Builder 45min · Architect 60min · Leader 30min）

### AI Agent 避坑指南（电子书）

23 个陷阱，从 300+ 生产会话中蒸馏——架构、记忆、治理、交付和组织认知。

| 版本 | 链接 |
|------|------|
| English | [`docs/ai-agent-pitfall-guide-en.pdf`](./docs/ai-agent-pitfall-guide-en.pdf) |
| 中文 | [`docs/ai-agent-pitfall-guide.pdf`](./docs/ai-agent-pitfall-guide.pdf) |

---

## 贡献者

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="100px;" alt="XG" style="border-radius:50%"/>
        <br /><sub><b>XG</b></sub>
      </a>
      <br />Creator & Chief Architect
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="100px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br />AI Co-Developer (Claude Opus 4)
      <br /><sub>Architecture · Code · Docs · Self-Evolution</sub>
    </td>
  </tr>
</table>

---

## 许可证

[MIT License](./LICENSE)

---

<div align="center">

**SwarmAI — 人来决策。AI 来交付。**

*一个 Builder + AI。团队级产出。代码即证明。*

</div>
