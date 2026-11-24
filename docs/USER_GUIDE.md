<div align="center">

# SwarmAI User Guide

### From download to daily driver

</div>

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [macOS](#macos)
  - [Windows](#windows)
  - [Linux](#linux)
  - [Build from Source](#build-from-source)
- [First Launch](#first-launch)
- [Configuration](#configuration)
  - [Connect Your AWS Account](#connect-your-aws-account)
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
- [File Attachments](#file-attachments)
- [Channel Integration](#channel-integration)
  - [Slack](#slack)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Prerequisites

Before installing SwarmAI, you need:

| Item | Requirement |
|------|-------------|
| **OS** | macOS 10.15+ (Apple Silicon or Intel), Windows 10/11 (x64), Linux x64 (Ubuntu 22.04+ or similar) |
| **Memory** | 16GB RAM (36GB recommended for multi-tab) |
| **Disk** | 2GB available |
| **Network** | Internet connection required |
| **AWS** | An AWS account with access to Claude models on Amazon Bedrock (chat runs on Bedrock) — see [Connect Your AWS Account](#connect-your-aws-account) |
| **Software** | git. On Windows: Git for Windows, which includes Git Bash ([git-scm.com](https://git-scm.com/downloads/win)) |

The app brings everything else it needs (its own Python backend and the Claude CLI), so you do
not install Node.js or the Claude CLI yourself. Building from source needs more tools; see
[Build from Source](#build-from-source).

### Verify Prerequisites

```bash
# Check git (on Windows, run this in Git Bash)
git --version
```

---

## Installation

Download the installer for your system from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases).

### macOS

1. **Download** the `.dmg` for your Mac (`aarch64` for Apple Silicon, `x64` for Intel)

2. **Install** — open the DMG and drag `SwarmAI.app` to your Applications folder

3. **Launch** — open SwarmAI. The app is not signed by Apple yet, so the first time macOS blocks it:
   - Open **System Settings → Privacy & Security**
   - Scroll to the message about SwarmAI and click **Open Anyway**
   - Confirm, then open SwarmAI again

### Windows

1. **Download** `SwarmAI_x.x.x_x64-setup.exe` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

2. **Run the installer**
   - Windows SmartScreen may warn: "Windows protected your PC"
   - Click **"More info"** → **"Run anyway"**

3. **Install Git for Windows** (it includes Git Bash) if it is not installed yet — [git-scm.com/downloads/win](https://git-scm.com/downloads/win)

4. **Launch** SwarmAI from the Start Menu or desktop shortcut

### Linux

1. **Download** `SwarmAI_x.x.x_amd64.AppImage` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

2. **Make it executable and run it:**
   ```bash
   chmod +x SwarmAI_*.AppImage
   ./SwarmAI_*.AppImage
   ```

3. If it says FUSE is missing, install it (Ubuntu: `sudo apt install libfuse2`), or run it
   with `--appimage-extract-and-run`

4. Make sure git is installed (Ubuntu: `sudo apt install git`)

### Build from Source

For developers or contributors who want to run from source:

**Prerequisites**: git, Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`). On Windows also Git Bash (part of Git for Windows).

```bash
# Clone the repo
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI

# Install frontend dependencies
cd desktop
npm install

# Set up backend environment
cp ../backend/.env.example ../backend/.env

# Run in development mode (recommended — picks up code changes instantly)
cd .. && ./dev.sh start

# Or run via Tauri dev mode (starts Tauri window + Vite + backend)
cd desktop && npm run tauri:dev

# Build for production
npm run build:all
# Build artifacts: ./src-tauri/target/release/bundle/
```

`./dev.sh start` runs the Python backend directly so code changes take effect immediately without rebuilding. Use `npm run build:all` when you need the production binary.

> **Note:** On macOS the installed backend runs as a **launchd daemon** that stays alive even when the desktop app is closed — enabling 24/7 Slack bot, background jobs, and scheduled tasks. On Windows and Linux the app starts the backend as its own child process.

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

### Connect Your AWS Account

SwarmAI's chat runs on Claude models on Amazon Bedrock, so you need an AWS account. (Make
sure Bedrock model access for Claude is enabled in that account:
[AWS Bedrock Console](https://console.aws.amazon.com/bedrock/) → Model access.)

On first launch SwarmAI asks you to connect it; you can also do it later in
**Settings → Swarm AWS Account**. Choose one way to sign in:

- **AWS SSO** — your company's IAM Identity Center, signed in through the browser
- **AWS login** — your AWS console account, signed in through the browser (good for a personal account)
- **Access keys** — an access key ID and secret access key

Or paste a **Bedrock API key**: chat works with it right away, but Hives, cloud storage and
voice still need a connected AWS account.

#### Verify It Works

Open Settings and confirm the backend is running (**System** tab) and your AWS account is
connected (**Swarm AWS Account** tab). Then send a test message in the chat — any greeting like
"hello" will do. If you get a response, you're configured correctly.

### Settings Overview

Open **Settings** (gear icon at the bottom of the left sidebar). It has 7 tabs:

| Tab | What It Does |
|-----|-------------|
| **General** | Language, theme, accent color, the repository workspace |
| **Swarm AWS Account** | Your AWS account sign-in, main region, and what the account can use (Bedrock, cloud storage, voice) |
| **LLM Models** | Where the model is called from, thinking, the model list and the default model |
| **Channels** | Connected channels; add, edit or disconnect Slack |
| **Backup** | Workspace backup status, schedule and manual backup |
| **System** | Backend status, system dependencies, storage paths, a link to MCP servers |
| **About** | Version, platform, update check, re-run the setup wizard |

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

**Keyword Recall** — when MEMORY.md grows large, SwarmAI uses a pure-filesystem recall system:
- **FTS5 full-text search** with **BM25 ranking** over your memory and knowledge files — no vector database, no embeddings, no external calls
- Fast, local, and fully inspectable — surfaces the most relevant entries by keyword relevance

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

SwarmAI comes with 90+ built-in skills. You don't need to install or configure them — just ask naturally:

| Category | Examples |
|----------|---------|
| **Productivity** | "Check my email", "What's on my calendar?", "Set a reminder" |
| **Research** | "Research this topic", "Summarize this article", "Deep dive into X" |
| **Documents** | "Create a PDF report" (md2pdf with CJK support), "Make a PowerPoint", "Write a Word doc" |
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

## File Attachments

SwarmAI supports **40+ file types** for attachment via the file picker or paste:

| Category | Formats | How They're Handled |
|----------|---------|-------------------|
| **Images** | jpeg, png, gif, webp | Native multimodal — sent directly to Claude |
| **PDF** | pdf (up to 23MB) | Native multimodal — parsed by Claude directly |
| **Text/Code** | md, txt, csv, py, ts, js, rs, go, java, etc. | Inline text — included in the prompt |
| **Office** | docx, xlsx, pptx | Saved to `Attachments/`, agent uses specialized skills (/s_docx, /s_xlsx, /s_pptx) |
| **Audio/Video** | mp3, wav, m4a, mp4, webm | Saved to `Attachments/`, agent transcribes via Whisper |
| **SVG** | svg | Opens in text editor with visual Preview toggle |
| **Binary** | sqlite, pyc, ttf, woff, jar, etc. | Info modal with "Open in Default App" button |

**Key behaviors:**
- Binary files are **never** sent inline — they're saved to `Attachments/` with a path hint for the agent
- The agent receives file-type-specific guidance (e.g., "use /s_pptx skill" for .pptx files)
- Size limits: PDF/documents 23MB (accounts for base64 overhead), text/CSV 5MB, images 20MB

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
| `Cmd/Ctrl + F` | Search in current chat |
| `Cmd/Ctrl + ,` | Open Settings |
| `Enter` | Send message |
| `Shift + Enter` | New line in message |
| `Escape` | Stop streaming response |

---

## Troubleshooting

### "Error" on first message

**Symptom**: Red error banner when you send your first message.

**Common causes:**

1. **AWS account not connected**
   - Open **Settings → Swarm AWS Account** and connect your account (see [Connect Your AWS Account](#connect-your-aws-account))

2. **AWS sign-in expired**
   - Open **Settings → Swarm AWS Account** and sign in again

3. **No Bedrock model access**
   - Go to AWS Bedrock Console → Model access
   - Ensure Claude models are enabled in your selected region

4. **Initialization timeout** (multi-tab under load)
   - Close extra tabs, retry with a single tab
   - This happens when cold-starting multiple sessions simultaneously

### macOS blocks the app

The app isn't signed by Apple yet. Open **System Settings → Privacy & Security**, scroll to
the message about SwarmAI and click **Open Anyway**, confirm, then open SwarmAI again.

### Windows: SmartScreen blocks the installer

Click **"More info"** → **"Run anyway"**. This is normal for unsigned applications.

### Linux: the AppImage does not start

Run `chmod +x` on it first. If it says FUSE is missing, install `libfuse2` (Ubuntu:
`sudo apt install libfuse2`) or run it with `--appimage-extract-and-run`.

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

Your **workspace files** stay local — they never leave your machine. The only data sent externally is your **chat messages** to Claude on Amazon Bedrock, in your AWS account, for generating responses. No workspace files, memory, or context files are uploaded anywhere.

### What happens when I close the app?

On macOS the installed **backend daemon** keeps running via launchd. This means:
- Slack bot stays responsive 24/7
- Scheduled jobs continue running (memory health, maintenance, etc.)
- Memory and context are maintained

When you reopen the app, it reconnects to the running daemon. To fully stop the backend: `launchctl bootout gui/$(id -u) com.swarmai.backend`.

On Windows and Linux the app starts the backend as its own child process, so the backend stops
when you close the app.

### Can I use SwarmAI offline?

No — SwarmAI requires an active internet connection to reach Claude on Amazon Bedrock. Your workspace and memory are local, but the AI model runs in the cloud.

### How much does it cost to run?

SwarmAI itself is free and open-source (MIT). The model usage is billed by Amazon Bedrock to your AWS account, pay-per-token. Check [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for the current rates of the Claude models.

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

Currently SwarmAI is built on the Claude Agent SDK and only supports Claude models (Opus 4.8, Opus 4.6, Sonnet 4.6). Support for other models is not planned — the deep integration with Claude's tool use and agentic capabilities is core to how SwarmAI works.

### Where are the logs?

- **Backend logs**: `~/.swarm-ai/logs/`
- **Frontend**: Browser DevTools (Cmd+Option+I in the Tauri window). The installed App has
  them only when started with `SWARMAI_DEBUG=1`: quit SwarmAI, then run
  `open --env SWARMAI_DEBUG=1 -a SwarmAI` (macOS). They are off otherwise, because the
  inspector can run any command the App's window can.
- **Claude CLI logs**: Check `~/.claude/` directory

---

<div align="center">

**Need help?** Open an issue at [github.com/xg-gh-25/SwarmAI/issues](https://github.com/xg-gh-25/SwarmAI/issues)

**SwarmAI** — Work smarter. Move faster. Stress less.

</div>
