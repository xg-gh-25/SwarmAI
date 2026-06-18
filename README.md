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

12 interconnected engines. Each independently valuable; together they compound.

| # | Engine | What It Does | Deep Dive |
|---|--------|-------------|-----------|
| 1 | **Context Management** | 11-file prompt architecture, 100K budget, 3-tier ownership | [docs](./docs/DDD-Platform-Overview.md) |
| 2 | **Memory Pipeline** | 4-tier persistence: DailyActivity → distillation → compound recall | [docs](./docs/Memory-Management-Design.md) |
| 3 | **DDD Cultivation** | Self-growing domain knowledge, 7-type ontology, Darwinian decay | [docs](./docs/DDD-Cultivation-Engine-HLD.md) |
| 4 | **Autonomous Pipeline** | One requirement → push-ready code. Dual-mode: Full + Goal Loop | [docs](./docs/Autonomous-Pipeline-Design.md) |
| 5 | **Pollinate Engine** | One message → multi-format brand content | [docs](./docs/Pollinate-Content-Engine.md) |
| 6 | **Self-Evolution** | Cognitive L0→L3 patching. 37 corrections, zero class repetition | [docs](./docs/Self-Evolution-Harness-Design.md) |
| 7 | **Self-Healing** | Invisible recovery: 5 sensors, auto-respawn, user sees nothing | — |
| 8 | **Multi-Tab + MessageStore** | Concurrent sessions, phase-gated single-writer, cross-tab isolation | — |
| 9 | **Hook System** | 21 hooks (17 runtime + 4 lifecycle). Sessions never cold-start | — |
| 10 | **Job System** | Background intelligence: 13 signal feeds, cron, budget-gated | — |
| 11 | **4-Platform Backend** | macOS daemon · Hive (EC2) · Windows · Linux. Compile-time isolation | — |
| 12 | **Skills + Channels** | 86 skills (lazy/always), Slack gateway, 3-tier permission | — |

**The compound loop:** Memory → Pipeline judgment → DDD → Evolution → Gates → Memory. Remove one, the rest weaken.

<img src="./assets/pipeline-architecture.svg" alt="Pipeline — Dual Mode (Full + Goal)" width="100%"/>

> 📊 More diagrams: [Flywheel](./assets/platform-flywheel.svg) · [Context](./assets/context-engineering.svg) · [Memory](./assets/memory-pipeline.svg) · [DDD](./assets/ddd-three-layer-stack.svg) · [Sessions](./assets/multi-tab-sessions.svg) · [Jobs](./assets/job-system.svg) · [Evolution](./assets/self-evolution.svg)

---

## Thesis & Design Philosophy

**Can one builder + AI operate at team scale?** We're testing it live.

<details>
<summary><strong>Seven design convictions (click to expand)</strong></summary>

1. **One-shot qualified delivery is the real token optimization.** Cheap models iterate 5×, cost more than one correct delivery. Code/content as black box: input → qualified output.
2. **Division of labor is a compromise for limited human cognitive bandwidth — not an optimal design.** One agent, many roles, one knowledge layer. (Sub-agents for adversarial verification ≠ division of labor.)
3. **Knowledge must eliminate itself.** Darwinian decay: 90d unreferenced = retirement. A system that can forget > one that can only remember.
4. **Evolution is cognitive patching, not data accumulation.** We change rules you can `git diff`. "Thinks differently" ≠ "knows more."
5. **Quality converges, not just improves.** Error classes monotonically decrease. Carefulness doesn't scale. Gates do.
6. **Sessions are discontinuous. Intelligence shouldn't be.** 21 hooks fire between sessions. Gets better through use, not updates.
7. **If you can't measure it, you didn't build it.** OS Eval + golden set + change-triggered. Proves convergence in git.

**The compound loop itself is the product.** You can't extract one piece and get the same effect.

</details>

> 📖 **Full thesis + CLASS A case study + convergence evidence:** [docs/THESIS.md](./docs/THESIS.md)
>
> 📖 **Discussion #39:** [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/39)

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
| **Discussions (45+)** | [Reading Matrix](https://github.com/xg-gh-25/SwarmAI/discussions/35) — Builder 45min · Architect 60min · Leader 30min |
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
