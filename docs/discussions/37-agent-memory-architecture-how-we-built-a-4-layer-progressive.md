# Agent Memory Architecture: How We Built a 4-Layer Progressive Memory System

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/37) | Category: Show and tell | Published: 2026-05-21

---

> 可信、可进化、越用越聪明的 Agent 记忆 — 不是一个 feature，是从"可用"到"可信赖"的基础设施。

**TL;DR:** 大多数 AI Agent 是无状态的 — 每次对话从零开始。我们构建了一个 4 层渐进式记忆系统：Context Directory（全量注入）→ Progressive Index（选择性加载）→ Session Recall（FTS5 搜索历史对话）→ Knowledge RAG（混合向量+关键词检索）。结合三层治理（Principles/Rules/Gates）实现自进化。

### 三条核心设计哲学

> **① 注入 > 检索** — 在 1M context 时代，46K tokens 的记忆直接全量注入 system prompt，零检索延迟。Vector retrieval 是给知识装不下 context 的时代设计的。
>
> **② 主动遗忘 > 无限积累** — 遗忘是能力，不是缺陷。无用知识占位 = 压缩有用知识的空间。90 天无引用 = 候选归档。
>
> **③ Memory sovereignty > 平台依赖** — 记忆是 Agent 最有价值的资产。不锁在任何供应商的黑盒里。换模型不丢失记忆。

---

## 为什么 Memory 是 AI Agent 最被低估的问题

大多数 AI Agent demo 看起来很惊艳 — 因为 demo 都是单轮的。

生产环境不是单轮：
- **跨 session 记忆**：昨天讨论的决策，今天要执行
- **知识积累**：从第 1 天到第 100 天，Agent 应该越来越聪明
- **行为校正**：用户纠正过的错误，不应该再犯
- **知识衰减**：3 个月前的 best practice 可能已经是 anti-pattern

**没有 Memory 的 Agent = 永远的实习生。有 Memory 的 Agent = 日益成长的同事。**

### Agent Memory ≠ Chat History

| 维度 | Chat History | Agent Memory |
|------|-------------|-------------|
| 生命周期 | 单个 session | 跨所有 session |
| 内容 | 原始对话文本 | 蒸馏后的决策、教训、知识 |
| 组织 | 线性时间排列 | 按类型结构化 |
| 更新机制 | Append-only | 蒸馏、合并、衰减、删除 |
| 规模挑战 | Token 溢出 → 截断 | 选择性注入 → 精准回忆 |

---

## Overall Architecture: 4-Layer Progressive Memory

![Agent Memory — 4-Layer Architecture](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-agent-memory/2026-05-21-agent-memory-4-layer-architecture.svg)

<details>
<summary>架构图文字说明（点击展开）</summary>

**Layer 4: Knowledge RAG Engine** — Triggered on first user message, 150ms budget
- KnowledgeStore: FTS5 + sqlite-vec over Knowledge/ directory (Notes, Designs, Signals)
- TranscriptStore: FTS5 + sqlite-vec over past session transcripts (JSONL)
- MemoryEmbeddings: sqlite-vec (1024-dim Titan v2) over MEMORY.md entries
- Knowledge Graph: .yaml entity→entity 1-hop expansion
- RecallEngine: Hybrid merge (0.6 vector + 0.4 keyword), threshold 0.05

**Layer 3: Session Recall** — FTS5 over past conversations, ~100ms
- messages_fts virtual table (auto-synced via triggers)
- Score = density×0.4 + recency×0.35 + richness×0.25
- 90-day decay, ±10 message context window, max 2 snippets

**Layer 2: Progressive Memory Index** — Smart selective injection
- L0 Compact Index: always injected (~500 tokens), machine-generated TOC
- L1 Section Selection: if MEMORY > 30K tokens, hybrid keyword+vector scoring
- L2 On-Demand: Agent reads specific sections via tool call
- CJK support: bidirectional substring + shared prefix matching

**Layer 1: Context Directory** — 11-file system prompt assembly
- Three ownership models: System (readonly) | User (editable) | Agent (locked_write only)
- Priority-based truncation (P10 first), direction-aware (MEMORY keeps newest)
- Token budget: 100K (1M model) / 50K (200K) / 30K (64K)
- L1 cache with git-based freshness (15s TTL)

**Write Path** (Session Close):
Summarization → DailyActivity → Distillation (git-verified) → MEMORY.md → Embedding Sync → DDD Cultivation

</details>

### 为什么是 4 层而不是一个 RAG Pipeline

| Layer | 触发时机 | 延迟 | 准确度 | 为什么独立 |
|-------|---------|------|--------|-----------|
| L1 Context Dir | Session start | 0ms (cached) | 100% (全量) | Identity/rules 每次都需要，无检索意义 |
| L2 Progressive Index | Session start | ~50ms | ~85% | 有结构的 indexed sections，比 chunk-and-embed 更精准 |
| L3 Session Recall | First message | ~100ms | ~75% | 过去对话的具体 fragments |
| L4 Knowledge RAG | First message | ~150ms | ~70% | Knowledge library + transcripts |

**传统做法**把所有知识扔进一个 vector store，每次 top-K retrieval。我们把确定性注入（L1）、结构化选择（L2）、和 RAG 检索（L3/L4）分离 — 每层有不同的触发条件、延迟预算和准确度保证。

---

## E2E Case: One Prompt Traversing All 4 Layers

一个真实场景："昨天 forecast gap 的结论是什么？哪几个 BU 风险最大？"

![E2E Flow — Single Prompt Across All Layers](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-agent-memory/2026-05-21-agent-memory-e2e-flow.svg)

<details>
<summary>E2E Flow 详细说明（点击展开）</summary>

**昨天 (Write Path):**
1. Session close → SummarizationPipeline 生成 DailyActivity
2. DistillationTriggerHook 判断为 key decision → 写入 MEMORY.md [KD30]: "MEAGS -12%, RFHC +3%, ISV flat"
3. MemoryEmbeddingStore 对 KD30 生成 1024-dim vector
4. DDD Cultivation → IMPROVEMENT.md: "MEAGS large deal slip is recurring signal"

**今天 Layer 1 (0ms):**
- MEMORY.md 全量注入（< 30K threshold）
- [KD30] 已经在 system prompt 里

**今天 Layer 2 (~50ms):**
- select_memory_sections() hybrid scoring
- "forecast" + "gap" → keyword 0.82, vector 0.89
- Combined: 0.6×0.89 + 0.4×0.82 = 0.862 (threshold 0.15)

**今天 Layer 3 (~100ms):**
- FTS5 on messages_fts: 找到昨天 3 条消息
- "BU 排风险：MEAGS > ISV > DNBP" + ±10 context window

**今天 Layer 4 (~150ms):**
- KnowledgeStore: CMHK_SalesIntel/TECH.md hierarchy model (cosine 0.78)
- TranscriptStore: 昨天 session detailed MEAGS breakdown (cosine 0.71)
- Graph: Entity "MEAGS" → 1-hop [RFHC, ISV, forecast_table]

**Agent Response:**
综合 4 层信息 → "MEAGS 风险最大 (-12%)，3 笔大单 slip: [A] ¥2.1M 预算冻结..."

**Without memory:** "我不知道你昨天做了什么，请再描述一次。"
**With memory:** 精准引用 + 补充细节 + actionable next step | Total overhead: < 200ms

</details>

---

## Memory × Self-Evolution: Three-Layer Governance

Memory 不只存"知识" — 也存 Agent 的行为校正历史，并驱动自进化。

![Three-Layer Governance + Evolution Loop](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-agent-memory/2026-05-21-agent-memory-evolution-loop.svg)

<details>
<summary>治理模型说明（点击展开）</summary>

**为什么 rule 累加是死路：**
- 32 条 corrections 记录中，同一个认知偏差（"跳过质量审查"）出现 4 次 (C011→C021→C025→C026)
- 每次加规则都不够，最终升级为 code-level gate 才彻底解决
- Rule 只能枚举症状，不能治根因。Principle 覆盖整个 failure class

**三层协同：**
- Principles (~70-80% compliance): 处理从未见过的新情况
- Rules (bounded ≤25): 已知情况的具体指导，add one = retire one
- Gates (code enforcement): 只用于 proven-stubborn failures (4+ recurrences)

**关键 insight:** Agent Memory 的深层功能不只是"记住做了什么"，而是"记住哪些认知模式有 bug，并结构性修复"。每条 correction 是 OS patch，不是 data update。

</details>

---

## Cognitive Stack: 5 Layers of Agent Intelligence

![Memory Cognitive Stack](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-agent-memory/2026-05-21-agent-memory-cognitive-stack.svg)

| Level | Content | Lifecycle | Loading |
|-------|---------|-----------|---------|
| **L4 Principles** | SOUL.md (3-5 cognitive orientations) | Permanent, never grows | Always (non-truncatable) |
| **L3 Rules** | AGENT.md / STEERING.md (bounded ≤25) | Expirable, add-one-retire-one | Always |
| **L2 Domain Knowledge** | DDD 4-doc per project + KNOWLEDGE index | Darwinian decay (use→strengthen→archive) | Full + on-demand |
| **L1 Working Memory** | Resume + DailyActivity + checkpoints | Auto-distilled to L2-L4 | Session start |
| **L0 Episodic Recall** | Session RAG + Knowledge RAG + Transcripts | Permanent raw data | On first message, 150ms |

**Key design:** Higher layers are more abstract, longer-lived, harder to change. Lower layers are concrete, ephemeral, auto-managed. The distillation pipeline continuously promotes insights upward.

---

## Design Philosophy: 4 Counter-Intuitive Decisions

### 1. Markdown + sqlite-vec > Pinecone/Weaviate/Neo4j

在 1M context 时代，核心记忆（~46K tokens）直接全量注入 system prompt — 零检索延迟。Vector DB 的 retrieval overhead 对这个规模完全没有意义。

我们用 sqlite-vec（本地）+ FTS5（本地）做 hybrid search，无网络开销。Markdown 是人类可读的，有 git history，可 diff，可 review。

### 2. 达尔文主义 > 百科全书

```
百科全书：知识 → 存储 → 永生（直到人工清理）
达尔文：  知识 → 使用 → 强化 ←→ 不用 → 衰减 → 归档 → 遗忘
```

- 每条知识有引用追踪（被 pipeline/决策/对话引用则强化）
- 90 天无引用 → 候选归档
- Superseded entries weighted 0.1x
- **遗忘是能力，不是缺陷** — 无用知识占位 = 压缩有用知识的空间

### 3. Memory Sovereignty > Platform Dependency

所有记忆 self-owned、self-managed。永远不使用平台记忆服务（Claude Memory、OpenAI Memory）。

- 控制 schema 和生命周期
- 随时换模型不丢失记忆
- 最有价值的资产不能 vendor-locked

### 4. Structured Extraction > Brute-Force Replay (Session Resume)

Claude Code 做完整 JSONL replay（到 12% 时 auto-compact）。我们从 DB 提取 5 层结构化信息注入：
1. Assistant conclusions (最后 5 个结论性文本块)
2. User directives (短决策)
3. Key tool results (15 个关键工具输出)
4. Uncommitted git state
5. Crash checkpoint merge

**哲学：** Resume 的核心不是"记住做了什么"，而是"记住发现了什么"。

---

## 企业场景映射

| SwarmAI 概念 | 企业 Agent 对等 | 价值 |
|-------------|----------------|------|
| 11-file Context Directory | 组织知识库（可审计、版本化） | 合规友好，可 review |
| DDD 4-doc per project | 项目域知识隔离 | 零交叉污染 |
| Three-layer governance | Agent 行为标准化 | 合规 + 灵活性并存 |
| Darwinian decay | 知识保鲜机制 | 替代人工清理 |
| Memory Guard (5 scans) | 写入保护 | 防注入、防泄露、防秘密 |
| Evolution Registry | 行为改进日志 | 可解释的改进轨迹 |

---

## Open Discussion Topics

### 1. "注入 > 检索" 的边界在哪？

当前论断基于一个前提：主流模型保持 100K+ context。如果某些专业场景降级到 32K（边缘部署、成本敏感客户），策略需要动态调整。

我们的回答是动态 token budget（100K/50K/30K/minimal 四档）+ progressive index 自动激活。但更深层的问题是：**什么时候 threshold 翻转？** 知识量级在什么点上，全量注入的 cost 开始超过 selective retrieval？

一个 model-agnostic 的判断框架：**memory tokens / context window < 5% 是舒适区，超过 10% 开始有 attention dilution risk。** 我们当前 46K / 1M = 4.6%，正好在舒适区。动态 budget tier 在模型切换时自动适配 — 从 Claude Opus (1M) 降到 Sonnet (200K) 时，budget 从 100K 降到 50K，L2 progressive mode 自然激活。不需要手动调参。

预计 MEMORY.md 自然增长到 ~80K 时（~8% on 1M），L2 selective mode 会成为常态。但这还远 — 达尔文衰减机制在主动控制增速。

### 2. Memory Sovereignty vs Auditability

文章强调 "memory 不能 vendor-lock"。但企业合规有一个对立需求：**记忆的每一次更新都需要审计日志。**

我们的方案：
- git 追踪 MEMORY.md 的每一次变化（谁改的、什么时候、diff 是什么）
- `locked_write.py` 的 fcntl.flock 保证原子写入
- MemoryGuard 扫描每次写入（secrets/injection/hijack/exfiltration/invisible）

但一个更深层的观点：**企业级 Agent Memory 应该 focus on domain knowledge（项目/业务领域），而不是个人记忆。** 个人记忆（个人偏好、工作习惯）有 privacy concern，不适合审计。而 domain knowledge（"MEAGS 的 forecast gap 是 -12%"、"这个 API 的正确调用方式"）天然需要版本化、可审计、可共享。

DDD 4-doc 结构正是这个分离的实现：domain knowledge 在 Projects/ 目录（可审计、可共享），personal memory 在 MEMORY.md（agent-owned、不外泄）。

一个边界 case：domain knowledge 里可能混入 PII（"forecast gap 的原因：某人的工作失误"）。我们的防护不是在 schema 里加 sensitivity metadata（over-engineering），而是两道已有的门：(1) MemoryGuard secrets/PII scan 在写入时拦截，(2) Distillation 是 rule-based + git-verified 的 — human-in-the-loop 本身就是 PII filter。结论是结论，归因是八卦 — distillation 自然只保留前者。

### 3. 引用追踪的粒度问题

"90 天无引用即归档" — 但什么算 "引用"？

- 被注入 system prompt（L1 全量注入时）算不算？如果算 → 永远不会衰减（因为每次都注入）
- 被 Agent "看过" 算不算？还是只有 "被决策用到" 才算？

我们的实现：**只有被 pipeline/决策/对话显式引用才计数**。全量注入不算引用（否则所有条目等价）。这避免了虚假强化，但也意味着 L2 selective mode 的激活会自然产生更准确的引用信号 — 被选中的条目才是真正相关的。

### 4. Multi-Agent Memory Sharing

当前架构是单 Agent 的。10 个 Agent 同时处理相关问题时，怎么让记忆互联？

我们的立场：**不共享 MEMORY.md**。每个 Agent 独立记忆。协作通过：
- **Shared domain knowledge**（DDD 4-doc 是 project 级别的，多 Agent 可以读同一份）
- **Message passing**（Agent A 的产出写入 DailyActivity，Agent B 下次启动时通过 L4 Knowledge RAG 自动检索到）
- **不共享的原因**：git merge conflict on concurrent writes + 不同 Agent 有不同的 correction history + 个人 context 污染

类比：公司里的同事共享 wiki（domain knowledge），但不共享私人笔记本（personal memory）。

### 5. Memory Poisoning 防护细节

MemoryGuard 的 5 层扫描：

| 类别 | 检测 | 动作 |
|------|------|------|
| Secrets | AWS keys, bearer tokens, PEM, passwords | REDACT（替换为 `[REDACTED]`） |
| Prompt Injection | "ignore previous", special tokens, base64 payloads, DAN | REJECT（拒绝写入） |
| Role Hijack | "act as", "pretend to be", "new role" | REJECT |
| Exfiltration | curl/wget with auth/secrets | REJECT |
| Invisible Characters | Zero-width spaces, RTL marks | STRIP（剥离后继续） |

每次写入 MEMORY.md / EVOLUTION.md 都经过这条 pipeline。这是写入时的第一道门。

额外防护：`validate_memory_content()` 专门检查 MEMORY.md 的结构完整性（section 格式、entry ID 唯一性等）。

### 6. Cold Start 策略

新 Agent（或新 project）第一天：
- **MEMORY.md 为空** — 正常，DailyActivity 从第一个 session close 开始积累
- **Knowledge/ 目录有内容** — KnowledgeStore 做 full delta-sync（content_hash based），首次约 30-60s
- **DDD 4-doc scaffold** — 项目创建时自动生成 template，人类填入 minimal context 即可

实践中 cold start 不是问题 — Agent 第一天就能工作（有 SOUL + AGENT + USER），只是没有历史记忆。3-5 个 session 后 MEMORY.md 开始积累，一周后基本到达有效工作状态。关键 insight：**Agent 不需要"所有知识"才能工作。它需要的是渐进式 competence 积累。**

一个粗略的 maturity timeline（基于我们 2+ 个月的观察）：

| Phase | Timeline | 表现 | 关键里程碑 |
|-------|----------|------|-----------|
| Stateless | Day 1-2 | ~60% baseline，所有决策从零开始 | 第一个 DailyActivity 文件生成 |
| Forming | Day 3-7 | ~75%，开始有历史 context | MEMORY.md 首次有内容，L2 index 有 entries |
| Evolving | Day 8-30 | ~85-90%，第一次 correction 被捕获 | 第一条 rule 升级，Agent 开始 "记住教训" |
| Trusted | Day 31+ | 90%+，主动提醒用户 "上次类似情况我们犯过错" | 自我纠正发生在用户指出之前 |

这个 timeline 对管理用户预期有用 — "为什么我的新 Agent 第一周不如预期" 是一个 legitimate question，答案是 competence 需要时间积累，就像人类同事的 onboarding 一样。

---

## Related Design Documents

- [Memory Architecture v2](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-04-01-memory-architecture-v2.md) — Brain + Library + Transcript + Evolution
- [Hybrid Memory Retrieval](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-04-01-hybrid-memory-retrieval-design.md) — sqlite-vec + Keyword + Transcript
- [Progressive Memory Disclosure](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-03-31-progressive-memory-disclosure-spec.md) — 三级渐进加载
- [Session Resume Enrichment](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-05-02-session-resume-enrichment-design.md) — 从 3K 到 100K 结构化恢复
- [Agent Self-Evolution Governance](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-05-19-agent-self-evolution-governance-design.md) — Three-layer model
- [DDD Cultivation v2](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-05-16-ddd-cultivation-v2-design.md) — Event-driven knowledge growth
- [Knowledge Lifecycle](https://github.com/xg-gh-25/SwarmAI/blob/main/Knowledge/Designs/2026-05-19-knowledge-lifecycle-design.md) — Per-entry reference tracking + decay

---

## Related Articles

- [Your AI Agent Doesn't Need More Rules](https://github.com/xg-gh-25/SwarmAI/discussions/29) — Three-Layer Governance
- [AI Agent 不需要 Neo4j — 知识管理的达尔文主义](https://github.com/xg-gh-25/SwarmAI/discussions/30) — Darwinian lifecycle

---

_Architecture running in production for 2+ months. 32 behavioral corrections captured and structurally resolved. Memory system serving 3 concurrent chat sessions + 1 Slack channel + scheduled jobs, 24/7._


