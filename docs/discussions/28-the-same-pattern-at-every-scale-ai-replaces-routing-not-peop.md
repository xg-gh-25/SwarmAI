# The Same Pattern at Every Scale: AI Replaces Routing, Not People

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/28) | Category: Ideas | Published: 2026-05-20

---


Block (formerly Square) just published a piece called ["From Hierarchy to Intelligence"](https://block.xyz/inside/from-hierarchy-to-intelligence) — co-authored by Jack Dorsey and Sequoia's Roelof Botha. It's one of the sharpest framings I've seen on AI and organizational design.

Their core insight: **hierarchy is not a management philosophy. It's an information routing protocol.**

The Roman Army invented it — contubernium (8 soldiers) → century → cohort → legion — to solve a hard constraint: one leader can effectively manage 3 to 8 people. For two thousand years, every organization on earth has been built on this same constraint. The Prussian General Staff (1806) was the first "middle management" — officers whose job was literally routing information. American railroads brought military hierarchy into business in the 1850s. Taylor optimized within it. McKinsey formalized it.

Every attempt to escape — Spotify's squads, Zappos's Holacracy, Valve's flat structure, Haier's rendanheyi — eventually reverted. Not because hierarchy is good, but because **no alternative routing mechanism was powerful enough to replace it.**

Until now.

### The Pattern

Block's argument is that AI can maintain a continuously updated model of an entire business (a "world model"), and use it to coordinate work that previously required humans relaying information through management layers. They're building four things:

1. **Capabilities** — atomic primitives (payments, lending, banking) with no UI
2. **A World Model** — continuous self-understanding of the company + deep customer model
3. **An Intelligence Layer** — composes capabilities into solutions for specific customers at specific moments ("No product manager decided to build either solution")
4. **Interfaces** — delivery surfaces (Square, Cash App), but "not where the value is created"

Their organizational structure inverts: *"In a conventional company, the intelligence is spread throughout the people and the hierarchy routes it. In this model, the intelligence lives in the system. The people are on the edge."*

### The Same Pattern at Personal Scale

Here's what struck me: **this is the exact same pattern that plays out at individual scale.**

Your brain also runs a routing protocol. When you context-switch between projects, remember where you left off, decide what to work on next, track dependencies — that's your wetware doing middle management. "I need to recall what I was doing yesterday" is your brain being a General Staff officer.

Most AI assistants today are Phase 1 — a better tool on your desk. They don't replace the routing. You still do the context management, the priority judgment, the "what should I work on and why."

At SwarmAI, we've been building toward the same structural shift Block describes, but at the personal level:

| Block (Company Scale) | SwarmAI (Personal Scale) | The Pattern |
|---|---|---|
| Company World Model | Memory + DDD + DailyActivity | Continuous self-model that accumulates |
| Intelligence Layer | Autonomous Pipeline | Compose capabilities without human routing each step |
| Capabilities (atomic, no UI) | Skills (68 execution units, no UI) | Building blocks, not products |
| "Failure signal = roadmap" | Evolution Pipeline (detect gaps → self-improve) | System that knows what it can't do |
| "People on the edge" | "Human directs, AI delivers" | Human = judgment + taste, not routing |

The structural parallel is not metaphorical — it's the same design pattern at different scales.

### Three Phases of Routing Replacement

We use a framework called AIDLC (AI-Driven Development Lifecycle) that maps how this transition happens in practice:

**Phase 1: AI as Tool** — You use AI for individual tasks. The routing (what to do, in what order, with what context) is still entirely human. Block would call this "AI on everyone's desk but the SOP topology unchanged."

**Phase 2: AI as Spec-Driven Executor** — You define intent through structured specifications. AI handles execution chains without human routing at each step. The "world model" starts forming — AI knows what you're working on, what changed, what matters.

**Phase 3: AI as Autonomous Intelligence** — AI maintains the world model, composes capabilities, detects gaps, self-improves. Humans set direction and make judgment calls the system shouldn't make alone. Block: "No PM decided to build either solution." AIDLC: "Coding as black box."

Most AI products are permanently stuck at Phase 1. They make individual tasks faster but never replace the routing overhead between tasks.

### The Diagnostic Question

Block poses a question every company should answer:

> "What does your company understand that is genuinely hard to understand, and is that understanding getting deeper every day?"

If the answer is nothing, AI is cost optimization. If the answer is deep, AI reveals what your company actually is.

The personal version: **Does your AI remember, learn, and compound — or do you start from zero every session?**

If your AI assistant has no memory, no world model, no self-improvement loop — it's a better hammer, not a better routing protocol. You're still the middle manager of your own workflow.

### What This Means for Builders

If you're building AI tools, the question isn't "how do I make task X faster?" It's: **"Am I replacing routing, or just adding tools?"**

The signal of real routing replacement:
- System maintains context without the human re-establishing it
- System can compose multi-step solutions the human didn't explicitly specify
- System improves its own model of the world through use
- Failure to solve a problem generates the signal for what to build next
- Human value shifts from execution and coordination to judgment and direction

Block's closing line applies at every scale:

> "The question was never whether you needed layers. The question was whether humans were the only option for what those layers do. They aren't anymore."

At company scale, Block is answering this. At personal scale, that's what we're building.

<details>
<summary><strong>中文版 (Chinese Translation)</strong></summary>

# 同一个 Pattern，不同的 Scale：AI 取代的是路由，不是人

Block（前身 Square）刚发了一篇文章叫 ["From Hierarchy to Intelligence"](https://block.xyz/inside/from-hierarchy-to-intelligence)——Jack Dorsey 和 Sequoia 的 Roelof Botha 联合署名。这是我见过的关于 AI 与组织设计最精准的 framing。

核心洞察：**层级制不是一种管理哲学，它是一个信息路由协议。**

罗马军团发明了它——contubernium（8人）→ century → cohort → legion——为了解决一个硬约束：一个领导者只能有效管理 3 到 8 个人。两千年来，地球上每个组织都建立在这同一个约束之上。普鲁士参谋制（1806）是第一批"中层管理"——专门负责路由信息的军官。美国铁路在 1850 年代把军事层级带入商业。Taylor 在其中做了优化。McKinsey 将其形式化。

所有逃脱尝试——Spotify 的 Squads、Zappos 的 Holacracy、Valve 的扁平结构、Haier 的人单合一——最终都回退了。不是因为层级好，而是**没有替代的路由机制强大到足以取代它**。

直到现在。

### 这个 Pattern

Block 的论点是：AI 可以维护一个持续更新的完整业务模型（"世界模型"），用它来协调之前需要人类通过管理层传递信息才能完成的工作。他们在建四样东西：

1. **Capabilities**——原子能力（支付、借贷、银行），没有 UI
2. **World Model**——公司的持续自我认知 + 深度客户模型
3. **Intelligence Layer**——在特定时刻为特定客户组合能力（"没有产品经理决定要建这两个方案"）
4. **Interfaces**——交付界面（Square, Cash App），但"价值不在这里产生"

组织结构倒置了：*"在传统公司中，智能分散在人脑中，层级负责路由。在这个模型中，智能在系统中，人在边缘。"*

### 个人 Scale 的同一 Pattern

让我震动的是：**同一个 pattern 在个人 scale 完全成立。**

你的大脑也在跑一个路由协议。当你在项目间切换上下文、记住昨天做到哪里、判断接下来该做什么、追踪依赖关系——这是你的脑子在当中层管理。"我需要想起昨天做到哪了"就是你的大脑在当参谋官。

今天大多数 AI 助手停在 Phase 1——桌上一把更好的锤子。它们不替换路由。你仍然在做上下文管理、优先级判断、"该做什么以及为什么"。

在 SwarmAI，我们一直在建造 Block 描述的同一结构转变，但在个人层面：

| Block（公司 Scale） | SwarmAI（个人 Scale） | Pattern |
|---|---|---|
| Company World Model | Memory + DDD + DailyActivity | 持续积累的自我模型 |
| Intelligence Layer | Autonomous Pipeline | 无需人类逐步路由即可组合能力 |
| Capabilities（原子，无 UI） | Skills（68个执行单元，无 UI） | 积木，不是产品 |
| "Failure signal = roadmap" | Evolution Pipeline（检测缺口→自我进化） | 知道自己不能做什么的系统 |
| "人在边缘" | "Human directs, AI delivers" | 人 = 判断 + 品味，不是路由 |

这个结构平行不是比喻——是不同 scale 上的同一设计模式。

### 路由替换的三个阶段

我们用一个叫 AIDLC 的框架来描述这个转变如何在实践中发生：

**Phase 1：AI 作为工具**——你用 AI 做单个任务。路由（做什么、什么顺序、带什么上下文）完全是人。Block 的说法："AI 在每人桌上但 SOP 拓扑没变。"

**Phase 2：AI 作为 Spec 驱动的执行者**——你通过结构化 spec 定义意图，AI 处理执行链无需人在每步路由。"世界模型"开始形成——AI 知道你在做什么、什么变了、什么重要。

**Phase 3：AI 作为自主智能**——AI 维护世界模型、组合能力、检测缺口、自我进化。人设定方向并做系统不应独自做的判断。Block："没有 PM 决定建这些方案。" AIDLC："Coding as black box。"

大多数 AI 产品永久停在 Phase 1。它们让单个任务更快，但从不替换任务之间的路由开销。

### 诊断问题

Block 提出了一个每家公司都应该回答的问题：

> "你的公司理解了什么真正难以理解的东西？这个理解每天都在加深吗？"

如果答案是"没有"，AI 就是降本增效。如果答案是"深"，AI 揭示了你公司的真正本质。

个人版本：**你的 AI 有记忆、会学习、能复利——还是每次 session 从零开始？**

如果你的 AI 助手没有记忆、没有世界模型、没有自我进化循环——它是把更好的锤子，不是更好的路由协议。你仍然是你自己工作流的中层管理。

### 对 Builder 们意味着什么

如果你在建 AI 工具，问题不是"怎么让任务 X 更快？"而是：**"我在替换路由，还是只在加工具？"**

真正路由替换的信号：
- 系统维护上下文，无需人类重新建立
- 系统能组合人类未显式指定的多步方案
- 系统通过使用改进自己的世界模型
- 无法解决问题会生成"该建什么"的信号
- 人的价值从执行和协调转向判断和方向

Block 的结尾在每个 scale 都适用：

> "问题从来不是你是否需要层。问题是人类是否是那些层能做的事情的唯一选项。不是了。"

在公司 scale，Block 在回答这个问题。在个人 scale，这就是我们在建的东西。

</details>

