# Requirements — Subprocess Recycle on Interrupt

## Problem

A CLI subprocess that serves many turns can be left in a **corrupt turn-state**
after a soft interrupt. The next `send()` reuses the IDLE subprocess and gets an
instant (<10ms) empty `error_during_execution`. The zombie detector
(`streaming_orchestrator`) then kills the tree and respawns via `--resume`.

Evidence (backend-daemon.log, 3.25 days):
- 21 `zombie_via_error` events; 19 recovered on the first respawn, 2 escalated.
- Confirmed trace: pid 21166 served ~22 turns over 2h28m, was interrupted by a
  client-disconnect + Stop at 20:28:31, and the next query 10s later returned an
  instant empty error → kill + respawn.
- The cost is paid on the USER's next turn: failed round-trip + 5s retry backoff
  + ~10s respawn ≈ a 15s stall at turn start, after an interrupt.

The detector is the **recovery**, working ~90% single-shot. The disease is that
poison is detected **lazily on reuse** instead of being handled when the
interrupt happens.

## Root cause (verified in live code)

- `SessionUnit.interrupt()` success path transitions `STREAMING → IDLE` and
  **leaves the subprocess alive and reusable** (session_unit.py ~2575). User
  Stop, autonomous watchdog escape, and the CompactionGuard ladder all flow
  through here.
- `SessionUnit.send()` Layer 0 reuses an IDLE subprocess with **no health
  check** (session_unit.py ~1340) → poison lands on the next turn.

## Hard constraints (must not regress)

1. **Disconnect mid-tool preservation.** `flush_subprocess_pipe()` deliberately
   does NOT kill on timeout because the subprocess may be executing a tool whose
   output is persisted to DB for frontend reconciliation (the lost-response bug
   Swarm fixed via `mergeTabFromDb`). Recycle MUST NOT kill a subprocess that
   may have in-flight tool output not yet persisted.
2. **Stop beats self-heal.** A user Stop must never trigger a proactive heal/kill
   loop (existing `_user_stopped_current_turn` invariant).
3. **No new un-tested kill path.** Reuse `_arm_recovery_checkpoint()` + the
   existing `--resume` respawn; do not add a parallel kill mechanism. (Project
   meta-lesson, recurred 5+ times: every recovery path needs a test that enters
   it.)
4. **RAM budget.** Any pre-warmed/extra subprocess must respect
   `ResourceMonitor.compute_max_tabs()` / spawn budget — never exceed it.
