---
inclusion: fileMatch
fileMatchPattern: "backend/core/session_router.py,backend/core/session_unit.py,backend/core/session_registry.py,backend/core/session_manager.py,backend/core/permission_manager.py,backend/core/security_hooks.py,backend/routers/chat.py"
---

# Session Identity and Backend Isolation Principles

## Core Invariant: One Tab = One Session ID = One Conversation

A session ID is the permanent identity of a conversation. It is the key for all message persistence, backend API calls, and frontend tab state. Once assigned, it MUST NOT change for the lifetime of that conversation.

## The Dual-ID Model

```
┌──────────────────────────────────────────────────────────────┐
│  app_session_id (frontend tab ID)                            │
│  ═══════════════════════════════                             │
│  Assigned by the frontend when a tab first sends a message.  │
│  Stable across backend restarts. This is the CANONICAL ID    │
│  for all persistence and SSE communication.                  │
│                                                              │
│  sdk_session_id (Claude SDK internal ID)                     │
│  ═══════════════════════════════                             │
│  Assigned by the Claude SDK subprocess. Changes on every     │
│  fresh client creation. This is an IMPLEMENTATION DETAIL     │
│  that MUST NOT leak into the app's session model.            │
│                                                              │
│  effective_session_id pattern:                               │
│    app_session_id ?? sdk_session_id                          │
│  Used in ~15 places for all persistence and keying.          │
└──────────────────────────────────────────────────────────────┘
```

## Session ID Rules

1. `session_start` SSE event MUST always carry the `app_session_id` (original tab session ID), never the SDK's internal session ID
2. All messages (user + assistant) MUST be saved under `app_session_id`
3. The `_active_sessions` dict MUST be keyed by `effective_session_id` so resume finds the client under the original tab ID
4. Frontend tab's `tabMapRef` entry MUST NOT be overwritten with a different session ID after `session_start`

## Resume-Fallback Path (Backend Restart Scenario)

When the backend restarts and loses its in-memory SDK client:

```
1. Frontend sends chat_stream with session_id (the app_session_id)
2. Backend: is_resuming=True, but _get_active_client() returns None
3. Backend: Falls back to PATH A (fresh SDK client)
4. Backend: session_context["app_session_id"] = original tab session ID
5. Backend: Emits session_start with app_session_id (NOT the new SDK ID)
6. Backend: Saves all messages under app_session_id
7. Backend: Stores client in _active_sessions keyed by app_session_id
```

Anti-pattern: Emitting `session_start` with the new SDK session ID — this causes the tab to silently switch IDs and orphan all previous messages.

## Per-Session Concurrency Guard

`_execute_on_session()` uses a per-session `asyncio.Lock` keyed by `app_session_id ?? session_id`. This prevents double-send corruption (e.g., user clicks Send twice quickly).

Rules:
- Lock key MUST use `app_session_id` when available (stable across resume-fallback)
- For brand-new sessions (both IDs None), use an ephemeral UUID so parallel new sessions don't collide
- If the lock is already held, return `SESSION_BUSY` error immediately — do NOT queue
- Ephemeral lock keys are cleaned up in the `finally` block; stable keys are cleaned up by `_cleanup_session()`

## Per-Session Permission Queues

`PermissionManager` provides per-session `asyncio.Queue` instances for command permission requests. This replaced a global queue that caused cross-session busy-loop contention.

Rules:
- Each session gets its own queue via `get_session_queue(session_id)`
- Security hooks write to the session's queue using the SDK session ID
- Queues are cleaned up via `remove_session_queue()` during session cleanup
- Never use the deprecated `get_permission_queue()` (global queue)

## CmdPermissionManager vs PermissionManager

Two distinct systems that MUST NOT be confused:

| System | Scope | Storage | Purpose |
|--------|-------|---------|---------|
| `CmdPermissionManager` | Global (all sessions) | Filesystem (`~/.swarm-ai/cmd_permissions/`) | Persistent command pattern approvals (glob matching) |
| `PermissionManager` | Per-session | In-memory (asyncio.Event + Queue) | Real-time HITL permission request/response signaling |

`CmdPermissionManager.is_approved()` is checked BEFORE `PermissionManager` — if a command pattern was previously approved, no HITL prompt is needed.

## Session Cleanup Lifecycle

Sessions are cleaned up via `_cleanup_session()` triggered by:
- TTL expiry (2h stale session cleanup loop)
- Explicit delete (user closes session)
- Backend shutdown (`disconnect_all()`)

Cleanup fires `SessionLifecycleHookManager.fire_post_session_close()` via `BackgroundHookExecutor` which runs 4 hooks in order:
1. `DailyActivityExtractionHook` — extracts conversation summary
2. `WorkspaceAutoCommitHook` — git auto-commit (uses shared `git_lock`)
3. `DistillationTriggerHook` — memory distillation check
4. `EvolutionMaintenanceHook` — deprecate/prune idle EVOLUTION.md entries

Hooks run as fire-and-forget `asyncio.Task`s via `BackgroundHookExecutor` — they never block the chat path. Each hook is error-isolated with 30s timeout. A failing hook does NOT block subsequent hooks.

## Regression Checklist

When modifying session or permission code:

- [ ] `session_start` SSE event uses `app_session_id`, not SDK session ID
- [ ] Messages saved under `effective_session_id` (app_session_id ?? sdk_session_id)
- [ ] `_active_sessions` keyed by `effective_session_id`
- [ ] Per-session lock uses `app_session_id` when available
- [ ] Permission requests routed to per-session queue, not global queue
- [ ] Session cleanup calls `remove_session_queue()` for the session
- [ ] No new global mutable state that could leak between sessions
- [ ] `_env_lock` held during client creation (subprocess inherits correct env)
