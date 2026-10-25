---
expert: state-machine
display_name_zh: 状态机评审官
display_name_en: State-Machine Auditor
domain: code
scope: "状态枚举、生命周期、状态转移完整性"
visibility: public
---

> **Pipeline v1: not dispatched.** The review roster is correctness and security (expert simplification E1; §3.5.1 merge); this checklist is merged into correctness. The file stays for the Experts library.

# State Machine Specialist Review
<!-- version: 2026-10-18 | origin: Pipeline Meta-Intelligence (GAP 8); orphan-adoption + live/reload parity added from the compaction-summary review -->

**Also check:** state machine completeness, live vs reload parity,
stateful helper never re-baselined.

Scope: Dispatched when changeset touches state enums, status fields, lifecycle
methods, state transition logic, or any code with explicit states/phases.

**Purpose:** Find bugs that exist in STATE TRANSITIONS — correctness of individual
states is caught by the correctness specialist; this specialist catches incomplete
or impossible transitions, unreachable states, and stuck states.

Proven pattern: state-machine completeness violations (unreachable states, untriggered transitions),
a stale subprocess, streaming loss on tab switch, spinner-hang
(3 occurrences of same stuck-state bug).

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"state-machine","summary":"...","fix":"...","fingerprint":"path:line:state-machine","specialist":"state-machine"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence, mitigation.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Identified specific state with no exit path, or transition with no trigger
- 7-8: Pattern matches known state machine failure class (from past incidents)
- 5-6: Plausible transition issue, needs runtime verification
- 3-4: Theoretical concern about state coverage
- 1-2: Speculation

---

## Checklist

### 1. State Completeness

**Does every declared state have:**
- At least ONE code path that ENTERS it? (state exists → something must set it)
- At least ONE code path that EXITS it? (no terminal states without explicit design intent)
- If a state is terminal by design (e.g., DEAD, COMPLETED): is there a recovery path
  for cases where it's reached erroneously? (timeout, admin reset, garbage collection)

**Red flags:**
- State declared in enum/type but no code ever assigns it
- State assigned but no code ever checks for it (orphan state)
- State has entry but no exit (stuck state — anything that enters never leaves)

### 2. Transition Triggers

**For every state transition (A → B):**
- Is the trigger clearly defined? (event, timeout, condition)
- Can the trigger fire when already in state B? (no-op or error?)
- Can the trigger fire from an UNRELATED state? (invalid transition → should it be blocked?)

**Red flags:**
- Timer-based transition with no cancellation on state change (timer fires after state already moved)
- Event-based transition where the event source can die (who transitions out if the trigger never comes?)
- Multiple triggers for the same transition (race condition — which wins?)

### 3. Non-Terminal State Recovery

**Every non-terminal state that depends on EXTERNAL input must have a force-recovery path.**

External input includes: user action, API response, subprocess exit, network event, timer callback.

**Ask:** "If the external input NEVER arrives, what happens?"
- Good: timeout → fallback state → recovery path
- Bad: state waits forever → system hangs

**Known patterns from SwarmAI:**
- STREAMING state → process dies → no event → stuck at STREAMING forever
  Fix: L2 poll (3s interval) checks backend state → forces IDLE
- WAITING_INPUT → user closes tab → no input event → stuck
  Fix: tab close handler explicitly transitions to DEAD

**Orphan ADOPTED by the next cycle.** Ask the follow-up question too: if the
completing event never arrives, does the NEXT cycle (turn, request, job run) pick
the stale item up as its own? A lookup like "the last item in state running" that
is not scoped to the current cycle finds the one an interrupted earlier cycle left
behind, then updates it in place — the new cycle's result lands at the old
position and the old one is overwritten. Verify every by-status lookup is bounded
to the current cycle (after the last user message, this request's id, this job's
run id), and that an abandoned item shows a terminal display ("did not finish"),
not its live one, once a newer cycle runs.
- Live "compacting" divider left `running` by a Stop; the next compaction's
  "started" found it, appended nothing, and its "done" replaced the OLD divider far
  up the history. Fix: search only after the last user message.

### 4. Parallel State Mutation

**If the state can be written by multiple paths (event handler + timer + error handler):**
- Is there a single authoritative writer? (single writer principle)
- If multiple writers: is there a priority/override order?
- Can two writers race? (both see state=A, both try A→B, second one operates on stale read)

**Red flags:**
- `state = newValue` in multiple event handlers without coordination
- State read + check + write is not atomic (TOCTOU)
- useRef state (React) written by both effect cleanup AND event handler

### 5. Persistence Across Boundaries

**If state must survive a process restart, session resume, or tab switch:**
- Is the state persisted before the boundary? (not just in-memory)
- On resume: is the persisted state validated? (could be stale/invalid after crash)
- On resume: is there a reconciliation step? (backend may have moved on while frontend was dead)

**Red flags:**
- State stored in-memory only (lost on crash/restart)
- State stored in localStorage without TTL (stale state from days ago applied on next visit)
- State stored in DB but not validated against current system state on read
- **Live vs reload disagree.** The same item reaches the view
  live (stream / optimistic insert) and on reload (DB / API). Compare the two routes:
  same id (or the merge keeps both), same order key in the same FORMAT (UTC `…Z` vs a
  naive-local DB string sorts hours apart), same position, same fields. Then ask where a
  live item with NO stored row lands after reconcile — can it become a cursor or anchor?

### 6. State Machine Diagram Verification (for complex changes)

**If the changeset introduces 3+ states or modifies 3+ transitions:**
- Draw the complete state machine (all states + all transitions + triggers)
- For each state: verify entry/exit paths exist in code
- For each transition: verify trigger exists and is tested
- Identify any "impossible" transitions that could become possible after this change

---

## Examples of HIGH findings from SwarmAI history

```json
{"severity":"HIGH","confidence":9,"path":"hooks/useChatStreamingLifecycle.ts","line":142,"category":"state-machine","summary":"isStreaming stuck: disconnect timeout handler mutates ref directly (tabState.isStreaming = false) but never triggers React re-render. State CHANGE without state PROPAGATION.","fix":"Use setIsStreaming(false) which triggers setPendingStreamTabs → re-render","specialist":"state-machine"}
```

```json
{"severity":"HIGH","confidence":9,"path":"core/session_unit.py","line":320,"category":"state-machine","summary":"STREAMING state has no recovery path if subprocess crashes (SIGKILL). Process dies → no exit event → state stuck at STREAMING forever.","fix":"Add TTL-based state recovery: if STREAMING for >60s with no stdout activity, force-transition to DEAD","specialist":"state-machine"}
```

```json
{"severity":"MED","confidence":7,"path":"components/chat/MessageBubbles.tsx","line":89,"category":"state-machine","summary":"Voice 'interrupted' state declared in enum but no code path ever sets it. Orphan state suggests incomplete implementation.","fix":"Either implement the interrupt trigger (user speaks while AI is responding) or remove the state from the enum","specialist":"state-machine"}
```
