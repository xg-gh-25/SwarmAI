# C023: Understand the State Machine

> Daemon upgrade hung 3 consecutive rounds. Each round declared
> "确定能 work" then failed. Only the 4th round (forced by PE review)
> got it right.

## What Happened

Task: implement a `/api/system/upgrade` endpoint that upgrades the running daemon binary in-place.

**Round 1 (SIGTERM):** Sent SIGTERM to gracefully stop the daemon before replacing the binary. Failed: SSE connections block graceful shutdown → process hangs forever waiting for streams to close.

**Round 2 (SIGKILL):** Sent SIGKILL for instant death. Failed: launchd's `KeepAlive` immediately restarts the OLD binary. Now rsync is racing a live process → I/O corruption.

**Round 3 (bootout):** Used `launchctl bootout` to deregister the service. Failed: `bootout` sends SIGTERM internally, waits `ExitTimeOut` (20 seconds). Our `subprocess.run` has a shorter timeout → our process dies first → daemon in undefined state.

**Round 4 (PE review forced the correct solution):** SIGKILL (instant death, no waiting) → bootout (deregister — instant because process is already dead) → rsync (safe, nothing running) → bootstrap (register fresh with new binary).

Each of rounds 1-3 was declared "确定能 work" before testing.

## Why It Happened

**Incremental fix-without-understanding.** Each round addressed only the symptom from the previous failure, never building a complete mental model of launchd's state machine.

The launchd state machine has:
- `kill SIGTERM` = process dies, service stays registered, KeepAlive restarts
- `kill SIGKILL` = process dies immediately, service stays registered, KeepAlive restarts
- `bootout` = sends SIGTERM, waits ExitTimeOut, then force-kills, deregisters service
- `bootstrap` = registers service, starts process

**Round 1** didn't understand: SIGTERM + SSE = indefinite hang.
**Round 2** didn't understand: KeepAlive fires between kill and rsync.
**Round 3** didn't understand: bootout's internal SIGTERM has the same SSE problem as Round 1.

The correct solution requires understanding ALL states and transitions BEFORE designing the fix. The correct sequence (SIGKILL → bootout → rsync → bootstrap) works because:
- SIGKILL bypasses SSE hang (instant)
- bootout after kill is instant (process already dead, just deregisters)
- rsync is safe (nothing running, nothing will restart)
- bootstrap explicitly restarts with new binary

## Structural Prevention

**State machine rule** (added to EVOLUTION.md + STEERING.md):

When fixing process/system management code, FIRST draw the state machine:
- All states
- All transitions between states
- All timing (what blocks, what's instant, what has timeouts)

Only then design the fix.

**Escalation trigger:** If the same operation has failed 2x → you don't understand the system. Stop coding. Draw the diagram. Enumerate every API's blocking behavior, side effects, and interaction with other APIs.

**Confidence calibration:** "确定能 work" without E2E verification = worthless. The more confident you feel, the more you need to verify. Three consecutive confident-but-wrong rounds prove that confidence and correctness are uncorrelated.

## The Generalizable Insight

**Process management code fails because developers fix symptoms sequentially instead of understanding the system holistically.**

Each round's fix was locally reasonable:
- "It hangs → use a harder kill" (reasonable)
- "It restarts → deregister it" (reasonable)
- "It times out → our timeout is wrong" (reasonable)

But each "reasonable" fix introduced a new interaction with a state transition the developer hadn't considered. The state machine has ~12 meaningful states (running/stopping/dead × registered/deregistered × keepalive-pending/not) and most developers only model 3-4.

**The fix is not "be smarter."** It's a process change: before writing ANY process management code, enumerate every API you'll call and document its blocking behavior, side effects, and interactions. This takes 15 minutes and prevents 3 rounds of debugging.

## Code References

- Final implementation: `/api/system/upgrade` endpoint in `backend/routers/system.py`
- launchd semantics: `desktop/src-tauri/src/daemon_lib.rs`
- STEERING.md: search "Daemon Lifecycle: Kill ≠ Bootout"
- 4 rounds of commits visible in PR history
