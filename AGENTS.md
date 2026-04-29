# AGENTS.md

Guidance for AI coding assistants (Claude Code, Kiro, Cursor, Copilot) working with this repository.

**Last refreshed:** 2026-04-29 | **Auto-refresh:** context_health_hook syncs this to SwarmWS on startup

## Project Overview

SwarmAI is a desktop AI command center — Tauri 2.0 + React 19 + Python FastAPI backend. 69 skills, 164K backend LOC, 3000+ tests, 163 React components.

## ⚠️ CRITICAL: Known Landmines

**Read these first. Every one has caused a P0.**

### 1. Two Backend Processes — Never Confuse Them

| Process | Env Var | Lifetime | Port | Log File |
|---------|---------|----------|------|----------|
| **launchd daemon** | `SWARMAI_MODE=daemon` | 24/7 via launchd | 18321 (fixed) | `backend-daemon.log` |
| **Tauri sidecar** | `SWARMAI_MODE=sidecar` | Desktop app lifecycle | random | `backend.log` |

- Slack/channels run on **daemon**, NOT sidecar
- Closing the desktop app does NOT stop Slack
- `curl http://127.0.0.1:18321/health` tests the daemon, NOT what the frontend hits
- They write separate log files — never share a log file (RotatingFileHandler race)

### 2. `isDesktop()` Detection (v1.9.0 P0)

```typescript
// desktop/src/services/tauri.ts
// Tauri 2.x: window.__TAURI_INTERNALS__  (NOT __TAURI__)
// Must check BOTH for cross-version compat
```

If `isDesktop()` returns wrong value → `getApiBaseUrl()` returns `''` → all API calls hit SPA fallback → HTML instead of JSON → app shows "failed to start" even though backend is healthy.

**Debug:** Open DevTools Console → look for `[Platform] isDesktop=...` log line.

### 3. No `import fcntl` at Module Top Level

`fcntl` is Unix-only. Windows CI will crash. Use:
```python
from utils.file_lock import flock_exclusive, flock_unlock  # cross-platform
```
Exception: `utils/file_lock.py` itself (it handles the platform check internally).

### 4. Test Execution Rules

```bash
# ✅ Targeted tests (always OK)
cd backend && python -m pytest tests/test_<module>.py -v --timeout=60

# ✅ Last-failed only
cd backend && python -m pytest --lf --timeout=60

# ❌ NEVER run full suite proactively — xdist deadlock risk
# Only with explicit user request:
SWARMAI_SUITE=1 python -m pytest --timeout=120

# Before modifying a function, check who tests it:
grep -rn "function_name(" tests/ --include="*.py"
```

### 5. Release Flow

```
commit on main → push → CI runs (4 jobs) → all green → tag → release.yml builds DMG + Windows + Hive
```

No branches. No PRs. CI is the only gate. Never tag with red CI.

CI jobs: `backend` (Linux tests) | `backend-windows` (smoke import) | `frontend` (tsc + build) | `version-check` (6 files match)

### 6. Version Files — 6 Must Stay in Sync

```bash
# Source of truth: VERSION (root)
# Synced to: config.py, pyproject.toml, package.json, Cargo.toml, tauri.conf.json
# One command: ./scripts/sync-version.sh
# Verify: ./scripts/sync-version.sh check
```

### 7. Build Scripts

```bash
./dev.sh              # Start dev (backend + frontend)
./dev.sh build        # Full build: PyInstaller + verify + Tauri → DMG
./prod.sh build       # Backend only: PyInstaller + verify + deploy to daemon
./prod.sh release     # Full release: preflight → build → DMG → smoke test
./prod.sh status      # Show daemon health, binary versions
```

`build-backend.sh` exits non-zero if `verify_build.py` fails. Don't bypass.

## Architecture

### Data Flow
```
User → React Frontend → FastAPI Backend → SessionRouter → SessionUnit → ClaudeSDKClient → SSE → UI
```

### Process Topology
```
┌─────────────────────────────────┐     ┌──────────────────────────────┐
│ Tauri Desktop App               │     │ launchd Daemon (24/7)        │
│  ├─ React Frontend (webview)    │     │  ├─ FastAPI (port 18321)     │
│  ├─ Rust Core (lib.rs)          │     │  ├─ Slack Socket Mode        │
│  └─ Python Sidecar (random port)│     │  ├─ Channel Gateway          │
│     └─ FastAPI + Claude SDK     │     │  └─ Background Jobs          │
└─────────────────────────────────┘     └──────────────────────────────┘
         ↕ backend.json (port discovery)          ↕ launchctl
```

### Backend Structure
```
backend/
├── main.py                    # FastAPI entry, startup lifespan, health endpoint
├── config.py                  # Settings from ~/.swarm-ai/config.json
├── core/                      # Session management, prompt building, lifecycle
│   ├── session_router.py      # Multi-session routing, slot management
│   ├── session_unit.py        # 5-state machine: COLD→IDLE→STREAMING→WAITING_INPUT→DEAD
│   ├── prompt_builder.py      # System prompt assembly from 11 context files
│   └── lifecycle_manager.py   # TTL kill, health check, dead cleanup
├── database/
│   └── sqlite.py              # SQLite + WAL + migrations (uses utils.file_lock)
├── utils/
│   └── file_lock.py           # Cross-platform flock (fcntl/msvcrt) — USE THIS
├── hooks/                     # Post-session hooks (context health, evolution, distillation)
├── routers/                   # API endpoints
├── channels/                  # Slack adapter, channel gateway
├── skills/                    # 69 built-in skills (SKILL.md + INSTRUCTIONS.md)
├── jobs/                      # Background job scheduler + handlers
└── scripts/                   # CLI tools (locked_write.py, verify_build.py, etc.)
```

### Frontend Structure
```
desktop/src/
├── services/tauri.ts          # isDesktop(), getApiBaseUrl(), backend init — START HERE for startup bugs
├── components/common/BackendStartupOverlay.tsx  # Startup health polling + error display
├── pages/ChatPage.tsx         # Main chat UI
├── hooks/useChatStreamingLifecycle.ts  # SSE streaming
└── services/                  # API layer (snake_case ↔ camelCase conversion)
```

### API Naming Convention

Backend: `snake_case` (Python). Frontend: `camelCase` (TypeScript).

Transformation functions in `desktop/src/services/*.ts` handle conversion:

| Service | File | Functions |
|---------|------|-----------|
| Agents | `agents.ts` | `toSnakeCase()`, `toCamelCase()` |
| Skills | `skills.ts` | `toCamelCase()` |
| MCP | `mcp.ts` | `toCamelCase()` |
| Chat | `chat.ts` | `toSessionCamelCase()`, `toMessageCamelCase()` |
| Workspace | `workspace.ts` | `projectToCamelCase()`, `projectUpdateToSnakeCase()` |

**When adding fields:** update the Pydantic model (snake_case) + TypeScript interface (camelCase) + the `toCamelCase()` function.

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
- `locked_write.py`: fcntl.flock for safe MEMORY.md modification by skills
- Auto-commit: git add -A + commit after every conversation turn (non-blocking background thread)

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

## Debugging Startup Failures

If the app shows "Backend service failed to start":

1. **Open DevTools Console** (Cmd+Option+I) — look for `[Platform]` and `[Health Check]` logs
2. `[Platform] isDesktop=false` → Tauri detection broken (check `__TAURI_INTERNALS__`)
3. `[Health Check] FATAL: got HTML instead of JSON` → API URL is wrong
4. `[Health Check] Response: {status: "healthy"}` → Backend OK, problem is elsewhere
5. Backend logs: `~/.swarm-ai/logs/backend-daemon.log` (daemon) or `backend.log` (sidecar)

## Debugging Backend

```bash
# Daemon health
curl -s http://127.0.0.1:18321/health | python3 -m json.tool

# Daemon status
./prod.sh daemon status

# Tail daemon logs
./prod.sh daemon logs

# Dev backend logs
tail -f ~/.swarm-ai/logs/backend-dev.log
```

## CI Configuration

`.github/workflows/ci.yml` runs on every push to `main`:

| Job | Platform | What | Gate? | Time |
|-----|----------|------|-------|------|
| `backend` | Ubuntu | Smoke import (all modules) + non-DB tests | Yes | ~3min |
| `backend-windows` | Windows | Smoke import all modules (pkgutil auto-discover) | Yes | ~1min |
| `frontend` | Ubuntu | `tsc --noEmit` + `npm run build` | Yes | ~2min |
| `version-check` | Ubuntu | `sync-version.sh check` (6 files) | Yes | ~10s |

7 test files are skipped in CI (need macOS launchd, async DB lifecycle, or `~/.swarm-ai/` filesystem). See `ci.yml` comments for per-file reasons and dates. Review quarterly.

Tag push (`v*`) triggers `release.yml`: builds macOS DMG + Windows installer + Hive tar.gz.

## Key Design Decisions

1. **Single agent with role-switching** > multi-agent orchestration (zero context transfer cost)
2. **Memory sovereignty** — all memory self-owned (.context/MEMORY.md), never use platform memory
3. **Daemon-first** — daemon is the primary process, sidecar is fallback
4. **Filesystem-first** for skills and context — no DB, git-tracked, human-readable
5. **Prevention over recovery** — timeouts, state guards > error handling

## DDD Documents (Deep Context)

For architectural decisions and lessons, read these in `~/.swarm-ai/SwarmWS/Projects/SwarmAI/`:

| File | Contains |
|------|----------|
| `PRODUCT.md` | Vision, priorities, non-goals, competitive positioning |
| `TECH.md` | Architecture details, patterns, conventions |
| `IMPROVEMENT.md` | What worked, what failed, watch-for patterns |
| `PROJECT.md` | Current status, recent decisions, open threads |

For cross-session memory and corrections: `~/.swarm-ai/SwarmWS/.context/MEMORY.md`
