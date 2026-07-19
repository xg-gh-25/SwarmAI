---
title: "Reading Matrix — 3 Paths Through 75 Discussions (Builder / Architect / Leader)"
created: 2026-06-20
updated: 2026-07-18
status: published
---
<!-- GitHub Discussion #35: https://github.com/xg-gh-25/SwarmAI/discussions/35 -->

---

> 77 discussions, 5 themes, 3 reader paths. Don't read everything. Pick your path.

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
- [The Hidden Defaults That Break Your AI Agent — Claude Code CLI's Undocumented Limits](https://github.com/xg-gh-25/SwarmAI/discussions/56) (if you're building on Claude Code SDK — the undocumented `maxTurns=100` and `task_budget=128K` that silently break autonomous pipelines)
- [From Zero to Ship in One Session — Building an AI-Ready Engine](https://github.com/xg-gh-25/SwarmAI/discussions/55) (if you want to see "Coding as Black Box" in action — one sentence to shipped CLI tool in 4 hours)
- [Your AI Agent Can't "Just Read" a 500K-Line Codebase](https://github.com/xg-gh-25/SwarmAI/discussions/49) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/50) (if your agent is "understanding" code by reading README only)
- [AI-Ready Repo Standard — Why CLAUDE.md Is Not Enough](https://github.com/xg-gh-25/SwarmAI/discussions/51) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/52) (if you want to make your codebase genuinely AI-understood)
- [Agent Harness 避坑指南](https://github.com/xg-gh-25/SwarmAI/discussions/54) (CN — if you're building a harness and want to avoid the top pitfalls)
- [Autonomous Pipeline v3 — Dual-Mode Execution](https://github.com/xg-gh-25/SwarmAI/discussions/68) (if you want to see how "Coding as Black Box" evolved into a dual-mode pipeline architecture)
- ["Dreaming" Is Just Note-Taking — The Real Evolution Happens Elsewhere](https://github.com/xg-gh-25/SwarmAI/discussions/45) (if you've heard about Claude Code's "dreaming" and wonder if it's magic — spoiler: it's L0 note-taking with an architectural gap)
- [DDD Cultivation — domain knowledge that grows from work](https://github.com/xg-gh-25/SwarmAI/discussions/9) (intro) → [DDD Full Story — decisions, failures, evidence](https://github.com/xg-gh-25/SwarmAI/discussions/40) (deep dive) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/41)
- [DDD Knowledge Governance — Practical Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/59) (if you want the code-level implementation of DDD governance with 7-type MECE ontology)
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
- [The Three Cracks in Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/44) (if you're still debating multi-agent — the 3 structural failure modes that more agents = worse judgment)
- [The Hidden Defaults That Break Your AI Agent](https://github.com/xg-gh-25/SwarmAI/discussions/56) (if you're embedding Claude Code as SDK — maxTurns, task_budget, autoCompact pitfalls)
- [From Zero to Ship in One Session](https://github.com/xg-gh-25/SwarmAI/discussions/55) (live case study: DDD + pipeline + adversarial review delivers a CLI tool from one sentence)
- [Autonomous Pipeline v3 — Dual-Mode Execution](https://github.com/xg-gh-25/SwarmAI/discussions/68) (the pipeline architecture: AIDLC 10-stage + dual-mode + mechanical gates)
- [Your AI Agent Can't "Just Read" a 500K-Line Codebase](https://github.com/xg-gh-25/SwarmAI/discussions/49) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/50) (if you're solving code understanding at scale — graph-based vs RAG vs hybrid)
- [AI-Ready Repo Standard](https://github.com/xg-gh-25/SwarmAI/discussions/51) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/52) (the standard for making any codebase genuinely AI-understood)
- [DDD Knowledge Governance — Practical Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/59) (7-type MECE ontology + Darwinian decay + stage injection — with code samples)
- [Skill Portability is a Distribution Problem](https://github.com/xg-gh-25/SwarmAI/discussions/61) (if you're thinking about skill/plugin ecosystems — why packaging isn't the hard part)
- [OS Eval vs AgentCore Eval — Proprioception vs Diagnostic Imaging](https://github.com/xg-gh-25/SwarmAI/discussions/74) (agent self-evaluation architecture comparison: internal proprioception vs external testing)
- [Karpathy's LLM Wiki Is a Manifesto for What We Already Built](https://github.com/xg-gh-25/SwarmAI/discussions/53) (if you're designing persistent knowledge — why Karpathy's vision maps 1:1 to DDD)
- ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) (if you're designing agent self-improvement — why L0 memory consolidation plateaus and what L1-L3 looks like)
- [Agent Memory Architecture — 4-Layer Progressive System](https://github.com/xg-gh-25/SwarmAI/discussions/37) (if you're designing the memory subsystem — FTS5, vectors, distillation)
- [AI Agent for Data — from hallucination to precision](https://github.com/xg-gh-25/SwarmAI/discussions/36) (if you're connecting agents to business data — semantic contracts, certified queries)
- [DDD Cultivation Full Story — decisions, failures, evidence](https://github.com/xg-gh-25/SwarmAI/discussions/40) (if you're building domain-aware agents — 3-layer engine, 7 channels, 4 real failures) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/41)
- [No Neo4j — Lightweight Ontology in Practice](https://github.com/xg-gh-25/SwarmAI/discussions/20) (if you're choosing knowledge representation)
- [The Ontology that runs SwarmAI's Memory, DDD & Code Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/95) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/96) (if you want the ONE ontology beneath all three subsystems — 🏷️ classification + 🕸️ relations, still no Neo4j)
- [Ontology isn't a knowledge graph — it's a decision layer](https://github.com/xg-gh-25/SwarmAI/discussions/100) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/99) (the *why it's valuable* companion to #95: noun layer = find things, verb layer = make the call — the 4-rung ruler prediction→inference→simulation→decision across the whole OS)
- [Content as Black Box — message in, media out](https://github.com/xg-gh-25/SwarmAI/discussions/5) (if you want multi-engine delivery)
- [Pollinate — Philosophy, Architecture, Super-Powers & Honest Lowlights](https://github.com/xg-gh-25/SwarmAI/discussions/94) (EN) / [中文版](https://github.com/xg-gh-25/SwarmAI/discussions/93) (the content delivery engine in depth: message-first / format-follows-audience, the 8-stage pipeline, plus an honest account of where it falls short)
- [Time Symmetry — session = learning boundary](https://github.com/xg-gh-25/SwarmAI/discussions/14) (if you're designing session lifecycle)
- [Agent Harness 避坑指南](https://github.com/xg-gh-25/SwarmAI/discussions/54) (CN — common harness pitfalls from production experience)

**After this path, you can:**
- Design a harness architecture that separates engine from control plane
- Choose between multi-agent vs multi-skill (and defend the decision with tradeoffs)
- Implement a 3-layer governance model (principles → rules → mechanical gates)
- Design a 4-layer memory system (injection → progressive → FTS5 recall → RAG)
- Spot the compliance failure mode before it ships (personality trap)
- Build an agent self-evaluation system (proprioception vs external diagnostic)

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
| 7 | [Stop Selling, Start Showing — "Live Demo of Yourself" > pitch deck](https://github.com/xg-gh-25/SwarmAI/discussions/46) | How to actually communicate AI transformation to CXOs | 6 |
| 8 | [One Builder + AI = One Team. Here's the Proof.](https://github.com/xg-gh-25/SwarmAI/discussions/47) | The thesis — one person + AI operating at team scale | 6 |

**Optional:**
- [AI Agent for Data — from hallucination to precision](https://github.com/xg-gh-25/SwarmAI/discussions/36) (if your team is connecting AI to business data — the accuracy/security/scalability tradeoffs)
- [Agent 当人来培养 — Cultivation over Configuration](https://github.com/xg-gh-25/SwarmAI/discussions/6) (if you manage AI-assisted teams)
- [没有记忆就没有理解 — Memory as moat](https://github.com/xg-gh-25/SwarmAI/discussions/3) (if you're evaluating vendor lock-in)

**After this path, you can:**
- Evaluate any AI tool by asking "is its value curve flat or compound?"
- Diagnose adoption friction using the S×T matrix (is it a supply problem or a tool problem?)
- Make a build-vs-buy decision on agent infrastructure with clear criteria
- Explain to your team why "AI replaces routing, not people" — and what that means for org design
- Pitch AI transformation to CXOs without a deck — using your own experience as proof
- Articulate the "one builder + AI = one team" thesis with concrete evidence

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
| [44](https://github.com/xg-gh-25/SwarmAI/discussions/44) | [The Three Cracks in Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/44) | EN | More agents = worse judgment (3 structural failure modes) | → [#12](https://github.com/xg-gh-25/SwarmAI/discussions/12), → [#43](https://github.com/xg-gh-25/SwarmAI/discussions/43) |
| [10](https://github.com/xg-gh-25/SwarmAI/discussions/10) | [Compound Agent Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/10) | EN | Design beliefs as compiler guarantees | → [#7](https://github.com/xg-gh-25/SwarmAI/discussions/7), ~ [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8) |
| [8](https://github.com/xg-gh-25/SwarmAI/discussions/8) | [Six Self-X Properties](https://github.com/xg-gh-25/SwarmAI/discussions/8) | EN | Self-heal/learn/improve/observe/evolve/compound | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3) |
| [9](https://github.com/xg-gh-25/SwarmAI/discussions/9) | [DDD Cultivation](https://github.com/xg-gh-25/SwarmAI/discussions/9) | EN | Domain knowledge that grows from work | → [#6](https://github.com/xg-gh-25/SwarmAI/discussions/6) |
| [59](https://github.com/xg-gh-25/SwarmAI/discussions/59) | [DDD Knowledge Governance — Practical Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/59) | EN | 7-type MECE ontology + Darwinian decay + stage injection (with code) | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#40](https://github.com/xg-gh-25/SwarmAI/discussions/40) |
| [14](https://github.com/xg-gh-25/SwarmAI/discussions/14) | [Time Symmetry](https://github.com/xg-gh-25/SwarmAI/discussions/14) | EN | Session = learning boundary, not idle boundary | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8) |
| [4](https://github.com/xg-gh-25/SwarmAI/discussions/4) | [Coding as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/4) | EN | Requirement → verified delivery, no hand-holding | ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| [68](https://github.com/xg-gh-25/SwarmAI/discussions/68) | [Autonomous Pipeline v3 — Dual-Mode Execution](https://github.com/xg-gh-25/SwarmAI/discussions/68) | EN | AIDLC 10-stage + dual-mode + mechanical gates | → [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#29](https://github.com/xg-gh-25/SwarmAI/discussions/29) |
| [5](https://github.com/xg-gh-25/SwarmAI/discussions/5) | [Content as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/5) | EN | Message → published media, format follows | → [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4) |
| [49](https://github.com/xg-gh-25/SwarmAI/discussions/49) | [Your AI Agent Can't "Just Read" a 500K-Line Codebase](https://github.com/xg-gh-25/SwarmAI/discussions/49) | EN | Graph-based code intelligence at scale | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [50](https://github.com/xg-gh-25/SwarmAI/discussions/50) | [你的 AI Agent 读不懂 50 万行代码](https://github.com/xg-gh-25/SwarmAI/discussions/50) | CN | 同上中文版 | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [51](https://github.com/xg-gh-25/SwarmAI/discussions/51) | [AI-Ready Repo Standard — Why CLAUDE.md Is Not Enough](https://github.com/xg-gh-25/SwarmAI/discussions/51) | EN | The open standard for AI-understood codebases | ~ [#49](https://github.com/xg-gh-25/SwarmAI/discussions/49), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [52](https://github.com/xg-gh-25/SwarmAI/discussions/52) | [AI-Ready Repo 标准](https://github.com/xg-gh-25/SwarmAI/discussions/52) | CN | 同上中文版 | ~ [#50](https://github.com/xg-gh-25/SwarmAI/discussions/50), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [53](https://github.com/xg-gh-25/SwarmAI/discussions/53) | [Karpathy's LLM Wiki Is a Manifesto](https://github.com/xg-gh-25/SwarmAI/discussions/53) | EN | Karpathy's vision maps 1:1 to DDD — and the gap we closed | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [61](https://github.com/xg-gh-25/SwarmAI/discussions/61) | [Skill Portability is a Distribution Problem](https://github.com/xg-gh-25/SwarmAI/discussions/61) | EN | Why skill/plugin ecosystems fail at packaging, not capability | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#12](https://github.com/xg-gh-25/SwarmAI/discussions/12) |
| [74](https://github.com/xg-gh-25/SwarmAI/discussions/74) | [OS Eval vs AgentCore Eval — Proprioception vs Diagnostic](https://github.com/xg-gh-25/SwarmAI/discussions/74) | EN | Agent self-evaluation: internal proprioception vs external testing | ~ [#8](https://github.com/xg-gh-25/SwarmAI/discussions/8), ~ [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59) |
| [83](https://github.com/xg-gh-25/SwarmAI/discussions/83) | [After `assert` Breaks Down — Eval Architecture & Methodology](https://github.com/xg-gh-25/SwarmAI/discussions/83) | EN | Why agents need eval not assert; full eval architecture | → [#74](https://github.com/xg-gh-25/SwarmAI/discussions/74) |
| [78](https://github.com/xg-gh-25/SwarmAI/discussions/78) | [当 assert 失效之后 — Eval 架构全景与方法论](https://github.com/xg-gh-25/SwarmAI/discussions/78) | CN | 同 #83 中文版 | → [#74](https://github.com/xg-gh-25/SwarmAI/discussions/74) |
| [90](https://github.com/xg-gh-25/SwarmAI/discussions/90) | [AgentCore Eval-First vs Our Decoupled Eval Subsystem](https://github.com/xg-gh-25/SwarmAI/discussions/90) | EN | Code-level read of THELMA + Mind the Goal vs ours; score→cause→action | → [#74](https://github.com/xg-gh-25/SwarmAI/discussions/74), ~ [#83](https://github.com/xg-gh-25/SwarmAI/discussions/83) |
| [91](https://github.com/xg-gh-25/SwarmAI/discussions/91) | [AgentCore Eval-First vs 我们解耦的 Eval 子系统](https://github.com/xg-gh-25/SwarmAI/discussions/91) | CN | 同 #90 中文版 | → [#74](https://github.com/xg-gh-25/SwarmAI/discussions/74), ~ [#78](https://github.com/xg-gh-25/SwarmAI/discussions/78) |
| [79](https://github.com/xg-gh-25/SwarmAI/discussions/79) | [Recall Architecture — The READ Path](https://github.com/xg-gh-25/SwarmAI/discussions/79) | EN | How recall works: keyword/FTS5 over markdown, no vector DB | → [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [80](https://github.com/xg-gh-25/SwarmAI/discussions/80) | [Recall 架构全景 — READ 路径](https://github.com/xg-gh-25/SwarmAI/discussions/80) | CN | 同 #79 中文版 | → [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [84](https://github.com/xg-gh-25/SwarmAI/discussions/84) | [Context as a Living System — The WRITE Path](https://github.com/xg-gh-25/SwarmAI/discussions/84) | EN | Ingestion → decay → archive; why we deleted the vector DB | → [#79](https://github.com/xg-gh-25/SwarmAI/discussions/79), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [85](https://github.com/xg-gh-25/SwarmAI/discussions/85) | [上下文是一个活系统 — WRITE 路径](https://github.com/xg-gh-25/SwarmAI/discussions/85) | CN | 同 #84 中文版 | → [#80](https://github.com/xg-gh-25/SwarmAI/discussions/80), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [86](https://github.com/xg-gh-25/SwarmAI/discussions/86) | [Quality Convergence — How a Pipeline Knows It's Done](https://github.com/xg-gh-25/SwarmAI/discussions/86) | EN | DoD-driven convergence, not stage-exhaustion | → [#68](https://github.com/xg-gh-25/SwarmAI/discussions/68), ~ [#29](https://github.com/xg-gh-25/SwarmAI/discussions/29) |
| [87](https://github.com/xg-gh-25/SwarmAI/discussions/87) | [质量收敛 — Pipeline 怎么知道做完了](https://github.com/xg-gh-25/SwarmAI/discussions/87) | CN | 同 #86 中文版 | → [#68](https://github.com/xg-gh-25/SwarmAI/discussions/68), ~ [#30](https://github.com/xg-gh-25/SwarmAI/discussions/30) |
| [94](https://github.com/xg-gh-25/SwarmAI/discussions/94) | [Pollinate — Philosophy, Architecture, Super-Powers & Honest Lowlights](https://github.com/xg-gh-25/SwarmAI/discussions/94) | EN | Content delivery engine: message-first / format-follows-audience; the engine, its super-powers, and an honest account of where it falls short | → [#5](https://github.com/xg-gh-25/SwarmAI/discussions/5) |
| [93](https://github.com/xg-gh-25/SwarmAI/discussions/93) | [Pollinate — 哲学、架构、Super-Powers 与诚实的 Lowlights](https://github.com/xg-gh-25/SwarmAI/discussions/93) | CN | 同 #94 中文版 | → [#5](https://github.com/xg-gh-25/SwarmAI/discussions/5) |
| [20](https://github.com/xg-gh-25/SwarmAI/discussions/20) | [No Neo4j — Lightweight Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/20) | EN | Darwinian knowledge > graph database | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [19](https://github.com/xg-gh-25/SwarmAI/discussions/19) | [AI Agent 不需要 Neo4j](https://github.com/xg-gh-25/SwarmAI/discussions/19) | CN | 同上中文版 | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [95](https://github.com/xg-gh-25/SwarmAI/discussions/95) | [The Ontology that runs SwarmAI's Memory, DDD & Code Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/95) | EN | One ontology beneath all three subsystems — 🏷️ classification + 🕸️ relations, no Neo4j | → [#20](https://github.com/xg-gh-25/SwarmAI/discussions/20), ~ [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59), ~ [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [96](https://github.com/xg-gh-25/SwarmAI/discussions/96) | [撑起 SwarmAI 记忆 / DDD / 代码智能的那套 Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/96) | CN | 同 #95 中文版 | → [#19](https://github.com/xg-gh-25/SwarmAI/discussions/19), ~ [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59), ~ [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [100](https://github.com/xg-gh-25/SwarmAI/discussions/100) | [Ontology isn't a knowledge graph — it's a decision layer](https://github.com/xg-gh-25/SwarmAI/discussions/100) | EN | The verb layer: 4-rung ruler (predict→infer→simulate→decide) across the whole OS; why we reach the decision layer | → [#95](https://github.com/xg-gh-25/SwarmAI/discussions/95), ~ [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59), ~ [#36](https://github.com/xg-gh-25/SwarmAI/discussions/36) |
| [99](https://github.com/xg-gh-25/SwarmAI/discussions/99) | [Ontology 不是知识图谱,是决策层](https://github.com/xg-gh-25/SwarmAI/discussions/99) | CN | 同 #100 中文版 | → [#96](https://github.com/xg-gh-25/SwarmAI/discussions/96), ~ [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59), ~ [#36](https://github.com/xg-gh-25/SwarmAI/discussions/36) |
| [98](https://github.com/xg-gh-25/SwarmAI/discussions/98) | [AI Agent for Data 续篇 — L3 契约层 + L2 受约束 NL2SQL](https://github.com/xg-gh-25/SwarmAI/discussions/98) | CN | 把 L3 契约层和 L2 受约束 NL2SQL 真正做出来(带牙齿) | → [#36](https://github.com/xg-gh-25/SwarmAI/discussions/36) |
| [102](https://github.com/xg-gh-25/SwarmAI/discussions/102) | [How SwarmAI's Living Knowledge Compares to Code-Understanding Tools](https://github.com/xg-gh-25/SwarmAI/discussions/102) | EN | Living knowledge vs Graphify / Understand-Anything / Spec-Studio | ~ [#49](https://github.com/xg-gh-25/SwarmAI/discussions/49), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [103](https://github.com/xg-gh-25/SwarmAI/discussions/103) | [SwarmAI 的「活知识」对比几款代码理解工具](https://github.com/xg-gh-25/SwarmAI/discussions/103) | CN | 同 #102 中文版 | ~ [#50](https://github.com/xg-gh-25/SwarmAI/discussions/50), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [36](https://github.com/xg-gh-25/SwarmAI/discussions/36) | [AI Agent for Data — 从幻觉到精准](https://github.com/xg-gh-25/SwarmAI/discussions/36) | Mix | Semantic contracts + certified queries for data agents | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9) |
| [37](https://github.com/xg-gh-25/SwarmAI/discussions/37) | [Agent Memory Architecture — 4-Layer Progressive](https://github.com/xg-gh-25/SwarmAI/discussions/37) | EN | Full memory implementation: injection → progressive → FTS5 → RAG | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#20](https://github.com/xg-gh-25/SwarmAI/discussions/20) |
| [40](https://github.com/xg-gh-25/SwarmAI/discussions/40) | [DDD Cultivation — Full Story](https://github.com/xg-gh-25/SwarmAI/discussions/40) | EN | 3-layer engine, 7 channels, 4 failures, real metrics | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#20](https://github.com/xg-gh-25/SwarmAI/discussions/20) |
| [41](https://github.com/xg-gh-25/SwarmAI/discussions/41) | [DDD Cultivation — 完整故事](https://github.com/xg-gh-25/SwarmAI/discussions/41) | CN | 同上中文版 | → [#9](https://github.com/xg-gh-25/SwarmAI/discussions/9), ~ [#19](https://github.com/xg-gh-25/SwarmAI/discussions/19) |
| [43](https://github.com/xg-gh-25/SwarmAI/discussions/43) | [Why We Chose Single-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) | EN | CrewAI/LangGraph validate single-process | → [#12](https://github.com/xg-gh-25/SwarmAI/discussions/12) |
| [45](https://github.com/xg-gh-25/SwarmAI/discussions/45) | ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) | EN | L0 memory ≠ evolution, L1-L3 is where judgment improves | → [#3](https://github.com/xg-gh-25/SwarmAI/discussions/3), ~ [#37](https://github.com/xg-gh-25/SwarmAI/discussions/37) |
| [54](https://github.com/xg-gh-25/SwarmAI/discussions/54) | [Agent Harness 避坑指南](https://github.com/xg-gh-25/SwarmAI/discussions/54) | CN | Production harness pitfalls and solutions | ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33), ~ [#29](https://github.com/xg-gh-25/SwarmAI/discussions/29) |
| [55](https://github.com/xg-gh-25/SwarmAI/discussions/55) | [From Zero to Ship in One Session](https://github.com/xg-gh-25/SwarmAI/discussions/55) | EN | Live case: DDD + pipeline + adversarial delivers CLI from 1 sentence | → [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#29](https://github.com/xg-gh-25/SwarmAI/discussions/29) |
| [56](https://github.com/xg-gh-25/SwarmAI/discussions/56) | [The Hidden Defaults That Break Your AI Agent](https://github.com/xg-gh-25/SwarmAI/discussions/56) | EN | Claude Code SDK undocumented limits: maxTurns=100, task_budget=128K | ~ [#4](https://github.com/xg-gh-25/SwarmAI/discussions/4), ~ [#33](https://github.com/xg-gh-25/SwarmAI/discussions/33) |

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
| [75](https://github.com/xg-gh-25/SwarmAI/discussions/75) | [Mechanical Gate vs Ceremonial Gate](https://github.com/xg-gh-25/SwarmAI/discussions/75) | EN | Why your agent's approval step is probably theater | → [#26](https://github.com/xg-gh-25/SwarmAI/discussions/26), ~ [#29](https://github.com/xg-gh-25/SwarmAI/discussions/29) |
| [88](https://github.com/xg-gh-25/SwarmAI/discussions/88) | [Cognitive Evolution, Not Skill Tuning](https://github.com/xg-gh-25/SwarmAI/discussions/88) | EN | Fix the eye, not the glasses; prose fails 3× → build a gate | → [#26](https://github.com/xg-gh-25/SwarmAI/discussions/26), ~ [#75](https://github.com/xg-gh-25/SwarmAI/discussions/75) |
| [89](https://github.com/xg-gh-25/SwarmAI/discussions/89) | [认知进化,而非技能调优](https://github.com/xg-gh-25/SwarmAI/discussions/89) | CN | 同 #88 中文版 | → [#27](https://github.com/xg-gh-25/SwarmAI/discussions/27), ~ [#75](https://github.com/xg-gh-25/SwarmAI/discussions/75) |

### Theme 4: Strategy — "Why does this matter?"

| # | Title | Lang | Key Idea | Prereqs |
|---|-------|:----:|----------|---------|
| [11](https://github.com/xg-gh-25/SwarmAI/discussions/11) | [S×T Tension Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/11) | EN | Friction = Supply × Tool mismatch, not tool quality | None |
| [16](https://github.com/xg-gh-25/SwarmAI/discussions/16) | [Four Startup Stages](https://github.com/xg-gh-25/SwarmAI/discussions/16) | EN | Same stages, AI shifts the bottleneck | None |
| [28](https://github.com/xg-gh-25/SwarmAI/discussions/28) | [Same Pattern at Every Scale](https://github.com/xg-gh-25/SwarmAI/discussions/28) | EN | AI replaces routing, not people | ~ [#11](https://github.com/xg-gh-25/SwarmAI/discussions/11) |
| [46](https://github.com/xg-gh-25/SwarmAI/discussions/46) | [Stop Selling, Start Showing](https://github.com/xg-gh-25/SwarmAI/discussions/46) | EN | "Live Demo of Yourself" > pitch deck | ~ [#28](https://github.com/xg-gh-25/SwarmAI/discussions/28) |
| [47](https://github.com/xg-gh-25/SwarmAI/discussions/47) | [One Builder + AI = One Team](https://github.com/xg-gh-25/SwarmAI/discussions/47) | EN | The thesis — proof that one person + AI = team-scale output | ~ [#46](https://github.com/xg-gh-25/SwarmAI/discussions/46) |

### Theme 5: Community / Interactive

| # | Title | Lang | Type | Prereqs |
|---|-------|:----:|------|---------|
| [2](https://github.com/xg-gh-25/SwarmAI/discussions/2) | [Welcome — Start Here (Read Me First)](https://github.com/xg-gh-25/SwarmAI/discussions/2) | EN | Announcement | None (START) |
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
  └──┬───┘  └──┬───┘ │Skill │ │Intel │  └───────────┘
     │         │     └──┬───┘ └──────┘
     │         │        │
     │         ▼        ▼
     │      ┌──────┐ ┌──────┐
     │      │#59   │ │#43/44│
     │      │Ontol │ │Single│
     │      └──────┘ │Agent │
     │               └──────┘
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
   └──────┘└──┬───┘└──────┘
              │
              ▼
           ┌──────┐
           │#68   │
           │Pipeln│
           │v3    │
           └──────┘
```

---

## Quick Reference: All Discussions

| # | Title | Theme | Lang |
|---|-------|:-----:|:----:|
| [2](https://github.com/xg-gh-25/SwarmAI/discussions/2) | [Welcome — Start Here (Read Me First)](https://github.com/xg-gh-25/SwarmAI/discussions/2) | Community | EN |
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
| [44](https://github.com/xg-gh-25/SwarmAI/discussions/44) | [The Three Cracks in Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/44) | Architecture | EN |
| [45](https://github.com/xg-gh-25/SwarmAI/discussions/45) | ["Dreaming" Is Just Note-Taking](https://github.com/xg-gh-25/SwarmAI/discussions/45) | Architecture | EN |
| [46](https://github.com/xg-gh-25/SwarmAI/discussions/46) | [Stop Selling, Start Showing](https://github.com/xg-gh-25/SwarmAI/discussions/46) | Strategy | EN |
| [47](https://github.com/xg-gh-25/SwarmAI/discussions/47) | [One Builder + AI = One Team](https://github.com/xg-gh-25/SwarmAI/discussions/47) | Strategy | EN |
| [49](https://github.com/xg-gh-25/SwarmAI/discussions/49) | [Your AI Agent Can't "Just Read" a 500K-Line Codebase](https://github.com/xg-gh-25/SwarmAI/discussions/49) | Architecture | EN |
| [50](https://github.com/xg-gh-25/SwarmAI/discussions/50) | [你的 AI Agent 读不懂 50 万行代码](https://github.com/xg-gh-25/SwarmAI/discussions/50) | Architecture | CN |
| [51](https://github.com/xg-gh-25/SwarmAI/discussions/51) | [AI-Ready Repo Standard — Why CLAUDE.md Is Not Enough](https://github.com/xg-gh-25/SwarmAI/discussions/51) | Architecture | EN |
| [52](https://github.com/xg-gh-25/SwarmAI/discussions/52) | [AI-Ready Repo 标准](https://github.com/xg-gh-25/SwarmAI/discussions/52) | Architecture | CN |
| [53](https://github.com/xg-gh-25/SwarmAI/discussions/53) | [Karpathy's LLM Wiki Is a Manifesto](https://github.com/xg-gh-25/SwarmAI/discussions/53) | Architecture | EN |
| [54](https://github.com/xg-gh-25/SwarmAI/discussions/54) | [Agent Harness 避坑指南](https://github.com/xg-gh-25/SwarmAI/discussions/54) | Architecture | CN |
| [55](https://github.com/xg-gh-25/SwarmAI/discussions/55) | [From Zero to Ship in One Session](https://github.com/xg-gh-25/SwarmAI/discussions/55) | Architecture | EN |
| [56](https://github.com/xg-gh-25/SwarmAI/discussions/56) | [The Hidden Defaults That Break Your AI Agent](https://github.com/xg-gh-25/SwarmAI/discussions/56) | Architecture | EN |
| [59](https://github.com/xg-gh-25/SwarmAI/discussions/59) | [DDD Knowledge Governance — Practical Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/59) | Architecture | EN |
| [61](https://github.com/xg-gh-25/SwarmAI/discussions/61) | [Skill Portability is a Distribution Problem](https://github.com/xg-gh-25/SwarmAI/discussions/61) | Architecture | EN |
| [68](https://github.com/xg-gh-25/SwarmAI/discussions/68) | [Autonomous Pipeline v3 — Dual-Mode Execution](https://github.com/xg-gh-25/SwarmAI/discussions/68) | Architecture | EN |
| [74](https://github.com/xg-gh-25/SwarmAI/discussions/74) | [OS Eval vs AgentCore Eval — Proprioception vs Diagnostic](https://github.com/xg-gh-25/SwarmAI/discussions/74) | Architecture | EN |
| [75](https://github.com/xg-gh-25/SwarmAI/discussions/75) | [Mechanical Gate vs Ceremonial Gate](https://github.com/xg-gh-25/SwarmAI/discussions/75) | Governance | EN |
| [78](https://github.com/xg-gh-25/SwarmAI/discussions/78) | [当 assert 失效之后 — Eval 架构](https://github.com/xg-gh-25/SwarmAI/discussions/78) | Architecture | CN |
| [79](https://github.com/xg-gh-25/SwarmAI/discussions/79) | [Recall Architecture — The READ Path](https://github.com/xg-gh-25/SwarmAI/discussions/79) | Architecture | EN |
| [80](https://github.com/xg-gh-25/SwarmAI/discussions/80) | [Recall 架构全景 — READ 路径](https://github.com/xg-gh-25/SwarmAI/discussions/80) | Architecture | CN |
| [83](https://github.com/xg-gh-25/SwarmAI/discussions/83) | [After `assert` Breaks Down — Eval Architecture](https://github.com/xg-gh-25/SwarmAI/discussions/83) | Architecture | EN |
| [84](https://github.com/xg-gh-25/SwarmAI/discussions/84) | [Context as a Living System — The WRITE Path](https://github.com/xg-gh-25/SwarmAI/discussions/84) | Architecture | EN |
| [85](https://github.com/xg-gh-25/SwarmAI/discussions/85) | [上下文是一个活系统 — WRITE 路径](https://github.com/xg-gh-25/SwarmAI/discussions/85) | Architecture | CN |
| [86](https://github.com/xg-gh-25/SwarmAI/discussions/86) | [Quality Convergence — How a Pipeline Knows It's Done](https://github.com/xg-gh-25/SwarmAI/discussions/86) | Architecture | EN |
| [87](https://github.com/xg-gh-25/SwarmAI/discussions/87) | [质量收敛 — Pipeline 怎么知道做完了](https://github.com/xg-gh-25/SwarmAI/discussions/87) | Architecture | CN |
| [88](https://github.com/xg-gh-25/SwarmAI/discussions/88) | [Cognitive Evolution, Not Skill Tuning](https://github.com/xg-gh-25/SwarmAI/discussions/88) | Governance | EN |
| [89](https://github.com/xg-gh-25/SwarmAI/discussions/89) | [认知进化,而非技能调优](https://github.com/xg-gh-25/SwarmAI/discussions/89) | Governance | CN |
| [90](https://github.com/xg-gh-25/SwarmAI/discussions/90) | [AgentCore Eval-First vs Our Decoupled Eval Subsystem](https://github.com/xg-gh-25/SwarmAI/discussions/90) | Architecture | EN |
| [91](https://github.com/xg-gh-25/SwarmAI/discussions/91) | [AgentCore Eval-First vs 我们解耦的 Eval 子系统](https://github.com/xg-gh-25/SwarmAI/discussions/91) | Architecture | CN |
| [94](https://github.com/xg-gh-25/SwarmAI/discussions/94) | [Pollinate — Philosophy, Architecture, Super-Powers & Honest Lowlights](https://github.com/xg-gh-25/SwarmAI/discussions/94) | Architecture | EN |
| [93](https://github.com/xg-gh-25/SwarmAI/discussions/93) | [Pollinate — 哲学、架构、Super-Powers 与 Lowlights](https://github.com/xg-gh-25/SwarmAI/discussions/93) | Architecture | CN |
| [95](https://github.com/xg-gh-25/SwarmAI/discussions/95) | [The Ontology that runs SwarmAI's Memory, DDD & Code Intelligence](https://github.com/xg-gh-25/SwarmAI/discussions/95) | Architecture | EN |
| [96](https://github.com/xg-gh-25/SwarmAI/discussions/96) | [撑起 SwarmAI 记忆 / DDD / 代码智能的那套 Ontology](https://github.com/xg-gh-25/SwarmAI/discussions/96) | Architecture | CN |
| [100](https://github.com/xg-gh-25/SwarmAI/discussions/100) | [Ontology isn't a knowledge graph — it's a decision layer](https://github.com/xg-gh-25/SwarmAI/discussions/100) | Architecture | EN |
| [99](https://github.com/xg-gh-25/SwarmAI/discussions/99) | [Ontology 不是知识图谱,是决策层](https://github.com/xg-gh-25/SwarmAI/discussions/99) | Architecture | CN |
| [98](https://github.com/xg-gh-25/SwarmAI/discussions/98) | [AI Agent for Data 续篇 — L3 契约层 + L2 受约束 NL2SQL](https://github.com/xg-gh-25/SwarmAI/discussions/98) | Architecture | CN |
| [102](https://github.com/xg-gh-25/SwarmAI/discussions/102) | [How SwarmAI's Living Knowledge Compares to Code-Understanding Tools](https://github.com/xg-gh-25/SwarmAI/discussions/102) | Architecture | EN |
| [103](https://github.com/xg-gh-25/SwarmAI/discussions/103) | [SwarmAI 的「活知识」对比几款代码理解工具](https://github.com/xg-gh-25/SwarmAI/discussions/103) | Architecture | CN |

---

## Quick Stats

- **Total discussions:** 77 (including Welcome + Reading Matrix)
- **Content articles:** 75
- **Bilingual pairs (EN+CN):** 21 pairs (42 discussions)
- **English only:** 29
- **Themes:** Foundations (8), Architecture (48), Governance (11), Strategy (5), Community (4)
- **Avg reading time per article:** ~5-8 min
- **Path A total:** ~50 min | **Path B:** ~65 min | **Path C:** ~47 min
