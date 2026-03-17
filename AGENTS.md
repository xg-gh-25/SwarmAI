# AGENT.md

Guidance for AI coding agents working with the SwarmAI codebase.

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
│   ├── session_manager.py         # Conversation session storage (DB + in-memory cache)
│   ├── initialization_manager.py  # Startup orchestration, workspace caching
│   ├── swarm_workspace_manager.py # SwarmWS filesystem (verify_integrity, projects)
│   ├── context_directory_loader.py# Centralized .context/ loader (10 files, L0/L1 cache)
│   ├── system_prompt.py           # Non-file prompt sections (safety, datetime, runtime)
│   ├── claude_environment.py      # SDK env config, credential validation
│   ├── app_config_manager.py      # In-memory config cache (config.json, zero-IO reads)
│   ├── skill_manager.py           # Filesystem skill discovery (3-tier: built-in > user > plugin)
│   ├── projection_layer.py        # Skill symlink projection into .claude/skills/
│   ├── plugin_manager.py          # Plugin marketplace, install/uninstall
│   ├── security_hooks.py          # 4-layer PreToolUse defense chain
│   ├── cmd_permission_manager.py  # Filesystem-backed command approvals (glob matching)
│   ├── permission_manager.py      # In-memory asyncio permission signaling
│   ├── chat_thread_manager.py     # ChatThread CRUD, project binding, summaries
│   ├── content_accumulator.py     # O(1) content block deduplication
│   ├── tool_summarizer.py         # Tool call summarization for UI
│   ├── tscc_state_manager.py      # Thread-scoped cognitive context (LRU)
│   └── credential_validator.py    # Pre-flight STS validation
├── routers/                       # API endpoints (agents, chat, skills, mcp, settings, etc.)
├── context/                       # Default context file templates (12 files)
├── skills/                        # Built-in skill definitions
├── database/
│   └── sqlite.py                  # SQLite with migrations, WAL mode
└── schemas/                       # Pydantic models
```

### Frontend Structure
```
desktop/src/
├── pages/
│   ├── ChatPage.tsx               # Main chat interface (multi-tab, streaming, TSCC)
│   ├── TasksPage.tsx              # Task management
│   ├── SettingsPage.tsx           # API & app configuration
│   ├── AgentsPage.tsx             # Agent configuration
│   ├── SkillsPage.tsx             # Skill management
│   ├── MCPPage.tsx                # MCP server management
│   ├── PluginsPage.tsx            # Plugin marketplace
│   └── ChannelsPage.tsx           # Channel gateway (Feishu, etc.)
├── hooks/
│   ├── useUnifiedTabState.ts      # Single source of truth for all tab state
│   ├── useChatStreamingLifecycle.ts # SSE streaming, messages, session management
│   ├── useTSCCState.ts            # Thread-scoped cognitive context
│   ├── useRightSidebarGroup.ts    # Sidebar mutual exclusion
│   ├── useFileAttachment.ts       # File upload processing
│   └── useRunningTaskCount.ts     # Background task tracking
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

## Context and Memory System

All agent context lives in `~/.swarm-ai/SwarmWS/.context/` — filesystem-only, no DB for context content.

10 source files assembled into the system prompt on every session start:
- P0–P2 (SWARMAI, IDENTITY, SOUL): system defaults, never truncated, readonly (0o444)
- P3 (AGENT): system default, truncatable
- P4–P9 (USER, STEERING, TOOLS, MEMORY, KNOWLEDGE, PROJECTS): user-customized, copy-only-if-missing (0o644)

Key behaviors:
- `ContextDirectoryLoader.ensure_directory()` runs at session start — two-mode copy
- Dynamic token budget: 40K for ≥200K models, 25K for 64K–200K, L0 compact for <64K
- L1 cache with budget-tier validation and git-first freshness check
- MEMORY.md truncates from head (keeps newest), all others truncate from tail
- DailyActivity: today + yesterday loaded ephemerally (2K token cap, disk never modified)
- `locked_write.py`: fcntl.flock for safe MEMORY.md modification by skills
- Auto-commit: git add -A + commit after every conversation turn (non-blocking background thread)

## Security Architecture

Four-layer PreToolUse defense chain:

1. **pre_tool_logger**: All tools — logs tool name + input keys (observability, never blocks)
2. **dangerous_command_blocker**: Bash only — 13 regex patterns (rm -rf /, fork bombs, etc.)
3. **human_approval_hook**: Bash only — CmdPermissionManager glob detection → SSE permission dialog → persistent approval
4. **skill_access_checker**: Skill only — validates skill in agent's allowed_skills set

Additional layers:
- **Workspace Isolation**: Per-agent directories
- **File Access Control**: `can_use_tool` handler validates paths (when `global_user_mode=False`)
- **Bash Sandboxing**: macOS/Linux sandbox with excluded commands
- **Error sanitization**: Strips tracebacks in production mode
- **CmdPermissionManager**: Filesystem-backed (`~/.swarm-ai/cmd_permissions/`), glob matching, shared across sessions

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

- **Font**: Inter (UI), JetBrains Mono (code)
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
