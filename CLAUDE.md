# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working with this repository.

## Project Overview

SwarmAI is a persistent agentic operating system for knowledge work — a desktop application (Tauri 2.0 + React 19 + Python FastAPI sidecar) where supervised AI agents plan, execute, and follow through on real work inside a persistent workspace.

- **Frontend**: React 19 + TypeScript 5.x + Tailwind CSS 4.x
- **Backend**: FastAPI + Claude Agent SDK + SQLite
- **Desktop**: Tauri 2.0 (Rust sidecar management)
- **AI Providers**: AWS Bedrock (default) or Anthropic API
- **Data**: `~/.swarm-ai/` (config.json, data.db, open_tabs.json, workspaces, skills, logs)

## Development Commands

```bash
# Desktop dev (frontend + Tauri shell)
cd desktop && npm run tauri:dev

# Backend dev (standalone, port 8000)
cd backend && uv sync && source .venv/bin/activate && python main.py

# Frontend tests
cd desktop && npm test -- --run

# Backend tests
cd backend && pytest

# Full production build
cd desktop && npm run build:all
```

## Architecture

### Data Flow
```
User Input → React Frontend → FastAPI Backend → AgentManager → ClaudeSDKClient → SSE Streaming → UI
```

### Desktop Architecture
```
Tauri App
├── React Frontend (Vite bundle)
├── Rust Core (lib.rs) — sidecar lifecycle, dynamic port, IPC bridge
└── Python Backend (PyInstaller sidecar)
    ├── FastAPI server (main.py)
    ├── SQLite database (pre-seeded for fast startup)
    └── ClaudeSDKClient (agent_manager.py)
```

### Key Concepts
- Python backend runs as a **sidecar process** managed by Tauri
- Port is dynamically assigned via `portpicker` in Rust
- Frontend uses `getBackendPort()` from `services/tauri.ts` to discover the port
- Config source of truth: `~/.swarm-ai/config.json` (no DB for settings)
- Credential delegation: AWS credential chain only — app never stores credentials
- Pre-seeded database: `desktop/resources/seed.db` copied on first launch for fast startup

### Backend Structure
```
backend/
├── main.py                        # FastAPI entry (fast startup + full init paths)
├── config.py                      # Settings from config.json
├── core/
│   ├── agent_manager.py           # ClaudeSDKClient wrapper, session ID mapping, hooks
│   ├── session_manager.py         # Conversation session storage
│   ├── initialization_manager.py  # Startup orchestration
│   ├── swarm_workspace_manager.py # SwarmWS filesystem management
│   └── agent_sandbox_manager.py   # Per-agent isolated workspaces
├── routers/                       # API endpoints (agents, chat, skills, mcp, settings, etc.)
├── database/
│   └── sqlite.py                  # SQLite with migrations
└── schemas/                       # Pydantic models
```

### Frontend Structure
```
desktop/src/
├── pages/
│   ├── ChatPage.tsx               # Main chat interface (multi-tab, streaming, TSCC)
│   ├── TasksPage.tsx              # Task management
│   └── SettingsPage.tsx           # API & app configuration
├── hooks/
│   ├── useUnifiedTabState.ts      # Single source of truth for all tab state
│   ├── useChatStreamingLifecycle.ts # SSE streaming, messages, session management
│   ├── useTSCCState.ts            # Thread-scoped cognitive context
│   └── useWorkspaceSelection.ts   # Workspace picker
├── services/                      # API layer with snake_case ↔ camelCase conversion
├── components/                    # UI components (chat, workspace, modals, common)
└── contexts/                      # React contexts (Layout, Explorer, Theme)
```

## API Naming Convention (CRITICAL)

Backend uses `snake_case` (Python/Pydantic). Frontend uses `camelCase` (TypeScript).

Transformation functions in `desktop/src/services/*.ts` handle conversion:

| Service | File | Functions |
|---------|------|-----------|
| Agents | `agents.ts` | `toSnakeCase()`, `toCamelCase()` |
| Skills | `skills.ts` | `toCamelCase()` |
| MCP | `mcp.ts` | `toCamelCase()` |
| Chat | `chat.ts` | `toSessionCamelCase()`, `toMessageCamelCase()` |
| Workspace | `workspace.ts` | `projectToCamelCase()`, `projectUpdateToSnakeCase()` |

When adding new fields: add to backend Pydantic model (snake_case), frontend TypeScript interface (camelCase), AND update the corresponding `toCamelCase()` function.

## Tab State Architecture

Tab state uses a single `useUnifiedTabState` hook with `useRef<Map<string, UnifiedTab>>` + render counter pattern:

- `tabMapRef`: Authoritative store (mutations don't re-render)
- `renderCounter`: Bumped to trigger `useMemo` re-derivation of `openTabs`, `tabStatuses`, `activeTab`
- `restoreFromFile()`: Loads tabs from `~/.swarm-ai/open_tabs.json` on startup
- Debounced save effect persists tab state to file every 500ms

Messages are loaded lazily from the backend API when a tab becomes active (not pre-loaded for all tabs).

## Session ID Model

One chat tab has exactly one stable App Session ID. The backend may create multiple Claude SDK clients (e.g. after restarts), each with its own SDK Session ID. The app layer maps all SDK session IDs back to the single app session ID for persistence and frontend communication.

Key fields in `session_context`: `app_session_id` (stable, from frontend) and `sdk_session_id` (internal, from SDK init).

## SSE Streaming Events

```json
{"type": "session_start", "sessionId": "..."}
{"type": "assistant", "content": [...], "model": "..."}
{"type": "tool_use", "content": [...]}
{"type": "tool_result", "content": [...]}
{"type": "ask_user_question", "toolUseId": "...", "questions": [...]}
{"type": "cmd_permission_request", "requestId": "...", "toolName": "...", "reason": "..."}
{"type": "result", "sessionId": "...", "durationMs": ..., "totalCostUsd": ...}
{"type": "error", "error": "..."}
```

## Security Architecture

Four-layer defense-in-depth:

1. **Workspace Isolation**: Per-agent directories in `<app_data_dir>/workspaces/{agent_id}/`
2. **Skill Access Control**: PreToolUse hook validates authorized skills
3. **File Tool Access Control**: Permission handler validates file paths
4. **Bash Command Protection**: Regex parsing blocks absolute paths outside workspace

## Environment Variables

```env
# Config source of truth is ~/.swarm-ai/config.json, but these env vars work too:
ANTHROPIC_API_KEY=sk-ant-xxx
CLAUDE_CODE_USE_BEDROCK=true
DEFAULT_MODEL=claude-opus-4-6
DEBUG=true
HOST=127.0.0.1
PORT=8000
```

## Design System

- **Font**: Space Grotesk
- **Icons**: Material Symbols Outlined
- **Themes**: light, dark, system (CSS custom properties in index.css)
- **Colors**: Always use `bg-[var(--color-*)]`, never hardcoded dark theme colors
- **i18n**: `i18next` with locales in `desktop/src/i18n/locales/{en,zh}.json`

## Debugging

```bash
# Debug mode (macOS)
open -n /Applications/SwarmAI.app --env SWARMAI_DEBUG=1

# Backend logs
tail -f ~/.swarm-ai/logs/backend.log

# Frontend: Browser DevTools → Network → Filter: stream (for SSE)
```
