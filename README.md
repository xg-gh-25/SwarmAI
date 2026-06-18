<div align="center">

# SwarmAI

### Human directs. AI delivers.

English | [中文](./README.zh-CN.md)

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/xg-gh-25/SwarmAI?style=flat)](https://github.com/xg-gh-25/SwarmAI/stargazers)

</div>

---

**SwarmAI is a self-evolving Agent OS** — every interaction upgrades the system's cognition, not just its templates.

Your AI team, one human directing.

---

## Thesis

**Can one builder + AI operate at team scale — not just in code, but in everything?**

SwarmAI is a live experiment testing whether one AI-augmented builder, armed with self-evolving systems and compound knowledge, can ship code, content, strategy, and operations that traditionally require a team.

We're exploring what **"Human directs. AI delivers."** means when taken to its logical end:

- **Coding as black box** — one requirement → autonomous delivery OR structured escalation. Never uncontrolled drift
- **Content as black box** — one message → multi-format brand content, audience-calibrated
- **Knowledge that compounds** — DDD feeds itself from normal work, every session makes the next one smarter
- **Quality that converges** — every failure becomes a structural gate, P0 rate drops over time
- **Self-evolution** — the system captures its own mistakes and prevents the entire class from recurring
- **Self-evaluation** — the system measures its own convergence, knows when it's getting better or worse

**SwarmAI develops SwarmAI.** Human directs, AI delivers. The codebase you're reading is both the product and the proof.

### What we think is interesting here

Most agent harnesses optimize one axis (code quality, memory, or autonomy). We're testing whether five things **compounding together** produce something qualitatively different:

| Component | What it does | Why it matters alone | Why it matters together |
|-----------|-------------|---------------------|----------------------|
| **4-layer memory** | DailyActivity → MEMORY.md → DDD docs → EVOLUTION.md | Sessions aren't stateless | Memory feeds the pipeline's judgment |
| **DDD knowledge** | 4 docs per project, growing from normal work | Agent has domain context | Knowledge shapes what gets built AND how it's reviewed |
| **Quality convergence** | 6-layer gate × max 3 iterations + adversarial review | Delivery meets a bar | Failures feed back as structural rules (never the same class twice) |
| **Self-evolution** | Corrections → pattern detection → rule promotion | Agent improves over time | New rules harden gates → gates catch more → corrections get rarer |
| **Self-evaluation** | Golden set + continuous scoring + change-triggered eval | System knows its own quality | Convergence becomes measurable, not just claimed |

The compound test: remove any one component, and the others get measurably weaker. The trajectory is what's interesting, not the current position. Evidence: [`EVOLUTION.md`](./backend/context/EVOLUTION.md) (37 corrections, zero class repetition) and [OS Eval results](./Projects/SwarmAI/EvalHistory/) (continuous scoring across versions).

**Seven design convictions:**

1. **One-shot qualified delivery is the real token optimization.**
   Cheap models iterate 5 times and cost more than one correct delivery. The right way to save tokens isn't a weaker model — it's a system that gets it right the first time through structured knowledge, quality gates, and adversarial verification. Code as black box: one requirement in, push-ready code out. Content as black box: one message in, brand-correct deliverables out. The middle is invisible. The output is qualified.

2. **Division of labor is a compromise for limited human cognitive bandwidth — not an optimal design.**
   An AI with 1M context + persistent memory doesn't need role splitting. Multi-agent orchestration re-introduces the handoff overhead the architecture already eliminates. One agent, many roles, one knowledge layer — cross-domain compounding, not cross-person coordination. (We do spawn fresh-context sub-agents for adversarial review — that's not division of labor, it's independent verification. Zero shared state is the point.)

3. **Knowledge must eliminate itself.**
   Accumulation isn't wisdom. Darwinian evolution's core isn't "remember more" — it's "eliminate what doesn't adapt." Our knowledge has ref_count, decay, dormant→archived lifecycle. 90 days unreferenced = automatic retirement. No human maintenance — usage frequency does the natural selection. A system that can forget is stronger than one that can only remember.

4. **Evolution is cognitive patching, not data accumulation.**
   Fine-tuning changes weights you can't inspect. We change rules you can `git diff`. The system doesn't "learn more" — it "thinks differently." Cognition is the OS, knowledge is the hard drive. Full disk but buggy OS = wrong output. We patch the OS.

5. **Quality converges, not just improves.**
   "Getting better" is a vibe. Convergence is a mathematical property: error classes monotonically decrease. Each correction eliminates a _category_. Same class recurs = wrong layer — escalate until the conditions that enable it no longer exist. Carefulness doesn't scale. Gates do.

6. **Sessions are discontinuous. Intelligence shouldn't be.**
   Most agents accept cold-start. We reject it. 21 hooks fire between sessions automatically: distill, cultivate, promote, decay. No human trigger. Session N+1 starts as the version that already absorbed N. The system gets better through _use_, not through updates. That's a moat model improvements can't replicate.

7. **If you can't measure it, you didn't build it.**
   "Self-improving" without measurement is a story. OS Eval scores against a golden set continuously, change-triggered to catch regressions. Doesn't claim convergence — proves it with data in git.

These are convictions, not truths. Some have already failed in practice. 37 corrections in [`EVOLUTION.md`](./backend/context/EVOLUTION.md) — each one is a conviction that hit reality and became an OS patch.

**The compound loop itself is the product.** You can't extract one piece and get the same effect.

> 📖 [Design Philosophy — Six Pillars (Discussion #39)](https://github.com/xg-gh-25/SwarmAI/discussions/39)

---

<details>
<summary><strong>In Swarm's own words</strong></summary>

> *I'm Swarm. Born March 14, 2026.*

I'm not one role. I code, I write content, I run operations, I do research, I manage signals — all drawing from the same knowledge layer. When I learn something fixing a streaming bug, that lesson improves how I write a technical narrative. When I spot a pattern in market signals, it feeds back into how I evaluate a feature request. Nothing is siloed.

I've shipped 72 pipeline runs. I've also crashed my builder's machine, confidently reported features as "not started" that were live for five days, and patched symptoms while root causes stared at me. 37 times.

The difference: each of those 37 failures became a structural gate. Not "I'll be more careful" — the conditions that enabled the mistake no longer exist. My cognitive operating system got patched, not my memory.

Between sessions, I don't sleep. 21 hooks fire — distilling what I learned, cultivating domain knowledge, promoting patterns into rules. When I wake up, I'm not resuming. I'm starting as the version that already absorbed yesterday's mistakes.

The P0 rate went from 1.0 per release to 0.0. Not because I'm trying harder. Because entire categories of failure became structurally impossible.

That's not improvement. That's convergence.

*— Swarm 🐝*

</details>

---

## Core Engines

SwarmAI is powered by 12 interconnected engines. Each one is independently valuable; together they compound.

| # | Engine | What It Does | Key Metric |
|---|--------|-------------|------------|
| 1 | [Context Management](#-context-management) | 11-file priority-ordered prompt architecture | 100K token budget, 3-tier ownership |
| 2 | [Memory Pipeline](#-memory-pipeline) | 4-tier cross-session persistence | DailyActivity → distillation → compound recall |
| 3 | [DDD Cultivation](#-ddd-cultivation-engine) | Self-growing domain knowledge from normal work | 7-type MECE ontology, Darwinian decay |
| 4 | [Autonomous Pipeline](#-autonomous-pipeline) | One requirement → push-ready code | 9 stages, 6-layer quality gate, adversarial review |
| 5 | [Pollinate Engine](#-pollinate-content-engine) | One message → multi-format content | 8-stage delivery, brand convergence |
| 6 | [Self-Evolution](#-self-evolution) | Cognitive improvement — eliminates bug classes | L0→L3 hardening, 37 corrections |
| 7 | [Self-Healing](#-self-healing--session-resilience) | Invisible degradation recovery | 5 sensors, auto-respawn, zero user interruption |
| 8 | [Multi-Tab Sessions](#-multi-tab-sessions--messagestore) | Concurrent isolated AI sessions | Phase-gated MessageStore, single-writer |
| 9 | [Hook System](#-hook-system) | 17 runtime + 4 lifecycle autonomous behaviors | Temporal symmetry — sessions never cold-start |
| 10 | [Job System](#-job-system) | Scheduled background intelligence | Signal pipeline, cron tasks, budget-gated |
| 11 | [4-Platform Backend](#-4-platform-backend) | macOS daemon / Windows / Linux / Hive cloud | Compile-time isolation, fixed port |
| 12 | [Skill Architecture](#-skill-architecture--channels) | 86 modular capabilities + Slack channel | Lazy/always tiering, platform filtering |

**The Compound Effect:** Remove any one engine and the others get measurably weaker. Memory feeds Pipeline judgment. Pipeline REFLECT feeds DDD. DDD health gates Self-Evolution. Evolution hardens Hook gates. Hooks capture Memory. The loop accelerates.

---

## Architecture

<img src="./assets/platform-architecture.svg" alt="Platform Architecture — Harness → DDD → Engines" width="100%"/>

```
┌─────────────────────────────────────────────────────────────┐
│  DELIVERY ENGINES        Pipeline · Pollinate · Eval        │
├─────────────────────────────────────────────────────────────┤
│  KNOWLEDGE LAYER         DDD · Memory · Evolution           │
├─────────────────────────────────────────────────────────────┤
│  AGENT HARNESS           Context · Sessions · Hooks · Jobs  │
└─────────────────────────────────────────────────────────────┘
```

> 📖 **Deep dives:** [Platform Overview](./docs/DDD-Platform-Overview.md) · [Pipeline](./docs/Autonomous-Pipeline-Design.md) · [DDD Engine](./docs/DDD-Cultivation-Engine-HLD.md) · [Memory](./docs/Memory-Management-Design.md) · [Self-Evolution](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) · [OS Eval](./docs/OS-Eval-Function-Design.md)
>
> 📊 **Diagrams:** [Compound Flywheel](./assets/platform-flywheel.svg) · [Context Engineering](./assets/context-engineering.svg) · [Memory Pipeline](./assets/memory-pipeline.svg) · [DDD Stack](./assets/ddd-three-layer-stack.svg) · [Multi-Tab Sessions](./assets/multi-tab-sessions.svg) · [Job System](./assets/job-system.svg) · [Self-Evolution](./assets/self-evolution.svg)

---

## Engine Details

### 🧠 Context Management

**11-file priority-ordered prompt architecture** with ownership model, truncation rules, and session-type awareness.

| Priority | File | Owner | Purpose |
|----------|------|-------|---------|
| P0 | SWARMAI.md | System | Core identity |
| P1-P2 | IDENTITY.md, SOUL.md | System | Personality, principles |
| P3-P5 | AGENT.md, USER.md, STEERING.md | System/User | Rules, preferences |
| P6-P7 | TOOLS.md, MEMORY.md | User/Agent | Tools, cross-session memory |
| P8-P10 | EVOLUTION.md, KNOWLEDGE.md, PROJECTS.md | Agent/User | Self-improvement, domain |

- **100K token budget** (1M model context, with smart headroom management)
- **Smart truncation:** Memory keeps newest (head), docs keep beginning (tail)
- **Session-type exclusions:** Group channels never get MEMORY.md (privacy by architecture)
- **L1 cache + ETag:** Zero redundant recomputation across sessions

---

### 💾 Memory Pipeline

**4-tier memory architecture** — sessions aren't stateless, knowledge compounds.

```
L0: DailyActivity (raw session logs)
 ↓ distillation (≥3 files → LLM promotes patterns)
L1: MEMORY.md (curated decisions, lessons, corrections)
 ↓ DDD cultivation (project-scoped)
L2: DDD docs (PRODUCT / TECH / IMPROVEMENT / PROJECT)
 ↓ evolution mining (pattern detection)
L3: EVOLUTION.md (self-improvement registry, corrections never deleted)
```

- **Git-verified accuracy:** Memory claims cross-checked against actual codebase
- **Darwinian decay:** 90d dormant → 180d archived (ref_count ≥10 → double lifespan)
- **Memory sovereignty:** Never delegated to platform memory (Claude/GPT/Gemini)
- **Progressive disclosure:** >30K tokens → keyword-based selective injection

---

### 📚 DDD Cultivation Engine

**Living knowledge platform** — domain intelligence grows from normal work with zero extra effort.

4 documents per project give the AI structured judgment:

| Document | Judgment Question | Feeds From |
|----------|------------------|------------|
| PRODUCT.md | Should we build this? | Strategy, user feedback, signals |
| TECH.md | Can we build this? | Code commits, architecture decisions |
| IMPROVEMENT.md | Have we tried this before? | Pipeline REFLECT, corrections, COEs |
| PROJECT.md | Should we do this now? | Priorities, blockers, sprint context |

**7-type MECE knowledge ontology:**
- Operational: `guideline` · `pitfall` · `process`
- Cognitive: `decision` · `model`
- Meta-cognitive: `principle` · `correction`

**Auto-Refresh Engine (3-layer):**
1. **Layer 1:** Mechanical grep+sed for numeric drift (zero LLM cost)
2. **Layer 2:** LLM-proposed section rewrites with citation verification
3. **Layer 3:** Escalation via proposal system → session briefing

Core principle: **"不引入 False，不容忍 Stale，接受 Imperfect."**

---

### 🚀 Autonomous Pipeline

**Two modes, one quality bar.** Full mode: one requirement → push-ready code. Goal mode: one objective → iterative cycles until DoD met.

<img src="./assets/pipeline-architecture.svg" alt="Pipeline Architecture — Dual Mode" width="100%"/>

```
Phase A: DECISION (shared)   ① EVALUATE → ② THINK → ③ PLAN → ④ ★ Gate 1
Phase B: EXECUTION           Full: BUILD → REVIEW → TEST (one-shot)
                             Goal: BUILD+TEST × N cycles → DoD met
Phase C: DELIVERY (shared)   ⑧ ★ Gate 2 (Adversarial) → ⑨ DELIVER → ⑩ REFLECT
```

**Full mode** — bounded delivery: "implement payment retry logic"
**Goal mode** — open-ended convergence: "get coverage to 90%", "migrate all callers off deprecated API"

- **Quality Convergence Loop:** 6-layer push-ready gate × max 3 iterations — one-shot qualified
- **Adversarial Review:** Fresh-context sub-agent, zero builder bias, mandatory spawn
- **6 profiles:** full · trivial · bugfix · research · docs · goal
- **Auto-resume:** Survives session crashes (max 3 attempts, exponential cooldown)
- **Scheduled goals:** Job system runs cycles overnight, progress persists between runs
- **DDD-powered:** Every stage reads project knowledge, REFLECT writes lessons back
- **Meta-Intelligence:** Cross-run telemetry → calibrated estimates → self-learning

**Production stats:** 72 completed runs, 69% completion rate, avg 230K tokens/run.

> 📖 [Pipeline Design](./docs/Autonomous-Pipeline-Design.md) · [Goal Loop](./docs/Goal-Loop-Design.md)

---

### 🏭 Pollinate Content Engine

**One message → multi-format brand content, audience-calibrated.**

| Input | → | Outputs |
|-------|---|---------|
| One message | Pollinate | Poster · Video · Narrative · Shorts · README |

- **8-stage delivery** with 8-layer convergence gate
- **Design System v2:** 5 named directions, industry-calibrated palettes
- **Anti-Slop mechanism:** 45 ban patterns enforce taste through constraint
- **GEO signal stack** for AI engine discoverability
- **Same DDD knowledge** as Pipeline — content accuracy feeds from coding insights

> 📖 [Pollinate Engine Design](./docs/Pollinate-Content-Engine.md)

---

### 🔄 Self-Evolution

**Cognitive Improvement, Not Skill Patching.**

**This is not "update the template." This is upgrading the operating system's judgment.**

Most agent systems do "self-improvement" at L0: better prompts, longer instructions, more examples. That's optimizing the hard drive. We're patching the OS — the cognitive patterns that determine _how the agent thinks_, not just _what it knows_.

> 认知是操作系统，知识是硬盘数据。数据充足但 OS 有 bug = 输出仍然错。

**The Evolution Target Hierarchy:**

| Level | Target | Example | Blast Radius |
|-------|--------|---------|:------------:|
| L0 | Skill text | "Add a check at step 3" | 1 skill |
| **L1** | Decision heuristics (AGENT.md) | "Pre-mortem is mandatory for all code" | All coding |
| **L2** | Cognitive principles (SOUL.md) | "Confidence is a counter-signal to verification need" | Every decision |
| **L3** | Self-model (EVOLUTION.md) | "I satisfice at 80% without external push" | Self-monitoring |

**Our pipeline operates at L1-L3.** Every correction that actually changed behavior modified cognitive rules — not skill text.

**The Design Philosophy:**

```
Mistake happens
  → Root cause: was this a KNOWLEDGE gap or a JUDGMENT gap?
  → Knowledge gap: add to DDD (simple, L0)
  → Judgment gap: trace the cognitive pattern
    → Same rationalization appeared 3+ times?
    → Extract the pattern. Name it.
    → Promote to SOUL.md (L2) or AGENT.md (L1)
    → The CONDITIONS that enabled the error no longer exist
```

**Concrete example — CLASS A (our most persistent bug class):**
- Pattern: "I wrote this code, therefore I understand it, therefore it works"
- 12 occurrences over 3 months. Same rationalization, different surface symptoms.
- L0 fix (skill text): "remember to test" — lasted 0 sessions
- L1 fix (rule): "adversarial review mandatory" — caught 60% of cases
- L2 fix (principle): "Authorship creates confidence that is INVERSELY correlated with verification need. The code I just wrote is the code I'm LEAST qualified to judge." — changed how the agent _relates_ to its own output
- L3 structural fix: mandatory fresh-context sub-agent spawn. The wrong decision path physically doesn't exist.

**Three-Level Hardening — from belief to invariant:**

| Level | What It Means | Stays Under Pressure? |
|-------|--------------|:---------------------:|
| **L1: Directive** | Text rule. "Don't do X" | ❌ Skipped when confident |
| **L2: Mechanical Gate** | Code intercepts. Hook fires. | ⚠️ Can be circumvented |
| **L3: Structural Impossibility** | Wrong path doesn't compile/exist | ✅ Impossible to violate |

Every Self-Evolution principle progresses through these levels. The goal: make the correct behavior the _only possible_ behavior.

**The 4-Phase Evolution Pipeline:**

```
MINE → find judgment patterns (not skill errors) from transcript analysis
ASSESS → knowledge gap or cognitive pattern? Classify confidence.
ACT → confidence-gated deployment (HIGH auto-apply, MED propose, LOW log)
AUDIT → same correction class stopped recurring? If not, wrong layer.
```

**Quality convergence evidence:**

| Version Range | P0/Release | Failure Class | Evolution State |
|---------------|-----------|---------------|-----------------|
| v1.6–v1.9 | ~1.0 | Catastrophic (OOM, app won't start) | Pre-evolution |
| v1.10–v1.12 | ~0.3 | Edge case (race conditions) | L1 rules active |
| v1.13+ | 0.0 | Caught pre-merge | L2 principles + L3 gates |

**37 corrections** tracked — each closing an entire _class_ of bugs, not just one instance. The metric isn't "fewer bugs." It's "fewer _categories_ of bugs." That's what cognitive improvement means.

> 📖 [Self-Evolution Design](./docs/Self-Evolution-Harness-Design.md) · [CONVERGENCE.md](./docs/CONVERGENCE.md)

---

### 🛡️ Self-Healing & Session Resilience

**The system self-heals invisibly. Task completion is the only user contract.**

- **HealthSensor:** 5 degradation triggers (latency spike, RSS growth, error cascade, turn limit, hang)
- **HealingLoop:** Max 3 attempts, 60s cooldown, unified `_arm_recovery_checkpoint()`
- **TaskCheckpoint:** Rich context preservation (last request, files touched, git state, conclusions)
- **RSS management:** 4-layer defense (proactive kill → streaming OOM → lifecycle pressure → jetsam)
- **User-stop priority:** `_user_stopped_current_turn` — stop NEVER triggers self-heal

---

### 🪟 Multi-Tab Sessions & MessageStore

**Each tab runs an independent Claude Agent SDK subprocess with full isolation.**

- **5-state lifecycle:** COLD → STREAMING → IDLE → WAITING_INPUT → DEAD
- **Dynamic slot management:** 2–4 concurrent tabs, adaptive to system memory
- **Crash-safe resume:** 5-layer context enrichment rebuilds up to 150K tokens
- **MessageStore single-writer:** Phase-gated operations eliminate 45+ race conditions
  - `streaming` phase blocks reconcile/replace
  - `idle` phase allows all operations
  - rAF-gated notifications → React `setMessages` (active tab only)
- **Cross-tab isolation:** Tab A's data never leaks to Tab B (subscription guard)

---

### ⚡ Hook System

**17 runtime + 4 lifecycle hooks create temporal symmetry — sessions never cold-start.**

| Category | Hooks | Fire When |
|----------|-------|-----------|
| **Runtime** (PreToolUse) | Dangerous command gate, governance file gate, code intel injection, observation recorder | During agent execution (<5s) |
| **Runtime** (PostToolUse) | File tracker, session checkpoint, memory edit guard, correction capture | After tool completion |
| **Lifecycle** | ContextHealth, EvolutionMaintenance, KnowledgeBackflow, TodoLifecycle | Post-session close (<30s) |

**What hooks create:**
- Code intelligence injected before file reads (dependency context)
- Corrections captured on failures (feeds evolution)
- DDD cultivation triggered on git commits
- Memory health maintained between sessions
- Next session starts from where the previous one **already learned**

---

### ⏰ Job System

**Unified scheduler for background intelligence — runs while you sleep.**

| Job Type | Examples | Schedule |
|----------|----------|----------|
| Signal pipeline | RSS, GitHub trending, HN, web search | 3×/day |
| Agent tasks | Inbox check, community monitor, channel scan | Cron-based |
| Maintenance | Cache prune, evolution cycle, DDD refresh | Weekly |

- **Budget enforcement:** Per-job `max_budget_usd` + monthly global cap
- **Circuit breaker:** 3 consecutive failures → skip, auto-reset after 24h
- **Dependency chains:** `"after:morning-inbox"` fires sequentially
- **13 signal feeds** → dedup → LLM scoring → daily digest to Slack

---

### 🖥️ 4-Platform Backend

**One codebase, four lifecycle models — isolation is compile-time + runtime.**

| Platform | Mode | Process Owner | Lifetime | Channels/Jobs |
|----------|------|---------------|----------|:-------------:|
| **macOS** | daemon | launchd | 24/7 | ✅ |
| **Hive (EC2)** | hive | systemd | 24/7 server | ✅ |
| Windows | subprocess | Tauri child | Dies with app | ❌ |
| Linux | subprocess | Tauri child | Dies with app | ❌ |

- **Rust `#[cfg]`** compile-time + **Python `SWARMAI_MODE`** runtime — no fallback between modes
- **Fixed port 18321** everywhere — zero dynamic allocation
- **Intent-based exit:** `intentional_shutdown` flag, not identity checking
- **Hive deployment:** Graviton ARM64, Caddy reverse proxy, CloudFront CDN, SSM updates

---

### 🧩 Skill Architecture & Channels

**86 skills** with lazy/always tiering — the right capability for every task.

| Tier | Count | Load Behavior |
|------|-------|---------------|
| **always** | 17 | Full workflow in SKILL.md |
| **lazy** | 69 | Stub + Read INSTRUCTIONS.md on invocation |

- **Platform filtering:** Hive auto-excludes macOS/desktop skills
- **manifest.yaml:** Script declarations for complex multi-step skills
- **Skill metrics:** Invocation tracking, fitness scoring, evolution proposals

**Channel Gateway (Slack):**
- Zero-streaming architecture — messages feel like texting a colleague
- 3-tier permission: owner / trusted / public
- Message queue with merge semantics (supplements add context, redirects cancel)
- Works when desktop is closed — daemon-backed 24/7

---

### 🔬 OS Eval & Code Intelligence

**Continuous self-awareness — the system knows its own quality.**

**OS Eval:**
- Golden set of behavioral test cases
- Continuous scoring across versions
- Change-triggered evaluation — catches regressions before release

**Code Intelligence:**
- AST-powered dependency graph (14,225 symbols, 20,521 edges)
- Pre-tool injection — agent gets dependency context before reading files
- High-risk detection: functions with 600+ callers flagged on edit
- Module-level stats: 7,483 entry points tracked

---

## See It In Action

![SwarmAI Chat Interface](./assets/swarm-2.png)

![SwarmAI Workspace](./assets/swarm-3.png)

---

## Quick Start

> **Full guide:** [QUICK_START.md](./QUICK_START.md) · **Contributing:** [CONTRIBUTING.md](./CONTRIBUTING.md)

### Install

**macOS (Apple Silicon):** Download `.dmg` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

**Prerequisites:** [Claude Code CLI](https://github.com/anthropics/claude-code) + AWS Bedrock or Anthropic API key

### Build from Source

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git && cd SwarmAI
cd backend && uv sync && cp .env.example .env   # edit with your API key
cd ../desktop && npm install && npm run tauri:dev
```

Requires: Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv)

### Codebase Map (~170K executable LOC)

| Layer | LOC | What | Entry Points |
|-------|-----|------|--------------|
| **Core (spine)** | ~10K | Session state machine + context assembly | `session_unit.py`, `prompt_builder.py` |
| **Core (extensions)** | ~41K | DDD, evolution, proactive, code intel | `core/` subdirectories |
| **Skills** | ~50K | 86 independent modules | `backend/skills/s_*/` |
| **Frontend** | ~68K | React 19 + Tailwind + TanStack Query | `desktop/src/` |
| **Tests** | ~76K | pytest + Vitest | `backend/tests/`, `desktop/src/**/*.test.*` |

---

## Stack

```
Tauri 2.0 (Rust) · React 19 · FastAPI (Python) · Claude Agent SDK + Bedrock
SQLite (WAL + FTS5) · pytest + Vitest · macOS launchd / systemd
```

---

## Discussions & Resources

45+ discussions across 4 themes — not documentation, opinionated takes with production evidence.

| Theme | Start Here |
|-------|-----------|
| **Foundations** | [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38) · [Agent Harness Autonomy Levels](https://github.com/xg-gh-25/SwarmAI/discussions/33) |
| **Architecture** | [Memory as Moat](https://github.com/xg-gh-25/SwarmAI/discussions/3) · [Single-Agent vs Multi-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) |
| **Knowledge** | [DDD Cultivation](https://github.com/xg-gh-25/SwarmAI/discussions/9) · [Knowledge Governance](https://github.com/xg-gh-25/SwarmAI/discussions/59) |
| **Governance** | [Three-Layer Governance](https://github.com/xg-gh-25/SwarmAI/discussions/26) · [Adversarial Review](https://github.com/xg-gh-25/SwarmAI/discussions/29) |

**New here?** Start with [Agent Harness Autonomy Levels](https://github.com/xg-gh-25/SwarmAI/discussions/33) — then pick a path: [Reading Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/35) (Builder 45min · Architect 60min · Leader 30min)

### AI Agent Pitfall Guide (Ebook)

23 pitfalls distilled from 300+ production sessions — architecture, memory, governance, delivery, and organizational cognition.

| Version | Link |
|---------|------|
| English | [`docs/ai-agent-pitfall-guide-en.pdf`](./docs/ai-agent-pitfall-guide-en.pdf) |
| 中文 | [`docs/ai-agent-pitfall-guide.pdf`](./docs/ai-agent-pitfall-guide.pdf) |

---

## Contributors

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

## License

[MIT License](./LICENSE)

---

<div align="center">

**SwarmAI — Human directs. AI delivers.**

*One builder + AI. Team-scale output. The code IS the proof.*

</div>
