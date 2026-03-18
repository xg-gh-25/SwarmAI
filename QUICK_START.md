# SwarmAI Quick Start Guide

Get SwarmAI running in minutes.

## System Requirements

| Item | Requirement |
|------|-------------|
| OS | macOS 10.15+, Windows 10/11, Linux (Ubuntu 20.04+) |
| Processor | x86_64 or ARM64 (Apple Silicon) |
| Memory | 8GB RAM (16GB recommended) |
| Disk | 500MB available |
| Network | Internet connection required |

---

## 1. Install SwarmAI

### macOS

1. Download the latest `.dmg` from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Open the DMG and drag `SwarmAI.app` to Applications
3. If macOS blocks the app:
   ```bash
   xattr -cr /Applications/SwarmAI.app
   ```

### Windows

1. Download `SwarmAI_x.x.x_x64.msi` from [GitHub Releases](https://github.com/xg-gh-25/SwarmAI/releases)
2. Run the installer (click "More info" → "Run anyway" if SmartScreen warns)
3. Install [Git Bash](https://git-scm.com/downloads/win) if not already present

### Build from Source

Prerequisites: Node.js 18+, Python 3.11+, Rust ([rustup.rs](https://rustup.rs/)), uv

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install
cp backend.env.example ../backend/.env
# Edit ../backend/.env — configure your API provider

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

**`./dev.sh build`** — rebuilds sidecar binary + Tauri app (takes longer, production-like)

```bash
# Full production build (PyInstaller + Tauri → DMG)
./dev.sh build

# Quick build: skip PyInstaller (frontend/Rust changes only)
./dev.sh quick
```

Use `./dev.sh start` for daily development — it runs the Python backend directly so code changes take effect immediately without rebuilding. Use `./dev.sh build` when you need to test the production binary or create a release.

---

## 2. Configure API

Launch SwarmAI and open Settings (gear icon in left sidebar).

### Option A: AWS Bedrock (Recommended)

1. Ensure you have an AWS account with Bedrock Claude model access
2. Configure AWS credentials:
   ```bash
   aws configure
   # Or for Amazon internal users:
   ada credentials update --account=ACCOUNT_ID --role=ROLE_NAME --provider=isengard
   ```
3. In SwarmAI Settings:
   - Enable "Use AWS Bedrock" toggle
   - Select your AWS Region
   - Verify the credentials status shows ✅
   - Save

### Option B: Anthropic API

1. In SwarmAI Settings:
   - Ensure "Use AWS Bedrock" is OFF
   - Enter your Anthropic API Key
   - Save

### Option C: LiteLLM Proxy

1. Deploy a [LiteLLM gateway](https://docs.litellm.ai/docs/simple_proxy)
2. In SwarmAI Settings:
   - Enter proxy URL in Base URL field
   - Enter your proxy API Key
   - Save


---

## 3. Verify Installation

Open SwarmAI Settings and confirm:

| Item | Expected |
|------|----------|
| Backend Service | ● Running |
| API Configuration | ✓ Configured |

Send a test message in the Chat page — if you get an AI response, you're all set.

---

## 4. Start Using SwarmAI

1. The Chat page is your command center — type naturally to explore or execute work
2. Open multiple tabs for parallel conversations
3. Check Swarm Radar (right sidebar) for ToDos, active tasks, and completed work
4. Use SwarmWS Explorer (left sidebar) to browse workspace memory and projects

---

## Data Storage

All data stays local in `~/.swarm-ai/`:

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
Wait a few seconds — it auto-starts. If it persists, check `~/.swarm-ai/logs/backend.log`.

**macOS blocks the app?**
Run `xattr -cr /Applications/SwarmAI.app` in Terminal.

**Windows SmartScreen warning?**
Click "More info" → "Run anyway".

**How to update?**
Download the new version, close SwarmAI, replace the app. Data is preserved automatically.

**How to completely uninstall?**
Remove the app, then optionally delete `~/.swarm-ai/` to remove all data.

---

## Get Help

- [GitHub Issues](https://github.com/xg-gh-25/SwarmAI/issues)
- See AGENT.md or CLAUDE.md for developer documentation
