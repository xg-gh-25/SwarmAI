---
inclusion: fileMatch
fileMatchPattern: ["backend/core/session_router.py", "backend/core/session_unit.py", "backend/core/session_registry.py", "backend/core/session_manager.py", "backend/core/lifecycle_manager.py", "backend/core/streaming_orchestrator.py", "backend/core/permission_manager.py", "backend/core/security_hooks.py", "backend/routers/chat.py"]
---

# Session Identity and Backend Isolation Principles

## Core Invariant: One Tab = One Session ID = One SessionUnit

The app session ID is the permanent identity of a conversation: the key for message
persistence, SSE events, permission queues, approvals, and frontend tab state. Once
assigned it MUST NOT change. Exactly one `SessionUnit` exists per app session ID, held in
`SessionRouter._units` (mutated under `_slot_lock`). The unit outlives its subprocess and
returns to COLD between lives.

## Two IDs, One Canonical

- **App session ID** — `SessionUnit.session_id`. Supplied by the frontend, or minted with
  `uuid4()` in `run_conversation`. Canonical for everything.
- **SDK session ID** — `SessionUnit._sdk_session_id`, captured from the CLI's messages.
  A private detail used ONLY for `--resume`. It never keys persistence or SSE.

Rules:
1. `session_start` always carries `unit.session_id` (streaming_orchestrator), never the SDK ID.
2. Messages, permission queues, and approvals are keyed by the app session ID.
3. The frontend tab's session ID is never overwritten after `session_start`.
4. The `prewarm-` prefix is server-mint-only (`prewarm_channel_session` / desktop
   prewarm pool). It grants TTL/orphan/poison/hook exemptions, so `run_conversation`
   rejects client-supplied IDs with it. Adoption re-keys the unit under `_slot_lock`.

## Resume and Identity Persistence

- A COLD unit with `_sdk_session_id` respawns with `--resume`. If `--resume` hits
  session-not-found, the stale ID is dropped and the DB history is re-injected (true cold
  resume) — never a silent history-less spawn.
- `_sdk_session_id` is KEPT on interrupt, recycle, kill, and shutdown
  (`_crash_to_cold_async(clear_identity=False)`). It is cleared only by `/chat/refresh`,
  give-up, or a session-not-found `--resume`.
- Shutdown: `disconnect_all` persists identities to `session_state.json` BEFORE killing
  anything. Boot: `SessionRouter.__init__` loads them; `get_or_create_unit` pops one into
  the new unit (one-shot).

## Send Admission and Concurrency

The state machine is the guard (`SessionUnit.send()`); there is no per-session lock dict.

- STREAMING with an interrupt in flight (`_stop_event` set) → wait up to 6s for it.
- STREAMING, last event < `AUTO_RECOVER_STALL_THRESHOLD` (180s) → `SessionBusyError`.
  Stalled longer → `force_unstick_streaming` → COLD → respawn.
- WAITING_INPUT with a live outstanding tool_use → `SessionBusyError`. A new message is
  NEVER auto-treated as the answer. A dead waiter is reaped to COLD first.
- If the unit flips out of IDLE during the slot/`_client_io` wait (concurrent refresh,
  kill, or release), yield SESSION_BUSY + `_abort`. The re-check and the STREAMING
  transition stay in one synchronous block with no `await` between them.

## Message Durability

- The user message is persisted BEFORE slot acquisition (`run_conversation`).
- On SESSION_BUSY / QUEUE_TIMEOUT the row is converted to pending
  (`session_pending.mark_pending_by_id`), never deleted. The router's serial drain worker
  delivers it at the next clean IDLE. `retryPayload` is sent to the frontend only when the
  pending conversion failed — otherwise a frontend re-send would double-deliver.
- The `finally` in `run_conversation` rolls back claimed-but-unfinalized pending sets on
  GeneratorExit / CancelledError (SSE disconnect). Don't catch these with a bare
  `except Exception` and skip that rollback.

## Stop, Disconnect, Release

- **User Stop** (`POST /chat/stop` → `interrupt()`): captures `_send_generation` and bails
  if a newer send started; sets `_user_stopped_current_turn` (suppresses self-heal); on
  success recycles the subprocess to COLD with identity kept (PIT01). Only
  `autonomous=True` (tool-hang watchdog) keeps it warm.
- **SSE disconnect**: does not touch the turn. The session owns it: it runs to its own
  end and persists, and the window's subscription (`GET /api/chat/events`) carries it
  to the tabs (`core/session_events`). Never POST `/stop` on disconnect.
- **Tab close** (`/chat/release`): kills an IDLE unit to free its slot; skips channel
  units; skips STREAMING/WAITING_INPUT unless `force=True`, which interrupts first.
  Never raw-`kill()` an active unit.
- Lock rules: `interrupt()` never takes `_client_io`; `_client_io` is a short
  barrier, never held across the stream; spawn holds `_spawn_lock` then `_env_lock`
  through `wrapper.__aenter__()`.

## Per-Session Permission State

`PermissionManager` (`permission_manager.py`) is the only permission system.

- Prompt board: a gate publishes its prompt (`request_decision` / `publish_prompt`) under
  the APP session ID; it is on the board exactly while its waiter is live and undecided.
  The session's stream and `/sessions/streaming-state` both READ it (`open_prompts`,
  `wait_prompt_change`); nothing takes an item, so no reader can lose one. A decision
  goes to the blocked hook whatever the unit's state (`continue_with_permission`); the
  unit reads on only when it was the one waiting for it. `clear_session` on session end.
- Approvals: `approve_command(session_id, command)` stores a SHA-256 command hash in memory
  for that session only. Approvals are not shared across tabs and don't survive a restart.
- Pending requests (not approvals) persist to `~/.swarm-ai/pending_approvals.json` so an
  open prompt survives a daemon restart; idempotency is keyed by (session_id, tool_call_id).
- Approval waits are 4h (`PERMISSION_ANSWER_TIMEOUT_SECONDS`), which must stay below the
  lifecycle WAITING_INPUT watchdog (4h05m).

## Session End and Hooks

- Lifetimes (`LifecycleManager`): 24h idle TTL (channel and prewarm units exempt), orphan
  reaper for IDLE units not in open_tabs.json (10 min), COLD units purged from `_units`
  after 1h.
- EVERY end path (TTL kill, crash→COLD, purge, release) must call
  `LifecycleManager._release_session_state(session_id)`, which clears registry dicts plus
  permission approvals and the queue. A new per-session dict must be added there.
- Post-session hooks fire through `LifecycleManager.enqueue_hooks` (skips `prewarm-`
  sessions; once per unit via `_hooks_enqueued`) → `BackgroundHookExecutor`: one serial
  worker, error-isolated, 180s per hook. The list and order live in `main.py` lifespan.

## Regression Checklist

- [ ] `session_start` and all persistence use `unit.session_id`, never the SDK ID
- [ ] `_sdk_session_id` preserved on every recycle/kill/shutdown path
- [ ] User message persisted before slot acquisition; busy/timeout → pending, not delete
- [ ] `interrupt()` keeps its `_send_generation` stale guard; no raw kill of active units
- [ ] Permission requests go to the per-session queue; approvals stay per session
- [ ] New per-session state is released in `_release_session_state`
- [ ] No new module-level mutable state that is not keyed by session ID
- [ ] Spawn still holds `_spawn_lock` + `_env_lock` through `wrapper.__aenter__()`
