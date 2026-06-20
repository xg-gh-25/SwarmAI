# Design — Frontend/Backend State Reconciliation

## Overview

The fix is to make the frontend's session view **reconcile from backend ground
truth (DB + permission_manager) on every reconnect**, not just trust the last
event of an ephemeral SSE stream. Today only the `isStreaming` *flag* is
reconciled (15s `/streaming-state` poll); message *content* and *pending
approvals* are not. We add a **content-level reconcile** and a **pending-approval
re-surface**, both keyed by stable ids so they are idempotent and collision-safe.

Design principle (matches repo meta-lesson): make the wrong thing impossible.
The UI must be a *projection of backend state reconciled on connect*, never a
write-once snapshot that silently diverges.

## Architecture

### Current architecture (verified)

```
SSE stream (ephemeral) ──┐
                         ├─► frontend tabMap.messages (in-memory, authoritative once populated)
DB (durable) ────────────┘        ▲
                                  │ loadSessionMessages()  ← GATED OFF when messages.length>0
backend subprocess (survives disconnect, keeps persisting via _persist_assistant_blocks)
```

- Backend disconnect handling: `routers/chat.py:_recover_streaming_on_disconnect`
  → STREAMING→IDLE, subprocess left alive, output persisted to DB.
- Flag reconcile: `GET /chat/sessions/streaming-state` (every 15s) — syncs
  `isStreaming` only.
- Content reload: `ChatPage.tsx` `loadSessionMessages()` — gated by
  `!isStreaming && messages.length === 0` → effectively never runs for a live tab.
- Permission: `permission_manager._pending_requests` holds pending; only
  approve/deny endpoints read it; **no reconnect re-surface**.

## Components and Interfaces

### D1 — Incremental message reconcile (Req 1, 5)

Backend: add (or extend) an endpoint to fetch messages since a cursor.
- `GET /chat/sessions/{id}/messages?since_seq=<n>` (or `?after_id=<id>`).
- Returns messages with stable `id`, monotonic `seq`/`created_at`, merged
  assistant rows (reuse the existing consecutive-assistant merge in chat.py).

Frontend: replace the coarse gate with an **incremental merge**.
- On reconnect / stream-resume / 15s reconcile tick: call the since-cursor
  endpoint with the tab's highest known `seq`/`id`.
- Merge into `MessageStore` via append-or-update by id (single-writer; reuse
  `store.append` / `store.updateById`). Never `replace()` the whole list.
- Guard: skip merge for messages whose id matches an in-flight optimistic/
  streaming message (avoid clobbering live tokens — Req 1.4).
- This removes the `messages.length === 0` gate while still preventing the
  "prior history re-appears" regression, because we only pull `seq > lastKnown`.

Key invariant: reconcile is **append-or-update by id**, ordered by `seq`. Running
it repeatedly is idempotent (Req 1.5).

### D2 — Pending-approval re-surface (Req 2)

Backend: add `GET /chat/sessions/{id}/pending-permissions` returning any
`_pending_requests` for the session that are still unresolved (status != resolved/
expired).

Frontend: on (re)connect, after content reconcile, call it; for each pending
request, inject a `cmd_permission_request` content block via the store (dedup by
`requestId` — Req 2.3). Reuse the existing `InlinePermissionRequest` render path
(no new UI).

Alternative considered: have the backend re-emit on the NEW SSE stream when a
session with pending requests reconnects. Rejected for v1 — the GET-on-connect
path is simpler, stateless, and works even if the SSE attaches late.

### D3 — Bounded permission wait (Req 3)

`permission_manager.wait_for_permission_decision(timeout=...)`: caller
(`security_hooks.py:~196`, confirmed single caller) passes a bounded interactive
timeout (300s). On timeout: return a distinct `"timeout"` outcome (not silently
`"deny"`) so the hook can emit a visible "approval timed out — auto-denied"
message. Preserve the existing 2h behavior only for non-interactive/headless modes
if needed (config flag).

NOTE: in-flight in a parallel Swarm session as "fix #2". This spec documents the
contract; coordinate before editing `permission_manager.py` to avoid collision.

### D4 — Narrow dangerous rm pattern (Req 4)

Replace blanket `rm -rf *` matching with target-aware logic in the dangerous
command gate:
- Auto-allow when ALL `rm -rf` targets are under OS temp (`/tmp/`, `/var/folders/`,
  `$TMPDIR`).
- Still gate when any target is a dangerous root (`/`, `~`, `$HOME`, workspace root)
  or uses a glob that could expand outside temp.

NOTE: in-flight "fix #3" — currently broken; `test_harmless_tmp_rm_auto_approved_no_prompt`
FAILS by 60s timeout (the `/tmp` rm still prompts). The fix must make that test
pass without hanging.

## Data Models

### Message (reconcile cursor view)

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Stable message id (dedup/merge key) |
| `seq` | int | Monotonic per-session sequence (cursor for `since_seq`) |
| `role` | enum | `user` \| `assistant` \| `system` |
| `content` | block[] | Assistant rows merged per existing consecutive-merge |
| `created_at` | iso8601 | Tiebreaker / display order |

### PendingPermission (re-surface view)

| Field | Type | Notes |
|-------|------|-------|
| `requestId` | string | `perm_<hex>` — dedup key for the dialog |
| `sessionId` | string | App session id (matches tab) |
| `toolName` | string | `Bash` |
| `toolInput` | object | command + args |
| `reason` | string | Why approval is required |
| `status` | enum | `pending` \| `resolved` \| `expired` (only `pending` re-surfaces) |

No DB schema change is required: messages already carry id/seq/created_at;
pending requests already live in `permission_manager._pending_requests`. This
spec only adds read endpoints + frontend merge logic.

## Correctness Properties

### Property 1: No loss
Every assistant row persisted during a disconnect window appears in the tab after
the next reconcile.

**Validates: Requirements 1.1, 1.3**

### Property 2: No regress
Reconcile never adds a message with `seq <= lastKnown`, so scrolled-past history
never re-appears.

**Validates: Requirements 1.2**

### Property 3: Idempotent
Running reconcile twice with the same cursor yields the same message list.

**Validates: Requirements 1.5**

### Property 4: No clobber
A live streaming/optimistic message id is never overwritten by a reconcile merge.

**Validates: Requirements 1.4**

### Property 5: Approval visible
An unresolved pending request always yields exactly one dialog after reconnect.

**Validates: Requirements 2.1, 2.3**

### Property 6: No zombie wait
No permission wait blocks longer than the configured interactive timeout, and the
timeout is user-visible.

**Validates: Requirements 3.1, 3.2**


## Error Handling

- since-cursor endpoint 4xx/5xx or network error → keep current in-memory messages,
  retry on next 15s tick; never clear the tab on a failed reconcile.
- Malformed/duplicate message ids from DB → merge dedups by id (last-write-wins on
  update); never throws into the render path.
- pending-permissions endpoint failure → no dialog injected this tick; retried next
  reconnect; never blocks streaming.
- Reconcile must be fully fallback-guarded (try/catch) — a reconcile failure must
  never abort the live SSE stream or crash the tab (matches repo invariant: guards
  never block chat).



A parallel Swarm session is actively editing `security_hooks.py`,
`permission_manager.py`, `streaming_orchestrator.py`, `routers/chat.py` and the
two test files, with repo auto-commit (~1/min). D3 and D4 overlap that work.
Recommended order: land **D1 + D2 (the genuinely new, frontend-led core)** first
once the Swarm session is paused/converged on D3/D4; otherwise stage D1/D2
independently to minimize overlap. Stage backend endpoint additions (new files /
new routes) to reduce diff conflict with the in-flight edits.

## Testing strategy

- D1: unit — merge appends `seq > lastKnown`, updates by id, ignores `seq <= lastKnown`,
  idempotent on double-run, never clobbers a streaming message id.
- D1: integration — simulate disconnect window (persist N assistant rows while
  "disconnected"), reconnect, assert the N rows surface exactly once.
- D2: unit — pending request re-surfaces once on connect; resolved request does not;
  dedup by requestId.
- D3: unit — timeout returns `"timeout"` outcome + emits visible message; bounded
  at configured seconds.
- D4: `test_harmless_tmp_rm_auto_approved_no_prompt` passes (no prompt, no hang);
  dangerous-root rm still prompts.
- Run targeted, single-process (`-n0 --timeout=60`); never the full xdist suite
  (deadlock — observed this session).
