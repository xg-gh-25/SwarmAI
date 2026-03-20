# SwarmAI Architecture

**Version:** 9.0
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
| Database | SQLite (pre-seeded for fast startup, WAL mode) |
| Styling | Tailwind CSS 4.x + CSS custom properties |
| State | TanStack Query (server) + useRef Map (tabs) |
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
│  layout:         │     │  │ SessionRouter + Units    │ │
│  • SwarmWS (L)   │     │  │ • ClaudeSDKClient/tab   │ │
│  • Chat (C)      │     │  │ • PromptBuilder         │ │
│  • Radar (R)     │     │  │ • LifecycleManager      │ │
│  + TSCC panel    │     │  └────────┬────────────────┘ │
└──────────────────┘     │           │                   │
                         │  ┌────────▼────────────────┐ │
                         │  │ Claude Code CLI          │ │
                         │  │ (managed by SDK)         │ │
                         │  │ • Built-in tools         │ │
                         │  │ • MCP servers            │ │
                         │  │ • Skills (symlinked)     │ │
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
| `useUnifiedTabState` | Single source of truth for all tab state (mutable ref + render counter) |
| `useChatStreamingLifecycle` | SSE streaming, messages, sessionId, pendingQuestion, isStreaming |
| `useTSCCState` | Thread-scoped cognitive context state |
| `useRightSidebarGroup` | Mutual exclusion for right sidebar panels |
| `useUnifiedAttachments` | Unified file attachment lifecycle (classify → validate → encode → store → clear) |
| `useRunningTaskCount` | Track active background task count |
| `useHealthMonitor` | Backend health polling and reconnection detection |
| `useMemorySave` | One-click memory save (🧠 button) via LLM extraction |
| `useRateLimiter` | Rate limit state tracking for API calls |
| `useRateLimitCountdown` | Countdown timer UI for rate limit cooldown |

### Tab State Model

`useUnifiedTabState` (`desktop/src/hooks/useUnifiedTabState.ts`) is the source of truth for all tab state, using a dual-state pattern optimized for streaming:

- `tabMapRef: useRef<Map<string, UnifiedTab>>`: Mutable ref for synchronous reads/writes in stream handlers (zero re-render overhead during SSE token deltas)
- `renderCounter: useState<number>`: Bumped to trigger `useMemo` re-derivation of display views (`openTabs`, `tabStatuses`, `activeTab`)
- Tab CRUD: `addTab()`, `closeTab()`, `selectTab()`
- Per-tab state: `updateTabState()`, `updateTabStatus()`, `getTabState()`
- Persistence: Debounced 500ms write to `~/.swarm-ai/open_tabs.json`
- `restoreFromFile()`: Load tab metadata from file on startup
- Messages loaded lazily from backend API when a tab becomes active
- Stream handlers read/write `tabMapRef` directly for O(1) synchronous access during high-frequency SSE events

### Frontend File Structure

```
desktop/src/
├── pages/
│   ├── ChatPage.tsx              # Main chat (multi-tab, streaming, TSCC)
│   ├── TasksPage.tsx             # Task management
│   ├── SettingsPage.tsx          # API and app configuration
│   ├── AgentsPage.tsx            # Agent configuration
│   ├── SkillsPage.tsx            # Skill management
│   ├── PluginsPage.tsx           # Plugin marketplace
│   ├── ChannelsPage.tsx          # Channel gateway (Feishu, etc.)
│   ├── SwarmCorePage.tsx         # Core system page
│   └── chat/
│       ├── components/           # ChatHeader, ChatInput, SessionTabBar, TSCCPanel
│       ├── constants.ts          # Welcome message, sidebar configs
│       └── utils.ts              # Session grouping helpers
├── hooks/
│   ├── useUnifiedTabState.ts     # Tab state (mutable ref + render counter)
│   ├── useChatStreamingLifecycle.ts  # SSE streaming lifecycle
│   ├── useTSCCState.ts           # Cognitive context state
│   ├── useUnifiedAttachments.ts  # Unified file attachment lifecycle
│   ├── useRunningTaskCount.ts    # Background task tracking
│   ├── useHealthMonitor.ts       # Backend health polling + reconnection
│   ├── useMemorySave.ts          # One-click memory save (🧠 button)
│   ├── useRateLimiter.ts         # Rate limit state tracking
│   └── useRateLimitCountdown.ts  # Rate limit cooldown timer UI
├── services/
│   ├── api.ts                    # Axios client with dynamic port
│   ├── tauri.ts                  # Tauri IPC bridge
│   ├── chat.ts                   # Chat API + SSE streaming
│   ├── agents.ts                 # Agent CRUD with case conversion
│   ├── skills.ts                 # Skill CRUD
│   ├── mcp.ts                    # MCP server CRUD
│   ├── mcpConfig.ts              # MCP configuration management
│   ├── plugins.ts                # Plugin marketplace
│   ├── channels.ts               # Channel gateway API
│   ├── tabPersistence.ts         # File-based tab save/load
│   ├── tscc.ts                   # TSCC snapshot API
│   ├── radar.ts                  # Radar panel API
│   ├── search.ts                 # Global search API
│   ├── settings.ts               # Config API
│   ├── evolution.ts              # Evolution event TypeScript interfaces
│   ├── system.ts                 # Health/status API
│   ├── tasks.ts                  # Task management API
│   ├── todos.ts                  # ToDo management API
│   ├── workspace.ts              # Workspace file operations
│   ├── workspaceConfig.ts        # Workspace configuration
│   └── updater.ts                # Auto-update service
├── components/
│   ├── chat/                     # PermissionRequestModal, ChatDropZone
│   ├── workspace-explorer/       # SwarmWS file tree
│   ├── layout/                   # ThreeColumnLayout, TopBar, BottomBar
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
    │                            • Defer refresh_builtin_defaults to background task
    │                            • channel_gateway.startup() (deferred if channels exist)
    │
    └── data.db missing? ──NO──► Full Init Path
                                 • Copy seed.db from bundled resources
                                 • initialize_database() (DDL + migrations)
                                 • run_full_initialization()
                                 • Register agents, skills, MCPs
                                 • channel_gateway.startup() (deferred if channels exist)
```

On the fast path, `refresh_builtin_defaults()` (skill re-scan + context file refresh) is deferred to a background `asyncio.Task` so it doesn't block `_startup_complete`. The frontend can start serving requests immediately while built-in defaults are refreshed in the background.

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
│   ├── session_registry.py        # Global singletons, startup/shutdown, skill creator entry
│   ├── session_unit.py            # SessionUnit: 5-state machine, subprocess lifecycle (per tab)
│   ├── session_router.py          # SessionRouter: routing, concurrency cap (MAX=2), queue
│   ├── prompt_builder.py          # PromptBuilder: system prompt, SDK options, MCP config
│   ├── lifecycle_manager.py       # LifecycleManager: 12hr TTL, hooks, orphan reaper
│   ├── session_utils.py           # Shared error helpers (_build_error_event, etc.)
│   ├── skill_creator.py           # AI skill generation agent config
│   ├── session_manager.py         # Session storage (DB + in-memory cache)
│   ├── initialization_manager.py  # Startup orchestration, workspace caching
│   ├── swarm_workspace_manager.py # SwarmWS filesystem (verify_integrity, projects)
│   ├── context_directory_loader.py# Centralized .context/ loader (11 files, L0/L1)
│   ├── context_injector.py        # Resume context injection (recent messages → system prompt)
│   ├── system_prompt.py           # Non-file prompt sections (safety, datetime, runtime)
│   ├── claude_environment.py      # SDK env config, credential validation
│   ├── app_config_manager.py      # In-memory config cache (config.json)
│   ├── chat_thread_manager.py     # ChatThread CRUD, project binding, summaries
│   ├── content_accumulator.py     # O(1) content block deduplication
│   ├── security_hooks.py          # 4-layer PreToolUse defense chain
│   ├── skill_manager.py           # Filesystem skill discovery (3-tier)
│   ├── skill_migration.py        # skill_ids (UUIDs) → allowed_skills (folder names) migration
│   ├── projection_layer.py        # Skill symlink projection into .claude/skills/
│   ├── plugin_manager.py          # Plugin marketplace, install/uninstall
│   ├── proactive_intelligence.py  # Session briefing, open thread parsing, learning state
│   ├── tool_summarizer.py         # Tool call summarization for UI
│   ├── tscc_state_manager.py      # Thread-scoped cognitive context (LRU)
│   ├── credential_validator.py    # Pre-flight STS validation
│   ├── cmd_permission_manager.py  # Bash command permission persistence
│   ├── permission_manager.py      # Per-session asyncio permission signaling
│   ├── search_manager.py          # Global search across workspace
│   ├── mcp_config_loader.py       # MCP server config loading and validation
│   ├── mcp_migration.py           # MCP config migration utilities
│   ├── hook_builder.py            # Hook construction helpers
│   ├── memory_extractor.py        # LLM-powered extraction for one-click 🧠 button
│   ├── daily_activity_writer.py   # write_daily_activity(), frontmatter handling
│   ├── task_manager.py            # Task CRUD
│   ├── todo_manager.py            # ToDo CRUD
│   ├── audit_manager.py           # Audit logging
│   ├── frontmatter.py             # YAML frontmatter parse/write
│   ├── agent_defaults.py          # Default agent configurations
│   ├── project_schema_migrations.py # Semver-based .project.json schema migrations
│   └── exceptions.py              # Custom exception hierarchy
├── routers/
│   ├── agents.py                  # Agent CRUD
│   ├── chat.py                    # Chat SSE streaming, session management
│   ├── skills.py                  # Skill CRUD, upload, generation
│   ├── mcp.py                     # MCP server CRUD
│   ├── plugins.py                 # Plugin marketplace
│   ├── settings.py                # Config, open_tabs persistence
│   ├── system.py                  # Health, status, channels
│   ├── workspace.py               # Workspace CRUD, file operations
│   ├── workspace_api.py           # Workspace file API (read/write)
│   ├── workspace_config.py        # Workspace configuration
│   ├── memory.py                  # Memory compliance, one-click save-session
│   ├── projects.py                # Project CRUD
│   ├── artifacts.py               # Artifact management
│   ├── channels.py                # Channel gateway (Feishu, etc.)
│   ├── tasks.py                   # Task management
│   ├── todos.py                   # ToDo management
│   ├── tscc.py                    # TSCC state API
│   ├── search.py                  # Global search
│   ├── auth.py                    # Authentication
│   ├── autonomous_jobs.py         # Background job management
│   └── dev.py                     # Development-only endpoints
├── context/                       # Default context file templates (16 files)
├── database/
│   └── sqlite.py                  # SQLite with migrations, WAL mode
├── schemas/                       # Pydantic models (snake_case)
├── scripts/
│   ├── generate_seed_db.py        # Build-time seed database generator
│   └── locked_write.py            # Locked read-modify-write for MEMORY.md
├── skills/                        # Built-in skill definitions
├── channels/                      # Channel gateway (Feishu integration)
└── templates/                     # Agent/skill templates
```

---

## 5. Context and Memory Management Architecture

This is the core knowledge system that gives agents persistent identity, memory, and contextual awareness.

### 5.1 Centralized Context Directory

All context lives in `~/.swarm-ai/SwarmWS/.context/` — a single hidden directory using filesystem-only storage (no DB for context content).

```
~/.swarm-ai/SwarmWS/.context/
├── SWARMAI.md              — Core system prompt (P0, never truncated)
├── IDENTITY.md             — Agent name, avatar, self-description (P1, never truncated)
├── SOUL.md                 — Personality, tone, communication style (P2, never truncated)
├── AGENT.md                — Behavioral rules, directives (P3, system default)
├── USER.md                 — User profile, preferences, timezone (P4, user-customized)
├── STEERING.md             — Session-level rules, overrides (P5, user-customized)
├── TOOLS.md                — Tool usage guidance (P6, user-customized)
├── MEMORY.md               — Cross-session persistent memory (P7, user-customized)
├── EVOLUTION.md            — Self-evolution registry (P8, user-customized, truncates from head)
├── KNOWLEDGE.md            — Domain knowledge, reference material (P9, user-customized)
├── PROJECTS.md             — Active projects summary (P10, user-customized)
├── EVOLUTION_CHANGELOG.jsonl — Evolution audit log (agent-managed, not in system prompt)
├── BOOTSTRAP.md            — First-run onboarding (ephemeral, not in cache)
├── L0_SYSTEM_PROMPTS.md    — Compact cache for small models (auto-generated)
└── L1_SYSTEM_PROMPTS.md    — Full cache for large models (auto-generated)
```

### 5.2 ContextFileSpec Data Model

Each context file is defined by a frozen dataclass:

```python
@dataclass(frozen=True)
class ContextFileSpec:
    filename: str                              # e.g. "SWARMAI.md"
    priority: int                              # 0 = highest, 9 = lowest
    section_name: str                          # Header in assembled output
    truncatable: bool                          # Can be shortened under budget pressure
    user_customized: bool = False              # True = copy-only-if-missing, 0o644
    truncate_from: Literal["head", "tail"] = "tail"  # Direction of truncation
```

| File | Priority | Truncatable | User-Customized | Truncate From |
|------|----------|-------------|-----------------|---------------|
| SWARMAI.md | 0 | No | No (system) | tail |
| IDENTITY.md | 1 | No | No (system) | tail |
| SOUL.md | 2 | No | No (system) | tail |
| AGENT.md | 3 | Yes | No (system) | tail |
| USER.md | 4 | Yes | Yes | tail |
| STEERING.md | 5 | Yes | Yes | tail |
| TOOLS.md | 6 | Yes | Yes | tail |
| MEMORY.md | 7 | Yes | Yes | head (keep newest) |
| EVOLUTION.md | 8 | Yes | Yes | head (keep newest) |
| KNOWLEDGE.md | 9 | Yes | Yes | tail |
| PROJECTS.md | 10 | Yes | Yes | tail |

### 5.3 Two-Mode Copy Behavior

`ensure_directory()` uses two copy strategies based on `user_customized`:

- **System defaults** (`user_customized=False`): Always overwrite from `backend/context/` templates, set readonly `0o444`. These are the app's voice — updated on every release.
- **User files** (`user_customized=True`): Copy only if missing, set read-write `0o644`. User edits are preserved across updates.

Byte-comparison optimization: system files are only rewritten if content actually changed.

### 5.4 Token Budget System

Dynamic budgets scale with model context window:

| Model Context | Token Budget | Strategy |
|---------------|-------------|----------|
| ≥ 200K | 50,000 | L1 full assembly or source files |
| 64K–200K | 30,000 | L1 full assembly |
| 32K–64K | 30,000 | L0 compact cache |
| < 32K | 30,000 | L0 compact, skip KNOWLEDGE + PROJECTS |

Truncation order: lowest priority first (PROJECTS → KNOWLEDGE → MEMORY → ...). Priorities 0–2 (SWARMAI, IDENTITY, SOUL) are never truncated.

MEMORY.md truncates from head (keeps newest content at the bottom). All other files truncate from tail (keeps the beginning).

### 5.5 L0/L1 Cache System

```
Source files (11 editable .md files)
         │
    ┌────┴────┐
    ▼         ▼
  L1 cache    L0 cache
  (full)      (compact)
  ~25-35K     ~3-6K tokens
  ≥64K models <64K models
```

- **L1**: Concatenation of all source files with `## {section_name}` headers. Regenerated when any source file mtime > L1 mtime.
- **L0**: AI-summarized compact version. Each file compressed to essential directives only.

### 5.6 Context Assembly Flow

```
PromptBuilder.build_system_prompt()
  │
  ├── 1. ContextDirectoryLoader (global context)
  │     loader = ContextDirectoryLoader(SwarmWS/.context/, budget)
  │     loader.ensure_directory()  → copy defaults from backend/context/
  │     context_text = loader.load_all(model_context_window)
  │       → ≥64K: L1 cache (if fresh) or assemble from 11 source files
  │       → <64K: L0 compact cache
  │     Appended to agent_config["system_prompt"]
  │
  ├── 2. BOOTSTRAP.md (ephemeral, first-run only)
  │     If exists → prepend as "## Onboarding" section
  │
  ├── 3. DailyActivity (ephemeral, today + yesterday)
  │     Read from Knowledge/DailyActivity/{date}.md
  │     Token cap per file (2,000 tokens) — truncation is ephemeral
  │     Appended as "## Daily Activity ({date})" sections
  │
  ├── 4. Metadata collection for TSCC system prompt viewer
  │     Per-file: filename, tokens, truncated flag, user_customized
  │     Stored on agent_config["_system_prompt_metadata"]
  │
  └── 5. SystemPromptBuilder (non-file sections only)
        → Identity line ("You are {name}...")
        → Safety principles (6 rules)
        → Workspace cwd path
        → Selected working directories (if add_dirs)
        → Current date/time (UTC + local)
        → Runtime metadata (agent, model, OS, channel)
```

### 5.7 Memory System

MEMORY.md is the curated long-term memory file:

- **Location**: `~/.swarm-ai/SwarmWS/.context/MEMORY.md`
- **Writes**: Via `locked_write.py` (fcntl.flock for safe concurrent access)
- **Truncation**: From head (keeps newest content at bottom)
- **Distillation**: Primary: code-driven via `DistillationTriggerHook` (regex extraction at session close). Fallback: flag file triggers agent `s_memory-distill` skill at next session start
- **DailyActivity extraction**: Code-enforced via `DailyActivityExtractionHook` at session close (rule-based `SummarizationPipeline`, no LLM)
- **DailyActivity loading**: Last 2 files by filename date (handles weekend gaps), ephemeral, 2K token cap per file
- **One-click save**: `POST /api/memory/save-session` → LLM extraction via `memory_extractor.py` (Bedrock Sonnet)
- **Auto-commit**: `WorkspaceAutoCommitHook` at session close with smart conventional commit messages (replaced per-turn commits)

### 5.7.1 Session Lifecycle Hooks (Code-Enforced Memory Pipeline)

Four hooks fire via `BackgroundHookExecutor` on every session close (2h TTL expiry, explicit delete, backend shutdown):

1. `DailyActivityExtractionHook` — extracts conversation summary → `Knowledge/DailyActivity/YYYY-MM-DD.md`
2. `WorkspaceAutoCommitHook` — smart git commit with conventional prefixes (uses shared `git_lock` to prevent `.git/index.lock` contention)
3. `DistillationTriggerHook` — checks undistilled count, runs direct regex distillation if threshold (>3) exceeded
4. `EvolutionMaintenanceHook` — deprecates idle EVOLUTION.md entries (>30 days, 0 usage), prunes deprecated entries, logs to `EVOLUTION_CHANGELOG.jsonl`

`BackgroundHookExecutor` wraps `SessionLifecycleHookManager` to run hooks as fire-and-forget `asyncio.Task`s — hooks never block the chat path. Each hook is error-isolated with 30s timeout. A shared `asyncio.Lock` (`git_lock`) prevents concurrent git operations between `WorkspaceAutoCommitHook` and other git-touching code.

### 5.7.2 LifecycleManager Background Loop

`LifecycleManager` runs a single background loop every 60 seconds:

1. **Health check**: Detect dead subprocesses across all SessionUnits
2. **TTL kill** (12hr idle): Kill SessionUnits idle > 43,200s, fire lifecycle hooks
3. **Dead cleanup**: Transition DEAD units to COLD (reset internal state)

At startup, `_reap_orphans()` runs once to kill claude CLI processes not owned by any SessionUnit.

Hooks are serialized through `BackgroundHookExecutor` — never block the chat path.

### 5.7.3 Resume Context Injection

When a session resumes after a backend restart, `context_injector.build_resume_context()` loads recent messages from SQLite and injects them into the system prompt as read-only history:

- Fetches last 30 messages from DB, filters tool-only turns, keeps last 10 human-readable messages
- Drops the last assistant message to prevent re-answer duplication
- Enforces a 2,000-token budget (oldest-first truncation)
- Wraps in `## Previous Conversation Context` section with explicit RULES telling the agent not to re-execute or re-answer

### 5.7.4 Tool Failure Evolution Trigger

`ToolFailureTracker` (per-session, stored in `SessionUnit`) watches for repeated tool failures and injects evolution nudges:

- Tracks failure signatures (tool_name + first 100 chars of error)
- After `FAILURE_THRESHOLD` (2) consecutive failures with same signature → emits evolution nudge
- Nudge cooldown: 120s per signature, max 3 nudges per session
- On tool success → resets all failure signatures for that tool
- Nudge is a system-level hint for the self-evolution skill, not user-visible

### 5.8 Knowledge Directory

```
~/.swarm-ai/SwarmWS/Knowledge/
├── Notes/           # Quick notes and scratchpad
├── Reports/         # Generated reports
├── Meetings/        # Meeting notes
├── Library/         # Reference material (migrated from Knowledge Base/)
├── Archives/        # Auto-pruned at 90 days via prune_archives()
└── DailyActivity/   # Append-only daily logs (last 2 by date loaded at session start)
```

---

## 6. Data Model

### Storage Model

| Category | Storage | Examples |
|----------|---------|----------|
| DB-Canonical | SQLite (`data.db`) | Agents, Skills, MCPs, Sessions, Messages, Tasks, ToDos, ChatThreads |
| Filesystem | `~/.swarm-ai/SwarmWS/` | Knowledge/, Projects/, .context/ files, artifacts |
| Config | `~/.swarm-ai/config.json` | API keys, model selection, Bedrock settings |
| Tab State | `~/.swarm-ai/open_tabs.json` | Open tabs, active tab ID |

### Key Database Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent configurations (model, prompt, permissions, skill/MCP IDs) |
| `skills` | Skill metadata (name, description, is_system, source_tier) |
| `mcp_servers` | MCP server configurations (connection type, config JSON) |
| `chat_sessions` | Session metadata (agent_id, title, work_dir, timestamps) |
| `chat_messages` | Message content (session_id, role, content JSON, model) |
| `chat_threads` | Thread binding (workspace_id, agent_id, project_id, mode) |
| `swarm_workspaces` | Workspace records (name, file_path, settings) |
| `tasks` | Task management (title, status, session_id, project) |
| `todos` | ToDo items (title, priority, source, status) |
| `app_settings` | Key-value settings (initialization_complete, etc.) |

### SwarmWS Filesystem

```
~/.swarm-ai/SwarmWS/
├── .context/                  # Centralized context directory (11 source files + caches)
├── .claude/
│   └── skills/                # Symlinked skills (projected by ProjectionLayer)
├── Knowledge/                 # Shared reusable assets (6 subdirectories)
│   ├── Notes/
│   ├── Reports/
│   ├── Meetings/
│   ├── Library/
│   ├── Archives/
│   └── DailyActivity/
└── Projects/                  # Active work containers
```

### 5.9 Proactive Intelligence (Session Briefing)

`proactive_intelligence.py` generates a `## Session Briefing` section (~185 tokens) injected into the system prompt at session start. Pure parsing, no LLM calls, <1ms.

Four levels of intelligence:
- **L0**: Parse MEMORY.md `## Open Threads` section → extract thread titles, priorities, status
- **L1**: Parse DailyActivity `**Next:**` lines → extract continuation hints, filter stale "Ongoing:" items
- **L2**: Rule-based scoring — `ScoredItem` dataclass with deterministic scoring (priority weight + staleness + frequency + blocking + momentum). Top items shown as "Suggested focus"
- **L3**: Learning loop — `LearningState` persisted to `.swarm-ai/SwarmWS/.proactive_state.json`. Tracks work-type preferences, applies skip penalties and affinity bonuses across sessions

Key functions: `build_session_briefing()`, `_parse_open_threads()`, `_parse_continue_hints()`, `_detect_patterns()`, `_detect_temporal_signals()`, `_build_suggestions()`, `_apply_learning()`

Multi-tab safe: read-only, no writes, no shared state, no locks.

### 5.10 Context Usage Warning

`_build_context_warning()` monitors context window consumption and emits warning events:

| Threshold | Level | Action |
|-----------|-------|--------|
| < 70% | `ok` | No event emitted |
| 70–84% | `warn` | "Heads up — we've used about N% of this session's context" |
| ≥ 85% | `critical` | "Recommend: save context and start a new session" |

Emitted after every turn via `SessionUnit._stream_response()`. Uses `_sum_usage_input_tokens()` from the SDK's usage dict.

### 5.11 Auto-Commit Workspace

Workspace auto-commit has been migrated from per-turn to per-session-close via `WorkspaceAutoCommitHook`:

```python
class WorkspaceAutoCommitHook:
    # Registered as 2nd session lifecycle hook
    # Analyzes git diff --stat, categorizes files by path pattern
    # Generates conventional commit messages (framework:, skills:, content:, project:, output:, chore:)
    # Skips trivial changes (only skill config syncs)
```

- Fires once per session close (not per message — cleaner git history)
- Smart commit messages derived from actual file changes, not user's first message
- Categorizes by path prefix: `.context/` → `framework:`, `Knowledge/` → `content:`, etc.
- Trivial changes (only skill syncs) get `chore: session sync` or are skipped
- The legacy `_auto_commit_workspace()` method is retained but no longer called per-turn

---

## 7. Configuration Management (AppConfigManager)

Single source of truth for non-secret application settings, backed by `~/.swarm-ai/config.json`.

```python
class AppConfigManager:
    def load(self) -> dict       # Once at startup (reads file, merges with defaults)
    def get(key, default) -> Any # Zero IO (reads from in-memory cache)
    def update(updates) -> None  # Write-through (merges + persists to disk)
    def reload() -> None         # Force re-read from file
```

Key design decisions:
- **Zero IO on reads**: Config loaded into memory at startup, all `get()` calls return from cache
- **Secret filtering**: `SECRET_KEYS` frozenset (AWS credentials, API keys, bearer tokens) stripped before persisting to disk
- **Graceful fallback**: Missing/empty/invalid JSON falls back to `DEFAULT_CONFIG`
- **File permissions**: Created with `0o600` (owner read/write only)

Default config includes: `use_bedrock`, `aws_region`, `default_model`, `available_models`, `bedrock_model_map`, `anthropic_base_url`, `claude_code_disable_experimental_betas`.

---

## 8. Channel Gateway

`ChannelGateway` in `backend/channels/gateway.py` manages external channel adapters (Feishu, future Slack/Web):

- **Startup**: Deferred to background `asyncio.Task` when channels exist (doesn't block `_startup_complete`)
- **Zero channels**: Gateway startup skipped entirely
- **Channel injection**: `_inject_channel_mcp()` adds a `channel-tools` MCP server with channel-specific env vars (FEISHU_APP_ID, CHAT_ID, etc.)
- **Lifecycle states**: `not_started` → `starting` → `started` (or `failed`)
- **Shutdown**: `channel_gateway.shutdown()` called during graceful app shutdown

---

## 9. API Design

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
| POST | `/api/chat/compact` | Compact session context window (/compact) |
| GET | `/api/chat/sessions` | List chat sessions |
| GET | `/api/chat/sessions/{id}/messages` | Get session messages |
| GET/POST | `/api/chat_threads/*` | ChatThread CRUD + binding |
| GET | `/api/projects/{id}/threads` | List threads by project |
| GET | `/api/threads/global` | List global threads |
| POST | `/api/chat_threads/{id}/bind` | Mid-session thread binding |
| GET/PUT | `/api/settings/open-tabs` | Tab state persistence |
| GET/PUT | `/api/settings/config` | App configuration |
| GET/POST/PUT/DELETE | `/api/plugins/*` | Plugin marketplace |
| GET/POST/PUT/DELETE | `/api/tasks/*` | Task management |
| GET/POST/PUT/DELETE | `/api/todos/*` | ToDo management |
| GET | `/api/tscc/*` | TSCC state snapshots |
| GET | `/api/search/*` | Global search |
| GET | `/api/autonomous-jobs` | Autonomous jobs (placeholder, mock data) |
| POST | `/api/skills/generate` | AI skill creation via Skill Creator Agent |

### SSE Event Types

| Event | Fields | Description |
|-------|--------|-------------|
| `session_start` | `sessionId` | New or resumed session established |
| `assistant` | `content[]`, `model` | Assistant message chunk |
| `tool_use` | `content[]` | Tool invocation |
| `tool_result` | `content[]` | Tool execution result |
| `ask_user_question` | `toolUseId`, `questions[]` | Agent needs user input |
| `cmd_permission_request` | `requestId`, `toolName`, `reason` | Permission gate |
| `agent_activity` | `activity` | Agent lifecycle event |
| `result` | `sessionId`, `durationMs`, `totalCostUsd` | Conversation complete |
| `error` | `error`, `code`, `detail` | Error occurred |
| `context_usage` | `level`, `pct`, `message` | Context window usage warning (warn at 70%, critical at 85%) |
| `heartbeat` | `timestamp` | Connection keepalive (15s interval) |

---

## 10. Skill System

### Three-Tier Skill Discovery

```
SkillManager scans three directories in precedence order:
  1. Built-in:  backend/skills/          (ships with app, always projected)
  2. User:      ~/.swarm-ai/skills/      (user-created or uploaded)
  3. Plugin:    ~/.swarm-ai/plugin-skills/ (installed via Plugin Manager)

First-seen folder name wins (built-in > user > plugin).
```

### Skill Projection (ProjectionLayer)

Skills are projected as symlinks into `SwarmWS/.claude/skills/` for Claude SDK discovery:

```
~/.swarm-ai/skills/my-skill/SKILL.md
        ↓ symlink
~/.swarm-ai/SwarmWS/.claude/skills/my-skill → ~/.swarm-ai/skills/my-skill
        ↓ SDK discovery
setting_sources=["project"] → SDK scans {cwd}/.claude/skills/
```

- Built-in skills: always projected unconditionally
- User/plugin skills: projected based on agent's `allowed_skills` list or `allow_all` flag
- Stale symlinks cleaned up on every projection pass
- Symlink targets validated against known tier directories

### Plugin Manager

`PluginManager` handles the git-based marketplace for skill distribution:

- **Marketplace sync**: `sync_git_marketplace()` clones/pulls a git repo containing a `marketplace.json` manifest
- **Plugin install**: Extracts skill directories from the marketplace repo to `~/.swarm-ai/plugin-skills/`
- **Plugin uninstall**: Removes skill directory and cleans up symlinks
- **Standalone detection**: Can detect a single skill repo as a plugin (no manifest needed)
- **Cache**: Marketplace data cached locally in `~/.swarm-ai/marketplace-cache/`

### Skill Creator Agent

`run_skill_creator_conversation()` provides AI-assisted skill creation:

- Uses a temporary agent config with `SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE`
- Invokes the built-in `skill-creator` skill for best-practice guidance
- Creates skills in `~/.swarm-ai/skills/` (user tier)
- Supports multi-turn iteration (resume via session_id)
- Called from `POST /api/skills/generate`

---

## 11. Security

### Four-Layer PreToolUse Defense Chain

```python
hooks = {
    "PreToolUse": [
        HookMatcher(hooks=[pre_tool_logger]),           # Layer 1: all tools (observability)
        HookMatcher(matcher="Bash", hooks=[blocker]),   # Layer 2: dangerous command regex
        HookMatcher(matcher="Bash", hooks=[approval]),  # Layer 3: human approval gate
        HookMatcher(matcher="Skill", hooks=[checker]),  # Layer 4: skill access control
    ]
}
```

| Layer | Matcher | Action |
|-------|---------|--------|
| 1. `pre_tool_logger` | All tools | Logs tool name + input keys (never blocks) |
| 2. `dangerous_command_blocker` | Bash only | Regex blocks 13 dangerous patterns (rm -rf /, fork bombs, etc.) |
| 3. `human_approval_hook` | Bash only | CmdPermissionManager glob-based check → SSE permission dialog |
| 4. `skill_access_checker` | Skill only | Validates skill in agent's allowed_skills set |

### Additional Security Layers

- **Workspace Isolation**: Each agent runs in `~/.swarm-ai/workspaces/{agent_id}/`
- **File Access Control**: `create_file_access_permission_handler()` validates paths against allowed directories (when `global_user_mode=False`)
- **Bash Sandboxing**: macOS/Linux sandbox with excluded commands and network config
- **System-managed folder protection**: HTTP 403 on delete/rename of system folders
- **Readonly context files**: System defaults set to `0o444`
- **Error sanitization**: `_build_error_event()` strips tracebacks, file paths, and library versions in production mode; debug mode passes full detail

### Credential Model

- Config source of truth: `~/.swarm-ai/config.json` (0o600 permissions)
- AWS credentials: Delegated to standard credential chain (never stored by app)
- Anthropic API key: Stored in config.json (user-provided)
- Secret filtering: `SECRET_KEYS` frozenset stripped before config persistence
- No database storage for credentials

---

## 12. Build & Deployment

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
| `data.db` | SQLite database (WAL mode) |
| `config.json` | API and app configuration (0o600) |
| `open_tabs.json` | Tab persistence state |
| `SwarmWS/` | Single persistent workspace |
| `SwarmWS/.context/` | Centralized context directory |
| `skills/` | User-created skills |
| `plugin-skills/` | Plugin-installed skills |
| `workspaces/` | Per-agent sandboxed directories |
| `logs/` | Backend logs |
| `cmd_permissions/` | Persistent bash command approvals |
