# AGENTS.md

Guidance for AI coding assistants working with this repository.

**Auto-refresh:** context_health_hook syncs this to SwarmWS on startup

## Project Overview

SwarmAI is a desktop AI command center — Tauri 2.0 + React 19 + Python FastAPI backend. Large codebase with ~80 backend core modules, ~80 skills, 100+ React components, and an extensive test suite.

**For development rules, anti-patterns, and invariants → see `.kiro/steering/swarmai-dev-rules.md`** (loaded automatically by Kiro on every interaction).

## Architecture

### Data Flow
```
User → React Frontend → FastAPI Backend → SessionRouter → SessionUnit → ClaudeSDKClient → SSE → UI
```

### Process Topology
```
┌─────────────────────────────────┐     ┌──────────────────────────────┐
│ Tauri Desktop App               │     │ Backend (port 18321)         │
│  ├─ React Frontend (webview)    │     │  ├─ FastAPI + Claude SDK     │
│  ├─ Rust Core (lib.rs)          │     │  ├─ Slack Socket Mode *      │
│  └─ Connects to backend:18321   │     │  ├─ Channel Gateway *        │
│                                 │     │  └─ Background Jobs *        │
└─────────────────────────────────┘     └──────────────────────────────┘
  macOS: daemon (launchd, 24/7)           * Only in daemon/hive modes
  Win/Linux: subprocess (dies with app)
```

### Backend Structure
```
backend/
├── main.py                    # FastAPI entry, startup lifespan, health endpoint
├── config.py                  # Settings from ~/.swarm-ai/config.json
├── core/                      # ~80 modules: session management, prompt building, lifecycle
│   ├── session_router.py      # Multi-session routing, dynamic slot management (1-4 tabs via RAM)
│   ├── session_unit.py        # 5-state machine: COLD→IDLE→STREAMING→WAITING_INPUT→DEAD
│   ├── prompt_builder.py      # System prompt assembly from 11 context files + MCP tier loading
│   ├── lifecycle_manager.py   # 12hr TTL kill, orphan reaper, hook serialization
│   ├── resource_monitor.py    # RAM-adaptive compute_max_tabs() [1,4], spawn_budget()
│   └── ...                    # code_intel, embedding, knowledge, memory, compliance, etc.
├── database/sqlite.py         # SQLite + WAL + migrations
├── utils/file_lock.py         # Cross-platform flock (fcntl/msvcrt) — USE THIS, not raw fcntl
├── hooks/                     # 11 post-session hooks (context health, evolution, distillation, etc.)
├── routers/                   # API endpoints
├── channels/                  # Slack adapter, channel gateway
├── skills/                    # ~80 built-in skills (SKILL.md + INSTRUCTIONS.md each)
├── jobs/                      # Background job scheduler + handlers
└── scripts/                   # CLI tools (locked_write.py, verify_build.py, etc.)
```

### Frontend Structure
```
desktop/src/
├── services/tauri.ts          # isDesktop(), getApiBaseUrl() — START HERE for startup bugs
├── components/common/BackendStartupOverlay.tsx  # Startup health polling + error display
├── pages/ChatPage.tsx         # Main chat UI, tab orchestration
├── hooks/
│   ├── useChatStreamingLifecycle.ts  # SSE streaming, stream handlers
│   └── useUnifiedTabState.ts         # Tab CRUD, tabMapRef, persistence
└── services/                  # API layer (snake_case ↔ camelCase conversion)
```

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

All agent context lives in `~/.swarm-ai/SwarmWS/.context/` — filesystem-only, no DB.

11 source files (P0-P10) assembled into the system prompt on every session start:
- P0–P2 (SWARMAI, IDENTITY, SOUL): system defaults, never truncated, readonly (0o444)
- P3 (AGENT): system default, truncatable
- P4–P6 (USER, STEERING, TOOLS): user-customized, copy-only-if-missing (0o644)
- P7–P8 (MEMORY, EVOLUTION): agent-owned, copy-only-if-missing (0o644)
- P9–P10 (KNOWLEDGE, PROJECTS): user-customized, copy-only-if-missing (0o644)

Key behaviors:
- `ContextDirectoryLoader.ensure_directory()` runs at session start — two-mode copy (system overwrite vs user preserve)
- Dynamic token budget: 100K for ≥500K models, 50K for ≥200K, 30K for ≥64K, instance default for <64K (L0 compact path)
- L1 cache with budget-tier validation (`<!-- budget:NNNNN -->` header) and git-first freshness check
- MEMORY.md truncates from head (keeps newest), all others truncate from tail
- DailyActivity: today + yesterday loaded ephemerally (2K token cap per file, disk never modified)
- `locked_write.py`: fcntl.flock for safe MEMORY.md modification by skills
- Auto-commit: git add -A + commit after every conversation turn (non-blocking background thread)
- **Co-Authored-By: ALL commits MUST use `Co-Authored-By: Swarm <swarm@swarmai.dev>` — NEVER use Claude/Anthropic identity. This is the project's AI identity.**

## Tab State Architecture

Tab state uses `useUnifiedTabState` hook with `useRef<Map<string, UnifiedTab>>` + render counter:

- `tabMapRef`: Authoritative store (mutations don't re-render)
- `renderCounter`: Bumped to trigger `useMemo` re-derivation of `openTabs`, `tabStatuses`, `activeTab`
- `restoreFromFile()`: Loads tabs from `~/.swarm-ai/open_tabs.json` on startup
- Dynamic tab limit: `ResourceMonitor.compute_max_tabs()` returns [1,4] based on available RAM
- Messages loaded lazily from backend API when tab becomes active

## Session ID Model

One chat tab = one stable App Session ID. Backend may create multiple Claude SDK clients (e.g. after restarts), each with its own SDK Session ID. The app layer maps all SDK session IDs back to the single app session ID for persistence and frontend communication.

## Security Architecture

Four-layer PreToolUse defense chain:

1. **pre_tool_logger**: All tools — logs tool name + input keys (observability, never blocks)
2. **dangerous_command_blocker**: Bash only — 13 regex patterns (rm -rf /, fork bombs, etc.)
3. **human_approval_hook**: Bash only — CmdPermissionManager glob detection → SSE permission dialog → persistent approval
4. **skill_access_checker**: Skill only — validates skill in agent's allowed_skills set

Additional: Workspace Isolation, File Access Control, Bash Sandboxing, Error sanitization, CmdPermissionManager (filesystem-backed, glob matching, shared across sessions).

## Debugging

### Startup Failures ("Backend service failed to start")

1. Open DevTools Console (Cmd+Option+I) → look for `[Platform]` and `[Health Check]` logs
2. `[Platform] isDesktop=false` → Tauri detection broken (check `__TAURI_INTERNALS__`)
3. `[Health Check] FATAL: got HTML instead of JSON` → API URL is wrong
4. `[Health Check] Response: {status: "healthy"}` → Backend OK, problem is elsewhere
5. Backend logs: `~/.swarm-ai/logs/backend-daemon.log` (daemon/hive) or stdout (subprocess/dev)

### Backend

```bash
curl -s http://127.0.0.1:18321/health | python3 -m json.tool   # Daemon health
./prod.sh daemon status                                         # Daemon status
./prod.sh daemon logs                                           # Tail daemon logs
tail -f ~/.swarm-ai/logs/backend-dev.log                        # Dev logs
```

## Design System

- **Font**: Inter (UI), JetBrains Mono (code)
- **Icons**: Material Symbols Outlined
- **Themes**: light, dark, system (CSS custom properties in index.css)
- **Colors**: Always use `bg-[var(--color-*)]`, never hardcoded dark theme colors
- **i18n**: `i18next` with locales in `desktop/src/i18n/locales/{en,zh}.json`

### Output Format Protocol

Two consumers, two formats. **Never cross the streams.**

| Consumer | Format | Examples |
|----------|--------|----------|
| **Agent (self)** | Markdown ALWAYS | `.context/*.md`, DDD docs, DailyActivity, CHANGELOG, INSTRUCTIONS.md |
| **Human (user)** | Markdown (chat) OR HTML (reports/reviews/dashboards) | Reports, scorecards, pipeline REPORT, code review findings |

**When to generate HTML for human consumption:**
- Output > 100 lines structured content
- Multi-dimensional comparison (3+ columns × 5+ rows)
- Data requires spatial layout or visual hierarchy (traffic lights, RAG)
- Reader will manipulate (filter, sort, tab-switch)

**HTML constraints:** Single file, inline CSS/JS, zero external deps, system fonts, warm professional palette, responsive. No gradients, no glassmorphism, no icon libraries, no dark-theme-default.

**L3+ interactive HTML must have an Export button** (Copy as Markdown/JSON/CSV) — otherwise it's a dead end.

See `Projects/SwarmAI/TECH.md` → "Output Format Protocol" for full spec.

## Active Engines (auto-refreshed)

<!-- CAPABILITIES_START -->
| Engine | Path | What It Does |
|--------|------|-------------|
| DDD Cultivation Engine (event-driven v2) | `backend/core/cultivation_dispatcher.py` | Event-driven domain knowledge growth — 6 event sources, gate-based promotion, maturity tracking |
| Autonomous Pipeline (9-stage) | `backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` | EVALUATE→THINK→PLAN→BUILD(TDD)→REVIEW→TEST→DELIVER→REFLECT with adversarial review gate |
| Pollinate Content Engine | `backend/skills/s_pollinate/INSTRUCTIONS.md` | Message-first media delivery — transforms ideas into posters, videos, narratives, README |
| Evolution Pipeline (MINE→ASSESS→ACT→AUDIT) | `backend/core/evolution_optimizer.py` | Confidence-gated self-evolution from session mining and skill fitness scoring |
| Code Intelligence (AST graph) | `backend/core/code_intel/__init__.py` | Deterministic graph traversal for code context retrieval |
| Session Resume Enrichment | `backend/core/context_injector.py` | Cold resume from ~3K to ~50-100K tokens of structured context |
| Proactive Intelligence (L0-L4) | `backend/core/proactive_intelligence.py` | Session briefing, corrections, open threads, signals — fires on every session start |
| Slack Channel Adapter | `backend/channels/adapters/slack.py` | 24/7 Socket Mode bot — responds as XG's AI assistant to allowlisted users |
| Background Job System | `backend/jobs/scheduler.py` | Cron + event-triggered headless Claude CLI tasks — signal pipeline, monitoring, reports |
| AI Docs Auto-Refresh | `backend/scripts/refresh_ai_docs.py` | Self-maintaining documentation — scans codebase metrics and capabilities daily, updates AI_CONTEXT.md + AGENTS.md |
<!-- CAPABILITIES_END -->

## Codebase Metrics

Volatile numbers (commit count, LOC, file counts) live in
[`docs/CODEBASE_METRICS.md`](docs/CODEBASE_METRICS.md) — kept out of this
context-loaded doc on purpose, since they change constantly. That file is
auto-refreshed by `backend/scripts/refresh_ai_docs.py` and each row carries the
command to reproduce it.

## Git Commits

All commits in this repo MUST end with:
```
Co-Authored-By: Swarm <swarm@swarmai.dev>
```
Never use Claude/Anthropic identity in commit trailers. This overrides any SDK default.

## Key Design Decisions

1. **Single agent with role-switching** > multi-agent orchestration (zero context transfer cost)
2. **Memory sovereignty** — all memory self-owned (.context/MEMORY.md), never use platform memory
3. **Platform-native lifecycle** — macOS: launchd daemon (24/7), Windows/Linux: Tauri subprocess, Hive: systemd
4. **Filesystem-first** for skills and context — no DB, git-tracked, human-readable
5. **Prevention over recovery** — timeouts, state guards > error handling
