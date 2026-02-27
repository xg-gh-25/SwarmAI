# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI Agent Platform that enables users to create, manage, and chat with customizable AI agents powered by **Claude Agent SDK**. It is a desktop application built with Tauri 2.0 + React + Python FastAPI, using SQLite for storage and local filesystem + Git for skill management.

The **desktop version** (`desktop/` directory) is the primary development target and supports **Windows, macOS, and Linux**.

## Development Commands

### Desktop Development (Primary)

```bash
cd desktop

# Install dependencies
npm install

# Configure environment (required: set ANTHROPIC_API_KEY)
cp backend.env.example ../backend/.env
# Edit ../backend/.env and add your API key

# Development mode (hot reload)
npm run tauri:dev

# Build production
npm run build:all      # Full build: backend + frontend + Tauri
npm run build:backend  # Only Python backend (PyInstaller)
npm run tauri:build    # Only Tauri app (requires backend built first)

# Testing
npm run test           # Watch mode
npm run test:run       # Single run
npm run test -- src/components/Button.test.tsx  # Run specific test file
npm run lint
```

### Backend Development

```bash
cd backend

# Setup (using uv - recommended)
uv sync                          # Creates venv and installs deps from pyproject.toml
source .venv/bin/activate

# Run development server
python main.py
# or: uvicorn main:app --reload --port 8000

# Testing
pytest                                    # Run all tests
pytest tests/test_agent_manager.py -v     # Run specific test file
pytest tests/test_agent_manager.py::test_function_name -v  # Run single test
```

### Development Ports

| Service | Port | Notes |
|---------|------|-------|
| Vite (desktop dev) | 1420 | HMR on 1421 |
| Python backend | 8000 | Dynamic in production |
| Vite (web dev) | 5173 | If running web version |

## Architecture Overview

### High-Level Data Flow

```
User Input → React Frontend → FastAPI Backend → AgentManager
                                                     ↓
                                            ClaudeSDKClient
                                                     ↓
                                            Claude Code CLI
                                                     ↓
                                            SSE Streaming → UI
```

### Desktop-Specific Architecture

```
Tauri App
├── React Frontend (Vite bundle)
├── Rust Core (lib.rs)
│   ├── Sidecar lifecycle management
│   ├── Dynamic port assignment
│   └── IPC bridge (Tauri commands)
└── Python Backend (PyInstaller sidecar)
    ├── FastAPI server
    ├── SQLite database
    └── ClaudeSDKClient
```

**Key Desktop Concepts:**
- Python backend runs as a **sidecar process** managed by Tauri
- Port is dynamically assigned via `portpicker` in Rust
- Frontend uses `getBackendPort()` from `services/tauri.ts` to get the port
- Data stored in platform-specific directories:
  - macOS: `~/Library/Application Support/Owork/`
  - Windows: `%LOCALAPPDATA%\Owork\` (typically `C:\Users\YourUsername\AppData\Local\Owork\`)
  - Linux: `~/.local/share/owork/`

### Backend Structure

```
backend/
├── main.py                   # FastAPI entry point
├── config.py                 # Settings
├── routers/                  # API endpoints (agents, skills, mcp, chat, plugins)
├── core/
│   ├── agent_manager.py     # ClaudeSDKClient wrapper, hooks, security
│   ├── session_manager.py   # Conversation session storage
│   └── workspace_manager.py # Per-agent isolated workspaces
├── database/
│   └── sqlite.py            # SQLite implementation
└── schemas/                  # Pydantic models
```

### Frontend Structure (Desktop)

```
desktop/
├── src/
│   ├── services/
│   │   ├── api.ts           # Axios client with dynamic port
│   │   ├── tauri.ts         # Tauri IPC bridge
│   │   └── *.ts             # Service modules with toCamelCase()
│   ├── pages/               # Route components
│   ├── components/          # UI components
│   └── types/               # TypeScript interfaces
├── src-tauri/
│   ├── src/lib.rs          # Rust: sidecar management, CLI detection
│   ├── binaries/           # PyInstaller output goes here
│   └── tauri.conf.json     # Tauri configuration
└── scripts/
    ├── build.sh            # Full build script
    └── build-backend.sh    # PyInstaller packaging
```

## API Data Naming Convention (CRITICAL)

**Backend uses `snake_case`, Frontend uses `camelCase`.**

Transformation functions in `desktop/src/services/*.ts` handle conversion:

| Service | File | Functions |
|---------|------|-----------|
| Agents | `agents.ts` | `toSnakeCase()`, `toCamelCase()` |
| Skills | `skills.ts` | `toCamelCase()` |
| MCP | `mcp.ts` | `toCamelCase()` |
| Chat | `chat.ts` | `toSessionCamelCase()`, `toMessageCamelCase()` |

**When adding new fields:**

1. Add to backend Pydantic model (`backend/schemas/*.py`) - `snake_case`
2. Add to frontend TypeScript interface (`desktop/src/types/index.ts`) - `camelCase`
3. **Update the corresponding `toCamelCase()` function** - this is commonly forgotten!

## Claude Agent SDK Usage

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher

options = ClaudeAgentOptions(
    system_prompt="...",
    model="claude-sonnet-4-20250514",
    permission_mode="bypassPermissions",  # or "default", "acceptEdits", "plan"
    cwd="/path/to/workspace",
    setting_sources=['project', 'user'],  # For global user mode
    allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"],
    mcp_servers={"server": {"type": "stdio", "command": "...", "args": [...]}},
    hooks={"PreToolUse": [HookMatcher(matcher='Bash', hooks=[hook_fn])]}
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("Hello!")
    async for message in client.receive_response():
        # Process streaming messages
```

**Built-in Tools:** `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `Skill`, `TodoWrite`, `NotebookEdit`

## Security Architecture

Four-layer defense-in-depth model (see `SECURITY.md` for details):

1. **Workspace Isolation**: Per-agent directories in `<app_data_dir>/workspaces/{agent_id}/`
2. **Skill Access Control**: PreToolUse hook validates authorized skills
3. **File Tool Access Control**: Permission handler validates file paths
4. **Bash Command Protection**: Regex parsing blocks absolute paths outside workspace

**Agent Modes:**
- `globalUserMode: false` (default): Restricted to agent workspace
- `globalUserMode: true`: Full file access, uses `~/` as working directory
- `enableHumanApproval: true`: Dangerous commands prompt user confirmation

## Environment Variables

```env
# Required
ANTHROPIC_API_KEY=sk-ant-xxx

# Database
DATABASE_TYPE=sqlite

# Optional
DEFAULT_MODEL=claude-sonnet-4-5-20250929
CLAUDE_CODE_USE_BEDROCK=false
DEBUG=true
HOST=127.0.0.1
PORT=8000
RATE_LIMIT_PER_MINUTE=1000
```

## Key Patterns

### Adding a New Agent Field

1. Backend schema: `backend/schemas/agent.py`
2. Database: `backend/database/sqlite.py` (add column)
3. Agent manager: `backend/core/agent_manager.py` (use in `_build_options()`)
4. Frontend types: `desktop/src/types/index.ts`
5. Frontend service: `desktop/src/services/agents.ts` (both `toSnakeCase` and `toCamelCase`)
6. Frontend UI: `desktop/src/pages/AgentsPage.tsx`

### SSE Streaming Events

```json
{"type": "assistant", "content": [...], "model": "..."}
{"type": "tool_use", "content": [...]}
{"type": "tool_result", "content": [...]}
{"type": "ask_user_question", "toolUseId": "...", "questions": [...]}
{"type": "permission_request", "requestId": "...", "toolName": "...", "reason": "..."}
{"type": "result", "sessionId": "...", "durationMs": ..., "totalCostUsd": ...}
{"type": "error", "error": "..."}
```

### Debugging
- debug tauri app
```shell
open -n /Applications/Owork.app --env OWORK_DEBUG=1
```

```bat
set OWORK_DEBUG=1                                                                                                   
"C:\Users\Administrator\AppData\Local\Owork\owork.exe"
```

```bash
# Backend logs
tail -f logs/backend.log | grep -E "(PRE-TOOL|BLOCKED|ERROR)"

# Security events
tail -f logs/backend.log | grep "FILE ACCESS DENIED\|BASH FILE ACCESS\|BLOCKED.*Skill"

# SSE stream
# Browser DevTools → Network → Filter: stream
```

## Internationalization (i18n)

The desktop app supports multiple languages using `i18next`:

- **Locales**: `desktop/src/i18n/locales/{en,zh}.json`
- **Config**: `desktop/src/i18n/index.ts`

**When adding new UI text:**
1. Add the key to both `en.json` and `zh.json`
2. Use `useTranslation()` hook: `const { t } = useTranslation(); t('key.path')`
3. Use nested keys: `"agents": { "create": "Create Agent" }` → `t('agents.create')`

## Design System

- **Font**: Space Grotesk
- **Icons**: Material Symbols Outlined
- **Styling**: Tailwind CSS 4.x with CSS custom properties for theming

### Theme System

The app supports **light**, **dark**, and **system** (follows OS preference) themes.

**Key Files:**
- `desktop/src/index.css` - CSS custom properties (`:root` for light, `:root.dark` for dark)
- `desktop/src/contexts/ThemeContext.tsx` - React context for theme state management
- `desktop/src/index.html` - FOUC prevention script (applies theme before React loads)
- `desktop/src/pages/SettingsPage.tsx` - Theme selector UI

**Color Variables (defined in index.css):**
```css
--color-bg          /* Page background */
--color-card        /* Card/panel backgrounds */
--color-hover       /* Hover states */
--color-border      /* Borders */
--color-text        /* Primary text */
--color-text-muted  /* Secondary/muted text */
--color-input-bg    /* Form input backgrounds */
```

**Usage Pattern:**
```tsx
// ✅ Correct - use CSS variables
className="bg-[var(--color-card)] text-[var(--color-text)] border-[var(--color-border)]"

// ❌ Wrong - hardcoded dark theme colors
className="bg-dark-card text-white border-dark-border"
```

**Default Theme:** `system` (stored in localStorage as `theme`)

**When adding new components:**
1. Never use hardcoded colors like `bg-dark-*`, `text-white`, `text-muted`, `border-dark-*`
2. Always use CSS variables: `bg-[var(--color-*)]`, `text-[var(--color-*)]`, `border-[var(--color-*)]`
3. Exception: `text-white` on `bg-primary` buttons is intentional (white on blue)

## Build Outputs


**Desktop (macOS):**
```
desktop/src-tauri/target/release/bundle/
├── dmg/Owork_*.dmg
└── macos/Owork.app
```

**Desktop (Windows):**
```
desktop/src-tauri/target/release/bundle/
├── msi/Owork_*_x64.msi
└── nsis/Owork_*_x64-setup.exe
```

**Desktop (Linux):**
```
desktop/src-tauri/target/release/bundle/
├── deb/owork_*.deb
└── appimage/owork_*.AppImage
```

## Related Documentation

- [SECURITY.md](./SECURITY.md) - Complete security architecture
- [SKILLS_GUIDE.md](./SKILLS_GUIDE.md) - Creating and using skills
- [ARCHITECTURE.md](./ARCHITECTURE.md) - Cloud deployment architecture
- [desktop/BUILD_GUIDE.md](./desktop/BUILD_GUIDE.md) - Desktop build instructions (Chinese)
