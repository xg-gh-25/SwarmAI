# SwarmAI Architecture

**Version:** 5.0
**Last Updated:** March 2026
**Status:** Production

---

## 1. Overview

SwarmAI is a persistent agentic operating system for knowledge work. It's a desktop application where supervised AI agents plan, execute, and follow through on real work inside a persistent workspace.

### Tech Stack

| Layer | Technology |
|-------|------------|
| Desktop Shell | Tauri 2.0 (Rust) |
| Frontend | React 19 + TypeScript 5.x + Vite |
| Backend | FastAPI (Python 3.12+, PyInstaller sidecar) |
| AI Engine | Claude Agent SDK + ClaudeSDKClient |
| AI Providers | AWS Bedrock (default), Anthropic API |
| Database | SQLite (pre-seeded for fast startup) |
| Styling | Tailwind CSS 4.x + CSS custom properties |
| State | TanStack Query (server) + useUnifiedTabState (tabs) |
| Testing | Vitest + fast-check (frontend), pytest + Hypothesis (backend) |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Tauri 2.0 Desktop Shell (Rust)                         │
│  • Sidecar lifecycle management                         │
│  • Dynamic port assignment (portpicker)                 │
│  • IPC bridge (Tauri commands)                          │
│  • Auto-updater                                         │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
┌───────▼──────────┐    ┌────────▼─────────────────────┐
│  React Frontend  │    │  Python Backend (sidecar)     │
│  (Vite bundle)   │◄──►│  FastAPI on dynamic port      │
│                  │HTTP │                               │
│  Three-column    │+SSE │  ┌─────────────────────────┐ │
│  layout:         │     │  │ AgentManager             │ │
│  • SwarmWS (L)   │     │  │ • ClaudeSDKClient        │ │
│  • Chat (C)      │     │  │ • Session ID mapping     │ │
│  • Radar (R)     │     │  │ • Hook system            │ │
│  + TSCC panel    │     │  └────────┬────────────────┘ │
└──────────────────┘     │           │                   │
                         │  ┌────────▼────────────────┐ │
                         │  │ Claude Code CLI          │ │
                         │  │ (managed by SDK)         │ │
                         │  │ • Built-in tools         │ │
                         │  │ • MCP servers            │ │
                         │  │ • Skills                 │ │
                         │  └────────┬────────────────┘ │
                         │           │                   │
                         │  ┌────────▼────────────────┐ │
                         │  │ SQLite + Filesystem      │ │
                         │  │ • data.db (DB-canonical) │ │
                         │  │ • SwarmWS/ (filesystem)  │ │
                         │  │ • config.json            │ │
                         │  └─────────────────────────┘ │
                         └───────────────────────────────┘
```

---

## 3. Frontend Architecture

### Layout Model

Three-column layout with embedded cognitive context:

| Area | Component | Role |
|------|-----------|------|
| Left | SwarmWS Explorer | Persistent workspace memory, knowledge, projects |
| Center | ChatPage (multi-tab) | Command and execution surface |
| Above Input | TSCC Panel | Live thread-scoped cognitive context |
| Right | Swarm Radar | Attention and action control panel |

### Key Hooks

| Hook | Purpose |
|------|---------|
| `useUnifiedTabState` | Single source of truth for all tab state (Map + render counter) |
| `useChatStreamingLifecycle` | SSE streaming, messages, sessionId, pendingQuestion, isStreaming |
| `useTSCCState` | Thread-scoped cognitive context state |
| `useWorkspaceSelection` | Active workspace picker |
| `useRightSidebarGroup` | Mutual exclusion for right sidebar panels |
| `useFileAttachment` | File upload processing for chat input |

### Tab State Model

`useUnifiedTabState` uses `useRef<Map<string, UnifiedTab>>` + `useState` render counter:

- `tabMapRef`: Authoritative store — mutations don't trigger re-renders
- `renderCounter`: Bumped after mutations to trigger `useMemo` re-derivation
- `restoreFromFile()`: Loads tabs from `~/.swarm-ai/open_tabs.json` on startup
- Debounced save effect persists to file every 500ms
- Messages loaded lazily from backend API when a tab becomes active

### Frontend File Structure

```
desktop/src/
├── pages/
│   ├── ChatPage.tsx              # Main chat (multi-tab, streaming, TSCC)
│   ├── TasksPage.tsx             # Task management
│   ├── SettingsPage.tsx          # API and app configuration
│   └── chat/
│       ├── components/           # ChatHeader, ChatInput, SessionTabBar, TSCCPanel
│       ├── constants.ts          # Welcome message, sidebar configs
│       └── utils.ts              # Session grouping helpers
├── hooks/
│   ├── useUnifiedTabState.ts     # Tab state (Map + render counter)
│   ├── useChatStreamingLifecycle.ts  # SSE streaming lifecycle
│   ├── useTSCCState.ts           # Cognitive context state
│   └── useWorkspaceSelection.ts  # Workspace picker
├── services/
│   ├── api.ts                    # Axios client with dynamic port
│   ├── tauri.ts                  # Tauri IPC bridge
│   ├── chat.ts                   # Chat API + SSE streaming
│   ├── agents.ts                 # Agent CRUD with case conversion
│   ├── tabPersistence.ts         # File-based tab save/load
│   └── tscc.ts                   # TSCC snapshot API
├── components/
│   ├── chat/                     # PermissionRequestModal, ChatDropZone
│   ├── workspace-explorer/       # SwarmWS file tree
│   ├── layout/                   # ThreeColumnLayout, GlobalSearchBar
│   ├── modals/                   # Agents, Skills, MCP, Settings modals
│   └── common/                   # Spinner, ConfirmDialog, Toast
└── contexts/
    ├── LayoutContext.tsx          # Sidebar state, attached files
    ├── ExplorerContext.tsx        # File tree state
    └── ThemeContext.tsx           # Light/dark/system theme
```

---

## 4. Backend Architecture

### Startup Model

Two startup paths for fast launch:

```
App Launch
    │
    ▼
_ensure_database_initialized()
    │
    ├── data.db exists? ──YES──► Fast Path
    │                            • initialize_database(skip_schema=True)
    │                            • ensure_default_workspace()
    │                            • Cache workspace path
    │                            • channel_gateway.startup()
    │
    └── data.db missing? ──NO──► Full Init Path
                                 • Copy seed.db from bundled resources
                                 • initialize_database() (DDL + migrations)
                                 • run_full_initialization()
                                 • Register agents, skills, MCPs
                                 • channel_gateway.startup()
```

### Session ID Mapping

One chat tab = one stable App Session ID. The backend may create multiple SDK clients (e.g. after restarts), each with its own SDK Session ID. The app layer maps all SDK IDs back to the single app session ID.

```
Chat Tab (frontend)
  └── App Session ID: "9240de91..."     ← stable, never replaced
        ├── SDK Client #1: "9240de91..."  (IDs match on first create)
        ├── SDK Client #2: "7a3e4821..."  (after restart, new SDK ID)
        └── SDK Client #3: "fd1b01f8..."  (another restart)

All messages saved under: "9240de91..." (the App Session ID)
_active_sessions keyed by: "9240de91..."
```

Key fields in `session_context`:
- `app_session_id`: Stable ID from frontend (set when `is_resuming=True`)
- `sdk_session_id`: Internal SDK ID (set from `init` SystemMessage)
- `effective_session_id`: `app_session_id ?? sdk_session_id`

### Backend File Structure

```
backend/
├── main.py                        # FastAPI entry, lifespan, startup paths
├── config.py                      # Settings from ~/.swarm-ai/config.json
├── core/
│   ├── agent_manager.py           # ClaudeSDKClient wrapper, session mapping, hooks
│   ├── session_manager.py         # Session storage (DB + in-memory)
│   ├── initialization_manager.py  # Startup orchestration, workspace caching
│   ├── swarm_workspace_manager.py # SwarmWS filesystem (verify_integrity, context files)
│   ├── agent_sandbox_manager.py   # Per-agent isolated workspaces, skill symlinks
│   └── claude_sdk_env.py          # SDK environment config, credential validation
├── routers/
│   ├── agents.py                  # Agent CRUD
│   ├── chat.py                    # Chat SSE streaming, session management
│   ├── skills.py                  # Skill CRUD, upload, generation
│   ├── mcp.py                     # MCP server CRUD
│   ├── settings.py                # Config, open_tabs persistence
│   ├── system.py                  # Health, status, channels
│   ├── workspace.py               # Workspace CRUD, file operations
│   └── channels.py                # Channel gateway (Feishu, etc.)
├── database/
│   └── sqlite.py                  # SQLite with migrations, WAL mode
├── schemas/                       # Pydantic models (snake_case)
├── scripts/
│   └── generate_seed_db.py        # Build-time seed database generator
└── templates/                     # Default agent JSON, skill templates
```

---

## 5. Data Model

### Storage Model

| Category | Storage | Examples |
|----------|---------|----------|
| DB-Canonical | SQLite (`data.db`) | Agents, Skills, MCPs, Sessions, Messages, Tasks, ToDos |
| Filesystem | `~/.swarm-ai/SwarmWS/` | Knowledge/, Projects/, context files, artifacts |
| Config | `~/.swarm-ai/config.json` | API keys, model selection, Bedrock settings |
| Tab State | `~/.swarm-ai/open_tabs.json` | Open tabs, active tab ID |

### Key Database Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent configurations (model, prompt, permissions, skill/MCP IDs) |
| `skills` | Skill metadata (name, description, is_system) |
| `mcp_servers` | MCP server configurations (connection type, config JSON) |
| `chat_sessions` | Session metadata (agent_id, title, timestamps) |
| `chat_messages` | Message content (session_id, role, content JSON, model) |
| `swarm_workspaces` | Workspace records (name, file_path, settings) |
| `tasks` | Task management (title, status, session_id, project) |
| `todos` | ToDo items (title, priority, source, status) |
| `app_settings` | Key-value settings (initialization_complete, etc.) |

### SwarmWS Filesystem

```
~/.swarm-ai/SwarmWS/
├── context-L0.md              # Workspace-level fast routing context
├── context-L1.md              # Workspace-level detailed context
├── system-prompts.md          # System prompt templates
├── Knowledge/                 # Shared reusable assets
│   ├── context-L0.md
│   └── context-L1.md
└── Projects/                  # Active work containers
    ├── context-L0.md
    └── context-L1.md
```

---

## 6. API Design

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET/POST/PUT/DELETE | `/api/agents/*` | Agent CRUD |
| GET/POST/DELETE | `/api/skills/*` | Skill CRUD + upload |
| GET/POST/PUT/DELETE | `/api/mcp/*` | MCP server CRUD |
| POST | `/api/chat/stream` | Start SSE streaming conversation |
| POST | `/api/chat/answer` | Answer ask_user_question |
| POST | `/api/chat/permission` | Respond to permission request |
| POST | `/api/chat/stop` | Stop active session |
| GET | `/api/chat/sessions` | List chat sessions |
| GET | `/api/chat/sessions/{id}/messages` | Get session messages |
| GET/PUT | `/api/settings/open-tabs` | Tab state persistence |
| GET/PUT | `/api/settings/config` | App configuration |

### SSE Event Types

| Event | Fields | Description |
|-------|--------|-------------|
| `session_start` | `sessionId` | New or resumed session established |
| `assistant` | `content[]`, `model` | Assistant message chunk |
| `tool_use` | `content[]` | Tool invocation |
| `tool_result` | `content[]` | Tool execution result |
| `ask_user_question` | `toolUseId`, `questions[]` | Agent needs user input |
| `cmd_permission_request` | `requestId`, `toolName`, `reason` | Permission gate |
| `result` | `sessionId`, `durationMs`, `totalCostUsd` | Conversation complete |
| `error` | `error` | Error occurred |

---

## 7. Security

### Four-Layer Defense-in-Depth

1. **Workspace Isolation**: Each agent runs in `~/.swarm-ai/workspaces/{agent_id}/`
2. **Skill Access Control**: PreToolUse hooks validate skill invocations against agent config
3. **File Tool Access Control**: Permission handler validates all file paths stay within workspace
4. **Bash Command Protection**: Regex parsing blocks absolute paths outside workspace boundary

### Credential Model

- Config source of truth: `~/.swarm-ai/config.json`
- AWS credentials: Delegated to standard credential chain (never stored by app)
- Anthropic API key: Stored in config.json (user-provided)
- No database storage for credentials

---

## 8. Build & Deployment

### Build Pipeline

```
npm run build:all
    │
    ├── npm run prebuild
    │   └── python scripts/generate_seed_db.py → desktop/resources/seed.db
    │
    ├── bash scripts/build-backend.sh
    │   └── PyInstaller → desktop/src-tauri/binaries/swarmai-backend-{arch}
    │
    └── npm run tauri:build
        └── Tauri bundles frontend + backend sidecar
            ├── macOS: .dmg / .app
            ├── Windows: .msi / .exe
            └── Linux: .deb / .AppImage
```

### Data Directory

All user data in `~/.swarm-ai/`:

| Path | Content |
|------|---------|
| `data.db` | SQLite database |
| `config.json` | API and app configuration |
| `open_tabs.json` | Tab persistence state |
| `SwarmWS/` | Single persistent workspace |
| `skills/` | Installed skills |
| `workspaces/` | Per-agent sandboxed directories |
| `logs/` | Backend logs |
