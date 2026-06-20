# Implementation Plan — Frontend/Backend State Reconciliation

## Overview

Make the frontend session view reconcile from backend ground truth (DB +
permission_manager) on every reconnect: add incremental message reconcile (core)
and pending-approval re-surface, plus the bounded permission wait and narrowed rm
pattern. Tasks 1–2 are the genuinely new core (lowest collision risk); tasks 4–5
overlap in-flight work in a parallel Swarm session.

## Tasks

> **Status (2026-06-21):** Tasks 3/4/5 landed by a parallel Swarm session
> (commit `46309b03`, 35 tests green). Task 2 landed by Kiro (commit `003ba8f1`).
> Task 1 deviated — see note. Task 6 partially done (tsc + targeted tests; real-app
> E2E still pending).

> Collision note: a parallel Swarm session may be editing `security_hooks.py`,
> `permission_manager.py`, `routers/chat.py`, `streaming_orchestrator.py` and the
> two test files (auto-commit ~1/min). Confirm it is paused/converged before
> starting tasks 3 and 4. Tasks 1–2 (new endpoints + frontend merge) have the
> least overlap — start there.

- [~] 1. Backend: incremental message endpoint  — DEVIATED (not implemented)
  - Reason: merged-assistant bubbles use the FIRST raw-row id and GROW as the
    backend appends consecutive assistant rows, so a raw `after_id`/`since_seq`
    cursor would split bubbles and mis-key the merge. Instead Task 2 reuses the
    existing `GET /sessions/{id}/messages` (ETag → 304 when unchanged, cheap) and
    merges by id on the frontend. The since-cursor remains a possible future
    optimization for very large sessions but is NOT required for correctness.
  - [ ] 1.1 (optional, deferred) add `after_id` only if full-fetch cost becomes a
    problem; must be merge-layer-aware (return whole bubbles, not raw rows).
  - _Requirements: 1.1, 1.2, 5.1, 5.2_

- [x] 2. Frontend: incremental content reconcile  — DONE (commit `003ba8f1`)
  - [x] 2.1 Replaced the `messages.length === 0` gate in `handleBackendRecovered`:
    empty tab → full load; non-empty non-streaming tab → merge.
  - [x] 2.2 Added `mergeTabFromDb()` → `MessageStore.reconcile` (`_applyMerge`):
    append-or-update by id, DB wins for completed, streaming message protected,
    resume-boundary respected. Never `replace()`.
  - [x] 2.3 Wired into backend-recovered for active + all background non-streaming
    tabs. (15s tick + post-Stop trigger deferred — see Notes.)
  - [x] 2.4 Relies on existing `_applyMerge` tests (49 MessageStore/resume green);
    tsc clean.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.2_

- [x] 3. Pending-approval re-surface  — DONE by Swarm (commit `46309b03`)
  - `/sessions/streaming-state` now falls back to the durable permission store
    (`get_pending_for_session` + `has_live_waiter`) and surfaces `pending_question`
    so the 15s reconcile re-renders the prompt even if the original SSE was lost.
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 4. Bounded + visible permission wait  — DONE by Swarm (commit `46309b03`)
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 5. Narrow dangerous rm pattern  — DONE by Swarm (commit `46309b03`)
  - `test_harmless_tmp_rm_auto_approved_no_prompt` now PASSES (previously hung 60s).
  - _Requirements: 4.1, 4.2, 4.3_

- [~] 6. End-to-end verification  — PARTIAL
  - [ ] 6.1 Disconnect-window integration test (persist N rows while "disconnected",
    reconnect, assert N surface once + spinner clears) — NOT yet written.
  - [x] 6.2 Targeted single-process tests only: backend 35 green (`-n0`), frontend
    49 green, tsc clean. Full xdist suite deliberately NOT run (deadlock observed).
  - [x] 6.3 Committed per-file with `Co-Authored-By: Swarm <swarm@swarmai.dev>`.
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
