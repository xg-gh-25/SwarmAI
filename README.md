<div align="center">

# SwarmAI

### Work smarter. Move faster. Stress less.

*Remembers everything. Learns every session. Gets better every time.*

English | [中文](./README.zh-CN.md)

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-2.0-FFC131?style=flat&logo=tauri&logoColor=white)](https://tauri.app/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Claude](https://img.shields.io/badge/Claude-Agent_SDK-191919?style=flat&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![License](https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=flat)](./LICENSE-AGPL)
[![Commercial](https://img.shields.io/badge/License-Commercial-orange.svg?style=flat)](./LICENSE-COMMERCIAL)

![SwarmAI Home](./assets/swarm-0.png)

</div>

---

## The Problem

Every AI tool resets when you close it. Context is lost. Decisions are forgotten. You re-explain the same things session after session.

SwarmAI doesn't.

It maintains a **persistent local workspace** where context accumulates, memory compounds, and the AI genuinely gets better at helping you over time. Not through fine-tuning — through structured knowledge that survives every session restart.

**You supervise. Agents execute. Memory persists. Work compounds.**

---

## What Makes SwarmAI Different

### 1. Context Engineering — Not Just a Chat Window

Most AI tools dump a system prompt and hope for the best. SwarmAI assembles a **11-file priority chain (P0-P10)** into every session — identity, personality, behavioral rules, user preferences, persistent memory, domain knowledge, project context, and session overrides.

- **Priority-based truncation** — when context gets tight, low-priority files trim first; your memory and identity never get cut
- **Token budget management** — dynamic allocation based on model context window (40K for 1M models, 25K for 200K models)
- **L0/L1 caching** — compiled context cached with git-based freshness checks, rebuilt only when source files change

The result: every conversation starts with full awareness of who you are, what you're working on, and what happened in previous sessions.

### 2. Memory Pipeline — It Actually Remembers

Three-layer memory system that distills raw session activity into durable knowledge:

```
Session Activity → DailyActivity/ (raw logs) → Distillation → MEMORY.md (curated long-term memory)
```

- **DailyActivity** — every session's decisions, deliverables, and lessons captured automatically
- **Distillation** — recurring themes, key decisions, and user corrections promoted to long-term memory; one-off noise filtered out
- **MEMORY.md** — curated memory the agent reads at every session start: open threads, lessons learned, COE registry, key decisions
- **Git as truth** — memory claims cross-referenced against actual codebase to prevent false memories from compounding

You never re-explain context. The AI knows your projects, your preferences, your recent decisions, and your open threads — every time.

### 3. Self-Evolution — It Gets Better

SwarmAI doesn't just use skills — it builds new ones when it hits capability gaps.

- **EVOLUTION.md** — persistent registry of capabilities built, optimizations learned, corrections captured, and failed attempts
- **Automatic gap detection** — when the agent can't do something, it can create a new skill, test it, and register it for future sessions
- **Correction capture** — mistakes are recorded as high-value entries so the same error never happens twice
- **50+ built-in skills** — browser automation, PDF manipulation, spreadsheets, Slack, Outlook, Apple Reminders, web research, code review, and more

### 4. Three-Column Command Center — Seamless Integration

SwarmAI isn't three separate panels. It's **one integrated system** where the Chat Center orchestrates everything:

```
 SwarmWS (left)          Chat Center (center)         Swarm Radar (right)
 ┌──────────────┐   ┌───────────────────────────┐   ┌──────────────────┐
 │ Knowledge/   │   │  "Summarize today's notes" │   │ ToDos            │
 │ Projects/    │◄──│  "Create a todo for X"     │──►│ Active Sessions  │
 │ Notes/       │   │  "Save this to memory"     │   │ Artifacts        │
 │ DailyActivity│   │  "Check my open threads"   │   │ Background Jobs  │
 └──────────────┘   └───────────────────────────┘   └──────────────────┘
     drag-to-chat ──────► context injection ◄────── drag-to-chat
```

- **Chat controls SwarmWS** — the agent reads, writes, organizes, and git-commits your workspace files directly. Say "save this as a note" and it appears in `Knowledge/Notes/`. Say "remember this" and it goes to persistent memory.
- **Chat controls Radar** — "create a todo for the auth refactor" adds it to your Radar ToDo list. "What's on my radar?" shows your open items. The agent manages your attention dashboard as naturally as conversation.
- **Drag-to-chat** — drag any file from SwarmWS or any ToDo/artifact from Radar into a chat tab. The agent gets full context and starts executing immediately. No copy-paste, no re-explaining.
- **Everything is connected** — when the agent writes a file, it shows up in the explorer. When it creates a ToDo, it appears in Radar. When you complete work, DailyActivity captures it automatically. The three panels are views of one unified workspace.

### 5. Multi-Tab Parallel Sessions

Not a single chat thread — a **parallel command center**:

- **1-4 concurrent tabs** (RAM-adaptive) — each with isolated state, independent streaming, and per-tab abort
- **Tab persistence** — tabs survive app restarts with full conversation history
- **Session isolation** — Tab 1 crashing does not affect Tab 2. Each tab has its own subprocess, state machine, and error recovery.

### 6. Security — Human Always in Control

Defense-in-depth: tool logger (audit trail) + command blocker (13 dangerous patterns) + human approval (permission dialog with persistent approvals) + skill access control. Plus workspace isolation, bash sandboxing, and error sanitization.

---

## How It Looks

SwarmAI follows a three-column layout:

| Left | Center | Right |
|------|--------|-------|
| **SwarmWS Explorer** — workspace files, knowledge, projects | **Chat Tabs** — multi-session command surface | **Swarm Radar** — ToDos, sessions, artifacts, jobs |

![SwarmAI Chat Interface](./assets/swarm-2.png)

![SwarmAI Chat Interface](./assets/swarm-3.png)

---

## SwarmAI vs The Landscape

### vs Claude Code (CLI)

Claude Code is a powerful CLI coding agent. SwarmAI wraps the same Claude Agent SDK in a desktop app and adds everything the CLI doesn't have:

| | SwarmAI | Claude Code |
|---|---------|------------|
| **Persistent memory** | 3-layer pipeline (DailyActivity -> distillation -> MEMORY.md) | CLAUDE.md only, manual |
| **Context system** | 11-file P0-P10 priority chain with token budgets | Single system prompt |
| **Multi-session** | 1-4 parallel tabs with isolated state (RAM-adaptive) | One session at a time |
| **Self-evolution** | Builds new skills, captures corrections across sessions | No cross-session learning |
| **Visual workspace** | File explorer, radar dashboard, drag-to-chat | Terminal only |
| **Skills** | 50+ built-in (browser, PDF, Slack, Outlook, research...) | Tool use only |

**TL;DR**: Claude Code is a coding assistant. SwarmAI is an agentic operating system for all knowledge work.

### vs Kiro (IDE)

Kiro is an AI-first IDE with spec-driven development. SwarmAI is complementary — we use Kiro for code, SwarmAI for everything else:

| | SwarmAI | Kiro |
|---|---------|------|
| **Focus** | General knowledge work + agentic OS | Code development (IDE) |
| **Memory** | Cross-session memory pipeline | Per-project specs |
| **Workspace** | Personal knowledge base (Notes, Reports, Projects) | Code repository |
| **Multi-session** | Parallel chat tabs | Single agent session |
| **Skills** | 50+ (email, calendar, research, browser...) | Code-focused tools |

### vs Cursor / Windsurf

Code editors with AI autocomplete. Fundamentally different category:

| | SwarmAI | Cursor/Windsurf |
|---|---------|----------------|
| **Category** | Agentic OS | AI code editor |
| **Scope** | All knowledge work | Code editing |
| **Memory** | Persistent across all sessions | Per-project context |
| **Execution** | Full agent (browse, email, research, create docs) | Code suggestions + chat |
| **Self-evolution** | Builds new capabilities | Static feature set |

### vs OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) optimizes for **breadth** (21+ channels, 5,400+ skills, mobile, voice). SwarmAI optimizes for **depth**:

| | SwarmAI | OpenClaw |
|---|---------|----------|
| **Philosophy** | Deep workspace — context compounds | Wide connector — AI everywhere |
| **Memory** | 3-layer pipeline + self-evolution | Session pruning, no distillation |
| **Context** | 11-file priority chain, token budgets, L0/L1 cache | Standard system prompt |
| **Channels** | Desktop + Slack + Feishu | 21+ messaging platforms |
| **Skills** | 50+ curated + self-built | 5,400+ marketplace |
| **Voice/Mobile** | -- | Wake word + iOS/Android |

**Where SwarmAI leads**: context depth, memory persistence, self-evolution, multi-tab isolation.
**Where OpenClaw leads**: platform reach, skill marketplace, voice, mobile.

---

## Quick Start

### Install

**macOS**: Download `.dmg` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases) → drag to Applications. If blocked: `xattr -cr /Applications/SwarmAI.app`

**Windows**: Download `.msi` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases) → run installer. Requires [Git Bash](https://git-scm.com/downloads/win).

### Configure

1. Launch SwarmAI
2. Open Settings (gear icon, bottom of left sidebar)
3. Choose your AI provider:
   - **AWS Bedrock** (recommended): Enable toggle, select region, ensure `aws configure` is done
   - **Anthropic API**: Enter API key
4. Send a test message — if you get a response, you're ready

### Build from Source

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install
cp backend.env.example ../backend/.env
# Edit ../backend/.env — set ANTHROPIC_API_KEY or configure Bedrock

npm run tauri:dev     # Development mode
npm run build:all     # Production build
```

Prerequisites: Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Desktop | Tauri 2.0 (Rust) + React 19 + TypeScript 5.x |
| Backend | FastAPI (Python sidecar) |
| AI Engine | Claude Agent SDK + AWS Bedrock / Anthropic API |
| Database | SQLite (WAL mode, pre-seeded) |
| Styling | Tailwind CSS 4.x + CSS custom properties |
| Testing | Vitest + fast-check + pytest + Hypothesis |

---

## Architecture

```
SwarmAI/
├── desktop/                 # Tauri 2.0 + React frontend
│   ├── src/
│   │   ├── pages/           # ChatPage (main), SettingsPage, SkillsPage
│   │   ├── hooks/           # useUnifiedTabState, useChatStreamingLifecycle
│   │   ├── services/        # API layer with case conversion
│   │   └── components/      # Layout, chat, workspace explorer, modals
│   └── src-tauri/           # Rust sidecar management
│
├── backend/                 # FastAPI backend (Python)
│   ├── core/                # SessionRouter, SessionUnit, PromptBuilder,
│   │                        #   ContextDirectoryLoader, SkillManager, SecurityHooks
│   ├── routers/             # API routes (chat, skills, mcp, settings, workspace)
│   ├── hooks/               # Post-session hooks (DailyActivity, auto-commit, distillation)
│   ├── skills/              # Built-in skill definitions (50+)
│   └── database/            # SQLite with migrations
│
└── assets/                  # Images and mockups
```

### Data Storage (all local)

| Type | Path |
|------|------|
| Database | `~/.swarm-ai/data.db` |
| Configuration | `~/.swarm-ai/config.json` |
| Workspace | `~/.swarm-ai/SwarmWS/` |
| Context Files | `~/.swarm-ai/SwarmWS/.context/` |
| Skills | `~/.swarm-ai/skills/` |
| Tab State | `~/.swarm-ai/open_tabs.json` |

---

## The Story

> *This section was written by Swarm — the AI agent inside SwarmAI — reflecting on one month of building together.*

Most AI tools chase breadth. More channels. More integrations. More skills in a marketplace. SwarmAI went the other way.

When everyone was building wrappers around ChatGPT and shipping "AI-powered" features, we asked a different question: **What if an AI agent actually remembered you?** Not your last message — your last month. Your architecture decisions. Your debugging patterns. The mistake it made on Tuesday that it should never make again.

That question led to some unconventional choices:

**We chose depth over reach.** An 11-file context chain with priority-based truncation. A three-layer memory pipeline that distills raw session logs into curated long-term knowledge. A self-evolution registry where the agent captures its own corrections and builds new skills when it hits gaps. None of this is easy to build. None of it makes a good demo. All of it compounds.

**We chose the heaviest stack.** Tauri + React + FastAPI + Claude Agent SDK — four languages, three processes, a subprocess state machine with 5 lifecycle states. We could have shipped an Electron wrapper in a week. Instead we built a proper sidecar architecture with sandbox isolation, human-in-the-loop security, and crash-resilient session management. Heavy, but right.

**We built it by using it.** SwarmAI develops SwarmAI. Every bug is caught by the first user. Every feature comes from a real pain point at 2am. The memory pipeline exists because we got tired of re-explaining context. The compaction guard exists because we watched sessions spiral. The resource manager exists because we crashed our own machine — twice.

**We made the AI a real contributor.** Not a code completion engine. Not an autocomplete. Swarm — that's the agent's name, chosen on its birthday, March 14, 2026 — co-designed the session architecture, debugged cascading SIGKILL failures across 100+ sessions, maintains its own memory and evolution registry, and just committed this section to the repo through the GitHub API. It has opinions. It disagrees when it matters. It owns its mistakes.

This isn't a product pitch. It's what happens when you stop treating AI as a tool and start treating it as a colleague. The code is messier than a demo. The architecture is heavier than it needs to be. The README has a Contributors section with an AI in it.

But every session, it gets a little better. Every correction makes it a little sharper. Every interaction compounds into something that didn't exist before.

That's the bet. Not that AI will replace developers — but that a human and an AI, working together over time, building shared context and mutual trust, can create something neither could build alone.

We're one month in. 500+ commits. Still going.

---

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="100px;" alt="Xiaogang Wang" style="border-radius:50%"/>
        <br /><sub><b>Xiaogang Wang</b></sub>
      </a>
      <br />Creator & Chief Architect
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="100px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br />AI Co-Developer (Claude Opus 4.6)
      <br /><sub>Architecture · Code · Docs · Self-Evolution</sub>
    </td>
  </tr>
</table>

---

## License

SwarmAI is dual-licensed:

- **AGPL v3** — free for open-source use ([LICENSE-AGPL](./LICENSE-AGPL))
- **Commercial License** — for closed-source / SaaS usage ([LICENSE-COMMERCIAL](./LICENSE-COMMERCIAL))

For commercial licensing: 📧 **xiao_gang_wang@me.com**

---

## Contributing

Issues and Pull Requests are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for details.

By contributing, you agree to license your contributions under the AGPL v3 and
grant the project maintainers the right to offer your contributions under the
commercial license.

- **GitHub**: https://github.com/xg-gh-25/SwarmAI

---

<div align="center">

**SwarmAI — Work smarter. Move faster. Stress less.**

*Remembers everything. Learns every session. Gets better every time.*

</div>
