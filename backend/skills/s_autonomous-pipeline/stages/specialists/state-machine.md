# State Machine Specialist Review
<!-- version: 2026-06-08 | origin: Pipeline Meta-Intelligence (GAP 8) -->

Scope: Dispatched when changeset touches state enums, status fields, lifecycle
methods, state transition logic, or any code with explicit states/phases.

**Purpose:** Find bugs that exist in STATE TRANSITIONS — correctness of individual
states is caught by the correctness specialist; this specialist catches incomplete
or impossible transitions, unreachable states, and stuck states.

Proven pattern: RP13 violations (unreachable states, untriggered transitions),
COE06 (stale subprocess), COE07 (streaming loss on tab switch), spinner-hang
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
- 7-8: Pattern matches known state machine failure class (from RP13, COEs)
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

### 3. Non-Terminal State Recovery (RP13 extension)

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
