# SwarmAI Codebase Summary

**Date:** 2026-02-27  

---

## Architecture

SwarmAI is a desktop AI assistant built with:

- **Desktop Shell:** Tauri 2.0 (Rust) + React 19 + TypeScript
- **Backend Sidecar:** Python FastAPI with Claude Agent SDK
- **Database:** SQLite (via aiosqlite)
- **Data Directory:** `~/.swarm-ai/` (all platforms)
- **Build/Bundle:** PyInstaller (backend sidecar), Vite + Tauri (desktop)

---

## Codebase Size

| Layer | Files | Lines of Code | Test Files | Test Lines |
|-------|-------|---------------|------------|------------|
| Backend (Python) | 95 | 28,152 | 77 | 26,599 |
| Desktop (TS/TSX) | 165 | 28,861 | 61 | 21,342 |
| Tauri (Rust) | 27 | 888 (hand-written) | — | — |
| **Total** | **287** | **57,901** | **138** | **47,941** |

Test-to-code ratio: ~0.83:1


### Git History Stats (cumulative across all 212 commits)

| Category | Lines Added | Lines Removed | Net Lines |
|----------|-------------|---------------|-----------|
| Source code | +80,696 | -21,017 | 59,679 |
| Tests | +64,905 | -16,608 | 48,297 |
| Docs | +55,661 | -12,333 | 43,328 |
| Config | +21,140 | -2,471 | 18,669 |
| Assets | +1,281 | -18 | 1,263 |
| **Total** | **+223,683** | **-52,447** | **171,236** |

Unique files touched across history: 838

---

## Backend Structure (Python FastAPI)

### Core Modules (`backend/core/`)
| Module | Purpose |
|--------|---------|
| `agent_manager.py` | Claude Agent SDK orchestration |
| `claude_environment.py` | SDK environment config and client wrapper |
| `session_manager.py` | Chat session lifecycle |
| `chat_thread_manager.py` | Thread persistence and retrieval |
| `context_manager.py` | Context file management |
| `context_assembler.py` | Context assembly for agent prompts |
| `context_snapshot_cache.py` | Snapshot caching layer |
| `swarm_workspace_manager.py` | Workspace CRUD and state |
| `task_manager.py` | Background task execution |
| `todo_manager.py` | ToDo item management |
| `skill_manager.py` | Skill registry and execution |
| `local_skill_manager.py` | Local filesystem skill loading |
| `plugin_manager.py` | Plugin lifecycle management |
| `search_manager.py` | Full-text search across entities |
| `permission_manager.py` | HITL permission flow |
| `security_hooks.py` | Security hook enforcement |
| `system_prompt.py` | Dynamic system prompt builder |
| `auth.py` | Authentication and JWT |
| `audit_manager.py` | Audit logging |
| `telemetry_emitter.py` | Telemetry events |
| `tscc_snapshot_manager.py` | TSCC snapshot management |
| `tscc_state_manager.py` | TSCC state tracking |
| `initialization_manager.py` | App startup initialization |
| `agent_sandbox_manager.py` | Per-agent sandbox isolation |
| `content_accumulator.py` | Streaming content block accumulation |
| `project_schema_migrations.py` | DB schema migrations for projects |

### API Routers (`backend/routers/`)
`agents`, `auth`, `autonomous_jobs`, `channels`, `chat`, `context`, `dev`, `mcp`, `plugins`, `projects`, `search`, `settings`, `skills`, `system`, `tasks`, `todos`, `tscc`, `workspace`, `workspace_api`, `workspace_config`

### Other Backend Modules
- `channels/adapters/` — Channel gateway adapters (e.g., Feishu)
- `database/` — SQLite schema and migrations
- `mcp_servers/` — MCP server integrations
- `middleware/` — Request middleware (rate limiting, auth)
- `schemas/` — Pydantic request/response models
- `templates/` — System prompt and agent templates
- `utils/` — Shared utilities

---

## Desktop Structure (React + TypeScript)

### Pages
`SwarmCorePage`, `ChatPage`, `AgentsPage`, `ChannelsPage`, `MCPPage`, `PluginsPage`, `SkillsPage`, `TasksPage`, `SettingsPage`

### Component Groups
- `chat/` — Chat UI components
- `common/` — Shared UI primitives
- `layout/` — App shell and navigation
- `modals/` — Modal dialogs
- `search/` — Search interface
- `workspace/` — Workspace management
- `workspace-explorer/` — File/context explorer
- `workspace-settings/` — Workspace config UI

### Services (`desktop/src/services/`)
API client layer with `toCamelCase()` field mapping (backend snake_case → frontend camelCase)

### Key Hooks (`desktop/src/hooks/`)
- `useUnifiedTabState` — Single source of truth for all tab state (CRUD, metadata, runtime state, lifecycle, localStorage persistence, tab status indicators). Replaces the former `useTabState`, `tabStateRef`, and `tabStatuses` stores.
- `useChatStreamingLifecycle` — Streaming lifecycle state machine (messages, sessionId, isStreaming, stream generation counter, auto-scroll, debounced activity labels, sessionStorage persistence). Receives unified tab state methods via deps for tab-aware stream handlers.
- `useFileAttachment` — File attachment processing
- `useTSCCState` — TSCC panel state
- `useRightSidebarGroup` — Mutual-exclusion sidebar management
- `useWorkspaceSelection` — Active workspace selection

### Other
- `contexts/` — React context providers
- `i18n/` — Internationalization (English + Chinese)
- `types/` — TypeScript type definitions (includes `isError` flag on `Message` for error visibility)

---

## Key Dependencies

### Backend (Python)
- `fastapi` ≥0.115, `uvicorn` ≥0.34
- `claude-agent-sdk` ≥0.1.34
- `pydantic` ≥2.10, `aiosqlite` ≥0.20
- `python-jose`, `passlib`, `bcrypt` (auth)
- `lark-oapi` (Feishu/Lark integration)
- `pytest` ≥8.0 (testing)

### Desktop (TypeScript)
- `react` 19, `react-dom` 19
- `@tauri-apps/api` v2 + plugins (dialog, fs, shell, updater, etc.)
- `@tanstack/react-query` ≥5.90
- `axios`, `mermaid`, `highlight.js`, `react-markdown`
- `i18next` + `react-i18next`

---

## Specs

15 spec directories under `.kiro/specs/`, covering:
- Workspace redesign (foundation, explorer UX, intelligence, projects, unified CWD)
- Swarm Radar (foundation, WIP/completed views)
- Operating loop cleanup
- Claude SDK auth error handling (bugfix)
- TSCC, context engine, chat session management
- Architecture and HITL permission flow docs

---

