<div align="center">

# SwarmAI User Guide

### From download to daily driver

</div>

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [macOS (Apple Silicon)](#macos-apple-silicon)
  - [Windows](#windows)
  - [Build from Source](#build-from-source)
- [First Launch](#first-launch)
- [Configuration](#configuration)
  - [AI Provider Setup](#ai-provider-setup)
  - [Settings Overview](#settings-overview)
- [Getting Started](#getting-started)
  - [Your First Conversation](#your-first-conversation)
  - [The Three-Column Layout](#the-three-column-layout)
  - [Multi-Tab Sessions](#multi-tab-sessions)
- [Core Features](#core-features)
  - [SwarmWS Explorer](#swarmws-explorer)
  - [Swarm Radar](#swarm-radar)
  - [Memory & Context](#memory--context)
  - [Skills](#skills)
  - [Drag-to-Chat](#drag-to-chat)
- [Channel Integration](#channel-integration)
  - [Slack](#slack)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Prerequisites

Before installing SwarmAI, you need:

| Requirement | Why | How to Get It |
|------------|-----|---------------|
| **Claude Code CLI** | SwarmAI's AI engine — spawns Claude as a subprocess | `npm install -g @anthropic-ai/claude-code` |
| **Node.js 18+** | Required for Claude Code CLI | [nodejs.org](https://nodejs.org/) or `brew install node` |
| **AI Provider Access** | At least one of the following: | See [AI Provider Setup](#ai-provider-setup) |
| — AWS Bedrock | Recommended. Claude models via your AWS account | [AWS Console](https://console.aws.amazon.com/bedrock/) |
| — Anthropic API Key | Direct API access | [console.anthropic.com](https://console.anthropic.com/) |

### Verify Prerequisites

```bash
# Check Node.js
node --version    # Should be 18+

# Check Claude Code CLI
claude --version  # Should return a version number

# Check AWS credentials (if using Bedrock)
aws sts get-caller-identity  # Should return your account info
```

---

## Installation

### macOS (Apple Silicon)

1. **Download** the `.dmg` file from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

2. **Install** — open the DMG and drag SwarmAI to your Applications folder

3. **Remove quarantine** — the app is not yet code-signed, so macOS will block it:
   ```bash
   xattr -cr /Applications/SwarmAI.app
   ```

4. **Launch** — double-click SwarmAI in Applications
   - If macOS still blocks it: **System Settings → Privacy & Security → scroll down → "Open Anyway"**

> **Note**: The current release supports Apple Silicon (M1/M2/M3/M4) only. Intel Mac support is not available yet.

### Windows

1. **Download** the `-setup.exe` (NSIS) or `.msi` installer from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

2. **Run the installer**
   - Windows SmartScreen may warn: "Windows protected your PC"
   - Click **"More info"** → **"Run anyway"**

3. **Install Git Bash** (if not already installed) — [git-scm.com/downloads/win](https://git-scm.com/downloads/win)
   - SwarmAI's backend uses bash scripts internally

4. **Launch** SwarmAI from the Start Menu or desktop shortcut

### Build from Source

For developers or contributors who want to run from source:

**Prerequisites**: Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

```bash
# Clone the repo
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI

# Install frontend dependencies
cd desktop
npm install

# Set up backend environment
cp .env.example ../backend/.env
# Edit ../backend/.env if needed (see AI Provider Setup below)

# Run in development mode
npm run tauri:dev

# Or build for production
npm run build:all
```

The development server starts the Tauri window, Vite dev server, and Python backend automatically.

---

## First Launch

When you first open SwarmAI:

1. **Workspace provisioning** — SwarmAI creates your personal workspace at `~/.swarm-ai/SwarmWS/`. This is a git-tracked directory where your knowledge, notes, and projects live.

2. **Context files** — 11 context files are created in `~/.swarm-ai/SwarmWS/.context/`. These define SwarmAI's identity, behavior, and memory. You'll customize some of these.

3. **Default project** — A "SwarmAI" project is created with DDD (Domain-Driven Design) documents as a reference.

4. **Database** — SQLite database initialized at `~/.swarm-ai/data.db` for sessions, todos, and metadata.

You should see the three-column layout: Explorer (left), Chat (center), Radar (right).

---

## Configuration

### AI Provider Setup

Open **Settings** (gear icon at the bottom of the left sidebar).

#### Option A: AWS Bedrock (Recommended)

Best for users with an AWS account. No API key management — uses your existing AWS credentials.

1. In Settings, enable the **Bedrock** toggle
2. Select your **AWS Region** (e.g., `us-east-1`)
3. Ensure your AWS credentials are configured:
   ```bash
   # Option 1: AWS SSO (recommended)
   aws sso login

   # Option 2: Static credentials
   aws configure
   ```
4. Your AWS account needs access to Claude models in Bedrock:
   - Go to [AWS Bedrock Console](https://console.aws.amazon.com/bedrock/) → Model access
   - Request access to **Claude 4.6 Opus** and/or **Claude 4.6 Sonnet**

#### Option B: Anthropic API Key

Direct API access, simpler setup:

1. Get an API key from [console.anthropic.com](https://console.anthropic.com/)
2. In Settings, enter your **Anthropic API Key**
3. Select your preferred model

#### Verify It Works

Send a test message in the chat — any greeting like "hello" will do. If you get a response, you're configured correctly.

### Settings Overview

| Setting | What It Does |
|---------|-------------|
| **AI Provider** | Bedrock or Anthropic API |
| **Model** | Claude Opus 4.6 (most capable) or Sonnet 4.6 (faster, cheaper) |
| **AWS Region** | Which Bedrock region to use |
| **MCP Servers** | External tool servers (Slack, GitHub, etc.) |

---

## Getting Started

### Your First Conversation

SwarmAI isn't just a chatbot — it's an agent that can take actions. Try these to get a feel:

```
"What can you do?"                    → Overview of capabilities
"Save a note about today's meeting"   → Creates a file in Knowledge/Notes/
"What's in my workspace?"             → Explores your SwarmWS directory
"Remember that I prefer dark mode"    → Saves to persistent memory
"Create a todo: review the Q2 report" → Adds to your Radar
```

### The Three-Column Layout

```
┌──────────────┬─────────────────────────┬──────────────┐
│   SwarmWS    │                         │    Swarm     │
│   Explorer   │      Chat Center        │    Radar     │
│              │                         │              │
│  Files       │   Your conversation     │  ToDos       │
│  Knowledge/  │   with Swarm            │  Sessions    │
│  Projects/   │                         │  Artifacts   │
│  Notes/      │   [Tab 1] [Tab 2] ...   │  Jobs        │
│              │                         │              │
└──────────────┴─────────────────────────┴──────────────┘
```

- **Left — SwarmWS Explorer**: Your personal workspace. Browse files, right-click for actions (rename, delete, open in system app). Files are organized into `Knowledge/`, `Projects/`, `Attachments/`, etc.

- **Center — Chat**: Where you interact with Swarm. Supports 1-4 parallel tabs — each is an independent session with its own context.

- **Right — Swarm Radar**: Your attention dashboard. Shows active todos, session list, artifacts from pipeline runs, and scheduled jobs.

### Multi-Tab Sessions

Click **"+"** in the tab bar to open a new chat tab. Each tab is fully independent:

- **Tab 1**: "Help me write an email to the team"
- **Tab 2**: "Debug this Python error" (paste code)
- **Tab 3**: "Research competitors in the AI agent space"

Tabs persist across app restarts. The number of concurrent tabs adapts to your available RAM (1-4 tabs).

---

## Core Features

### SwarmWS Explorer

Your workspace lives at `~/.swarm-ai/SwarmWS/` and is git-tracked automatically.

| Directory | What Goes Here |
|-----------|---------------|
| `Knowledge/Notes/` | Quick notes, jottings, ideas |
| `Knowledge/Reports/` | Research reports, analyses |
| `Knowledge/Meetings/` | Meeting notes and summaries |
| `Knowledge/Library/` | Reference material |
| `Knowledge/DailyActivity/` | Auto-generated session logs |
| `Projects/` | Project folders with DDD documents |
| `Attachments/` | Downloaded files, exports |
| `.context/` | SwarmAI's 11 context files (advanced) |

**File operations**: Right-click any file for rename, delete (moves to trash), or "Ask Swarm" (sends to chat for analysis).

**Git integration**: The explorer shows git status (modified, untracked, etc.) with color indicators. Swarm auto-commits workspace changes after each session.

### Swarm Radar

The right sidebar — your task and attention dashboard:

- **ToDos**: Work packets you or Swarm create. Each todo carries full context (description, related files, next steps). Drag a todo into chat and Swarm picks up exactly where it left off.
- **Sessions**: List of your chat sessions with timestamps.
- **Artifacts**: Outputs from autonomous pipeline runs.
- **Jobs**: Scheduled background tasks (if configured).

### Memory & Context

SwarmAI remembers across sessions. This is the core differentiator.

**What it remembers automatically:**
- Decisions you make ("let's use PostgreSQL for this project")
- Lessons learned ("that approach didn't work because...")
- Your preferences ("I prefer concise responses")
- Open threads and their status
- Git commits and deliverables from each session

**How to interact with memory:**
```
"Remember that the deploy key is in 1Password"  → Saves to MEMORY.md
"What do you remember about the auth refactor?" → Recalls from memory
"Forget the old API endpoint"                   → Removes from memory
"What did we work on this week?"                → Summarizes DailyActivity
```

**Customizing your profile** — edit `.context/USER.md` to tell Swarm about yourself:
- Your name, role, timezone
- Communication preferences
- Technical background
- Pet peeves

The more Swarm knows about you, the better it helps.

### Skills

SwarmAI comes with 55+ built-in skills. You don't need to install or configure them — just ask naturally:

| Category | Examples |
|----------|---------|
| **Productivity** | "Check my email", "What's on my calendar?", "Set a reminder" |
| **Research** | "Research this topic", "Summarize this article", "Deep dive into X" |
| **Documents** | "Create a PDF report", "Make a PowerPoint", "Write a Word doc" |
| **Data** | "Create a spreadsheet", "Analyze this CSV" |
| **Code** | "Review this PR", "Run QA on my changes", "Build a landing page" |
| **Browser** | "Go to this website and extract data", "Fill out this form" |
| **Media** | "Generate an image", "Transcribe this audio" |
| **System** | "Check system health", "What's eating my RAM?" |

### Drag-to-Chat

One of SwarmAI's most powerful features — **drag any item into chat**:

- **Drag a file** from Explorer → Swarm reads it and has full context
- **Drag a todo** from Radar → Swarm loads the full work packet and starts executing
- **Drag an artifact** → Swarm analyzes pipeline output

No copy-paste, no re-explaining. Just drag and go.

---

## Channel Integration

### Slack

SwarmAI can run as a Slack bot, giving you access to Swarm from any Slack conversation. It shares the same memory and context as your desktop app — one brain, multiple channels.

**Setup requirements:**
1. A Slack app with Bot Token and App Token (Socket Mode)
2. Environment variables: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`
3. The Slack daemon service running on your machine

**What you can do via Slack:**
- Ask Swarm questions using your full workspace context
- Swarm responds as your AI assistant
- Conversations carry over — ask on Slack, continue on desktop

> Detailed Slack setup guide coming soon. For now, see the channel adapter code in `backend/channels/adapters/slack.py`.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + N` | New chat tab |
| `Cmd/Ctrl + W` | Close current tab |
| `Cmd/Ctrl + 1-4` | Switch to tab 1-4 |
| `Cmd/Ctrl + ,` | Open Settings |
| `Enter` | Send message |
| `Shift + Enter` | New line in message |
| `Escape` | Stop streaming response |

---

## Troubleshooting

### "Error" on first message

**Symptom**: Red error banner when you send your first message.

**Common causes:**

1. **Claude Code CLI not installed**
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude --version  # Verify
   ```

2. **AWS credentials expired** (if using Bedrock)
   ```bash
   aws sts get-caller-identity  # Check if credentials work
   aws sso login                # Re-authenticate if needed
   ```

3. **No Bedrock model access**
   - Go to AWS Bedrock Console → Model access
   - Ensure Claude models are enabled in your selected region

4. **Initialization timeout** (multi-tab under load)
   - Close extra tabs, retry with a single tab
   - This happens when cold-starting multiple sessions simultaneously

### macOS: "SwarmAI is damaged and can't be opened"

The app isn't code-signed yet. Fix:
```bash
xattr -cr /Applications/SwarmAI.app
```

### macOS: App won't open (no error message)

1. Check System Settings → Privacy & Security → scroll down for "Open Anyway"
2. Try launching from terminal: `open /Applications/SwarmAI.app`

### Windows: SmartScreen blocks the installer

Click **"More info"** → **"Run anyway"**. This is normal for unsigned applications.

### Chat is slow / high latency

- **Cross-region Bedrock**: If your AWS region is far from your location, consider switching to a closer region in Settings
- **Multiple tabs**: Each tab spawns a separate Claude process. Close tabs you're not using.
- **MCP servers**: Each MCP server adds startup time. Disable unused ones in Settings.

### Workspace not showing files

- Check that `~/.swarm-ai/SwarmWS/` exists
- Try restarting the app — workspace is provisioned on startup
- Check the backend logs: `~/.swarm-ai/logs/`

### "Control request timeout: initialize"

The Claude subprocess took too long to start. Common with:
- Slow network + cross-region Bedrock
- Many MCP servers loading simultaneously
- System under memory pressure

Fix: close other tabs, wait 30 seconds, try again. The timeout is set to 180 seconds — if it persists, check your network connection.

---

## FAQ

### Is my data sent to the cloud?

Your **workspace files** stay local — they never leave your machine. The only data sent externally is your **chat messages** to the AI provider (AWS Bedrock or Anthropic API) for generating responses. No workspace files, memory, or context files are uploaded anywhere.

### Can I use SwarmAI offline?

No — SwarmAI requires an active internet connection to reach the AI provider (Bedrock or Anthropic API). Your workspace and memory are local, but the AI model runs in the cloud.

### How much does it cost to run?

SwarmAI itself is free (AGPL v3). The AI model usage costs depend on your provider:
- **AWS Bedrock**: Pay-per-token, billed to your AWS account. Claude Opus 4.6 is ~$15/M input tokens, ~$75/M output tokens. A typical heavy session uses $1-5.
- **Anthropic API**: Similar per-token pricing. Check [anthropic.com/pricing](https://www.anthropic.com/pricing) for current rates.

### Can multiple people use one installation?

SwarmAI is designed as a **personal** AI assistant — one user per installation. The workspace, memory, and context are all personalized to you. For team use, each person should have their own installation.

### How do I back up my workspace?

Your workspace at `~/.swarm-ai/SwarmWS/` is git-tracked. You can:
```bash
cd ~/.swarm-ai/SwarmWS
git remote add backup <your-backup-repo-url>
git push backup main
```

### How do I reset everything?

```bash
# Nuclear option — removes all SwarmAI data
rm -rf ~/.swarm-ai/

# Next launch will re-provision everything fresh
```

### Can I use models other than Claude?

Currently SwarmAI is built on the Claude Agent SDK and only supports Claude models (Opus 4.6, Sonnet 4.6). Support for other models is not planned — the deep integration with Claude's tool use and agentic capabilities is core to how SwarmAI works.

### Where are the logs?

- **Backend logs**: `~/.swarm-ai/logs/`
- **Frontend**: Browser DevTools (Cmd+Option+I in the Tauri window)
- **Claude CLI logs**: Check `~/.claude/` directory

---

<div align="center">

**Need help?** Open an issue at [github.com/xg-gh-25/SwarmAI/issues](https://github.com/xg-gh-25/SwarmAI/issues)

**SwarmAI** — Work smarter. Move faster. Stress less.

</div>
