# SwarmAI Quick Start Guide

Get SwarmAI running in minutes.

## System Requirements

| Item | Requirement |
|------|-------------|
| OS | macOS 10.15+ (Apple Silicon or Intel), Windows 10/11 (x64), Linux x64 (Ubuntu 22.04+ or similar) |
| Memory | 16GB RAM (36GB recommended for multi-tab) |
| Disk | 2GB available |
| Network | Internet connection required |
| AWS | An AWS account with access to Claude models on Amazon Bedrock (chat runs on Bedrock) |
| Software | git. On Windows: Git for Windows, which includes Git Bash ([git-scm.com](https://git-scm.com/downloads/win)) |

The app brings everything else it needs (its own Python backend and the Claude CLI). Building
from source needs more tools; see [Build from Source](#build-from-source).

---

## 1. Install SwarmAI

Download the installer for your system from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases).

### macOS

1. Download the `.dmg` for your Mac (`aarch64` for Apple Silicon, `x64` for Intel)
2. Open the DMG and drag `SwarmAI.app` to Applications
3. Open SwarmAI. The app is not signed by Apple yet, so the first time macOS blocks it:
   - Open **System Settings → Privacy & Security**
   - Scroll to the message about SwarmAI and click **Open Anyway**
   - Confirm, then open SwarmAI again

### Windows

1. Download `SwarmAI_x.x.x_x64-setup.exe`
2. Run it (if SmartScreen warns, click "More info" → "Run anyway")
3. Install [Git for Windows](https://git-scm.com/downloads/win) (it includes Git Bash) if it is not installed yet

### Linux

1. Download `SwarmAI_x.x.x_amd64.AppImage`
2. Make it executable and run it:
   ```bash
   chmod +x SwarmAI_*.AppImage
   ./SwarmAI_*.AppImage
   ```
3. If it says FUSE is missing, install it (Ubuntu: `sudo apt install libfuse2`), or run it
   with `--appimage-extract-and-run`
4. Make sure git is installed (Ubuntu: `sudo apt install git`)

### Build from Source

Prerequisites: git, Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`). On Windows also Git Bash (part of Git for Windows).

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install
cp ../backend/.env.example ../backend/.env

npm run build:all
# Build artifacts: ./src-tauri/target/release/bundle/
```

### Development Mode

Two paths to develop and test:

**`./dev.sh start`** — runs Python directly (picks up all new code, no rebuild needed)

```bash
# Start backend + frontend in dev mode
./dev.sh start

# Or start them separately:
./dev.sh backend   # Restart backend only (after Python changes)
./dev.sh frontend  # Start frontend only (backend already running)
./dev.sh kill      # Stop all dev processes
./dev.sh status    # Show what's running
```

**`./dev.sh deploy`** — builds backend binary and deploys to daemon (fastest way to push code changes)

```bash
# Build backend + deploy to daemon (3 min)
./dev.sh deploy

# Quick build: skip PyInstaller, rebuild Tauri only (frontend/Rust changes)
./dev.sh quick

# Full release (backend + DMG + tag + publish)
./prod.sh release
```

Use `./dev.sh start` for daily development — it runs the Python backend directly so code changes take effect immediately without rebuilding. Use `./dev.sh deploy` when you need to update the production daemon binary. Use `./prod.sh release` for full releases with DMG packaging.

> **Note:** On macOS the installed backend runs as a **launchd daemon** that stays alive even when the desktop app is closed. This enables 24/7 Slack bot, background jobs, and scheduled tasks. On Windows and Linux the app starts the backend as its own child process. The dev mode uses direct Python execution instead.

---

## 2. Connect Your AWS Account

SwarmAI's chat runs on Claude models on Amazon Bedrock, so you need an AWS account. (Make
sure Bedrock model access for Claude is enabled in that account.)

On first launch SwarmAI asks you to connect it; you can also do it later in
Settings → SwarmAI Account. Choose one way to sign in:

- **AWS SSO** — your company's IAM Identity Center, signed in through the browser
- **AWS login** — your AWS console account, signed in through the browser (good for a personal account)
- **Access keys** — an access key ID and secret access key

Or paste a **Bedrock API key**: chat works with it right away, but Hives, cloud storage and
voice still need a connected AWS account.

---

## 3. Verify Installation

Open SwarmAI Settings and confirm:

| Item | Expected |
|------|----------|
| Backend Service | ● Running |
| SwarmAI Account | AWS account connected |

Send a test message in the Chat page — if you get an AI response, you're all set.

---

## 4. Your First 5 Minutes

### The Layout

SwarmAI has three columns:
- **Left — SwarmWS Explorer**: Your persistent workspace. Knowledge, notes, projects — all git-tracked. Click the Swarm logo to toggle it.
- **Center — Chat Tabs**: Your command surface. Open multiple tabs (`Cmd+N`) for parallel conversations.
- **Right — Swarm Radar**: Live dashboard — ToDos, active sessions, recent artifacts, background jobs.

### Try These

1. **Ask anything** — "What can you do?" to see the 56+ built-in skills
2. **Open a second tab** — `Cmd+N` to run a parallel conversation
3. **Browse Skills** — click the puzzle icon in the left sidebar to see all capabilities
4. **Check MCP Servers** — click the hub icon to see connected tool servers (Slack, Outlook, etc.)
5. **Explore your workspace** — SwarmWS Explorer shows your Knowledge/, Projects/, and context files
6. **Say "remember this"** — the agent saves to persistent memory, available in every future session

### How Memory Works

SwarmAI remembers across sessions automatically:
- **DailyActivity/** — raw session logs captured after every conversation
- **MEMORY.md** — curated long-term memory (key decisions, lessons, open threads)
- **EVOLUTION.md** — capabilities the agent has built and corrections it has learned
- **.context/** — 11 files that define who the agent is and what it knows about you
- **Hybrid Recall** — FTS5 keyword search + sqlite-vec vector search for finding relevant memories on demand

Everything is local, git-tracked, and yours. No cloud sync, no data leaving your machine.

### File Attachments

SwarmAI supports **40+ file types** for attachment:
- **Native**: Images (jpeg/png/gif/webp), PDF, plain text, CSV, markdown
- **Office**: .docx, .xlsx, .pptx — routed through specialized skills
- **Audio/Video**: .mp3, .wav, .mp4, .webm — transcription via Whisper
- **Code**: .py, .ts, .js, .rs, .go, .java, and more
- **SVG**: Opens in text editor with visual Preview toggle

Binary files are saved to `Attachments/` with path hints for the agent. No more "Prompt is too long" errors.

---

## Data Storage

All data stays local in `~/.swarm-ai/` (on Windows `%USERPROFILE%\.swarm-ai\`):

| Type | Path |
|------|------|
| Database | `~/.swarm-ai/data.db` |
| Configuration | `~/.swarm-ai/config.json` |
| Workspace | `~/.swarm-ai/SwarmWS/` |
| Context Files | `~/.swarm-ai/SwarmWS/.context/` |
| Skills | `~/.swarm-ai/skills/` |
| Plugin Skills | `~/.swarm-ai/plugin-skills/` |
| Logs | `~/.swarm-ai/logs/` |

---

## FAQ

**Backend shows Stopped after startup?**
The first start can take a few minutes — it auto-starts. If it persists, check the logs in `~/.swarm-ai/logs/`.

**macOS blocks the app?**
Open System Settings → Privacy & Security and click **Open Anyway** next to the SwarmAI message.

**The Linux AppImage does not start?**
Run `chmod +x` on it first. If it says FUSE is missing, install `libfuse2` or run it with `--appimage-extract-and-run`.

**Windows SmartScreen warning?**
Click "More info" → "Run anyway".

**How to update?**
Download the new version, close SwarmAI, replace the app. Data is preserved automatically.

**How to completely uninstall?**
Remove the app, then optionally delete `~/.swarm-ai/` to remove all data.

---

## Get Help

- [GitHub Issues](https://github.com/xg-gh-25/SwarmAI/issues)
- See AGENTS.md for developer documentation
