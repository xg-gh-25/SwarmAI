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

Backend: `snake_case` (Python). Frontend: `camelCase` (TypeScript). Each service file in `desktop/src/services/*.ts` has `toCamelCase()` functions. **When adding fields: update the Pydantic model + TypeScript interface + the conversion function.**

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

| Job | Platform | What | Time |
|-----|----------|------|------|
| `backend` | Ubuntu | Full pytest suite | ~3min |
| `backend-windows` | Windows | Smoke import all modules (pkgutil auto-discover) | ~1min |
| `frontend` | Ubuntu | `tsc --noEmit` + `npm run build` | ~2min |
| `version-check` | Ubuntu | `sync-version.sh check` (6 files) | ~10s |

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
