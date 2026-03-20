<div align="center">

# SwarmAI

### Your AI Team, 24/7

*Work Smarter. Stress Less.*

**A Persistent Agentic Operating System for Knowledge Work**

SwarmAI gives you a supervised team of AI agents that plan, execute, and follow through on real work — emails, meetings, tasks, documents, and projects — inside a single persistent workspace where context accumulates and productivity compounds.

![SwarmAI Home](./assets/swarmai-home-mockup.png)

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-2.0-FFC131?style=flat&logo=tauri&logoColor=white)](https://tauri.app/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?style=flat&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Claude](https://img.shields.io/badge/Claude-Agent_SDK-191919?style=flat&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat)](./LICENSE)

</div>

---

## Why SwarmAI?

Most AI tools reset every session. SwarmAI doesn't.

It maintains **persistent local memory** across projects and workflows. You delegate intent signals, your AI team executes under governance, and outcomes become durable knowledge that compounds over time.

**You supervise. Agents execute. Memory persists. Work compounds.**

<div align="center">

| 🧠 You Supervise | 🤖 Agents Execute | 📁 Memory Persists | 📈 Work Compounds |
|:---:|:---:|:---:|:---:|
| Define goals & guardrails | Plan, coordinate, carry out work | Context accumulates across sessions | Outputs become reusable knowledge |

</div>

---

## Core Layout

SwarmAI follows a three-column layout with embedded cognitive context:


| Area | Role |
|------|------|
| **Left — SwarmWS Explorer** | Persistent workspace memory, knowledge, and project context |
| **Center — Chat Threads** | Command and execution surface with multi-tab support |
| **Right — Swarm Radar** | Unified attention and action control panel |

![SwarmAI Chat Interface](./assets/swarmai-chat-mockup.png)

---

## Product Architecture

### 1️⃣ Chat Command Center — Execution Surface

Chat is the primary command surface where you interact with your AI team.

- **Multi-Tab Sessions**: Run parallel conversations across multiple tabs
- **Execution Threads**: Each thread is a live workspace, not a passive chat log
- **Explore → Execute**: Start with brainstorming, convert to governed execution when ready
- **Full Audit Trail**: Transparent activity logs and outcome summaries
- **Persistent Messages**: Conversations survive app restarts with full history

### 2️⃣ SwarmWS — Persistent Workspace Memory

A single, non-deletable workspace that acts as your long-term memory container.

- **Shared Knowledge** (`Knowledge/`): Reusable assets, notes, and distilled memory
- **Active Work** (`Projects/`): Self-contained execution and knowledge containers
- **Hierarchical Context**: Context files at workspace, section, and project levels
- **Automatic Integrity**: System-managed templates, idempotent initialization, self-healing

### 3️⃣ Swarm Radar — Attention & Action Control

The right sidebar cockpit showing all work items across their lifecycle.

- **Needs Attention**: ToDos and items requiring your input
- **In Progress**: Active execution tasks with live status
- **Waiting Input**: Agent requests for clarification or permission
- **Completed**: Recently finished outcomes within an archive window
- **Autonomous Jobs**: Background and recurring agent work

### 4️⃣ TSCC — Thread-Scoped Cognitive Context

A collapsible panel above the chat input showing live AI cognition.

- Where am I working? (workspace/project scope)
- What is the AI doing right now?
- Which agents and capabilities are active?
- What sources ground the reasoning?
- What is the current working conclusion?

### 5️⃣ SwarmAgent — Governed Multi-Agent Orchestration

A central orchestrator that coordinates specialized subagents under human supervision.

- **Skills & MCP Tools**: Extensible capabilities via skills, MCP servers, and plugins
- **Human-in-the-Loop**: Permission gates before sensitive or irreversible actions
- **Observable Execution**: TSCC shows which agents, tools, and sources are active
- **AWS Bedrock & Anthropic API**: Flexible model provider configuration

---

## Desktop Application

### System Requirements

| Item | Requirement |
|------|-------------|
| Operating System | macOS 10.15+, Windows 10+, Linux (Ubuntu 20.04+) |
| Processor | Apple Silicon (M1+) or Intel x86_64 |
| Memory | 8GB RAM (16GB recommended) |
| Disk Space | 500MB available |
| Network | Internet connection required |

### Quick Installation

#### macOS

1. Download the latest `.dmg` from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Open the DMG and drag SwarmAI.app to Applications
3. If macOS blocks the app: `xattr -cr /Applications/SwarmAI.app`

#### Windows

1. Download the latest `.msi` from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Run the installer (click "More info" → "Run anyway" if SmartScreen warns)
3. Requires [Git Bash](https://git-scm.com/downloads/win) as a system dependency

#### Configure API

After launching, open Settings to configure your AI provider:

- **Anthropic API**: Enter your API Key
- **AWS Bedrock**: Enable Bedrock toggle, configure AWS region, ensure credentials are set via `aws configure` or credential chain

### Build from Source

**Prerequisites:** Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install
cp backend.env.example ../backend/.env
# Edit ../backend/.env — set ANTHROPIC_API_KEY or configure Bedrock

npm run tauri:dev     # Development mode
npm run build:all     # Production build
```

### Data Storage

All data is stored locally in `~/.swarm-ai/`:

| Type | Path |
|------|------|
| Database | `~/.swarm-ai/data.db` |
| Configuration | `~/.swarm-ai/config.json` |
| Workspaces | `~/.swarm-ai/SwarmWS/` |
| Context Files | `~/.swarm-ai/SwarmWS/.context/` |
| Skills | `~/.swarm-ai/skills/` |
| Plugin Skills | `~/.swarm-ai/plugin-skills/` |
| Command Permissions | `~/.swarm-ai/cmd_permissions/` |
| Logs | `~/.swarm-ai/logs/` |
| Tab State | `~/.swarm-ai/open_tabs.json` |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Desktop Framework | Tauri 2.0 + React 19 + TypeScript 5.x |
| Backend | FastAPI (PyInstaller sidecar) |
| AI Engine | Claude Agent SDK + AWS Bedrock / Anthropic API |
| Database | SQLite (pre-seeded for fast startup, WAL mode) |
| Styling | Tailwind CSS 4.x + CSS custom properties |
| State Management | TanStack Query + useUnifiedTabState hook |
| Build Tools | Vite + Rust + PyInstaller |
| Testing | Vitest + fast-check (PBT) + pytest + Hypothesis |

---

## Security

Defense-in-depth with four PreToolUse hook layers:

| Layer | Protection |
|-------|------------|
| **Tool Logger** | Logs all tool invocations (observability, never blocks) |
| **Command Blocker** | Regex blocks 13 dangerous bash patterns (rm -rf /, fork bombs, etc.) |
| **Human Approval** | Glob-based detection → SSE permission dialog → persistent filesystem-backed approvals |
| **Skill Access Control** | Validates skill invocations against agent's allowed list |

Additional: workspace isolation, file path validation, bash sandboxing, error sanitization in production.

---

## Project Structure

```
SwarmAI/
├── desktop/                 # Desktop app (Tauri 2.0 + React)
│   ├── src/                 # React frontend
│   │   ├── pages/           # Route components (ChatPage, TasksPage, SettingsPage, etc.)
│   │   ├── hooks/           # Custom hooks (useUnifiedTabState, useChatStreamingLifecycle)
│   │   ├── services/        # API service layer with case conversion
│   │   └── components/      # UI components
│   ├── src-tauri/           # Tauri/Rust sidecar management
│   └── resources/           # Bundled assets (seed.db)
│
├── backend/                 # FastAPI backend (Python)
│   ├── main.py              # Entry point with fast startup / full init paths
│   ├── core/                # Business logic (session_router, session_unit, prompt_builder,
│   │                        #   context_directory_loader, skill_manager, security_hooks, etc.)
│   ├── routers/             # API routes (agents, chat, skills, mcp, plugins, settings, etc.)
│   ├── context/             # Default context file templates (12 files)
│   ├── skills/              # Built-in skill definitions
│   ├── database/            # SQLite with migrations
│   └── schemas/             # Pydantic models
│
├── .kiro/specs/             # Architecture docs and product specifications
└── assets/                  # Images and mockups
```

---

## SwarmAI vs OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) is an open-source local-first AI assistant (300k+ GitHub stars) with broad platform reach. Here's how SwarmAI differs:

| Dimension | SwarmAI | OpenClaw |
|-----------|---------|----------|
| **Philosophy** | Deep workspace — context compounds | Wide connector — AI everywhere |
| **Runtime** | Tauri 2.0 desktop app | Node.js Gateway daemon |
| **AI Engine** | Claude (Bedrock / Anthropic) | OpenAI primary, multi-provider |
| **Channels** | 2 (Slack, Feishu) | 21+ (WhatsApp, Telegram, Discord, Signal, ...) |
| **Skills** | 42 built-in + user skills | 5,400+ via ClawHub marketplace |
| **Voice** | -- | Wake word + continuous voice |
| **Mobile** | -- | iOS / Android node devices |

### Where SwarmAI leads

- **Context & Memory depth** — 11-file priority chain (P0-P10), token budget management, 3-layer memory pipeline (DailyActivity -> distillation -> MEMORY.md) with code-enforced lifecycle hooks. OpenClaw uses session pruning with no structured knowledge distillation.
- **Self-evolution engine** — Detects capability gaps, builds new skills, persists learnings to EVOLUTION.md across sessions. Guardrailed with approval gates and per-session trigger limits.
- **Multi-tab session isolation** — 3-6 concurrent tabs with 8 strict isolation principles. Per-tab abort controllers, stream handlers, and session identity.
- **Desktop-native experience** — Tauri provides deeper OS integration than a background daemon.

### Where OpenClaw leads

- **Platform reach** — 21+ messaging integrations vs 2. If "AI on every touchpoint" is the goal, OpenClaw wins on coverage.
- **Skill ecosystem** — ClawHub's 5,400+ community skills with auto-search-and-pull far exceeds SwarmAI's curated 42.
- **Voice & mobile** — Wake word detection, continuous voice mode, and mobile node devices are capabilities SwarmAI doesn't yet have.
- **Visual Canvas** — Live Canvas with A2UI provides an agent-driven visual workspace.

### TL;DR

SwarmAI optimizes for **depth** — making AI truly understand your work and compound value over time. OpenClaw optimizes for **breadth** — putting AI on every device and channel you use. Different problems, complementary strengths.

---

## Contributing

Issues and Pull Requests are welcome.

- **GitHub**: https://github.com/xg-gh-25/SwarmAI

---

<div align="center">

**SwarmAI — Your AI Team, 24/7**

*You supervise. Agents execute. Memory persists. Work compounds.*

</div>
