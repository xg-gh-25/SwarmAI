---
inclusion: manual
---

# SwarmAI Consolidated Development & Architecture Guide

> Single-file reference for all critical invariants, isolation principles, and regression-prone areas.
> Drop this into any development environment (Kiro, Claude Code, Cursor, etc.) as project context.

---

## 1. Architecture Overview

- Desktop app: Tauri 2.0 + React frontend + Python FastAPI backend sidecar
- Backend uses Claude Agent SDK with `ClaudeSDKClient` (spawns CLI subprocess)
- SQLite database for structured data, local filesystem for skills and context
- Data directory: `~/.swarm-ai/` (all platforms)
- Workspace: `~/.swarm-ai/SwarmWS/` (agent's working directory, git-tracked)

### Storage Model

| Category | Examples | Query Via |
|----------|----------|-----------|
| DB-Canonical | Tasks, ToDos, PlanItems, ChatThreads, Sessions | API endpoints, NOT filesystem |
| Filesystem | Artifacts/, ContextFiles/, .context/*.md | Direct file read |
| Hybrid | Artifacts, Reflections | DB metadata + filesystem content |

### API Naming Convention

- Backend (Python/Pydantic): `snake_case`
- Frontend (TypeScript): `camelCase`
- ALWAYS update `toCamelCase()` functions in `desktop/src/services/*.ts` when adding fields

### Development Commands

```bash
cd desktop && npm run tauri:dev          # Desktop dev
cd backend && uv sync && source .venv/bin/activate && python main.py  # Backend dev
cd desktop && npm test -- --run          # Frontend tests
cd backend && pytest                     # Backend tests
cd desktop && npm run build:all          # Production build
```

---

## 2. Multi-Tab Chat Isolation (8 Principles)

Each chat tab is a standalone session. The system supports 3-6 concurrent tabs streaming in parallel. Tabs MUST NOT interfere with each other at any layer.

### State Ownership Model

```
tabMapRef (useRef<Map<string, UnifiedTab>>)
  → AUTHORITATIVE source of truth for ALL per-tab state
  → messages, sessionId, pendingQuestion, isStreaming, abortController, status

React useState (messages, sessionId, isStreaming, etc.)
  → DISPLAY MIRROR — reflects ONLY the active tab's state
  → Updated on tab switch via restore + bumpStreamingDerivation()

pendingStreamTabs (useState<Set<string>>)
  → PENDING TRACKER — covers gap between handleSendMessage and session_start SSE
```

### The 8 Isolation Principles

**P1 — Tab-Scoped State Mutations**: Every state mutation MUST target a specific `tabId`. `setIsStreaming(value, tabId)` — always pass the originating tab's ID. Stream handlers capture `capturedTabId` at creation time.

**P2 — Active Tab = Display Mirror Only**: React `useState` values exist solely for rendering. On stream event: write to `tabMapRef` always, write to React state only if `isActiveTab`. Never read React state to make decisions about a specific tab.

**P3 — Stream Handler Closure Capture**: Stream handlers capture `capturedTabId` at creation time (immutable closure). `isActiveTab` is computed dynamically: `capturedTabId === activeTabIdRef.current`. Background tabs update only `tabMapRef`. Closed tabs → no-op.

**P4 — Per-Tab Session Identity for Backend Calls**: All backend API calls (stop, answer, permission) MUST use `tabMapRef.current.get(tabId)?.sessionId`, not the shared React `sessionId` state.

**P5 — Per-Tab Abort Controller Isolation**: Each tab stores its own `abortController` in `tabMapRef`. Stopping Tab A must not affect Tab B's streaming.

**P6 — Permission: Shared Approval, Per-Tab Request**: `CmdPermissionManager` approvals are global (user trust is shared). Permission request prompts are per-tab (each tab raises its own `cmd_permission_request`, guarded by `isActiveTab`).

**P7 — Tab Switch = Save + Restore + Re-derive**: 1) Save current React state into source tab's `tabMapRef` entry. 2) Restore target tab's state from `tabMapRef`. 3) Call `bumpStreamingDerivation()`. NEVER call `setIsStreaming()` during tab switch.

**P8 — Session ID Stability Across Restarts**: Once assigned, a session ID MUST NEVER be replaced. Backend restart → fresh SDK client → map SDK's internal ID to original `app_session_id` → emit `session_start` with original ID.

### Tab Persistence

- Persisted to `~/.swarm-ai/open_tabs.json` via backend settings API (not localStorage)
- Debounced 500ms to avoid excessive writes during streaming
- Save gated by `fileRestoreDone` — prevents overwriting persisted state with temporary default tab
- Race condition guard: if user started a conversation before file restore, skip restore

### Key Files

`ChatPage.tsx`, `useChatStreamingLifecycle.ts`, `useUnifiedTabState.ts`, `tabPersistence.ts`, `chat.ts`

---

## 3. Session Identity & Backend Isolation

### The Dual-ID Model

```
app_session_id  — Frontend tab ID. Stable across backend restarts. CANONICAL for all persistence.
sdk_session_id  — Claude SDK internal ID. Changes on every fresh client. IMPLEMENTATION DETAIL.
effective_session_id = app_session_id ?? sdk_session_id  (used in ~15 places)
```

### Session ID Rules

1. `session_start` SSE event MUST carry `app_session_id`, never the SDK's internal ID
2. All messages saved under `app_session_id`
3. `_active_sessions` dict keyed by `effective_session_id`
4. Frontend tab's `tabMapRef` entry MUST NOT be overwritten with a different session ID

### Resume-Fallback Path (Backend Restart)

```
Frontend sends chat_stream with session_id (app_session_id)
→ Backend: is_resuming=True, _get_active_client() returns None (client lost)
→ Backend: Falls back to PATH A (fresh SDK client)
→ Backend: session_context["app_session_id"] = original tab session ID
→ Backend: Emits session_start with app_session_id (NOT new SDK ID)
→ Backend: Saves all messages under app_session_id
→ Backend: Stores client in _active_sessions keyed by app_session_id
```

### Per-Session Concurrency Guard

`_execute_on_session()` uses per-session `asyncio.Lock` keyed by `app_session_id ?? session_id`. If lock is held → return `SESSION_BUSY` immediately (no queuing). Ephemeral lock keys cleaned up in `finally`; stable keys cleaned up by `_cleanup_session()`.

### Per-Session Permission Queues

`PermissionManager` provides per-session `asyncio.Queue` instances. Each session gets its own queue via `get_session_queue(session_id)`. Never use deprecated `get_permission_queue()` (global queue).

### CmdPermissionManager vs PermissionManager

| System | Scope | Storage | Purpose |
|--------|-------|---------|---------|
| `CmdPermissionManager` | Global | Filesystem (`~/.swarm-ai/cmd_permissions/`) | Persistent command pattern approvals (glob) |
| `PermissionManager` | Per-session | In-memory (asyncio.Event + Queue) | Real-time HITL request/response signaling |

`CmdPermissionManager.is_approved()` checked BEFORE `PermissionManager` — if pattern previously approved, no HITL prompt needed.

### Session Cleanup Lifecycle

Triggers: TTL expiry (12h) | explicit delete | backend shutdown

`SessionLifecycleHookManager.fire_post_session_close()` runs 3 hooks in order:
1. `DailyActivityExtractionHook` — conversation summary extraction
2. `WorkspaceAutoCommitHook` — git auto-commit
3. `DistillationTriggerHook` — memory distillation check

Each hook error-isolated with 30s timeout. Failure does NOT block subsequent hooks.

### Key Files

`agent_manager.py`, `session_manager.py`, `permission_manager.py`, `cmd_permission_manager.py`, `security_hooks.py`, `chat.py`

---

## 4. Context & Memory Safety

### The 11 Context Files

```
P0  SWARMAI.md     System  Never truncated     Core identity
P1  IDENTITY.md    System  Never truncated     Agent name/avatar
P2  SOUL.md        System  Never truncated     Personality/principles
P3  AGENT.md       System  Truncatable         Behavioral rules
P4  USER.md        User    Truncatable         User profile
P5  STEERING.md    User    Truncatable         Session-level rules
P6  TOOLS.md       User    Truncatable         Tool guidance
P7  MEMORY.md      User    Head-truncated      Cross-session memory (newest kept)
P8  EVOLUTION.md   User    Head-truncated      Self-evolution registry (newest kept)
P9  KNOWLEDGE.md   User    Truncatable         Domain knowledge
P10 PROJECTS.md    User    Lowest priority     Active projects
```

### Two-Mode Copy Rules (ensure_directory)

- System files (P0-P3): ALWAYS overwrite on startup. Permissions 0o444 (readonly). Byte-comparison optimization.
- User files (P4-P10): Copy ONLY if missing. NEVER overwrite. Permissions 0o644.
- BOOTSTRAP.md: NOT in CONTEXT_FILES. Ephemeral onboarding, created only when USER.md is unfilled template.

### Token Budget

| Model Context Window | Token Budget |
|---------------------|-------------|
| ≥ 200K | 40,000 |
| 64K – 200K | 25,000 |
| < 64K | 25,000 (L0 compact cache) |

Truncation order: P10 → P9 → P8 → ... → P3. P0-P2 never truncated.
MEMORY.md and EVOLUTION.md truncate from HEAD (keep newest). All others truncate from TAIL (keep beginning).
Models <32K: KNOWLEDGE.md and PROJECTS.md excluded entirely.

### Context Assembly Pipeline

```
ContextDirectoryLoader.ensure_directory()     → Provision/update .context/ files
ContextDirectoryLoader.load_all()             → L1 cache or assemble from sources
_build_system_prompt()                        → Add BOOTSTRAP.md, DailyActivity, metadata
SystemPromptBuilder.build()                   → Add identity, safety, workspace, datetime, runtime
→ Final system prompt sent to Claude SDK
```

Context loading failures NEVER block agent startup — entire pipeline wrapped in try/except.

### L0/L1 Cache

- L1 cache: `L1_SYSTEM_PROMPTS.md` with `<!-- budget:NNNNN -->` header for budget-tier validation
- L1 freshness: git-first (`git status --porcelain`), mtime fallback
- L0 cache: AI-summarized compact version for <64K models
- Never edit cache files directly — always edit source `.context/*.md` files

### Memory Lifecycle (Closed Loop)

```
Conversation → DailyActivity (code-enforced hook) → Distillation (code-enforced hook) → MEMORY.md → Next session
```

| Operation | Mechanism | Can model forget? |
|-----------|-----------|-------------------|
| DailyActivity extraction | SessionLifecycleHookManager | No — code-enforced |
| Distillation (primary) | DistillationTriggerHook | No — code-enforced |
| Distillation (fallback) | `.needs_distillation` flag | Yes — prompt-dependent |
| MEMORY.md loading | CONTEXT_FILES P7 | No — code-enforced |
| EVOLUTION.md loading | CONTEXT_FILES P8 | No — code-enforced |
| DailyActivity loading | `_build_system_prompt()` scan | No — code-enforced |

### File Locking Rules

- MEMORY.md / EVOLUTION.md: ALWAYS use `locked_write.py` (fcntl.flock via `.md.lock`)
- DailyActivity: Append-only with OS `O_APPEND` — no lock needed
- L1 cache: No lock — single writer, stale reads harmless

### DailyActivity Safety

- Files NEVER modified on disk during loading — truncation is ephemeral (in-memory only)
- Per-file token cap: 2,000 tokens. Truncates from head (keeps newest entries).
- Last 2 files loaded by filename date (handles weekends/gaps)
- Frontmatter tracks `distilled: true/false` for distillation trigger

### Key Files

`context_directory_loader.py`, `system_prompt.py`, `agent_manager.py`, `memory_extractor.py`, `daily_activity_writer.py`, `locked_write.py`, `hooks/*.py`, `backend/context/*.md`

---

## 5. Self-Evolution Guardrails

### Design Philosophy

- Prompt-driven by design: trigger detection and evolution loops are agent self-monitoring instructions
- Code-enforced where it matters: EVOLUTION.md loading, SSE parsing, config defaults, file provisioning
- Filesystem as single source of truth: all evolution data in `.context/EVOLUTION.md`

### Code-Enforced vs Prompt-Dependent

| Layer | Reliability |
|-------|-------------|
| EVOLUTION.md loading at P8 | Code-enforced |
| SSE event marker parsing (`_extract_evolution_events()`) | Code-enforced |
| Frontend evolution rendering | Code-enforced |
| Config defaults (`AppConfigManager`) | Code-enforced |
| Trigger detection | Prompt-dependent |
| Evolution loop execution | Prompt-dependent |
| EVOLUTION.md writes | Prompt-dependent |

### SSE Event Chain

```
Agent text → <!-- EVOLUTION_EVENT: {...} --> marker
→ _extract_evolution_events() regex in chat.py
→ Separate SSE data line
→ Frontend: event.type.startsWith('evolution_')
→ Message with evolutionEvent property → EvolutionMessage component
```

Markers are HTML comments — invisible in rendered markdown, survive DB persistence.
Extracted events are frontend-only — NOT persisted separately to DB.

### EVOLUTION.md Format

5 sections with sequential IDs: E-entries (Capabilities), O-entries (Optimizations), C-entries (Corrections), K-entries (Competence), F-entries (Failed). Soft cap: 30 active entries. Lifecycle: active → deprecated (30 days idle) → superseded.

### Configuration Boundaries

- `auto_approve_skills/scripts/installs: false` — user must approve by default. NEVER change defaults to `true`.
- `max_triggers_per_session: 3` — hard cap via `/tmp/swarm-evo-triggers-{session_id}`
- `max_retries: 3` — per-trigger attempt limit
- ADL priority: Stability > Interpretability > Reusability > Extensibility > Novelty

### Key Files

`s_self-evolution/SKILL.md`, `EVOLUTION.md`, `EVOLUTION_CHANGELOG.jsonl`, `chat.py` (SSE parsing), `evolution.ts`, `EvolutionMessage.tsx`

---

## 6. Global Anti-Patterns

These apply across the entire codebase. Violating any of these has caused regressions in the past.

1. **Shared mutable state between sessions**: Never add module-level mutable state (dicts, lists, sets) that isn't keyed by session ID. Use per-session data structures or existing `_active_sessions` / `_session_locks` patterns.

2. **React useState for cross-tab decisions**: Never read React `useState` values to make decisions about a specific tab. Always read from `tabMapRef` (authoritative source). React state is a display mirror only.

3. **Overwriting user files**: Never overwrite files with `user_customized=True` in `ensure_directory()`. User edits are sacred — they survive across app updates.

4. **Global permission queue**: Never use `permission_manager.get_permission_queue()` (deprecated). Use `get_session_queue(session_id)` for proper per-session isolation.

5. **Direct MEMORY.md writes**: Never write to MEMORY.md without `locked_write.py`. Concurrent writes from hooks + skills can corrupt the file.

6. **Session ID leakage**: Never emit `session_start` with the SDK's internal session ID. Always use `app_session_id` (the frontend tab's original ID).

7. **setIsStreaming() during tab switch**: Never call `setIsStreaming()` during tab switch — it modifies `pendingStreamTabs` which corrupts the source tab's pending state. Use `bumpStreamingDerivation()` instead.

8. **Shared abortController**: Never use a shared `abortRef` for multiple tabs. Each tab stores its own `abortController` in `tabMapRef`.

9. **Reading sessionIdRef for specific tab**: `sessionIdRef.current` reflects the active tab, not necessarily the originating tab. In stream handlers, use `tabMapRef.current.get(capturedTabId)?.sessionId`.

10. **Saving tabs before fileRestoreDone**: Never persist tab state before `fileRestoreDone.current` is true — this overwrites real persisted tabs with a single default tab.

---

## 7. Code Documentation Standards

All code files MUST include module-level documentation:

- Python: Triple-quoted docstring as first statement. One-line summary, key public symbols, re-export notes.
- TypeScript/React: `/** */` block comment at top. File purpose, key exports.
- Test files: What is tested, methodology, key properties/invariants.

---

## 8. Regression Checklists

### Multi-Tab Changes
- [ ] Every `setIsStreaming()` passes explicit `tabId`
- [ ] Backend API calls use `tabMapRef.current.get(tabId)?.sessionId`
- [ ] Stream handlers receive `tabId` at creation time
- [ ] Tab switch uses `bumpStreamingDerivation()`, not `setIsStreaming()`
- [ ] New React state is per-tab (in UnifiedTab) or display-only
- [ ] `isActiveTab` guard before writing to React state in stream handlers
- [ ] Error/complete handlers use `capturedTabId`, not `activeTabIdRef.current`

### Session/Backend Changes
- [ ] `session_start` SSE uses `app_session_id`
- [ ] Messages saved under `effective_session_id`
- [ ] `_active_sessions` keyed by `effective_session_id`
- [ ] Per-session lock uses `app_session_id` when available
- [ ] Permission requests routed to per-session queue
- [ ] Session cleanup calls `remove_session_queue()`
- [ ] `_env_lock` held during client creation

### Context/Memory Changes
- [ ] `ensure_directory()` respects two-mode copy
- [ ] System files get 0o444 after write
- [ ] Truncation order follows priority DESC
- [ ] MEMORY.md and EVOLUTION.md truncate from HEAD
- [ ] L1 cache includes budget header
- [ ] DailyActivity files never modified on disk during loading
- [ ] `locked_write.py` used for all MEMORY.md writes
- [ ] Lifecycle hooks fire in order and are error-isolated

### Self-Evolution Changes
- [ ] `_extract_evolution_events()` regex matches skill marker format
- [ ] Evolution SSE events emitted as separate data lines
- [ ] Frontend `startsWith('evolution_')` catches all event types
- [ ] EVOLUTION.md at P8 with `truncate_from="head"`, `user_customized=True`
- [ ] `auto_approve_*` defaults remain `false`

---

## 9. File Structure Quick Reference

```
backend/
├── core/
│   ├── agent_manager.py             # Session orchestration, system prompt assembly
│   ├── session_manager.py           # Session CRUD with DB + in-memory cache
│   ├── permission_manager.py        # Per-session HITL permission queues
│   ├── cmd_permission_manager.py    # Global filesystem-backed command approvals
│   ├── context_directory_loader.py  # 11 context files, token budget, L0/L1 cache
│   ├── system_prompt.py             # Non-file prompt sections (identity, safety, etc.)
│   ├── session_hooks.py             # SessionLifecycleHookManager framework
│   ├── daily_activity_writer.py     # Append-only DailyActivity writes
│   ├── memory_extractor.py          # LLM-powered extraction for 🧠 button
│   └── app_config_manager.py        # Zero-IO config cache, evolution defaults
├── hooks/
│   ├── daily_activity_hook.py       # DailyActivityExtractionHook
│   ├── auto_commit_hook.py          # WorkspaceAutoCommitHook
│   └── distillation_hook.py         # DistillationTriggerHook
├── routers/chat.py                  # SSE streaming, evolution event parsing
├── scripts/locked_write.py          # Locked MEMORY.md/EVOLUTION.md modification
├── context/*.md                     # Default templates for 11 context files
└── skills/s_self-evolution/         # Prompt-driven evolution engine

desktop/src/
├── pages/ChatPage.tsx               # Tab orchestration, message rendering
├── hooks/
│   ├── useChatStreamingLifecycle.ts # SSE event processing, stream handlers
│   └── useUnifiedTabState.ts        # Tab CRUD, tabMapRef, persistence
├── services/
│   ├── chat.ts                      # SSE connection, backend API calls
│   ├── tabPersistence.ts            # Filesystem-backed tab state persistence
│   └── evolution.ts                 # Evolution event TypeScript interfaces
└── components/chat/
    └── EvolutionMessage.tsx          # Evolution event renderer

~/.swarm-ai/
├── SwarmWS/.context/                # 11 context files + L0/L1 cache
├── SwarmWS/Knowledge/DailyActivity/ # Append-only daily logs
├── cmd_permissions/                 # Approved command patterns
├── open_tabs.json                   # Persisted tab state
├── config.json                      # App config (evolution settings, etc.)
└── data.db                          # SQLite (sessions, messages, tasks, etc.)
```
