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

# Backend tests (MUST use venv — system python is missing test deps)
cd backend && .venv/bin/python -m pytest

# Full production build
cd desktop && npm run build:all
```

## Architecture

### Data Flow
```
User Input → React Frontend → FastAPI Backend → SessionRouter → SessionUnit → ClaudeSDKClient → SSE Streaming → UI
```

### Desktop Architecture
```
Tauri App
├── React Frontend (Vite bundle)
├── Rust Core (lib.rs) — sidecar lifecycle, dynamic port, IPC bridge
└── Python Backend (PyInstaller sidecar)
    ├── FastAPI server (main.py)
    ├── SQLite database (pre-seeded for fast startup)
    └── SessionRouter → SessionUnit → ClaudeSDKClient
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
│   ├── session_router.py          # Multi-session routing, concurrency cap, slot management
│   ├── session_unit.py            # 5-state machine per tab (COLD/IDLE/STREAMING/WAITING_INPUT/DEAD)
│   ├── session_registry.py        # Global singletons, startup/shutdown, kill_all_claude_processes
│   ├── prompt_builder.py          # System prompt assembly, SDK options, MCP config
│   ├── lifecycle_manager.py       # 12hr TTL, orphan reaper, hook serialization
│   ├── session_utils.py           # Shared error helpers, retriable error detection
│   ├── skill_creator.py           # AI skill generation agent config
│   ├── session_manager.py         # Conversation session storage (DB + in-memory cache)
│   ├── initialization_manager.py  # Startup orchestration, workspace caching
│   ├── swarm_workspace_manager.py # SwarmWS filesystem (verify_integrity, projects)
│   ├── context_directory_loader.py# Centralized .context/ loader (11 files P0-P10, L0/L1 cache)
│   ├── system_prompt.py           # Non-file prompt sections (safety, datetime, runtime)
│   ├── claude_environment.py      # SDK env config, credential validation
│   ├── app_config_manager.py      # In-memory config cache (config.json, zero-IO reads)
│   ├── skill_manager.py           # Filesystem skill discovery (3-tier: built-in > user > plugin)
│   ├── projection_layer.py        # Skill symlink projection into .claude/skills/
│   ├── plugin_manager.py          # Plugin marketplace, install/uninstall
│   ├── security_hooks.py          # 4-layer PreToolUse defense chain
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
│   ├── SettingsPage.tsx           # API & app configuration
│   └── SkillsPage.tsx             # Skill browser (opened as modal from nav)
├── hooks/
│   ├── useUnifiedTabState.ts      # Single source of truth for all tab state
│   ├── useChatStreamingLifecycle.ts # SSE streaming, messages, session management
│   ├── useUnifiedAttachments.ts   # File attachment state management
│   ├── useTSCCState.ts            # Thread-scoped cognitive context
│   └── useRunningTaskCount.ts     # Background task tracking
├── services/                      # API layer with snake_case ↔ camelCase conversion
├── components/                    # UI components (chat, workspace, modals, common)
└── contexts/                      # React contexts (Layout, Explorer, Theme, Health)
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

11 source files (P0-P10) assembled into the system prompt on every session start:
- P0–P2 (SWARMAI, IDENTITY, SOUL): system defaults, never truncated, readonly (0o444)
- P3 (AGENT): system default, truncatable
- P4–P6 (USER, STEERING, TOOLS): user-customized, copy-only-if-missing (0o644)
- P7–P8 (MEMORY, EVOLUTION): agent-owned, copy-only-if-missing (0o644)
- P9–P10 (KNOWLEDGE, PROJECTS): user-customized, copy-only-if-missing (0o644)

Key behaviors:
- `ContextDirectoryLoader.ensure_directory()` runs at session start — two-mode copy (system overwrite vs user preserve)
- Dynamic token budget: 40K for ≥200K models, 25K for 64K–200K, L0 compact for <64K
- L1 cache with budget-tier validation (`<!-- budget:NNNNN -->` header) and git-first freshness check
- MEMORY.md truncates from head (keeps newest), all others truncate from tail
- DailyActivity: today + yesterday loaded ephemerally (2K token cap per file, disk never modified)
- BOOTSTRAP.md: ephemeral first-run onboarding (not in CONTEXT_FILES, detected separately)
- `locked_write.py`: fcntl.flock for safe MEMORY.md modification by skills

## Security Architecture

Four-layer PreToolUse defense chain:

1. **pre_tool_logger**: All tools — logs tool name + input keys (observability, never blocks)
2. **dangerous_command_blocker**: Bash only — 13 regex patterns (rm -rf /, fork bombs, etc.)
3. **human_approval_hook**: Bash only — CmdPermissionManager glob detection → SSE permission dialog → persistent approval
4. **skill_access_checker**: Skill only — validates skill in agent's allowed_skills set

Additional layers:
- **Workspace Isolation**: Per-agent directories in `<app_data_dir>/workspaces/{agent_id}/`
- **File Access Control**: `can_use_tool` handler validates paths (when `global_user_mode=False`)
- **Bash Sandboxing**: macOS/Linux sandbox with excluded commands
- **Error sanitization**: `_build_error_event()` strips tracebacks in production mode
- **CmdPermissionManager**: Filesystem-backed (`~/.swarm-ai/cmd_permissions/`), glob matching, shared across sessions
- **PermissionManager**: In-memory asyncio signaling (events + queue) for HITL flow

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
