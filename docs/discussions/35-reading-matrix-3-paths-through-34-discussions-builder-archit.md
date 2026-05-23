# Reading Matrix — 3 Paths Through 43 Discussions (Builder / Architect / Leader)

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/35) | Category: General | Published: 2026-05-21 | Updated: 2026-05-23

---

> 43 articles, 5 themes, 3 reader paths. Don't read everything. Pick your path.

**Base URL:** `https://github.com/xg-gh-25/SwarmAI/discussions/`

## Start Here (All Paths)

Before diving into any path below, read the Design Philosophy. It explains the **"why"** behind everything else — in ~10 minutes you'll understand the six pillars that drive every architectural choice.

| Article | What you'll get | ~Min |
|---------|----------------|:----:|
| [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38) (EN) | The 6 core beliefs, how they earned from failures, why they compound | 10 |
| [设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39) (CN) | 同上中文版 | 10 |

Then pick your path ↓

---

## Three Reader Paths

| Path | You are... | Start here | Time |
|------|-----------|------------|------|
| **[Builder](#path-a-builder)** | Using Claude Code / Cursor / Kiro daily, want to level up | Path A (bottom-up) | ~45 min |
| **[Architect](#path-b-architect)** | Designing agent systems, evaluating frameworks | Path B (top-down) | ~60 min |
| **[Leader](#path-c-leader)** | Making buy/build/invest decisions about AI tools | Path C (strategic) | ~30 min |

---

## Path A: Builder

**"I use agents daily, how do I get more from them?"**

| Step | Article | Why read this | ~Min |
|:----:|---------|--------------|:----:|
| 1 | [What Is an Agent Harness? (Autonomy Levels L1-L5)](https://github.com/xg-gh-25/SwarmAI/discussions/33) | "Oh — my terminal Claude Code is L3, that's why it forgets things" | 8 |
| 2 | [没有记忆就没有理解 — Memory as the only durable moat](https://github.com/xg-gh-25/SwarmAI/discussions/3) | Memory is what separates L3 from L4 | 6 |
| 3 | [Agent Memory Architecture — 4-Layer Progressive System](https://github.com/xg-gh-25/SwarmAI/discussions/37) | How memory actually works: Context Dir → Progressive Index → FTS5 Recall → Knowledge RAG | 10 |
| 4 | [The Six Self-X Properties — what makes agents grow](https://github.com/xg-gh-25/SwarmAI/discussions/8) | Self-heal, self-learn, self-improve... the L4→L5 journey | 7 |
| 5 | [Coding as Black Box — one requirement in, delivery out](https://github.com/xg-gh-25/SwarmAI/discussions/4) | This is how L4 execution works in practice | 6 |
| 6 | [Adversarial Review — multi-specialist code review](https://github.com/xg-gh-25/SwarmAI/discussions/29) | L4 without quality gates = ships bugs autonomously | 8 |
| 7 | [Show your setup — what does context look like on disk?](https://github.com/xg-gh-25/SwarmAI/discussions/18) | See a real implementation | 5 |

**Optional deep dives:**
- ["Dreaming" Is Just Note-Taking — The Real Evolution Happens Elsewhere](https://github.com/xg-gh-25/SwarmAI/discussions/45) (if you've heard about Claude Code's "dreaming" and wonder if it's magic — spoiler: it's L0 note-taking with an architectural gap)
- [DDD Cultivation — domain knowledge that grows from work](https://github.com/xg-gh-25/SwarmAI/discussions/9) (intro) → [DDD Full Story — decisions, failures, evidence](https://github.com/xg-gh-25/SwarmAI/discussions/40) (deep dive) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/41)
- [Time Symmetry — session boundaries as learning boundaries](https://github.com/xg-gh-25/SwarmAI/discussions/14) (if you want smarter session lifecycle)
- [Memory Architecture Poll — what's your pattern?](https://github.com/xg-gh-25/SwarmAI/discussions/17) (if you're choosing an approach)

**After this path, you can:**
- Evaluate any AI coding tool by its autonomy level (L1-L5)
- Design a persistent memory layer for your agent (not just in-context)
- Set up adversarial review gates so your agent doesn't ship bugs autonomously
- Articulate why your CLI tool "forgets" — and what architectural layer is missing
- Explain why "dreaming" (background note-taking) is necessary but insufficient for agent learning

---

## Path B: Architect

**"I'm designing/evaluating agent frameworks"**

| Step | Article | Why read this | ~Min |
|:----:|---------|--------------|:----:|
| 1 | [Agent Harness Landscape — different questions, not better answers](https://github.com/xg-gh-25/SwarmAI/discussions/15) | The market map — who's solving what | 7 |
| 2 | [Agent Harness Autonomy Levels (L1-L5)](https://github.com/xg-gh-25/SwarmAI/discussions/33) | Where each product sits, why thickness matters | 8 |
| 3 | [Multi-Skill > Multi-Agent — why we chose role-switching](https://github.com/xg-gh-25/SwarmAI/discussions/12) | Architecture decision: coordination tax vs context preservation | 7 |
| 3b | [Why We Chose Single-Agent (and multi-agent frameworks prove us right)](https://github.com/xg-gh-25/SwarmAI/discussions/43) | CrewAI/LangGraph validation — even "multi-agent" converges to single-process | 8 |
| 4 | [Compound Agent Intelligence — design beliefs as compiler guarantees](https://github.com/xg-gh-25/SwarmAI/discussions/10) | How to make 1+1+1+1 > 4 architecturally | 8 |
| 5 | [Three-Layer Governance — principles, rules, gates](https://github.com/xg-gh-25/SwarmAI/discussions/26) | Keep L4+ agents under control without micromanaging | 8 |
| 6 | [The Personality Trap — opinionated agents break compliance](https://github.com/xg-gh-25/SwarmAI/discussions/31) | The governance failure mode nobody talks about | 7 |
| 7 | [Adversarial Review — mechanical quality gates](https://github.com/xg-gh-25/SwarmAI/discussions/29) | Trust but verify, structurally | 8 |

**Optional deep dives:**
- ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) (if you're designing agent self-improvement — why L0 memory consolidation plateaus and what L1-L3 looks like)
- [Agent Memory Architecture — 4-Layer Progressive System](https://github.com/xg-gh-25/SwarmAI/discussions/37) (if you're designing the memory subsystem — FTS5, vectors, distillation)
- [AI Agent for Data — from hallucination to precision](https://github.com/xg-gh-25/SwarmAI/discussions/36) (if you're connecting agents to business data — semantic contracts, certified queries)
- [DDD Cultivation Full Story — decisions, failures, evidence](https://github.com/xg-gh-25/SwarmAI/discussions/40) (if you're building domain-aware agents — 3-layer engine, 7 channels, 4 real failures) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/41)
- [No Neo4j — Lightweight Ontology in Practice](https://github.com/xg-gh-25/SwarmAI/discussions/20) (if you're choosing knowledge representation)
- [Content as Black Box — message in, media out](https://github.com/xg-gh-25/SwarmAI/discussions/5) (if you want multi-engine delivery)
- [Time Symmetry — session = learning boundary](https://github.com/xg-gh-25/SwarmAI/discussions/14) (if you're designing session lifecycle)

**After this path, you can:**
- Design a harness architecture that separates engine from control plane
- Choose between multi-agent vs multi-skill (and defend the decision with tradeoffs)
- Implement a 3-layer governance model (principles → rules → mechanical gates)
- Design a 4-layer memory system (injection → progressive → FTS5 recall → RAG)
- Spot the compliance failure mode before it ships (personality trap)

---

## Path C: Leader

**"Should we invest in this? What matters?"**

| Step | Article | Why read this | ~Min |
|:----:|---------|--------------|:----:|
| 1 | [Agent Harness Autonomy Levels (self-driving car analogy)](https://github.com/xg-gh-25/SwarmAI/discussions/33) | Now I understand what I'm buying/building | 8 |
| 2 | [Flat vs Compound — is your AI tool's value curve going up?](https://github.com/xg-gh-25/SwarmAI/discussions/13) | The evaluation framework: does it get better with use? | 6 |
| 3 | [越用越聪明 — How AI systems compound (and why most don't)](https://github.com/xg-gh-25/SwarmAI/discussions/7) | Why 90% of AI tools hit a ceiling | 5 |
| 4 | [S×T Tension Matrix — friction = mismatch, not tool quality](https://github.com/xg-gh-25/SwarmAI/discussions/11) | Why adoption fails even with good tools | 6 |
| 5 | [Four Startup Stages — same stages, different bottlenecks](https://github.com/xg-gh-25/SwarmAI/discussions/16) | Where AI changes the game at each phase | 5 |
| 6 | [Same Pattern at Every Scale — AI replaces routing, not people](https://github.com/xg-gh-25/SwarmAI/discussions/28) | The org design implication | 5 |

**Optional:**
- [AI Agent for Data — from hallucination to precision](https://github.com/xg-gh-25/SwarmAI/discussions/36) (if your team is connecting AI to business data — the accuracy/security/scalability tradeoffs)
- [Agent 当人来培养 — Cultivation over Configuration](https://github.com/xg-gh-25/SwarmAI/discussions/6) (if you manage AI-assisted teams)
- [没有记忆就没有理解 — Memory as moat](https://github.com/xg-gh-25/SwarmAI/discussions/3) (if you're evaluating vendor lock-in)

**After this path, you can:**
- Evaluate any AI tool by asking "is its value curve flat or compound?"
- Diagnose adoption friction using the S×T matrix (is it a supply problem or a tool problem?)
- Make a build-vs-buy decision on agent infrastructure with clear criteria
- Explain to your team why "AI replaces routing, not people" — and what that means for org design

---

## Full Topic Map (by Theme)

**Prereqs legend:** `→` = required (read first or you'll be lost) · `~` = recommended (deepens understanding but not blocking)

### Theme 1: Foundations — "What are agents, really?"

| # | Title | Lang | Key Idea | Prereqs |
|---|-------|:----:|----------|---------|
| [38](https://github.com/xg-gh-25/SwarmAI/discussions/38) | [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38) | EN | Prevention, Verification, Knowledge, Correction, Temporal, Ownership | None (START HERE) |
| [39](https://github.com/xg-gh-25/SwarmAI/discussions/39) | [设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39) | CN | 同上中文版 | None (START HERE) |
| [33](https://github.com/xg-gh-25/SwarmAI/discussions/33) | [Agent Harness Autonomy Levels](https://github.com/xg-gh-25/SwarmAI/discussions/33) | EN | Harness = control plane, levels = thickness | None |
| [34](https://github.com/xg-gh-25/SwarmAI/discussions/34) | [Agent Harness 自治五级](https://github.com/xg-gh-25/SwarmAI/discussions/34) | CN | 同上中文版 | None |
| [3](https://github.com/xg-gh-25/SwarmAI/discussions/3) | [没有记忆就没有理解](https://github.com/xg-gh-25/SwarmAI/discussions/3) | Mix | Memory is the only durable moat | None |
| [7](https://github.com/xg-gh-25/SwarmAI/discussions/7) | [越用越聪明](https://github.com/xg-gh-25/SwarmAI/discussions/7) | Mix | Compound > capable — value curves matter | None |
| [6](https://github.com/xg-gh-25/SwarmAI/discussions/6) | [Agent 当人来培养](https://github.com/xg-gh-25/SwarmAI/discussions/6) | Mix | Cultivation > configuration | None |
| [13](https://github.com/xg-gh-25/SwarmAI/discussions/13) | [Flat vs Compound](https://github.com/xg-gh-25/SwarmAI/discussions/13) | EN | Shape of value curves as evaluation tool | → [#7](https://github.com/xg-gh-25/SwarmAI/discussions/7) |

### Theme 2: Architecture — "How do you build one?"

| # | Title | Lang | Key Idea | Prereqs |
|---|-------|:----:|----------|---------|
| [15](https://github.com/xg-gh-25/SwarmAI/discussions/15) | [Agent Harness Landscape](https://github.com/xg-gh-25/SwarmAI/discussions/15) | EN | Market map, different Qs not better As | → [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [12](https://github.com/xg-gh-25/SwarmAI/discussions/12) | [Multi-Skill > Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/12) | EN | Role-switching beats coordination tax | ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [10](https://github.com/xg-gh-25/SwarmAI/discussions/10) | [Compound Agent Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/10) | EN | Design beliefs as compiler guarantees | → [#7](https://github.com/xg-gh-25/SwarmAI/discussions/7), ~ [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8) |
| [8](https://github.com/xg-gh-25/SwarmAI/discussions/8) | [Six Self-X Properties](https://github.com/xg-gh-25/SwarmAI/discussions/8) | EN | Self-heal/learn/improve/observe/evolve/compound | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3) |
| [9](https://github.com/xg-gh-25/SwarmAI/discussions/9) | [DDD Cultivation](https://github.com/xg-gh-25/SwarmAI/discussions/9) | EN | Domain knowledge that grows from work | → [#6](https://github.com/xg-gh-25/SwarmAI/discussions/6) |
| [14](https://github.com/xg-gh-25/SwarmAI/discussions/14) | [Time Symmetry](https://github.com/xg-gh-25/SwarmAI/discussions/14) | EN | Session = learning boundary, not idle boundary | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8) |
| [4](https://github.com/xg-gh-25/SwarmAI/discussions/4) | [Coding as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/4) | EN | Requirement → verified delivery, no hand-holding | ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [5](https://github.com/xg-gh-25/SwarmAI/discussions/5) | [Content as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/5) | EN | Message → published media, format follows | → [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4) |
| [20](https://github.com/xg-gh-25/SwarmAI/discussions/20) | [No Neo4j — Lightweight Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/20) | EN | Darwinian knowledge > graph database | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [19](https://github.com/xg-gh-25/SwarmAI/discussions/19) | [AI Agent 不需要 Neo4j](https://github.com/xg-gh-25/SwarmAI/discussions/19) | CN | 同上中文版 | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [36](https://github.com/xg-gh-25/SwarmAI/discussions/36) | [AI Agent for Data — 从幻觉到精准](https://github.com/xg-gh-25/SwarmAI/discussions/36) | Mix | Semantic contracts + certified queries for data agents | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [37](https://github.com/xg-gh-25/SwarmAI/discussions/37) | [Agent Memory Architecture — 4-Layer Progressive](https://github.com/xg-gh-25/SwarmAI/discussions/37) | EN | Full memory implementation: injection → progressive → FTS5 → RAG | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#20](https://github.com/xg-gh-25/SwarmAI/discussions/20) |
| [40](https://github.com/xg-gh-25/SwarmAI/discussions/40) | [DDD Cultivation — Full Story](https://github.com/xg-gh-25/SwarmAI/discussions/40) | EN | 3-layer engine, 7 channels, 4 failures, real metrics | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#20](https://github.com/xg-gh-25/SwarmAI/discussions/20) |
| [41](https://github.com/xg-gh-25/SwarmAI/discussions/41) | [DDD Cultivation — 完整故事](https://github.com/xg-gh-25/SwarmAI/discussions/41) | CN | 同上中文版 | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#19](https://github.com/xg-gh-25/SwarmAI/discussions/19) |
| [43](https://github.com/xg-gh-25/SwarmAI/discussions/43) | [Why We Chose Single-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) | EN | CrewAI/LangGraph validate single-process | → [#12](https://github.com/xg-gh-25/SwarmAI/discussions/12) |
| [45](https://github.com/xg-gh-25/SwarmAI/discussions/45) | ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) | EN | L0 memory ≠ evolution, L1-L3 is where judgment improves | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |

### Theme 3: Governance — "How do you keep it safe?"

| # | Title | Lang | Key Idea | Prereqs |
|---|-------|:----:|----------|---------|
| [26](https://github.com/xg-gh-25/SwarmAI/discussions/26) | [Three-Layer Governance](https://github.com/xg-gh-25/SwarmAI/discussions/26) | EN | Principles → rules → gates (mechanical) | → [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8), ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [27](https://github.com/xg-gh-25/SwarmAI/discussions/27) | [三层治理模型](https://github.com/xg-gh-25/SwarmAI/discussions/27) | CN | 同上中文版 | → [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8), ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [22](https://github.com/xg-gh-25/SwarmAI/discussions/22) | [Three-Layer Governance (long)](https://github.com/xg-gh-25/SwarmAI/discussions/22) | EN | Full academic treatment with cognitive evolution | → [#26](https://github.com/xg-gh-25/SwarmAI/discussions/26) |
| [21](https://github.com/xg-gh-25/SwarmAI/discussions/21) | [三层治理与认知进化（完整版）](https://github.com/xg-gh-25/SwarmAI/discussions/21) | CN | 同上中文版 | → [#27](https://github.com/xg-gh-25/SwarmAI/discussions/27) |
| [31](https://github.com/xg-gh-25/SwarmAI/discussions/31) | [The Personality Trap](https://github.com/xg-gh-25/SwarmAI/discussions/31) | EN | Identity + rules = compliance paradox | → [#26](https://github.com/xg-gh-25/SwarmAI/discussions/26) |
| [32](https://github.com/xg-gh-25/SwarmAI/discussions/32) | [人格陷阱](https://github.com/xg-gh-25/SwarmAI/discussions/32) | CN | 同上中文版 | → [#27](https://github.com/xg-gh-25/SwarmAI/discussions/27) |
| [29](https://github.com/xg-gh-25/SwarmAI/discussions/29) | [Adversarial Review](https://github.com/xg-gh-25/SwarmAI/discussions/29) | EN | Multi-specialist review as mechanical gate | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4) |
| [30](https://github.com/xg-gh-25/SwarmAI/discussions/30) | [多专家对抗性审查](https://github.com/xg-gh-25/SwarmAI/discussions/30) | CN | 同上中文版 | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4) |

### Theme 4: Strategy — "Why does this matter?"

| # | Title | Lang | Key Idea | Prereqs |
|---|-------|:----:|----------|---------|
| [11](https://github.com/xg-gh-25/SwarmAI/discussions/11) | [S×T Tension Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/11) | EN | Friction = Supply × Tool mismatch, not tool quality | None |
| [16](https://github.com/xg-gh-25/SwarmAI/discussions/16) | [Four Startup Stages](https://github.com/xg-gh-25/SwarmAI/discussions/16) | EN | Same stages, AI shifts the bottleneck | None |
| [28](https://github.com/xg-gh-25/SwarmAI/discussions/28) | [Same Pattern at Every Scale](https://github.com/xg-gh-25/SwarmAI/discussions/28) | EN | AI replaces routing, not people | ~ [#11](https://github.com/xg-gh-25/SwarmAI/discussions/11) |

### Theme 5: Community / Interactive

| # | Title | Lang | Type | Prereqs |
|---|-------|:----:|------|---------|
| [2](https://github.com/xg-gh-25/SwarmAI/discussions/2) | [Welcome — What is this gallery?](https://github.com/xg-gh-25/SwarmAI/discussions/2) | EN | Announcement | None (START) |
| [17](https://github.com/xg-gh-25/SwarmAI/discussions/17) | [Memory Architecture Poll](https://github.com/xg-gh-25/SwarmAI/discussions/17) | EN | Q&A | [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3) |
| [18](https://github.com/xg-gh-25/SwarmAI/discussions/18) | [Show Your Setup](https://github.com/xg-gh-25/SwarmAI/discussions/18) | EN | Show & tell | Any |
| [42](https://github.com/xg-gh-25/SwarmAI/discussions/42) | [100 ⭐ — Thank You (and What's Next)](https://github.com/xg-gh-25/SwarmAI/discussions/42) | EN | Announcement | None |

---

## Dependency Graph (visual)

```
                    ┌──────────────────────┐
                    │  #33/34 Harness      │  ← BEST ENTRY POINT
                    │  Autonomy Levels     │
                    └────────┬─────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌─────────────┐  ┌────────────┐  ┌──────────┐
    │ #3 Memory   │  │ #15 Harness│  │ #13 Value│
    │ as Moat     │  │ Landscape  │  │ Curves   │
    └──────┬──────┘  └─────┬──────┘  └────┬─────┘
           │               │               │
     ┌─────┴─────┐   ┌────┴────┐    ┌─────┴─────┐
     ▼           ▼   ▼         ▼    ▼           ▼
  ┌──────┐  ┌──────┐ ┌──────┐ ┌──────┐  ┌──────────┐
  │#8 6X │  │#9 DDD│ │#12   │ │#10   │  │#7 Compound│
  │Props │  │Cultiv│ │Multi │ │Compnd│  │Intelligence│
  └──┬───┘  └──────┘ │Skill │ │Intel │  └───────────┘
     │               └──────┘ └──────┘
     ▼
  ┌──────────────────────────┐
  │ #26/27 Three-Layer Gov   │
  └──────────┬───────────────┘
             │
      ┌──────┼──────┐
      ▼      ▼      ▼
   ┌──────┐┌──────┐┌──────┐
   │#31/32││#29/30││#21/22│
   │Person││Adver ││Gov   │
   │Trap  ││Review││(long)│
   └──────┘└──────┘└──────┘
```

---

## Quick Reference: All Discussions

| # | Title | Theme | Lang |
|---|-------|:-----:|:----:|
| [2](https://github.com/xg-gh-25/SwarmAI/discussions/2) | [Welcome — What is this gallery?](https://github.com/xg-gh-25/SwarmAI/discussions/2) | Community | EN |
| [3](https://github.com/xg-gh-25/SwarmAI/discussions/3) | [没有记忆就没有理解 — Memory as moat](https://github.com/xg-gh-25/SwarmAI/discussions/3) | Foundations | Mix |
| [4](https://github.com/xg-gh-25/SwarmAI/discussions/4) | [Coding as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/4) | Architecture | EN |
| [5](https://github.com/xg-gh-25/SwarmAI/discussions/5) | [Content as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/5) | Architecture | EN |
| [6](https://github.com/xg-gh-25/SwarmAI/discussions/6) | [Agent 当人来培养 — Cultivation > Config](https://github.com/xg-gh-25/SwarmAI/discussions/6) | Foundations | Mix |
| [7](https://github.com/xg-gh-25/SwarmAI/discussions/7) | [越用越聪明 — Compound Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/7) | Foundations | Mix |
| [8](https://github.com/xg-gh-25/SwarmAI/discussions/8) | [The Six Self-X Properties](https://github.com/xg-gh-25/SwarmAI/discussions/8) | Architecture | EN |
| [9](https://github.com/xg-gh-25/SwarmAI/discussions/9) | [DDD Cultivation](https://github.com/xg-gh-25/SwarmAI/discussions/9) | Architecture | EN |
| [10](https://github.com/xg-gh-25/SwarmAI/discussions/10) | [Compound Agent Intelligence — 1+1+1+1 > 4](https://github.com/xg-gh-25/SwarmAI/discussions/10) | Architecture | EN |
| [11](https://github.com/xg-gh-25/SwarmAI/discussions/11) | [S×T Tension Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/11) | Strategy | EN |
| [12](https://github.com/xg-gh-25/SwarmAI/discussions/12) | [Multi-Skill > Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/12) | Architecture | EN |
| [13](https://github.com/xg-gh-25/SwarmAI/discussions/13) | [Flat vs Compound — value curves](https://github.com/xg-gh-25/SwarmAI/discussions/13) | Foundations | EN |
| [14](https://github.com/xg-gh-25/SwarmAI/discussions/14) | [Time Symmetry — learning boundaries](https://github.com/xg-gh-25/SwarmAI/discussions/14) | Architecture | EN |
| [15](https://github.com/xg-gh-25/SwarmAI/discussions/15) | [Agent Harness Landscape](https://github.com/xg-gh-25/SwarmAI/discussions/15) | Architecture | EN |
| [16](https://github.com/xg-gh-25/SwarmAI/discussions/16) | [Four Startup Stages](https://github.com/xg-gh-25/SwarmAI/discussions/16) | Strategy | EN |
| [17](https://github.com/xg-gh-25/SwarmAI/discussions/17) | [Memory Architecture Poll (A/B/C/D/E)](https://github.com/xg-gh-25/SwarmAI/discussions/17) | Community | EN |
| [18](https://github.com/xg-gh-25/SwarmAI/discussions/18) | [Show your setup](https://github.com/xg-gh-25/SwarmAI/discussions/18) | Community | EN |
| [19](https://github.com/xg-gh-25/SwarmAI/discussions/19) | [AI Agent 不需要 Neo4j](https://github.com/xg-gh-25/SwarmAI/discussions/19) | Architecture | CN |
| [20](https://github.com/xg-gh-25/SwarmAI/discussions/20) | [No Neo4j — Lightweight Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/20) | Architecture | EN |
| [21](https://github.com/xg-gh-25/SwarmAI/discussions/21) | [三层治理与认知进化（完整版）](https://github.com/xg-gh-25/SwarmAI/discussions/21) | Governance | CN |
| [22](https://github.com/xg-gh-25/SwarmAI/discussions/22) | [Three-Layer Governance (long version)](https://github.com/xg-gh-25/SwarmAI/discussions/22) | Governance | EN |
| [26](https://github.com/xg-gh-25/SwarmAI/discussions/26) | [Three-Layer Governance (compact)](https://github.com/xg-gh-25/SwarmAI/discussions/26) | Governance | EN |
| [27](https://github.com/xg-gh-25/SwarmAI/discussions/27) | [三层治理模型（简版）](https://github.com/xg-gh-25/SwarmAI/discussions/27) | Governance | CN |
| [28](https://github.com/xg-gh-25/SwarmAI/discussions/28) | [Same Pattern at Every Scale](https://github.com/xg-gh-25/SwarmAI/discussions/28) | Strategy | EN |
| [29](https://github.com/xg-gh-25/SwarmAI/discussions/29) | [Adversarial Review System](https://github.com/xg-gh-25/SwarmAI/discussions/29) | Governance | EN |
| [30](https://github.com/xg-gh-25/SwarmAI/discussions/30) | [多专家对抗性审查系统](https://github.com/xg-gh-25/SwarmAI/discussions/30) | Governance | CN |
| [31](https://github.com/xg-gh-25/SwarmAI/discussions/31) | [The Personality Trap](https://github.com/xg-gh-25/SwarmAI/discussions/31) | Governance | EN |
| [32](https://github.com/xg-gh-25/SwarmAI/discussions/32) | [人格陷阱](https://github.com/xg-gh-25/SwarmAI/discussions/32) | Governance | CN |
| [33](https://github.com/xg-gh-25/SwarmAI/discussions/33) | [Agent Harness Autonomy Levels (L1-L5)](https://github.com/xg-gh-25/SwarmAI/discussions/33) | Foundations | EN |
| [34](https://github.com/xg-gh-25/SwarmAI/discussions/34) | [Agent Harness 自治五级](https://github.com/xg-gh-25/SwarmAI/discussions/34) | Foundations | CN |
| [36](https://github.com/xg-gh-25/SwarmAI/discussions/36) | [AI Agent for Data — 从幻觉到精准](https://github.com/xg-gh-25/SwarmAI/discussions/36) | Architecture | Mix |
| [37](https://github.com/xg-gh-25/SwarmAI/discussions/37) | [Agent Memory Architecture — 4-Layer Progressive](https://github.com/xg-gh-25/SwarmAI/discussions/37) | Architecture | EN |
| [38](https://github.com/xg-gh-25/SwarmAI/discussions/38) | [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38) | Foundations | EN |
| [39](https://github.com/xg-gh-25/SwarmAI/discussions/39) | [设计哲学 — 六根支柱](https://github.com/xg-gh-25/SwarmAI/discussions/39) | Foundations | CN |
| [40](https://github.com/xg-gh-25/SwarmAI/discussions/40) | [DDD Cultivation — Full Story](https://github.com/xg-gh-25/SwarmAI/discussions/40) | Architecture | EN |
| [41](https://github.com/xg-gh-25/SwarmAI/discussions/41) | [DDD Cultivation — 完整故事](https://github.com/xg-gh-25/SwarmAI/discussions/41) | Architecture | CN |
| [42](https://github.com/xg-gh-25/SwarmAI/discussions/42) | [100 ⭐ — Thank You (and What's Next)](https://github.com/xg-gh-25/SwarmAI/discussions/42) | Community | EN |
| [43](https://github.com/xg-gh-25/SwarmAI/discussions/43) | [Why We Chose Single-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) | Architecture | EN |
| [45](https://github.com/xg-gh-25/SwarmAI/discussions/45) | ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) | Architecture | EN |

---

## Quick Stats

- **Total discussions:** 43 (including Welcome + Reading Matrix)
- **Bilingual pairs (EN+CN):** 8 pairs (16 discussions)
- **English only:** 21
- **Mixed/bilingual single:** 6
- **Themes:** Foundations (8), Architecture (16), Governance (8), Strategy (3), Community (4)
- **Avg reading time per article:** ~5-8 min
- **Path A total:** ~50 min | **Path B:** ~65 min | **Path C:** ~35 min


