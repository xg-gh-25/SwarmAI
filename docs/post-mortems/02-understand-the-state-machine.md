# 3 Consecutive "I'm Sure This Works" → 3 Consecutive Hangs

> Daemon self-upgrade hung 3 rounds. Each round declared "确定能 work."
> Only the 4th round got it right — after being forced to draw the state machine first.

## The Setup

Task: implement a `/api/system/upgrade` endpoint that replaces the running daemon binary in-place and restarts with the new version.

Sounds simple: stop process → replace binary → restart process.

It is not simple.

## Three Rounds of Confident Failure

### Round 1: SIGTERM

**Reasoning:** "Gracefully stop the daemon, then replace the binary."

**What happened:** SSE connections hold the process alive indefinitely. SIGTERM asks nicely; active connections say "no." Process hangs forever.

**"Fix":** Use a harder kill signal.

---

### Round 2: SIGKILL

**Reasoning:** "Force-kill bypasses SSE. Process dies instantly."

**What happened:** Process dies. launchd's `KeepAlive` immediately restarts the OLD binary (before rsync finishes). Now rsync is racing a live process → I/O corruption.

**"Fix":** Deregister the service so KeepAlive can't fire.

---

### Round 3: bootout

**Reasoning:** "`launchctl bootout` deregisters the service. No more KeepAlive."

**What happened:** `bootout` sends SIGTERM internally (same SSE hang as Round 1), waits `ExitTimeOut` (20 seconds). Our subprocess timeout is shorter → our process dies first → daemon in undefined state.

**"Fix":** None. At this point, a PE review forced the correct approach.

---

### Round 4: Draw the State Machine First

```
launchd state machine:

kill SIGTERM → process dies (eventually), service STAYS registered, KeepAlive restarts
kill SIGKILL → process dies (instantly), service STAYS registered, KeepAlive restarts  
bootout      → sends SIGTERM, waits ExitTimeOut, force-kills, DEREGISTERS service
bootstrap    → registers service, starts process
```

The correct sequence:
1. **SIGKILL** — instant death, no SSE negotiation
2. **bootout** — instant (process already dead, just deregisters; no SIGTERM wait)
3. **rsync** — safe (nothing running, nothing will restart)
4. **bootstrap** — registers with new binary, starts fresh

Each step depends on the previous step's state. You can't design this without knowing ALL states and transitions.

## Why This Keeps Happening

**The pattern: incremental fix-without-understanding.**

Each round was *locally reasonable*:
- "It hangs → kill harder" (reasonable)
- "It restarts → deregister it" (reasonable)  
- "It times out → fix the timeout" (reasonable)

But each "reasonable" fix introduced a new interaction with a state transition the developer hadn't considered. The state machine has ~12 meaningful states (running/stopping/dead × registered/deregistered × keepalive-pending/not). Each round modeled 3-4 states and got surprised by the 5th.

**Confidence was inversely correlated with correctness.** Each round was declared "确定能 work" before testing. Three confident assertions, three failures. Confidence ≠ understanding.

## Structural Prevention

The fix is not "be smarter." It's a process gate:

| Trigger | Action |
|---------|--------|
| Writing process/system management code | FIRST draw all states, all transitions, all timing (blocking vs instant vs timeout) |
| Same operation failed 2x | STOP coding. You don't understand the system. Draw the diagram. |
| High confidence without E2E test | Run it. "确定能 work" without evidence = worthless. |

## What This Means For Your Code

If you're writing code that manages processes (daemons, services, containers, orchestrators):

1. **Don't fix symptoms sequentially.** Three "reasonable" fixes that each introduce new state interactions = three failures.
2. **Enumerate every API's blocking behavior BEFORE designing.** Takes 15 minutes. Prevents 3 rounds of debugging.
3. **Draw the state machine.** Not in your head — on paper/screen. Include: what blocks, what's instant, what has timeouts, what fires callbacks.
4. **The 2-strike rule.** If the same category of fix has failed twice, your mental model is wrong. Stop adding code. Read documentation. Trace actual behavior.

---

*This correction (C023) also applies to Kubernetes pod lifecycle, Docker container management, systemd services, and any orchestration code. The root cause is always the same: fixing one symptom without understanding how the fix interacts with the rest of the state machine.*

**Evidence:** Final implementation in `backend/routers/system.py`. launchd semantics in `desktop/src-tauri/src/daemon_lib.rs`. 4 rounds visible in commit history.
