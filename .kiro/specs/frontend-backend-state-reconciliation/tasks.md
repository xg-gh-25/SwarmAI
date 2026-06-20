# Implementation Plan — Frontend/Backend State Reconciliation

## Overview

Make the frontend session view reconcile from backend ground truth (DB +
permission_manager) on every reconnect: add incremental message reconcile (core)
and pending-approval re-surface, plus the bounded permission wait and narrowed rm
pattern. Tasks 1–2 are the genuinely new core (lowest collision risk); tasks 4–5
overlap in-flight work in a parallel Swarm session.

## Tasks

> Collision note: a parallel Swarm session may be editing `security_hooks.py`,
> `permission_manager.py`, `routers/chat.py`, `streaming_orchestrator.py` and the
> two test files (auto-commit ~1/min). Confirm it is paused/converged before
> starting tasks 3 and 4. Tasks 1–2 (new endpoints + frontend merge) have the
> least overlap — start there.

- [ ] 1. Backend: incremental message endpoint
  - [ ] 1.1 Add `GET /chat/sessions/{id}/messages?since_seq=<n>` returning messages
    with stable `id` + monotonic `seq`/`created_at`, reusing the consecutive-assistant
    merge helper. Register BEFORE `/sessions/{session_id}` to avoid path capture.
  - [ ] 1.2 Unit tests: returns only `seq > since_seq`, ordered, merged; empty when
    none newer.
  - _Requirements: 1.1, 1.2, 5.1, 5.2_

- [ ] 2. Frontend: incremental content reconcile
  - [ ] 2.1 Replace the `messages.length === 0` gate in `ChatPage.tsx` reconnect path
    with an incremental merge that calls the since-cursor endpoint using the tab's
    highest known `seq`/`id`.
  - [ ] 2.2 Merge via `MessageStore.append`/`updateById` (append-or-update by id);
    never `replace()`. Skip ids that match an in-flight streaming/optimistic message.
  - [ ] 2.3 Wire the merge into: active-tab reconnect, stream-resume, and the 15s
    `/streaming-state` reconcile tick.
  - [ ] 2.4 Tests: appends newer, updates by id, ignores older (no history re-appear),
    idempotent on double-run, does not clobber a live streaming message.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.2_

- [ ] 3. Pending-approval re-surface
  - [ ] 3.1 Backend: `GET /chat/sessions/{id}/pending-permissions` returning unresolved
    `_pending_requests` for the session.
  - [ ] 3.2 Frontend: on (re)connect (after content reconcile), inject a
    `cmd_permission_request` block per pending request via the store; dedup by
    `requestId`; reuse `InlinePermissionRequest`.
  - [ ] 3.3 Tests: surfaces once on connect, deduped by requestId, skips resolved.
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [ ] 4. Bounded + visible permission wait  (overlaps in-flight "fix #2")
  - [ ] 4.1 `wait_for_permission_decision`: return a distinct `"timeout"` outcome;
    caller `security_hooks.py` passes bounded interactive timeout (300s) and emits a
    visible "approval timed out — auto-denied" message on timeout.
  - [ ] 4.2 Tests: bounded at configured seconds; timeout → visible message + deny;
    decision path unchanged.
  - _Requirements: 3.1, 3.2, 3.3_

- [ ] 5. Narrow dangerous rm pattern  (overlaps in-flight "fix #3", currently FAILING)
  - [ ] 5.1 Make the dangerous-command gate target-aware: auto-allow `rm -rf` when all
    targets are under OS temp (`/tmp/`, `/var/folders/`, `$TMPDIR`); still gate
    dangerous roots and temp-escaping globs.
  - [ ] 5.2 Make `test_harmless_tmp_rm_auto_approved_no_prompt` pass WITHOUT hanging;
    keep a dangerous-root rm test still prompting.
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 6. End-to-end verification
  - [ ] 6.1 Disconnect-window integration test: persist N assistant rows while
    "disconnected", reconnect, assert all N surface exactly once and the spinner clears.
  - [ ] 6.2 Run targeted single-process only (`-n0 --timeout=60`); NEVER full xdist
    suite (deadlock observed). Confirm the two touched test files green before commit.
  - [ ] 6.3 Commit per-file immediately after editing (repo auto-commit sweeps
    uncommitted work). Co-Authored-By: Swarm <swarm@swarmai.dev>.
  - _Requirements: 1.*, 5.*_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "3.1", "4", "5"], "note": "independent starts; 4 and 5 overlap the in-flight Swarm session — gate on it being paused" },
    { "wave": 2, "tasks": ["2", "3.2"], "note": "depend on wave 1 (since-cursor endpoint + reconnect path)" },
    { "wave": 3, "tasks": ["6"], "note": "end-to-end verification, depends on all" }
  ]
}
```

- Task 2 depends on Task 1 (needs the since-cursor endpoint).
- Task 3.2 (frontend) depends on Task 2's reconnect path; Task 3.1 (backend) is independent.
- Tasks 4 and 5 overlap the in-flight Swarm session — do them only after it pauses/converges.
- Task 6 depends on all.

## Notes

- Run targeted, single-process only (`-n0 --timeout=60`); NEVER the full xdist
  suite — deadlock was observed this session.
- Commit per-file immediately after editing — repo auto-commit (~1/min) sweeps
  uncommitted work into unrelated commits.
- All commits: `Co-Authored-By: Swarm <swarm@swarmai.dev>`.
- Confirm the parallel Swarm session is paused before tasks 4–5 to avoid the
  documented multi-agent collision.

## Suggested landing order

1. Tasks 1–2 (the genuinely new core: content reconcile) — lowest collision risk.
2. Task 3 (pending-approval re-surface).
3. Tasks 4–5 only after the parallel Swarm session is paused/converged (avoid
   the documented multi-agent collision + auto-commit sweep).
