<div align="center">

# SwarmAI

### Human directs. AI delivers.

English | [中文](./README.zh-CN.md)

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](./LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20·%20Windows%20·%20Linux-blue.svg?style=flat)](#quick-start)
[![Built with](https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-8A2BE2.svg?style=flat)](https://github.com/anthropics/claude-code)

</div>

![SwarmAI](./assets/swarm-demo.gif)

<div align="center"><sub>▶ <a href="./assets/swarm-demo.mp4">Watch the full 60-second demo</a></sub></div>

---

**SwarmAI is a self-evolving Agent OS** — every interaction sharpens how the system judges, not just what it knows.

---

## Why an Agent OS

Every other AI tool starts each session from zero. SwarmAI doesn't — **value compounds.** A model answers; **a mind persists**: continuous across sessions, self-correcting, forgetting what stopped mattering, sharpening its judgment with use. Not a bigger model — the **operating system around one.**

---

## The Four Ideas

Everything in SwarmAI serves one of four:

### 🧬 Self-Evolution — it upgrades its own judgment

Most agent-memory projects pile up entries. SwarmAI separates **cognition** (the OS) from **knowledge** (the disk): one edited line in `SOUL.md` shifts judgment more than a thousand memory rows — and every change is a `git diff`. A recurring mistake doesn't become one more logged lesson; it becomes a **gate** — a path where the wrong move can't happen. Not aspirational: a dozen-plus live guards sit in [`security_hooks.py`](./backend/core/security_hooks.py) (commit gate, pytest guard, dangerous-command gate). Read the file — the wrong move is blocked in code, not in a guideline. Progress isn't a growing correction count; it's an error class that **stops recurring**.

### 🧠 Brain-first — every project is a domain brain

A project isn't a folder of files — it's a **brain** with one six-section structure (Identity · Knowledge · Gates · Capabilities · Delivery · Refresher), the same for every user and domain. Only what it governs varies: `0..N` assets of any kind — a code repo, a data source, a document corpus, a process, or nothing at all. A codebase, a research topic, a consultant's client, even "my wedding" get the *same* brain. Knowledge sediments in as you work; knowledge that stops mattering **decays and dies**. Accumulation without elimination is how every memory system rots.

### ⚙️ Agent OS — the cognition lives between sessions, not in them

Sessions are discontinuous; intelligence shouldn't be. Hooks fire *between* sessions so the next one starts warm. The system self-heals when a session breaks, cultivates and decays its knowledge on a schedule, and rebuilds each system prompt fresh from governed context files.

### 🖐️ Proprioception — it inhabits its body, not just answers through it

The desktop app isn't a frontend the agent talks *to* — it's a body it **senses and drives.** SwarmAI reads its own live UI state (which overlay is open, which tab is active, what's on the Canvas) and acts on it: opens its own Brain Hub, pushes a report onto the Canvas, flags a decision to your attention channel. It's inspectable in return — **TSCC** (Thread-Scoped Cognitive Context) shows the real cognition behind a turn: which files loaded, the token budget, every recall hit and its score, the security scan, the full prompt. Most agents are black boxes you send text to; this one has a body you watch move — and inspect while it does.

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
│  KNOWLEDGE LAYER         DDD (brains) · Memory · Evolution  │
├─────────────────────────────────────────────────────────────┤
│  AGENT HARNESS           Context · Sessions · Hooks · Jobs  │
└─────────────────────────────────────────────────────────────┘
```

Each engine is independently useful; together they form the loop that makes the system sharper with use. (Click `code` to read the engine itself — the implementation is the documentation.)

| Engine | What It Does | Read |
|--------|-------------|------|
| **Context Management** | Governed-file prompt architecture, tiered ownership, live-measured budget | [docs](./docs/DDD-Platform-Overview.md) |
| **Memory** | Tiered persistence: DailyActivity → distillation → compound recall (pure-filesystem FTS/BM25) | [docs](./docs/Memory-Management-Design.md) |
| **DDD Cultivation** | Self-growing domain brains, 7-type × 3-layer ontology, Darwinian decay | [docs](./docs/DDD-Cultivation-Engine-HLD.md) |
| **Self-Evolution** | Cognitive L0→L3 patching — recurring error classes become structural gates | [docs](./docs/Self-Evolution-Harness-Design.md) |
| **Autonomous Pipeline** | One requirement → push-ready code. 9 stages · 3 gates · 2 modes | [docs](./docs/Autonomous-Pipeline-Design.md) |
| **Pollinate** | One message → multi-format content. Same DDD-driven pattern, for media | [docs](./docs/Pollinate-Content-Engine.md) |
| **Self-Healing** | Invisible recovery: sensors, auto-respawn, the user sees nothing | [code](./backend/core/session_healing.py) |
| **Multi-Tab + MessageStore** | Concurrent sessions, phase-gated single-writer, cross-tab isolation | [code](./desktop/src/stores/MessageStore.ts) |
| **Hooks + Jobs** | Between-session hooks + background intelligence. Sessions never cold-start | [code](./backend/core/hook_builder.py) |
| **Eval** | Decoupled, system-level: golden set + git-bound regression gate | [docs](./docs/OS-Eval-Function-Design.md) |

**The compound loop:** Memory → Pipeline judgment → DDD brains → Evolution → Gates → Memory. Remove one, the rest weaken.

<img src="./assets/aidlc-autonomous-pipeline-v4.svg" alt="Autonomous Pipeline — 9 Stages · 3 Gates · 2 Modes" width="100%"/>

---

## 🤖 For AI Agents

Coding in this repo? Start with **[`AGENTS.md`](./AGENTS.md)** — data flow, process topology, conventions, and invariants. It's the agent-facing entry point; this README is the human one.

---

## Design Philosophy

1. **One-shot qualified delivery is the real token optimization.** Cheap models iterate 5×, costing more than one correct pass. Code and content are a black box: input → qualified output.
2. **Division of labor is a workaround for limited human bandwidth, not good design.** One agent, many roles, one knowledge layer. (Sub-agents for adversarial checks ≠ division of labor.)
3. **Knowledge must eliminate itself.** Darwinian decay: unreferenced knowledge retires. A system that can forget beats one that can only remember.
4. **Evolution is cognitive patching, not data accumulation.** We change rules you can `git diff`. "Thinks differently" ≠ "knows more."
5. **Quality converges, not just improves.** Error classes decrease monotonically. Carefulness doesn't scale; gates do.
6. **Sessions are discontinuous. Intelligence shouldn't be.** Hooks fire between sessions. It gets better through use, not updates.
7. **If you can't measure it, you didn't build it.** Eval + golden set + change-triggered regression, proven in git.

> 📖 Full thesis + case study: [docs/THESIS.md](./docs/THESIS.md)

---

## Stack

**Tauri 2.0** (Rust) · **React 19** · **FastAPI** · **Claude Agent SDK + Bedrock** · **SQLite** (WAL + FTS5)

Four-platform backend (compile-time isolation): macOS daemon (prebuilt `.dmg`) · Hive (EC2) · Windows · Linux (source-build).

---

## Resources

| What | Link |
|------|------|
| **Design Docs** | [Platform](./docs/DDD-Platform-Overview.md) · [Pipeline](./docs/Autonomous-Pipeline-Design.md) · [Memory](./docs/Memory-Management-Design.md) · [Evolution](./docs/Self-Evolution-Harness-Design.md) · [Pollinate](./docs/Pollinate-Content-Engine.md) |
| **AI Agent Pitfall Guide** | [EN PDF](./docs/ai-agent-pitfall-guide-en.pdf) · [中文 PDF](./docs/ai-agent-pitfall-guide.pdf) |
| **For AI Agents** | [AGENTS.md](./AGENTS.md) |
| **Contributing** | [CONTRIBUTING.md](./CONTRIBUTING.md) |

---

[MIT License](./LICENSE)

---

<div align="center">

**SwarmAI — Human directs. AI delivers.**

</div>
