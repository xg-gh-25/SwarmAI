<div align="center">

<img src="./assets/swarmai-logo-final.png" alt="SwarmAI Logo" width="120" />

# SwarmAI

### Your AI Team, 24/7

*Work Smarter. Stress Less.*



**A Persistent Agentic Workspace for Knowledge Workers**

SwarmAI gives you a supervised team of AI agents that plan, act, and follow through across your work. It brings emails, meetings, communications, tasks, documents, and projects into one intelligent command center — where everything stays connected and moving forward.

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

Unlike traditional AI tools that reset every session, SwarmAI builds **private, long-term local memory**. It remembers context, preferences, and ongoing priorities so productivity compounds instead of starting from scratch each day.

**You delegate. Your AI team executes. Every action is transparent, reviewable, and secure.**

<div align="center">

| 🧠 You Supervise | 🤖 Agents Execute | 📁 Memory Persists | 📈 Work Compounds |
|:---:|:---:|:---:|:---:|
| Stay in control | Delegate daily tasks | Context accumulates | Productivity scales |

</div>

---

## Product Architecture

SwarmAI is built on **5 Core Pillars**:

### 1️⃣ Command — Execution & Interaction Layer

The operational control center where you interact with your AI team.

- **Work Threads**: Every chat session maps to a Swarm Task
- **Parallel Execution**: Run multiple tasks simultaneously
- **Full Audit Trail**: Transparent activity logs and summary outcomes

![SwarmAI Chat Interface](./assets/swarmai-chat-mockup.png)

### 2️⃣ Workspaces — Persistent Memory Layer

Structured memory containers that ensure work compounds over time.

- **Root Workspace**: Global memory with context, knowledge sources, and tools
- **Swarm Workspaces**: Project/domain-specific memory that inherits from Root
- **Persistent Context**: Every interaction enriches workspace memory

### 3️⃣ Swarm ToDos — Structured Intent Layer

Intelligent task extraction and management.

- **Auto-Extraction**: From email, calendar, Slack, meeting notes, Jira, and more
- **Smart Lifecycle**: Pending → Handled → Completed
- **Priority Management**: Organize and prioritize your work queue

### 4️⃣ Autonomy — Supervised Execution Layer

AI agents that execute under human supervision.

- **Delegated Execution**: Plan, execute, communicate, and report
- **Intelligent Extraction**: Continuous ToDo extraction — nothing slips through
- **Briefings & Subscriptions**: Inbox summaries, Slack digests, workspace health reports

### 5️⃣ Swarm Core — Personalization Layer

Your personal AI configuration center.

- **Personal Context**: Profile, goals, communication style, priorities
- **Knowledge Layer**: Local folders, cloud storage, vector database
- **Sub-Agents & Skills**: Specialized agents for research, documents, communication, and more
- **Tools & Integrations**: MCP tools, connected apps, plugins

---

## Core Models

| Entity | Layer | Function |
|--------|-------|----------|
| **Workspace** | Memory | Persistent structured context |
| **ToDo** | Intent | Structured work signal |
| **Task** | Execution | Active execution thread |
| **Chat** | Interface | User interaction surface |

---


## Desktop Application

### System Requirements

| Item | Requirement |
|------|-------------|
| Operating System | macOS 10.15+ (Catalina or later), Windows 10+ |
| Processor | Apple Silicon (M1/M2/M3) or Intel |
| Memory | 8GB RAM (16GB recommended) |
| Node.js | 18.0+ |

### Quick Installation

#### macOS

1. Download the latest release from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Double-click to open the DMG file
3. Drag SwarmAI.app to the Applications folder
4. If you encounter "File Damaged", execute in terminal:
```shell
xattr -cr /Applications/SwarmAI.app
```

#### Windows

1. Download the latest release from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Windows might display SmartScreen warning, click "More info" → "Run anyway"
3. Windows requires Git Bash dependency: https://git-scm.com/downloads/win

#### Configure API

After launching, go to the Settings page to configure:

- **Anthropic API**: Enter your API Key
- **AWS Bedrock**: Enable Bedrock toggle and configure authentication

### Build from Source

**Prerequisites:**
- Node.js 18+
- Python 3.11+
- Rust (install from https://rustup.rs/)
- uv (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`

```bash
cd desktop

# Install dependencies
npm install

# Configure environment variables
cp backend.env.example ../backend/.env
# Edit ../backend/.env and add ANTHROPIC_API_KEY

# Development mode
npm run tauri:dev

# Build production version
npm run build:all
```

### Data Storage

| Type | macOS Path | Windows Path |
|------|------------|--------------|
| Data Directory | `~/Library/Application Support/SwarmAI/` | `%LOCALAPPDATA%\SwarmAI\` |
| Database | `~/Library/Application Support/SwarmAI/data.db` | `%LOCALAPPDATA%\SwarmAI\data.db` |
| Skills | `~/Library/Application Support/SwarmAI/skills/` | `%LOCALAPPDATA%\SwarmAI\skills\` |
| Logs | `~/Library/Application Support/SwarmAI/logs/` | `%LOCALAPPDATA%\SwarmAI\logs\` |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Desktop Framework | Tauri 2.0 + React 19 + TypeScript |
| Backend | FastAPI (PyInstaller packaged as Sidecar) |
| AI Engine | Claude Agent SDK |
| Database | SQLite |
| Styling | Tailwind CSS 4.x |
| State Management | TanStack Query |
| Build Tools | Vite + Rust |

---

## Security

SwarmAI implements a **defense-in-depth security model** with four layers:

| Layer | Protection |
|-------|------------|
| **Workspace Isolation** | Each agent runs in an isolated directory |
| **Skill Access Control** | PreToolUse hooks validate skill invocations |
| **File Tool Access Control** | Validates all file operation paths |
| **Bash Command Protection** | Parses and validates file paths in commands |


## Configuration

### Environment Variables

#### Required
- `ANTHROPIC_API_KEY` - Anthropic API key

#### API Configuration
- `ANTHROPIC_BASE_URL` - Custom API endpoint
- `CLAUDE_CODE_USE_BEDROCK` - Use AWS Bedrock
- `DEFAULT_MODEL` - Default model

#### Server Configuration
- `DEBUG` - Debug mode
- `HOST` - Server host
- `PORT` - Server port

For complete configuration, see `backend/.env.example` or `desktop/backend.env.example`

---


## Project Structure

```
SwarmAI/
├── desktop/                 # Desktop app (Tauri 2.0)
│   ├── src/                 # React frontend source
│   ├── src-tauri/           # Tauri/Rust code
│   └── scripts/             # Build scripts
│
├── backend/                 # FastAPI backend
│   ├── main.py              # Application entry
│   ├── routers/             # API routes
│   ├── core/                # Core business logic
│   ├── database/            # SQLite database
│   └── schemas/             # Pydantic models
│
├── assets/                  # Images and mockups
└── .kiro/specs/             # Product specifications
```

---

## Contributing

Issues and Pull Requests are welcome!

- **GitHub**: https://github.com/xg-gh-25/SwarmAI

---



<div align="center">

**SwarmAI — Transform fragmented tasks into coordinated execution.**

*You delegate. Your AI team executes. Work compounds.*

</div>
