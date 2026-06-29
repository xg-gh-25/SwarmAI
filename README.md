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

## Why SwarmAI

We finally have software smart enough to reason, write code, and make judgment calls — and it **wakes up with amnesia every morning.** Every session starts from zero. The context you gave it, the mistake it made yesterday, the correction you taught it — gone. Most "AI tools" are **flat**: a brilliant model trapped in Groundhog Day.

SwarmAI is built on the opposite bet — that the value should **compound.** Every interaction should leave the system a little sharper than before, permanently.

Which reframes the obvious question. Ask *"why does a desktop app need 170K lines and 13 engines?"* and you've mismeasured it: **this isn't application complexity — it's the complexity of an agent's cognition.** Four things separate a mind from a model: it stays **continuous** across time, it **corrects itself**, it **forgets** what stopped mattering, and its **judgment compounds** with use. Conventional software has no analog for any of them — a program doesn't get wiser between runs, and it never rewrites its own rules. SwarmAI is an attempt to build that missing layer: not a bigger model, but the **cognitive operating system around one.**

The design choices only make sense through that lens:

- **Evolution is an OS patch, not stored data.** Most agent-memory projects pile up entries. We separate *cognition* (the OS) from *knowledge* (the disk): one edited line in `SOUL.md` shifts judgment more than a thousand memory rows — and every change is a `git diff`.
- **Recurring mistakes are made structurally impossible.** When an error class repeats, we don't add another lesson — we add a gate, then a path where the wrong move physically cannot happen. Humans rely on carefulness; an agent should rely on structure.
- **Knowledge must be able to die.** Unreferenced for 90 days → retired. Accumulation without elimination is how every memory system rots. Decay is natural selection for what an agent knows.
- **Sessions are discontinuous. Intelligence shouldn't be.** Hooks fire *between* sessions, so the next one starts warm. Most frameworks accept the cold start; we refuse it.

The thesis, tested live and in public: **can one builder + AI operate at the scale of a whole team?** Not by scaling the model — by building the compounding loop around it. The loop *is* the product; you can't extract one engine and keep the effect.

As of **v1.22.0**, that loop is running healthy end-to-end — sessions self-heal, knowledge cultivates and decays on its own, and the evolution engine has logged **42 corrections**, converting recurring failure classes into structural gates rather than repeated lessons.

> **This isn't a product demo — it's a living experiment, documented as it happens.** Below are 60+ deep-dive discussions: every architecture decision, failure, and post-mortem behind the engines.

### 📚 Start Here — The Thinking Behind the Code

| | |
|---|---|
| 🗺️ **[Reading Matrix — 3 Curated Paths](https://github.com/xg-gh-25/SwarmAI/discussions/35)** | **Builder** (~45 min) · **Architect** (~60 min) · **Leader** (~30 min) — don't read everything, pick your path |
| 💬 **[All Discussions (68)](https://github.com/xg-gh-25/SwarmAI/discussions)** | Thought leadership, architecture deep-dives, and post-mortems — also mirrored in [`docs/discussions/`](./docs/discussions/) |
| 🧭 **[Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38)** | The beliefs that became enforcement — why each one earned its place from a failure |

![SwarmAI](./assets/swarm-2.png)

---

## Quick Start

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git && cd SwarmAI
cd backend && uv sync && cp .env.example .env   # edit with your API key
cd ../desktop && npm install && npm run tauri:dev
```

**macOS (Apple Silicon):** Or download `.dmg` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

Requires: Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv), [Claude Code CLI](https://github.com/anthropics/claude-code)

> 📖 Full setup guide: [QUICK_START.md](./QUICK_START.md)

---

## Architecture

<img src="./assets/platform-architecture.svg" alt="Platform Architecture" width="100%"/>

```
┌─────────────────────────────────────────────────────────────┐
│  DELIVERY ENGINES        Pipeline · Pollinate · Eval        │
├─────────────────────────────────────────────────────────────┤
│  KNOWLEDGE LAYER         DDD · Memory · Evolution           │
├─────────────────────────────────────────────────────────────┤
│  AGENT HARNESS           Context · Sessions · Hooks · Jobs  │
└─────────────────────────────────────────────────────────────┘
```

---

## Core Engines

13 interconnected engines. Each independently valuable; together they compound.

| # | Engine | What It Does | Deep Dive |
|---|--------|-------------|-----------|
| 1 | **Context Management** | 11-file prompt architecture, 100K budget, 3-tier ownership | [docs](./docs/DDD-Platform-Overview.md) |
| 2 | **Memory Pipeline** | 4-tier persistence: DailyActivity → distillation → compound recall | [docs](./docs/Memory-Management-Design.md) |
| 3 | **DDD Cultivation** | Self-growing domain knowledge, 7-type ontology, Darwinian decay | [docs](./docs/DDD-Cultivation-Engine-HLD.md) |
| 4 | **Autonomous Pipeline** | One requirement → push-ready code. Dual-mode: Full + Goal Loop | [docs](./docs/Autonomous-Pipeline-Design.md) |
| 5 | **Pollinate Engine** | One message → multi-format brand content | [docs](./docs/Pollinate-Content-Engine.md) |
| 6 | **Self-Evolution** | Cognitive L0→L3 patching. 42 corrections → recurring classes become structural gates | [docs](./docs/Self-Evolution-Harness-Design.md) |
| 7 | **Self-Healing** | Invisible recovery: 5 sensors, auto-respawn, user sees nothing | — |
| 8 | **Multi-Tab + MessageStore** | Concurrent sessions, phase-gated single-writer, cross-tab isolation | — |
| 9 | **Hook System** | 21 hooks (17 runtime + 4 lifecycle). Sessions never cold-start | — |
| 10 | **Job System** | Background intelligence: 13 signal feeds, cron, budget-gated | — |
| 11 | **4-Platform Backend** | macOS daemon · Hive (EC2) · Windows · Linux. Compile-time isolation | — |
| 12 | **Skills + Channels** | 88 skills (lazy/always), Slack gateway, 3-tier permission | — |
| 13 | **Eval (Proprioception)** | Decoupled, system-level: golden set + git-bound regression gate. Proves convergence, not vibes | [docs](./docs/OS-Eval-Function-Design.md) |

**The compound loop:** Memory → Pipeline judgment → DDD → Evolution → Gates → Memory. Remove one, the rest weaken.

<img src="./assets/pipeline-architecture.svg" alt="Pipeline — Dual Mode (Full + Goal)" width="100%"/>

> 📊 More diagrams: [Flywheel](./assets/platform-flywheel.svg) · [Context](./assets/context-engineering.svg) · [Memory](./assets/memory-pipeline.svg) · [DDD](./assets/ddd-three-layer-stack.svg) · [Sessions](./assets/multi-tab-sessions.svg) · [Jobs](./assets/job-system.svg) · [Evolution](./assets/self-evolution.svg)

---

## Thesis & Design Philosophy

**Can one builder + AI operate at team scale?** We're testing it live.

1. **One-shot qualified delivery is the real token optimization.** Cheap models iterate 5×, cost more than one correct delivery. Code/content as black box: input → qualified output.
2. **Division of labor is a compromise for limited human cognitive bandwidth — not an optimal design.** One agent, many roles, one knowledge layer. (Sub-agents for adversarial verification ≠ division of labor.)
3. **Knowledge must eliminate itself.** Darwinian decay: 90d unreferenced = retirement. A system that can forget > one that can only remember.
4. **Evolution is cognitive patching, not data accumulation.** We change rules you can `git diff`. "Thinks differently" ≠ "knows more."
5. **Quality converges, not just improves.** Error classes monotonically decrease. Carefulness doesn't scale. Gates do.
6. **Sessions are discontinuous. Intelligence shouldn't be.** 21 hooks fire between sessions. Gets better through use, not updates.
7. **If you can't measure it, you didn't build it.** OS Eval + golden set + change-triggered. Proves convergence in git.

**The compound loop itself is the product.** You can't extract one piece and get the same effect.

> 📖 **Full thesis + CLASS A case study + convergence evidence:** [docs/THESIS.md](./docs/THESIS.md)
>
> 📖 **Discussion #38:** [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38)

---

## Codebase (~170K LOC)

| Layer | LOC | Entry Points |
|-------|-----|--------------|
| **Core (spine)** | ~10K | `session_unit.py`, `prompt_builder.py` |
| **Core (extensions)** | ~41K | `core/` — DDD, evolution, proactive, code intel |
| **Skills** | ~50K | `backend/skills/s_*/` (86 modules) |
| **Frontend** | ~68K | `desktop/src/` — React 19, Tailwind, TanStack Query |
| **Tests** | ~76K | pytest + Vitest |

**Stack:** Tauri 2.0 (Rust) · React 19 · FastAPI · Claude Agent SDK + Bedrock · SQLite (WAL + FTS5)

---

## Resources

| What | Link |
|------|------|
| **Discussions (68)** | [Reading Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/35) — Builder 45min · Architect 60min · Leader 30min · [all](https://github.com/xg-gh-25/SwarmAI/discussions) |
| **AI Agent Pitfall Guide** | [EN PDF](./docs/ai-agent-pitfall-guide-en.pdf) · [中文 PDF](./docs/ai-agent-pitfall-guide.pdf) |
| **Design Docs** | [Platform](./docs/DDD-Platform-Overview.md) · [Pipeline](./docs/Autonomous-Pipeline-Design.md) · [Memory](./docs/Memory-Management-Design.md) · [Evolution](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) |
| **Contributing** | [CONTRIBUTING.md](./CONTRIBUTING.md) |

---

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="80px;" alt="XG" style="border-radius:50%"/>
        <br /><sub><b>XG</b></sub>
      </a>
      <br /><sub>Creator & Chief Architect</sub>
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="80px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br /><sub>AI Co-Developer (Claude Opus 4)</sub>
    </td>
  </tr>
</table>

[MIT License](./LICENSE)

---

<div align="center">

**SwarmAI — Human directs. AI delivers.**

</div>
