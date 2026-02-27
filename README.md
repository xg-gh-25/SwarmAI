<div align="center">

<img src="./assets/swarmai-logo-final.png" alt="SwarmAI Logo" width="120" />

# SwarmAI

### Your AI Team, 24/7

*Work Smarter. Stress Less.*

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-2.0-FFC131?style=flat&logo=tauri&logoColor=white)](https://tauri.app/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?style=flat&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Claude](https://img.shields.io/badge/Claude-Agent_SDK-191919?style=flat&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat)](./LICENSE)

A full-stack AI agent platform built on Claude Agent SDK, supporting both desktop application and cloud deployment modes.

[✨ Features](#features) • [�️ Desktop App](#desktop-application) • [☁️ Cloud Deployment](#cloud-deployment) • [�️ Security](#security) • [📚 Documentation](#documentation)

</div>

---

## Overview

SwarmAI is a powerful AI Agent management platform that enables you to:

- **Chat with AI Agents**: Interactive chat interface with SSE streaming
![alt text](./assets/image-4.png)
- **Background task**: Create and run background tasks in batch mode
![alt text](./assets/image-5.png)
![alt text](./assets/image-7.png)

- **Manage Agents**: Create, configure, and monitor AI agents
![alt text](./assets/image-3.png)
- **Manage Skills**: Upload, install, and manage custom skills (with Git version control)
![alt text](./assets/image-2.png)
- **Manage Plugins**: Plugin system to extend platform functionality
![alt text](./assets/image-1.png)
- **Manage MCP Servers**: Configure Model Context Protocol servers
![alt text](./assets/image.png)

## Deployment Modes

| Mode | Frontend | Backend | Database | Skill Storage | Use Case |
|------|----------|---------|----------|---------------|----------|
| **Desktop** | Tauri 2.0 + React | Python FastAPI (Sidecar) | SQLite | Local filesystem + Git | Personal use |

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

## Add Plugins

In **Plugin Management** page, click **Install Plugin**, copy and paste plugin GitHub repo. Recommended:

| Name | Repo |
|------|------|
| Knowledge Work Plugins | https://github.com/anthropics/knowledge-work-plugins.git |
| Official Skills | https://github.com/anthropics/skills.git |
| Official Plugins | https://github.com/anthropics/claude-plugins-official.git |

> 📖 For detailed installation instructions, see [QUICK_START.md](./QUICK_START.md)

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

## Cloud Deployment

### Prerequisites

- Node.js 18+ and npm
- Python 3.12+
- uv (Python package manager, recommended) or pip
- AWS account (for Bedrock, optional)
- ANTHROPIC_API_KEY

### Quick Start

```bash
# Start backend and frontend
./start.sh

# Stop all services
./stop.sh
```

### Manual Setup

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend will be available at http://localhost:5173

#### Backend

```bash
cd backend

# Create virtual environment
uv sync
source .venv/bin/activate

# Configure environment variables
cp .env.example .env
# Edit .env and add ANTHROPIC_API_KEY

# Start server
python main.py
```

Backend API will be available at http://localhost:8000

API Documentation: http://localhost:8000/docs

---

## Tech Stack

### Desktop Version
- **Frontend Framework**: Tauri 2.0 + React 19 + TypeScript
- **Backend**: FastAPI (PyInstaller packaged as Sidecar)
- **Database**: SQLite
- **Build Tools**: Vite + Rust

### Common
- **AI Engine**: Claude Agent SDK
- **Styling**: Tailwind CSS 4.x
- **State Management**: TanStack Query
- **Routing**: React Router v6

---

## Features

### Chat Interface
- SSE real-time streaming responses
- Message history
- Tool call visualization
- File attachment support (images, PDF, TXT, CSV)
- Drag-and-drop and paste upload

### Agent Management
- Create, edit, and delete agents
- Configure model, max tokens, and permissions
- Assign skills and MCP servers to agents
- Global user mode (access full filesystem)
- Human approval mode (confirm dangerous operations)

### Skill Management
- Install skills from Git repositories
- Local skill directory
- Git version control (update, rollback)
- Install via ZIP upload

### Plugin Management
- Enable/disable plugins
- Plugin configuration

### MCP Server Management
- Support for stdio, SSE, HTTP connection types
- Connection status monitoring
- Test connection functionality

---

## Project Structure

```
SwarmAI/
├── desktop/                 # Desktop app (Tauri 2.0)
│   ├── src/                 # React frontend source
│   │   ├── components/      # UI components
│   │   ├── pages/           # Page components
│   │   ├── services/        # API services
│   │   └── types/           # TypeScript types
│   ├── src-tauri/           # Tauri/Rust code
│   │   ├── src/lib.rs       # Rust main logic
│   │   └── tauri.conf.json  # Tauri configuration
│   ├── scripts/             # Build scripts
│   └── BUILD_GUIDE.md       # Build guide
│
├── frontend/                # Cloud version frontend (React)
│   ├── src/
│   └── package.json
│
├── backend/                 # FastAPI backend
│   ├── main.py              # Application entry
│   ├── config.py            # Configuration
│   ├── routers/             # API routes
│   ├── core/                # Core business logic
│   │   ├── agent_manager.py # Agent management
│   │   ├── session_manager.py
│   │   └── local_skill_manager.py
│   ├── database/
│   │   └── sqlite.py        # SQLite
│   └── schemas/             # Pydantic models
│
├── CLAUDE.md                # Claude Code development guide
├── SECURITY.md              # Security architecture documentation
├── ARCHITECTURE.md          # System architecture documentation
└── README.md                # This file
```

---

## Security

The platform implements a **defense-in-depth security model**:

### Four-Layer Security Protection

1. **Workspace Isolation**: Each agent runs in an isolated directory
2. **Skill Access Control**: PreToolUse hooks validate skill invocations
3. **File Tool Access Control**: Validates all file operation paths
4. **Bash Command Protection**: Parses and validates file paths in bash commands

### Agent Modes

| Mode | Description |
|------|-------------|
| Default Mode | Can only access files within workspace |
| Global User Mode | Can access full filesystem (`~/` as working directory) |
| Human Approval Mode | Dangerous operations require user confirmation |

> 📖 For detailed security documentation, see [SECURITY.md](./SECURITY.md)

---

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
- `DATABASE_TYPE` - Database type (`sqlite`)

For complete configuration, see `backend/.env.example` or `desktop/backend.env.example`

---

## Documentation

| Document | Description |
|----------|-------------|
| [BUILD_GUIDE.md](./desktop/BUILD_GUIDE.md) | Desktop build guide |
| [CLAUDE.md](./CLAUDE.md) | Claude Code development guide |
| [SECURITY.md](./SECURITY.md) | Security architecture documentation |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | System architecture documentation |
| [SKILLS_GUIDE.md](./SKILLS_GUIDE.md) | Skill development guide |

---

## Design System

- **Primary Color**: `#2b6cee` (Blue)
- **Background**: `#101622` (Dark)
- **Card Background**: `#1a1f2e`
- **Font**: Space Grotesk
- **Icons**: Material Symbols Outlined

---

## License

MIT License

---

## Contributing

Issues and Pull Requests are welcome!

- **GitHub**: https://github.com/xg-gh-25/SwarmAI
