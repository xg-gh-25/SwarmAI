<div align="center">

<img src="./assets/swarmai-logo-final.png" alt="SwarmAI Logo" width="120" />

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
| **Above Input — TSCC** | Live thread-scoped cognitive context (what AI is doing now) |
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
| Skills | `~/.swarm-ai/skills/` |
| Logs | `~/.swarm-ai/logs/` |
| Tab State | `~/.swarm-ai/open_tabs.json` |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Desktop Framework | Tauri 2.0 + React 19 + TypeScript 5.x |
| Backend | FastAPI (PyInstaller sidecar) |
| AI Engine | Claude Agent SDK + AWS Bedrock / Anthropic API |
| Database | SQLite (pre-seeded for fast startup) |
| Styling | Tailwind CSS 4.x + CSS custom properties |
| State Management | TanStack Query + useUnifiedTabState hook |
| Build Tools | Vite + Rust + PyInstaller |
| Testing | Vitest + fast-check (PBT) + pytest + Hypothesis |

---

## Security

Defense-in-depth with four layers:

| Layer | Protection |
|-------|------------|
| **Workspace Isolation** | Each agent runs in an isolated directory |
| **Skill Access Control** | PreToolUse hooks validate skill invocations |
| **File Tool Access Control** | Validates all file operation paths |
| **Bash Command Protection** | Parses and validates file paths in commands |

---

## Project Structure

```
SwarmAI/
├── desktop/                 # Desktop app (Tauri 2.0 + React)
│   ├── src/                 # React frontend
│   │   ├── pages/           # Route components (ChatPage, TasksPage, etc.)
│   │   ├── hooks/           # Custom hooks (useUnifiedTabState, useChatStreamingLifecycle)
│   │   ├── services/        # API service layer with case conversion
│   │   └── components/      # UI components
│   ├── src-tauri/           # Tauri/Rust sidecar management
│   └── resources/           # Bundled assets (seed.db)
│
├── backend/                 # FastAPI backend (Python)
│   ├── main.py              # Entry point with fast startup / full init paths
│   ├── core/                # Business logic (agent_manager, session_manager, etc.)
│   ├── routers/             # API routes (agents, chat, skills, mcp, settings)
│   ├── database/            # SQLite with migrations
│   └── schemas/             # Pydantic models
│
├── .kiro/specs/             # Product specifications (organized by topic)
└── assets/                  # Images and mockups
```

---

## Contributing

Issues and Pull Requests are welcome.

- **GitHub**: https://github.com/xg-gh-25/SwarmAI

---

<div align="center">

**SwarmAI — Your AI Team, 24/7**

*You supervise. Agents execute. Memory persists. Work compounds.*

</div>
