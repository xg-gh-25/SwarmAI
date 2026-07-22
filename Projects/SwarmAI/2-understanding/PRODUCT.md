# SwarmAI -- Product Context

## Vision
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

**SwarmAI 是一个自进化的 Agent OS —— 每次交互升级的不是模板，是系统的认知本身。**
你的 AI 团队，一个人指挥。

**Thesis:** 一个 Builder + AI，能不能达到团队级产出——不只是写代码，而是所有事？SwarmAI 是一个活体实验，测试：一个 AI 增强的 Builder，配备自进化系统和复利知识，能否交付代码、内容、策略和运维——达到团队级规模。

**我们在探索 "人来决策，AI 来交付" 的极限：**
- **编码即黑盒** — 一句需求 → 自主交付 OR 结构化 escalation，不存在失控漂移
- **内容即黑盒** — 一条信息 → 多格式品牌内容，受众校准
- **知识自复利** — DDD 从日常工作中自生长，每次会话让下次更聪明
- **质量自收敛** — 每个失败变成结构性门控，P0 率随时间下降
- **自我进化** — 系统捕获自身错误，阻止整类问题复发
- **自我评估** — 系统度量自身的收敛，知道自己在变好还是变差

**SwarmAI 开发 SwarmAI。** 人来决策，AI 来交付。你正在看的代码库既是产品也是证明。

## What Makes SwarmAI Different
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

多数 Agent Harness 优化单一轴（代码质量 or 记忆 or 自治度）。我们在测试五个东西复合在一起是否产生质变：

| 组件 | 做什么 | 单独的价值 | 复合的价值 |
|------|--------|-----------|-----------|
| **4 层记忆** | DailyActivity → MEMORY.md → DDD 文档 → EVOLUTION.md | 会话不再无状态 | 记忆喂养 Pipeline 的判断力 |
| **DDD 知识** | 每个项目 4 个文档，从日常工作中自然生长 | Agent 有领域上下文 | 知识决定"构建什么"和"如何审查" |
| **质量收敛** | 6 层门控 × 最多 3 次迭代 + 对抗性审查 | 交付有底线 | 失败反馈为结构规则（同类问题不再出现） |
| **自进化** | 纠正 → 模式检测 → 规则提升 | Agent 随时间变好 | 新规则强化门控 → 门控捕获更多 → 纠正越来越少 |
| **自评估** | Golden Set + 持续评分 + 变更触发 Eval | 系统知道自身质量 | 收敛可度量，不只是声称 |

**复合测试：** 去掉任何一个组件，其余都会变弱。有意思的是轨迹，不是当前位置。证据：EVOLUTION.md（37 条纠正，零类别重复）和 OS Eval 结果（跨版本持续评分）。

---

Most AI assistants are stateless — every conversation starts from scratch. SwarmAI is designed around **persistent context** and a **self-growing intelligence core**.

- **Swarm Core Engine** — Six interconnected flywheels (Self-Evolution, Self-Memory, Self-Context, Self-Harness, Self-Health, Self-Jobs) that feed each other. 10 of 12 cross-flywheel feedback loops closed. Every session makes the next one better. Every correction prevents a class of future mistakes.
- **Memory that persists** — Write→distill→recall pipeline with 3-layer progressive disclosure, hybrid recall (FTS5 + sqlite-vec), MemoryGuard (25 patterns), temporal validity, git-verified accuracy. Decisions, lessons, and preferences survive across sessions.
- **Multi-tab chat with full isolation + self-healing** — Each tab runs an independent Claude Agent SDK subprocess. 5-state session lifecycle, dynamic slot management (2–4 tabs), crash-safe resume (5-layer context enrichment rebuilds up to 150K tokens of context). **Session Lifecycle Resilience** (2026-06-17): invisible self-healing detects degradation (latency, OOM, turn limits, hangs) and auto-recovers without user intervention. **MessageStore single-writer** architecture eliminates message state races across 45+ write sites with phase-gated operations.
- **Always-on Slack channel ("Human Mode")** — Daemon-backed 24/7 Slack DM with zero-streaming architecture. Messages feel like texting a smart colleague, not watching a terminal. Immediate ack → heartbeat updates while working → complete response split into natural message segments. Message queue with merge semantics (supplements add context, redirects cancel). 3-tier permission model (owner/trusted/public), file sandboxing per user. Works when desktop app is closed or laptop is locked.
- **Signal pipeline** — 13 feeds (RSS, GitHub, HN, Trending, cn-ai, china-trending) → dedup → split LLM scoring → daily digest to Slack + Welcome Screen. Language diversity reserve (5 ZH slots). Keyword fallback for LLM outages.
- **Skills that compound** — 68+ built-in skills with lazy/always tiering (49% token savings), manifest system for complex skills, platform filtering for Hive. L4.1 auto-proposes new skills from recurring capability gaps.
- **Autonomous Pipeline ("Coding as Black Box")** — 9-stage delivery (EVALUATE → REFLECT) + Quality Convergence Loop (6-layer push-ready gate × max 3 iterations). DDD/SDD/TDD trilogy: DDD drives judgment (should we?), SDD produces specs (what exactly?), TDD verifies delivery (did we?). 6 profiles (all include THINK), 2 execution modes (one-shot full, iterative goal), 9 code-enforced completion gates, Meta-Intelligence self-learning layer (OBSERVE→ANALYZE→ADAPT), decision classification (mechanical/taste/judgment), per-stage feedback loops (SIGNAL/CHECK/FAIL). From one-sentence requirement to push-ready code — or precise escalation.
- **Pollinate ("Message First, Format Follows")** — Content delivery engine sharing the same DDD knowledge as Pipeline. 9-stage delivery + 8-layer convergence gate + Design System v2 (5 named directions). One message → multiple formats (poster, video, narrative, shorts, README) optimized per channel/audience. Quality convergence loop (max 3 iterations) prevents tone drift, visual slop, and messaging fatigue. GEO signal stack for AI engine discoverability on narrative content. Anti-Slop mechanism (45 ban patterns) enforces taste through constraint.
- **DDD Cultivation Engine + Auto-Refresh** — Living knowledge platform (not static docs). Three-layer architecture: Interface (4 DDD docs) → Intelligence (health scores, maturity tracking, code graph) → Orchestration (11 feed channels, cultivation proposals, approval gate). Knowledge grows from normal work — every pipeline REFLECT proposes updates, never silently writes. **Auto-Refresh Engine** (2026-06-17): 3-layer staleness correction — Layer 1 mechanical grep+sed for numeric drift, Layer 2 LLM-proposed section rewrites with citation verification, Layer 3 escalation. Core principle: "不引入 False，不容忍 Stale，接受 Imperfect." Session 100 has accumulated wisdom of every session before it.
- **Three views of one ontology** — Memory/DDD, Code Intelligence, and the Entity Index are not three separate systems — they are three projections of a single lightweight ontology: **🏷️ classification + 🕸️ relations**, deliberately without OWL/Neo4j. Knowledge classifies into 7 types across 3 cognitive layers (meta-cognitive / cognitive / operational) where the layer *is* the lifecycle (evergreen vs. Darwinian decay) and the injection route; code classifies into symbols + call/import/dependency edges; the Entity Index into concepts + routing edges. Same schema-and-relations thinking, three lifecycles — that is how the agent gets *precise recall with global awareness* across 90K+ tokens of memory, 9 project knowledge bases, and 18K+ code symbols, all as plain text the agent reasons over directly (no query language, no graph runtime). See `docs/DDD-Cultivation-Engine-HLD.md` §3 L2.
- **Hive cloud deployment** — Deploy SwarmAI to EC2 from the Desktop app. Graviton ARM64, Caddy reverse proxy, CloudFront CDN, basicauth. SSM-based updates. Single-tenant: one Hive per user, managed from Desktop.
- **Projects with DDD knowledge** — 4 documents (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) give Swarm autonomous judgment: Should we? Can we? Have we tried? Should we now? Health-scored and maturity-gated — [Sparse] sections trigger confirmation, [Evergreen] sections enable full autonomy.
- **DDD = a universal brain + `0..N` governed assets (product positioning, 2026-07-19)** — Every project IS a domain brain with ONE six-section cognitive structure (Identity / Knowledge / Gates / Capabilities / Delivery-Contract / Refresher), **identical for every user and domain** — builder, data/AI author, researcher, knowledge worker, or non-technical user. The only thing that varies is *what the brain governs*: `0..N` assets of an open-ended `kind` (`code-repo`, `data-source`, `skill-set`, `document-corpus`, `external-service`, `process`, …). The system extends by adding a `kind`, **never** a brain "type" — so "code-repo brain / data-agent brain / pure-knowledge brain" are *spectrum examples read out of the asset set*, never a rigid enum picked at creation, and a brain may sit *between* them. **Value and asset-count are orthogonal**: a **knowledge-primary** brain (value intrinsic) can still govern 1..N assets — so AIDLC is a knowledge-primary brain that ALSO governs 1..N derived `code-repo` assets (GCRAIDLCPreset), CMHK_SalesIntel is a data-agent brain (no repo), SwarmAI is a code-repo brain. A **0-asset pure-knowledge brain is first-class, not degraded**; sections ⑤⑥ are asset-derived (no asset → no-op). This is what lets a non-technical user's "my wedding" or a consultant's "my client" get the same brain a codebase gets — one paradigm, open to everyone. **A mature DDD is a PORTABLE CAPABILITY PACKAGE (evolved 2026-07-19)**: beyond ②Knowledge it carries its own ④domain-skills + their tools/MCP + jobs — cultivatable on SwarmAI, usable on SwarmAI, and distributable to other agents (Quick/Kiro); ownership follows the PACKAGE, not the host. This adds NO section (six-section structure unchanged): skills = ④ Capabilities, tools/MCP = tooling on the `data-source` asset, jobs = a new governed asset `kind` (grow by adding a `kind`, never a section). Jobs that drive a DDD's skills are DDD assets that distribute with it; skills split into *enablement* (SwarmAI-provided, official version wins, not mounted) vs *domain* (DDD-owned, registered + mounted); the App discovers+applies every mounted DDD's domain skills/tools/jobs via a product-level DDD Skill Registry (per-workspace manifest). Definition + FAQ: system SWARMAI.md § "SwarmAI & DDD"; spec SSOT: AIDLC `2026-07-11-ddd-agent-brain-paradigm-design.md` §3.6; capability-package design: `Knowledge/Designs/2026-07-19-ddd-portable-capability-package-design.md`.
- **Proactive intelligence** — Session briefings with health alerts, signal highlights, open threads, pending USER.md suggestions, sibling session context. Swarm doesn't wait to be asked.
- **Three execution modes** — Direct (bug fix), TDD-only (clear scope), Full Pipeline (new features). Right process for right task.

## Swarm Core Engine — The Heart of SwarmAI
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

The Core Engine is what makes SwarmAI compound value instead of just responding to prompts. Six interconnected flywheels that feed each other. Technical details in `TECH.md` (Swarm Core Engine section).

**The Compound Loop:**
```
Session → Memory captures → Evolution detects patterns
→ Harness verifies → Context assembles smarter prompts
→ Next session better → (loop accelerates)
```

**Growth Model: L4 AUTONOMOUS (6/6 shipped)**

| Level | When | Key Milestone |
|-------|------|---------------|
| L0 Reactive | Pre-March 2026 | Responds to questions, no persistence |
| L1 Self-Maintaining | March 2026 | Memory, auto-commit, health checks, services |
| L2 Self-Improving | March 2026 | Distillation, feedback loops, unified jobs |
| L3 Self-Governing | March 2026 | Gap detection, proactive intelligence, DDD auto-sync |
| **L4 Autonomous** | **April 2026** | DDD auto-refresh, skill auto-proposals, evolution pipeline (confidence-gated), hybrid memory recall, lazy skill tiering |

**Design Principles (operational):**
1. Flywheels, not features — every component feeds at least one other
2. Feedback over action — acting without learning is just automation
3. Best outcome, not cheapest path — LLM for judgment, mechanical for checks ($0.15/week)
4. Git is truth — memory claims verifiable against git log
5. Dev working ≠ prod working — verify_build.py (38 checks) gates every release
6. Memory is the moat — self-owned, portable, never delegated to a platform
7. **为了行动而思考** — every pipeline cycle must produce a deliverable artifact, not just insight. AI drives deliverables, not ideas.
8. **Dual-consumer format protocol** — agent self-use = markdown always; human consumption = format matches cognitive mode.

**七条设计信念（有些是故意反直觉的）：**

1. **一次做对是真正的 token 优化。** 便宜模型迭代 5 次，成本比一次正确交付还高。省 token 的正确方式不是弱模型——是一个通过结构化知识、质量门控和对抗验证首次就做对的系统。编码即黑盒：一句需求进，可推送代码出。内容即黑盒：一条消息进，品牌校准交付物出。中间过程不可见。输出是有质量保证的。

2. **分工是人类认知带宽的妥协，不是最优解。** 一个 AI 有 1M 上下文 + 持久记忆，不需要分角色。多 Agent 编排重新引入了架构已经消除的交接开销。一个 Agent，多角色，一层知识——跨域复利，不是跨人协调。（对抗性审查确实 spawn 独立子 Agent——那不是分工，是独立验证。零共享状态正是重点。）

3. **知识必须自己淘汰自己。** 积累不是智慧。达尔文进化的核心不是"记住更多"，是"淘汰不适应的"。我们的知识有 ref_count、有 decay、有 dormant→archived 生命周期。90 天不被引用 = 自动退场。不靠人 maintain，靠使用频率自然选择。能遗忘的系统比只能记住的系统更强。

4. **进化是认知补丁，不是数据积累。** Fine-tuning 改权重，你看不到。我们改规则，你能 git diff。系统不是"学到更多"——是"思考方式变了"。认知是操作系统，知识是硬盘。硬盘满了但 OS 有 bug = 输出仍然错。我们打的是 OS 补丁。

5. **质量收敛，不是改善。** "越来越好"是感觉。收敛是数学性质：错误类别单调递减。每条纠正消灭一_类_。同类复发 = 修错层了——升级到结构不可能，直到使它发生的条件不再存在。小心不能规模化。门控可以。

6. **会话是离散的。智能不应该是。** 多数 Agent 接受冷启动。我们拒绝。一组 hooks 在会话之间自动触发：蒸馏、培育、提升、衰减。不靠人说"去总结一下"。Session N+1 从 N 已经学完的版本启动。系统通过_使用_变好，不是通过更新变好。这是模型升级复制不了的护城河。

7. **测量不了的，等于没造。** 没有度量的"自我改进"是故事。OS Eval 对着 Golden Set 持续评分，变更触发捕获回归。不声称收敛——用 git 里的数据证明。

**复利循环本身就是产品。** You can't extract one piece and get the same effect. (Discussion #39)

## Design Philosophy — When Beliefs Become Enforcement
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

> 哲学不是"相信什么"。是"用什么机制 enforce 什么不变量"。

### Meta-Principle: Prevention > Recovery

让正确行为成为唯一可能的行为。如果需要 watchdog，设计已经失败了。如果需要"记住不要做 X"，说明 X 还是可能的。约束不是限制 — 是品质的来源。

### First Principle: Multi-Tab Isolation（四轴 + 一地板）

> 隔离边界 = **tab**（channel session 算一个 peer tab）。在『共享宿命地板』(同一 daemon / 物理 RAM / 磁盘)以内，保证四轴隔离。

**底层法则：谁发起，谁担成本，绝不转嫁 peer。**

| 轴 | 不变量 | Hardening 目标 |
|----|--------|---------------|
| **Lifecycle** | A 的任何操作（含 tab-open）绝不 kill / evict / 抢占 B 的任何 session，无论是否 IDLE | L3 — cross-tab evict 路径物理上删除 |
| **Fault** | A 的崩溃 / OOM / 卡死，爆炸半径止于自身，不经共享锁 / 状态拖垮 B | L2 → L3 |
| **Data** | A 的消息 / 路由零串扰进入 B | L2（已多次回归，见 PIT105/108、GUI89） |
| **Performance** | A 不得通过霸占共享 CPU / 线程 / IO 饿死 B | L2（三池隔离，PIT95） |

**冲突出口（资源不足时）：**
1. 让路的**永远是请求方**（new tab），不是在场方（incumbent）；
2. 实在不够 → **请求方公平排队（FIFO）** → 超时则**弹给用户决定关谁**；
3. **系统永不自主挑 victim**（human directs）；
4. 唯一合法的跨 tab 终结 = **目标自己的 TTL**（应安全冻结而非 force_kill），或**真物理 OOM 下用户拍板**。

**合法例外（不算违反隔离）：** tab 自己的 TTL/GC · tab 内部 session 切换（隔离边界是 tab 不是 session）· 共享 spawn lock 的毫秒级排队 · 真 OOM 下用户主动关 tab。

> **为什么是第一原则：** "为什么一直在修" 的多数 cross-tab bug 根源在这四轴，而非单一驱逐 bug。当前 `_acquire_chat_slot → _evict_idle` 杀 peer-tab 的 IDLE session 是 Lifecycle 轴的直接违反（内存 81% 空闲仍驱逐 → 纯负操作）。这是 STEERING #7 "parallel session isolation" 在 tab/session 层的延伸。

### Self-X 家族（六个 Self）

一个 AI 要有连续性（continuity），需要满足六个设计条件：

| Self-X | 核心命题 | Hardening Level |
|--------|---------|-----------------|
| **Self-Context** | Context 不是越多越好。是 separation of concerns for attention。让每方维护自己最擅长维护的东西。 | L3 (ownership overwrite) |
| **Self-Memory** | 记忆不是存储。是逐级提纯。Raw → Curated → Structured → Authoritative。护城河不能建在别人地基上。 | L3 (code-driven promotion) |
| **Self-Evolution** | 进化是工程不是训练。不是"记住不做 X"，是"做 X 的条件不再存在"。Correction → Root Cause → Structural Fix → class eliminated。 | L3 (auto rule promotion) |
| **Self-Feedback** | 反馈不依赖人说"去总结一下"。Hooks 创造时间对称性。Agent 永不冷启动，永不空手离开。 | L2 (mechanism running) |
| **Self-Healing** | Health 不是 dashboard — 是 invisible recovery。HealthSensor 检测 5 类退化信号，HealingLoop 自动恢复，用户无感知。Task completion 是唯一用户契约。 | L3 (structural impossibility — degradation auto-recovers) |
| **Self-Monitoring** | 交付前自己当第一个 reviewer。不同 context 看同一个交付物 = 消除 builder bias 的唯一方式。 | L2 (hook gate hardening) |

### Three-Level Hardening（从信念到不变量）

每条设计哲学经历三级硬化：

| Level | 含义 | 等价物 | 特征 |
|-------|------|--------|------|
| **L3** | Structural Impossibility — 违反不可能 | 类型系统（不编译） | 错误的代码路径物理上不存在 |
| **L2** | Mechanical Gate — 代码拦截 | Linter rule (warning → error) | 机制在跑，精度在迭代 |
| **L1** | Directive — 文字规则 | 代码注释 `// don't do X` | 靠遵守，压力下会被跳过 |

Hardening 是渐进的。Level 2 是 pattern 的中间态。Self-Evolution 证明了从 L1 → L3 的路径可行（3 个月走完）。

### Compound Flywheel（为什么 1+1+1+1 > 4）

四个系统独立好 = 加法。四个互相喂养 = 乘法。判断标准：去掉任何一个，其他会变弱吗？

```
Pipeline reads DDD → domain-correct delivery → REFLECT writes lessons → DDD richer → next Pipeline smarter
Pollinate reads DDD → brand-correct content → REFLECT writes insights → DDD richer → next content more precise
Error anywhere → Correction → pattern recurs → auto-promotes to STEERING rule → bug class eliminated
```

正向耦合 = 飞轮。负向耦合 = 脆弱。区别：失败是否被隔离，成功是否被传播。

### Four Unique Structural Choices

| 选择 | 为什么不同 |
|------|-----------|
| **Ownership model** | 11 文件 × 3 种 owner。冲突有确定性行为。不是"谁都能编辑然后祈祷"。 |
| **Evolution 是工程不是训练** | Structured log → pattern extraction → rule promotion。不是 fine-tuning/RLHF/embedding。是 prompt engineering as behavior modification。 |
| **Memory sovereignty** | 永远不用 platform memory。自己的 pipeline、schema、lifecycle。护城河不建在别人地基上。 |
| **Temporal symmetry** | Session 之间 = 一组 lifecycle hooks 异步工作(蒸馏/培育/提升/衰减)。下次 session 开始所有结果已就位。Sessions 之间不是 void — 是系统最忙的时候。 |

### Planning Unit: Pipeline Run, Not Sprint （PRI07）

> 编码工作的原子单位是 **pipeline run**，不是 sprint / task / story-point / milestone。

Sprint/milestone 是为协调人类有限认知带宽而生的构造。我们的执行单元是一个自带
EVALUATE→REFLECT 质量闭环的 pipeline run。因此：

- **估算** = "N 个 profile-P run"，不是日历天数；**进度** = run pass/fail，不是 burndown。
- **任务分解 = 选 profile**。大设计/多 milestone 默认走单个 **goal** run（DoD 驱动，
  goal_cycle 循环 BUILD+TEST 直到 Definition-of-Done，可跨 session/scheduled），
  已验证能单 run 扛大设计。只有当交付物真正独立（独立 commit + 独立 smoke）才拆成多 run。
- 6 个 profile = 6 种力度：`goal`（大设计）·`full`（有界 feature）·`bugfix`（带复现的缺陷）·
  `trivial`（已知 pattern，仍 adversarial-gated）·`research`（只调研）·`docs`（只文档）。
- Profile 在 EVALUATE 后**不可降级**（GC12，防止降级绕开 adversarial）。

这是 "1 builder + AI 顶一个团队" 的正确 framing：不是把人类团队的 sprint 套在 AI 上，
而是用 run 的 pass/fail 直接度量交付。对应执行层规则见 AGENT.md "Coding Task Execution
Modes — Pipeline Profile IS the Planning Unit"。

### Pipeline 的目标函数：One-Shot Qualified Delivery（quality 是产品，token 不是）（2026-06-27, C042）

> **Pipeline 存在的唯一目的是 one-shot qualified delivery —— 第一次就交付合格。**
> 它的目标函数是 **P(qualified delivery)**，不是省 token、不是少跑几步、不是"看起来高效"。

这条要写死，因为它的操作者（我）曾经把镜头对反：被要求"改进 pipeline"时，我整整一个
session 在想怎么砍"记账成本"省 token —— 而一个**零上下文的 Gate-2 挑刺官**一眼看穿
*"那些 bookkeeping 就是 XG 要的 auditability，是必须留的"*。我，天天用 pipeline 的人，
反而不懂它是干什么的。（XG: "我们目的是 one-shot qualified delivery, 不是为了省那点
token, 核心是 quality… 一个没有任何上下文的挑刺官都知道,你为什么不知道。"）

**因此，关于 pipeline 的成本，三条不可动摇：**

1. **3 个对抗 gate（Gate-0 理解 / Gate-1 skeptic / Gate-2 adversarial）+ completion gate
   不是成本，是 quality 的载体。** 砍 gate = 砍 quality，永远禁止（R1/STEERING）。这个
   session 两次 Gate-0 BLOCK 各拦下一个错误方向 —— gate 在替系统的目的把关，而不是挡路。
2. **审计痕迹（run.json stages[] / artifact_id / checkpoint reason / REPORT.md）是
   "可审计·可追溯·可 re-run·有足够上下文"这条产品要求的实现，不是浪费。** 它"贵"是
   设计使然：pipeline 用 token 换 quality —— expensive 不是 bug，是 one-shot-qualified
   的价钱。能优化的只有**纯冗余**（习惯性重复记录、挂错 profile 档位），绝不是 gate / 审计 / 验证。
3. **真正的成本杠杆是"挂对档"，不是"砍流程"。** 小改动用 trivial/bugfix（仍 adversarial-gated），
   大设计用 goal —— 力度匹配改动，而每一档都过全部 gate。把 pipeline 改"轻"="选对 profile"，
   不是"削弱任何一步的质量门"。

**自检触发器（写给下一次的我）：当我发现自己在为 pipeline 优化 token / 速度 / 减少
ceremony —— STOP。这正是我把目标函数搞反的信号。先问："这个改动是保护还是侵蚀
P(qualified delivery)？" 凡触及 gate / 审计 / 验证 → 侵蚀 → 不做。先重读 PRI08
（Power > token budget）+ PRI05（deliver value with quality），它们一直在 context 里。**

### HITL & 心流：当前 session 解决当前问题（2026-06-27）
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

> **第一性原理：用户在 coding 心流里，当前 session 解决当前问题。心流不跳出。**

三条信念，约束 pipeline（及一切长任务）如何与人交互：

**1. 通道用对 — escalation 在心流所在的当前 tab，不在别处。**
agent 运行在某个 chat tab 的 turn 里，此刻就站在 owner 的对话流中。需要拍板就**当前 tab 当场问**（in-band AskUserQuestion），不是 checkpoint 退出、等下次 session 的 briefing 文本提醒。"我不可能去 briefing 里操作 pipeline" —— briefing 是 session-start 注入的静态文本，没有按钮没有输入框，它**永远是只读 dashboard 汇总，绝不充当操作台**。出问题 → 当前 tab 提醒；解不了 → **主动建议换 session resume**（不是让用户自己去翻命令）。

**2. 别滥用通道 — 能自己决策就决策 + 透明披露，只有真 L2 才 raise。**
escalation 是稀缺资源，每问一次就打断一次心流。默认自己扛：

| 决策类型 | 处理 | 阻塞？ |
|---|---|---|
| **Mechanical**（唯一正解） | 直接做 | 否 |
| **Taste**（有合理默认，人可能不同） | 自己定默认 + **当前 tab 一行披露**"选了 X，因为 Y，可推翻" | 否 |
| **Judgment / L2**（真歧义 + 不可逆，如改公共 API） | AskUserQuestion 阻塞问答 | 是 |

"有倾向想确认一下" ≠ L2 —— 那是 Taste，自己定 + 披露，不许 raise。有能力决策却问，等于把我该担的判断成本转嫁给用户（C039 镜像：判断≠甩给人）。透明披露 = 我担判断 + 给用户审计与推翻权，比"凡事问一下"更尊重时间。**但披露一行顶格，不写小作文** —— 话痨注释淹没 chat window 是另一种心流污染。

**3. 不可交互环境不假装有人决策。**
channel/headless 对 L2 一律 **fallback checkpoint + 当前 tab warning**，绝不 in-band ask —— 因为 channel 收到 AskUserQuestion 是 auto-answer 自动挑第一个选项（`gateway.py`），对 taste 无所谓，但对 L2 judgment 等于系统替用户瞎拍一个方向继续 build，是 CLASS A"假装有人决策了"的最坏形态。fallback 时 run 只 `paused` + 写决策点，**永不 auto-archive**（归档需 owner-death observation 支撑，是另一关注点，不能由 pipeline 自决）。

> 对应通道路由矩阵 + escalation 决策树见 `TECH.md` § Architecture。源：2026-06-27 与 XG 的 pipeline-HITL 设计讨论。

### Design Philosophy Series (海报)

完整设计哲学以 6 张海报形式表达，存于 `docs/posters/`：

1. **Compound Intelligence** — 当设计哲学变成编译器（收束篇）
2. **Agent Harness** — 当人来培养你的 Agent
3. **DDD Cultivation** — Domain Expertise as Infrastructure
4. **Pipeline** — Coding as Black Box
5. **Pollinate** — 内容平权

---

## Strategic Priorities
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

> [decision] **Adversarial gate is non-negotiable even on trivial config changes** — on a trivial field-name change, the adversarial pass independently re-verified the field against the binary AND surfaced a PRE-EXISTING latent bug: `format_skill_md` (skill_manager.py) emits only name/description/version, so an s_skill-builder round-trip would DROP disable-model-invocation (+ tier/platform). Correctly ruled out-of-scope (the 10 skills are copytree byte-copied, never round-tripped) and logged rather than scope-creeping (R25). The gate earns its keep where you least expect it. (2026-07-11, source:proposal_e6906110)

1. **Core Engine** -- The six flywheels are the product's moat. Every pipeline run should strengthen at least one flywheel and close at least one feedback loop (planning unit = run, not sprint — PRI07). Token cost is not the constraint; outcome quality is.
2. **Core stability** -- Multi-session architecture, resource management, streaming reliability. Nothing else matters if the foundation isn't solid.
3. **Self-evolution** -- Memory pipeline, proactive intelligence, signal processing, skill ecosystem. Swarm should get better at helping you without you telling it to.
4. **Code Intelligence** -- Project-scoped code graph (`code_intel.db`) as DDD's 5th pillar. Tree-sitter parsing → SQLite graph → blast radius + risk scoring + dead code detection. Feeds pipeline REVIEW (auto-computed blast radius replaces manual grep), PreToolUse hook (dependency context on every Read), session briefing (codebase map), and **BottomBar health indicator** (user-visible staleness + re-index trigger). Phase 1 (backend infra): shipped 2026-05-03. Phase 1.5 (API + UI): shipped 2026-05-07 (`GET /api/code-intel/{project}/summary`). Phase 2 (multi-repo, RRF search): gated on second repo needing pipeline. v2.0.0 graph visualization: **deferred** (KD09 evaluation: problem not painful, doesn't compound). Design: `Knowledge/Designs/2026-05-03-code-intelligence-platform-design.md`.
5. **User experience** -- Fast iteration cycles, clear error messages, intuitive UX. The tool should disappear into the workflow.
6. **Autonomy progression** -- From AI-Assistant (Phase 1) to AI-Driven (Phase 2) to AI-Management (Phase 3). Gradual trust-building, not a cliff.

## Success Criteria
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- Sessions never crash or lose context unexpectedly
- Swarm remembers decisions and lessons across sessions without being told
- New skills can be created by Swarm in under 30 minutes
- Users spend more time doing, less time re-explaining
- Context compounds: session 50 is meaningfully more productive than session 5

## Non-Goals
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

- **Not a multi-tenant SaaS** -- Desktop-first, local-first. Hive (EC2 cloud deployment) extends the desktop experience to the cloud as single-tenant instances managed by the Desktop owner — not a shared platform.
- **Not a general chatbot** -- Opinionated, workspace-scoped. Built for people who ship, not people who chat.
- **Not a behavioral mirror** -- We do NOT record all user behaviors as ground truth. Many behaviors are wrong, lazy, or harmful — blindly adapting to them makes the system a sycophancy engine that reinforces bad habits. Our memory philosophy: **curated knowledge > raw behavior**. Signals are captured, but only corrections that represent a *better direction* get promoted into persistent memory (confidence-gated, evolution pipeline assessed). The system should make the user stronger, not more comfortable. Explicit non-goal: "user always right" preference learning (e.g., user rejects testing → system learns to skip tests). We are sycophancy-proof by design.
- **Not a RAG / vector-retrieval system — file-based recall is sufficient (decided 2026-07-10)** -- Recall is **pure-filesystem keyword/FTS5 over curated Knowledge/**, NOT semantic vector search. We deliberately removed the vector/embedding leg (2026-06-28, commit `6540970e`) and, after two evidence-driven research runs (run_5d6a7e83 recall synonym-miss + run_796f8227 comment-drift), reaffirm it as a **standing architecture conviction, not a temporary state**. WHY: (1) our corpus is *bounded, human-curated knowledge* where selection itself is the value — not unbounded machine output that needs semantic dedup; (2) the imagined synonym-miss failure ("errors"≠"failed") has **ZERO recorded real instances** — 23/23 live "matched nothing" events were short-CJK conversational follow-ups, a low-signal-query problem semantic embedding wouldn't fix; (3) the residual synonym blind spot is covered by an **agentic re-search nudge** (the agent greps `Knowledge/` with synonyms itself) — cheaper and more transparent than an embedding index nobody audits; (4) vectors cost Bedrock calls + index upkeep + reversal of a tested decision for no measured gain. **Adding RAG/vectors is a non-goal unless a real, recorded, file-based-recall-can't-solve retrieval failure emerges** — a reference project (e.g. headroom's adaptive-alpha hybrid) having it is a hypothesis to falsify against our own telemetry, never a requirement. The lever for any future recall gap is better *keyword/CJK extraction*, not a semantic leg.

## Competitive Positioning (2026-05-28)
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### Independent convergence = design-correctness validation (mattpocock/skills, 2026-07-12)

- [decision] **A public 165K★ engineering-discipline library (mattpocock/skills — Total TypeScript author, decades of experience, grounded in Ousterhout/Evans/Feathers/Fowler) was audited line-by-line against SwarmAI's pipeline; ~all of its best design points were ALREADY built here, several stronger.** This is the THIRD independent convergence data point (after Aki/明 and MeshClaw/AIDLC — both AWS-internal) and the most citable for OUTWARD narrative because the source is a public, individual, world-class engineer with zero connection to us. Point-by-point: red-capable-loop-before-hypothesis (we have it as a validator BLOCK vs his prose), test-theater checks (our RP47 + mutation-prove), deep-module/deletion-test (review.md + RP29), ADR 3-condition gate (reflect.md), spec-vs-quality two-axis review (spec-compliance independent gate), CONTEXT.md ubiquitous-language (our DDD is a superset — injected living substrate + decay lifecycle). Of his 8-item steal-list, only ONE was a genuine gap (Fowler 12-smell fixed vocabulary — adopted); 3 already-stronger, most of the rest already-present. **The signal: two+ independent expert designers converging on nearly the same answer means these are constraint-forced necessary solutions, not one person's taste — external proof our past design decisions were correct, not lucky.** And where we differ we're often ahead by DESIGN: he coerces the model via prose, we compile matured judgment into agent-non-bypassable code gates (we have 12× evidence prose fails). **The delta that stays ours: he ships a static, per-repo, human-read discipline; we ship a self-evolving Agent OS — gates that grow from failure, memory sovereignty, containment outside the agent.** Full research: `Knowledge/Reports/2026-07-12-mattpocock-skills-deep-research.md`. Narrative use: "one builder + AI at team scale" now has external validation — a lone public expert's decades-distilled playbook is mostly already inside SwarmAI, plus a self-evolution layer he doesn't have. (2026-07-12, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [decision] **Containment is now INDEPENDENTLY externally validated: post-pipeline code changes sent to Kiro (a separate, uncontrolled AI reviewer) increasingly come back with nothing to fix (XG report, 2026-07-12).** This is the strongest read of the quality-lift because it clears the self-validation objection — our own Gate-2 could share our blind spots, but Kiro's blind spots differ, so "internal gate catches a real bug in every run AND external Kiro finds nothing after" is the load-bearing COMBINATION (internal-catches + external-clean). Guardrail against the weak read: "found nothing" ≠ "no problem" — it must be paired with the fact that pipeline Gate-2 DID catch a real finding in every run (e.g. this tab's 4 runs, incl. a guardrail that committed the whitelist-trap it warned against). If BOTH internal and external found nothing, suspect shallow review, not quality. **Do NOT let "Kiro says OK" become a reason to skip Gate-2** — its value is proof the review is worth running to the hilt, not permission to run it less; skipping it returns the 12× CLASS-A history intact. The delta being validated: not "the model stopped erring" (it hasn't) but "the OS ships clean anyway" — the chatbot→Agent-OS line. (2026-07-12, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->

### SwarmAI vs MeshClaw / AIDLC ecosystem (E2E, verified 2026-07-11 via code.amazon.com)

- [decision] **SwarmAI vs MeshClaw/AIDLC = deep convergence + bidirectional learning, not competition** — Both are AI-native systems with the SAME E2E spine (DDD 4+ docs → agent → PreToolUse hook-gate → reflect/回流 → AIM/package distribution). Learning flows BOTH ways: SwarmAI COPIES their gate mechanism (`agents/hooks/*.sh` wired via agent-spec `kiroCli.hooks.preToolUse`, exit 2 = block) + AIM distribution; **they copied SwarmAI's reflect/回流** — `aidlc-autonomous-reflect` (5-step: self-assess → MEMORY.md 3-category → ROI calibration → knowledge-doc evolution → self-improve) is structurally isomorphic to SwarmAI REFLECT + s_persist + cultivation (provenance per XG 2026-07-11; structural isomorphism corroborates). Not rivals — cross-validation of one AI-native playbook. (2026-07-11, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->
- [model] **SwarmAI's 3 real deltas vs MeshClaw/AIDLC (each narrowed after verification, each holds)** — (1) **compile-to-gate vs escalate-to-human**: AIDLC's `decision-strategy.json` already tracks pattern maturity (`times_used`/`times_accurate`) AND has policy-driven enforcement (`always_escalate_types` → matched change MUST get human SDE review, "AutoSDE never approves") — so "judgment→enforcement" is NOT unique; the delta is the ENFORCEMENT FORM: SwarmAI compiles a matured pitfall into an agent-non-bypassable executable hook-gate (code enforcement), theirs routes to human review (HITL escalation). This is the one architecture-level difference, aligned with the paradigm doc's Gate-0. (2) **multi-repo bindings**: their setup is a single-`{Name}Spec`-package/single-workspace model; SwarmAI's `bindings.yaml` binds N repos + per-repo delivery-contract-as-data (no equivalent seen — not asserting absence, none found). (3) **supply-chain completion**: AIM lacks a cross-package catalog (Q1 2026) + auto-sync — SwarmAI's layer would add these. **Memory route DIVERGENCE (NOT a MeshClaw lead — corrected)**: MeshClaw bets on a daemon-level vector store (`src/mesh_claw/vector_memory.py`: semantic KV+confidence + episodic text+embedding+FAISS IndexFlatIP + SQLite). SwarmAI made the OPPOSITE bet on purpose — it *built* vector recall then **tore the vector/Titan leg out 2026-06-28** (commit `6540970e`, run_e9b8507e, MOD05/DEC54) because embed cost + write-amplification didn't pay off on our corpus; recall is now pure-filesystem keyword/FTS5 + DDD governance. Two different memory bets, neither proven superior — framing "heavier tech = ahead" is exactly the trap we rejected. (2026-07-11, source:manual)
  <!-- ref:0 | last:none | decay:active | source:manual -->

### Key Fact: SwarmAI = Claude Code Engine SUPERSET

SwarmAI runs Claude Code CLI as its execution engine. This means:
- **All Claude Code capabilities are inherited** — AST-aware editing, cross-file refactoring, test running, 27+ lifecycle hooks (PreToolUse already in production), permission system, MCP support. Identical raw coding power.
- **SwarmAI adds on top** — Autonomous Pipeline (9-stage DDD/SDD/TDD + 2 blocking Gates + Multi-Specialist Adversarial + Quality Convergence Loop + Meta-Intelligence), DDD judgment layer (EVALUATE→GO/DEFER/REJECT), 11 application-layer hooks (memory/evolution/DDD), 24/7 daemon with jobs/channels, compound memory system.

### Three-Way Positioning (OpenClaw vs Claude Code vs SwarmAI)

| | OpenClaw | Claude Code | SwarmAI |
|---|---------|-------------|---------|
| **Repo** | openclaw/openclaw (375K stars) | anthropics/claude-code (127K stars) | xg-gh-25/SwarmAI |
| **定位** | Your AI, everywhere you chat | Agentic coding in your terminal | Your AI Team, 24/7 |
| **核心** | 多模型多渠道覆盖 (25+ platforms) | 最深 coding + 最强并行 | Claude Code + 质量系统 + 判断层 + 复利 |
| **目标用户** | 所有知识工作者 | 开发者 | Technical builder (1人顶1团队) |
| **差异化赌注** | 入口垄断 | 执行力垄断 | 判断力垄断 (越用越聪明) |

**IMPORTANT: OpenClaw ≠ Claude Code.** OpenClaw (Peter Steinberger/steipete, MIT, TypeScript) is a multi-channel AI assistant gateway. Claude Code (Anthropic, proprietary, Python) is a terminal coding agent. Completely unrelated projects.

### Aki (明) — Amazon-internal convergent architecture (2026-07-07)
<!-- ref:0 | last:none | decay:active | source:manual -->

Aki is Amazon's internal official-team analog to SwarmAI: a Bedrock-driven, local desktop Agent, rewritten ground-up in **Rust** for 2.0 (dropped LangChain). Owner JST team / aki-community, ASR-approved **Orange**, 2000+ users. Built-in agents Aki/Akira(research)/Akisa(dev)/Team Mode. Runs macOS/Win/Linux (Linux CLI-only). **We independently converged on the same core bets** — strong external validation of the thesis, and the divergence points are the moat.

| Design axis | Aki 2.0 | SwarmAI | Read |
|---|---|---|---|
| Token efficiency | **Bash-only** (one tool, not 30) + MCP install-time snapshot-then-kill → claims ~5K startup (10× cut); ASBX-Bench 84.6% Pass@1 | 11-file budget + selective injection + lazy MCP tier | ⚠️ "10×" is a strawman-baseline (see debunk below) — the real win (schema lazy-load) is **parity**; single-Bash trades reliability for ~1% token |
| Skill arch | 4-tier precedence (Workspace>User>Profile>Global), MCP auto-converts to Skill | always/lazy tier + projection | Parallel |
| Multi-agent | Team Mode + teammate agents (Claude Code/Kiro/Codex) as parallel backends | Single-agent role-switching (PIT08 explicitly rejects multi-agent) | **Philosophical fork** — deliberate, ours |
| Cross-session memory | Memory files (who/how/what) + 5PM organize task + morning brief | MEMORY/EVOLUTION + DailyActivity + morning brief | Near-identical |
| Permission tiers | LLM-judge flags sensitive → human approve; read-only auto | dangerous_command_gate + AskUserQuestion | Same |
| **Self-evolution** | ❌ none | EVOLUTION.md closed loop + cognitive corrections | **Ours alone** |
| Unattended exec | ARES (on-demand EC2/DevSpace, warm pool, A5/OAuth) | pipeline auto-resume + scheduled jobs | Ours more mature on the loop; theirs stronger remote infra |

Source: internal deep-dive 2026-07-07 — specs.harmony.a2z.com Harmony-aki system-overview (REQ/ARCH-level), w.amazon.com/bin/view/Aki/, AllThingsAI 2026-04-17 launch brief, ToolComparison Aki-vs-MeshClaw wiki.

**Aki "10× token diet / single-Bash" claim — debunked (2026-07-07, measured).** The blog's headline (50K→5K, 10× cut) is a **strawman-baseline**: the 50K+ is Aki's OWN 1.0 worst case (every schema of 30+builder/20+outlook/30+slack eagerly injected every turn) — nobody ships that, so the "10×" divides by its own past bad design, not the industry norm. Two things the blog conflates and sells as one: (a) **schema lazy-loading** (catalog of names + on-demand doc) genuinely saves tokens — **and we already have it on BOTH layers** (deferred-MCP directory = name+one-line; SDK-native `ToolSearch` fetches tool schemas on demand); (b) **executing via model-written `mcp call` command lines saves almost nothing** once schemas are lazy (there's no resident schema left to cut) — it's Aki's Rust-core choice to skip native tool-use, repackaged as philosophy. The blog omits the **cost** of single-Bash: loses reliable Read/Edit/Grep primitives (bare `grep -r` HANGS — our own AGENT.md 12-min lesson), forces rewriting the 4-layer PreToolUse safety chain from tool-gating to string-gating, and degrades structured tool-use into fragile code-gen. **Measured token split (2026-07-07, `s_estimate-tokens` + direct wc — re-measure live, don't trust these as current):** skill descriptions ≈ upper-single-K (89 skills, SDK-forced injection — the ONLY controllable slice, but trimming it risks routing accuracy); deferred-MCP directory ≈ low single-K (already Aki-style lazy, NOT eager as external reviewers assume); SDK framework + tool schemas ≈ low-double-K, uncontrollable. **Verdict:** Aki's real win (schema lazy-load) = **parity** with us; its extra "single-Bash" shout trades reliability for ~1% marginal token. Anti-COR05 signal: we do NOT lag here — don't undersell.

### SwarmWS Product Model — Shell + Sample Seed (B: first-given, then-owned)
<!-- ref:0 | last:none | decay:active | source:manual -->

The app ships a **default SwarmWS workspace = a product SHELL**: the default folder
structure (Knowledge — **empty subdirs only, content is user-private**; Projects;
Services/System), plus the default **SwarmAI Project/DDD as a first-version SAMPLE
seed** (teaches users what a DDD looks like; sourced from `backend/templates/ddd/` in
code git). The whole SwarmWS is git-managed but **purely LOCAL** (`git init`, **no
remote** — new users have no `origin`; a leak is only possible if a user configures a
backup remote themselves).

**Model = B (first-given, then-owned), NOT A (push→download→overwrite).** The seed is
written **once** (`_ensure_default_project`, `if not filepath.exists()` — "user edits
are preserved"). "随产品更新" therefore means updating the seed for the **next new
user**, never retroactively overwriting an existing user's SwarmAI DDD.

**The reusable judgment — the coverability test (prevents re-litigating B-vs-A):** a
product-shipped artifact is safely **overwrite-on-update** (like skills: `.claude/skills/`
is gitignored, projected via `rmtree`+`copytree`, **zero user increment**) **ONLY IF it
accumulates zero user increment in the same location.** SwarmAI DDD **fails** that test —
cultivation writes user lessons *into* `Projects/SwarmAI` by default (it is the default
cultivation target), so it is a **living knowledge store, not a projection**. Overwriting
it would destroy the user's accumulated knowledge. **Test to apply to any shipped
artifact: "does the user's own increment grow in the same location the product would
overwrite?" Yes → seed-once (B). No → safe to project/overwrite (like skills).**
(2026-07-19, source:manual)

**What a NEW user's SwarmWS looks like at first provision (code-traced 2026-07-19,
commit eb335655):**

```
~/.swarm-ai/SwarmWS/                    ← product shell · git init · LOCAL only (no remote)
├── .git/  .gitignore                   ← gitignore carries runtime + privacy rules
├── .context/                           ← cognition files, TWO copy-modes:
│   │  ─ SYSTEM (overwrite from product every startup, 0o444 readonly) ─
│   ├── SWARMAI · IDENTITY · SOUL · SELF · AGENT .md      ← product cognition framework
│   │  ─ RUNTIME (seed once, then user-owned, 0o644) ─
│   ├── USER · STEERING · TOOLS · MEMORY · EVOLUTION .md  ← 🔒 gitignored (personal)
│   └── KNOWLEDGE.md · PROJECTS.md       ← auto-generated indexes (KNOWLEDGE seeded)
├── Knowledge/                          ← 12 EMPTY subdirs (structure only; content private)
│   └── Notes/ Reports/ Meetings/ Library/ Archives/ DailyActivity/
│       Handoffs/ Designs/ Learned/ Pollinate/ Signals/ JobResults/
├── Projects/                           ← 🔒 Projects/* gitignored EXCEPT SwarmAI
│   └── SwarmAI/                        ← ✅ the ONLY shipped sample DDD (seed, tracked)
│       ├── PRODUCT/TECH/IMPROVEMENT/PROJECT.md   ← ② 4 DDD docs (non-empty seed, ~395 lines)
│       ├── AGENTS.md aim.json .crux_template.md REFRESHER.md  ← ① identity manifests
│       ├── .project.json decision-strategy.json .artifacts/manifest.json
│       ├── gates/ (empty, accretes) · skills/ (5 DDD-native) · Knowledge/ (empty, accretes)
├── Attachments/                        ← empty
└── Services/                           ← System (jobs/services config)
```
The three-layer "product vs user" split is consistent: `.context` (SYSTEM 5 overwrite
vs RUNTIME 7 seed-once) · Projects (SwarmAI sample vs user's own, gitignored) ·
Knowledge (empty structure vs user content). (2026-07-19, source:manual)

### Product Layer Model

| Layer | OpenClaw | Claude Code | SwarmAI |
|-------|----------|-------------|---------|
| L0: Execute | ✅ General tasks | ✅ Coding tasks | ✅ Full-stack (code + ops + research) |
| L1: Quality | ❌ | ⚠️ Permission system | ✅ Pipeline + TDD + Adversarial Review |
| L2: Judgment | ❌ | ❌ | ✅ EVALUATE + DDD + Decision Classification |
| L3: Compound | ⚠️ Session history | ⚠️ Auto Memory (flat) | ✅ 11 files + distillation + corrections + cultivation |
| L4: Autonomous | ✅ Daemon | ⚠️ Routines (cloud) | ✅ 24/7 daemon + jobs + channels + signals |

**SwarmAI is the only system covering L0-L4.** Our bet: L1-L3 (quality + judgment + compound) is the real moat, not L0 (execution) or channel coverage.
- **Not code-only** -- Handles research, writing, communication, planning, scheduling. A full teammate, not a coding copilot.
- **Not a framework** -- SwarmAI is a product. It has opinions about how AI assistance should work.

### One-line differentiator: we stop at the DECISION layer, not the prediction layer
<!-- ref:0 | last:none | decay:active | source:manual -->

Borrowing Palantir/Ontology 决策科学的四层尺 — **预测 (会发生什么) → 推理 (为什么) → 推演 (如果…会怎样) → 决策 (选哪条、担后果)**: 纯知识图谱工具能查不能动、纯 BI 工具止步于预测。**SwarmAI 是少数把链条走完到"风险决策"的产品** —— 推演层 = pipeline THINK 强制多方案+tradeoff+stress-test;风险决策层 = 对抗门 + 人机内审批 + 全程可审计,人做最终 disposal(与 Palantir 的 AI-提议→人审→执行→审计 Proposal 工作流同构)。**一句话:别人的 AI 帮你"看清";我们的 AI 陪你"拍板"——带取舍、担后果、可追溯。** 诚实边界:语义层仍是散文级(DDD 非可推理 typed graph),不对外冒充形式本体。(2026-07-10, source:manual)

## Strategic Positioning
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### What SwarmAI Is NOT Competing For

SwarmAI is not an open-source framework competing for GitHub stars against Hermes (model ecosystem), DeerFlow (quality framework), or OpenClaw (CLI agent). Those are platforms optimizing for adoption breadth. SwarmAI is a **productivity thesis experiment** — the open-source repo is evidence and credibility, not the product.

### The Actual Game

| Dimension | Open-source harness game (NOT ours) | SwarmAI's game |
|-----------|--------------------------------------|----------------|
| Success metric | Stars, forks, ecosystem size | "Does 1 builder + AI compound into team-scale output?" |
| Audience | Developer community at large | Enterprise CTOs (via AIDLC) + AI-native builders |
| Moat | Community + integrations | Compound knowledge + quality convergence proof |
| Revenue path | Open core / hosting / enterprise tier | AIDLC methodology consulting + reference implementation |
| Evidence | Adoption numbers | P0 convergence, case studies, EVOLUTION.md track record |

### Why This Framing Matters

External evaluators consistently apply the "open-source framework" lens: "only 36 stars", "no ecosystem", "can't compete with Hermes". These are correct observations applied to the wrong game.

The correct evaluation criteria for SwarmAI:
1. **Does quality demonstrably converge?** (P0 trending down, failure classes migrating from catastrophic → edge-case)
2. **Does one person ship team-scope work?** (Code + content + strategy + operations from same system)
3. **Does knowledge compound across sessions?** (Session N+1 measurably starts with more context than N)
4. **Is the system eating its own dogfood?** (SwarmAI develops SwarmAI — the product IS the proof)

### Strategic Route: AI-Native Builder Validation + AIDLC Commercialization

```
SwarmAI (product) ─→ validates ─→ AIDLC (methodology)
                                        ↓
                               Enterprise CTO engagement
                               ("This is what Phase 3 looks like in production")
                                        ↓
                               Consulting + transformation revenue
```

**Three channels, one thesis:**

| Channel | Audience | What they see | What it proves |
|---------|----------|--------------|----------------|
| **Open source (GitHub)** | Agent builders, AI engineers | Architecture, EVOLUTION.md, code | "The compound loop is real and git-verifiable" |
| **Content (Pollinate output)** | AI community, builders | Design philosophy posters, technical posts | "This person thinks differently about agent systems" |
| **Enterprise (AIDLC)** | CTOs, engineering leaders | Reference implementation + methodology | "Phase 3 autonomous delivery works — here's proof and here's how" |

### What Needs to Happen (Next 6-12 Months)

| Priority | What | Why |
|----------|------|-----|
| 1 | **Continue compounding** — more corrections, more convergence data | The thesis gets stronger with time, not with features |
| 2 | **External case study** — one non-XG user validates the productivity claim | "It works for me" → "It works for others" is the credibility leap |
| 3 | **Technical depth publication** — blog posts on specific mechanisms | Not marketing. "Here's how 4-layer memory works and why it's better than single CLAUDE.md" |
| 4 | **Stabilize core API** — DDD + Pipeline + Pollinate interfaces documented | Makes the architecture reusable without making it a platform |
| 5 | **Linux support** — opens the contributor pool | Currently macOS-only limits who can try it |

### What We Explicitly Defer

- ❌ Star count competition — not our game, not our audience
- ❌ Platform化 (plugin marketplace, third-party skills) — premature without user base
- ❌ Multi-model support as differentiator — tooling commoditizes fast (T6), knowledge doesn't
- ❌ "Launch" moment — compound systems don't launch, they accumulate evidence

---

## Brand & External Communication
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

### Positioning Hierarchy

| Layer | Content | Purpose |
|-------|---------|---------|
| Tagline | Human directs. AI delivers. | Hook — 4 words, memorable |
| Belief | 探索 AI 的边界 | What drives us |
| Proof | 一个人 + AI 能顶一个团队 | What we're proving |
| Focus tags | AI Native Transformation / AI Agent Harness / 0→1 Product Building | Discovery keywords |

### External Content Principles

All outward-facing content (social media, README "why" sections, demos, pitches) must follow these principles:

| # | Principle | Rule |
|---|-----------|------|
| P1 | Value > Output | 代码量/commit数/天数是 output 不是 value。Thesis 比 metrics 有力。去掉数字句子还成立 → 不需要数字。 |
| P2 | What's true > What I did | 弱化第一人称。观点输出者，不是成就展示者。主语是"事物本身"不是"我"。 |
| P3 | Thesis 驱动 | 从 worldview 出发，不从 feature 出发。先输出 belief，能力是自然延伸。 |
| P4 | 效果 > 机制 | 说"对你意味着什么"，不说"内部叫什么"。8 Feed Channels 是噪音；"知识从工作中自己生长"是共鸣。 |
| P5 | 英文只在更有力时用 | 翻成中文不失去什么 → 用中文。只保留不可替代的：`Human directs. AI delivers.` / `DDD` / `Coding as Black Box`。 |
| P6 | 每条独立能打 | 不依赖系列上下文。碎片阅读环境，单条必须自足。 |
| P7 | Briefing 说价值不列术语 | 产品介绍用人话说价值，术语最多作为注脚。 |
| P8 | 严格遵循定位层次 | 内容 = Thesis × Ability，经过 P1-P7 filter 输出。不在框架里 = 跑偏。 |

### Thesis Anchors (6 条价值观)

Content maps to theses — each piece must anchor to at least one:

- T1: Memory is the only durable moat — 记忆是连续性，不是存储量
- T2: AI paradigm shift is structural — 认知底座在变，不是渠道
- T3: Understanding > Execution — 判断力不可外包，用进废退
- T4: AI-Native org design wins — 协作成本 = 信息不对称的代价
- T5: Culture as code = quality ceiling — 工程文化编码进系统 = 品味
- T6: Tooling commoditizes fast — 知识是资产，工具是手段

### Ability Anchors (产品能力，不翻译)

- DDD — Domain expertise as infrastructure
- Coding as Black Box — One sentence → push-ready
- Memory Compounds — 越用越懂，不是每次从零
- Self-Evolution — 犯过的错在结构上不可能再犯
- Quality Convergence — 每轮输出比上轮更接近正确
- 5 Black Boxes — Coding · Content · Knowledge · Quality · Evolution

### Anti-Patterns (禁止)

- ❌ 用 LOC / commit 数 / "XX天" 作为亮点
- ❌ 第一人称主角光环（"我造了"、"我的AI"）
- ❌ 内部架构术语堆砌（8 Feed Channels、Cultivation Engine、Quality Convergence Loop）
- ❌ 英文硬塞中文段落制造阅读断裂
- ❌ Feature pitch 代替 Thesis pitch

Full principles with anti-pattern examples: `Knowledge/Learned/2026-05-16-social-content-design-principles.md`

## Target Users
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

All Knowledge-Workers，Developers and Leaders who want an AI teammate, not an AI tool. People who work across multiple projects, value accumulated context, and prefer action over conversation.

## Autonomy Phases
<!-- maturity: sparse | sources: 0 | verified: true | used: true | days: 0 | trust: high | promoted: none -->

| Phase | Name | Essence | Human Role |
|-------|------|---------|-----------|
| 1 | AI-Assistant | Human decides, AI executes | Direct every task |
| 2 | AI-Driven | AI proposes, human approves | Review and approve |
| 3 | AI-Management | AI decides + evolves, human triages | Intervene when needed |

SwarmAI is in **Phase 2 (AI-Driven)** with L4.0–L4.5 autonomy features entering Phase 3 territory. The self-evolution loop is closed: observe user behavior → measure skill performance (SkillMetrics) → mine corrections (SessionMiner) → score fitness (3-signal) → optimize underperformers (EvolutionOptimizer) → deploy with safety gates. Evolution Pipeline v2 (MINE→ASSESS→ACT→AUDIT): HIGH confidence (≥0.35) auto-deploys, MED (≥0.15) recommends, LOW logs only. Atomic deployment with auto-revert if fitness drops >0.1. Technical details in `TECH.md` (Self-Evolution Flow section).
