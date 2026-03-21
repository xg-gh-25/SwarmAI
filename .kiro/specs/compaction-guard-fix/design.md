# CompactionGuard Bugfix Design

## Overview

The CompactionGuard in `backend/core/compaction_guard.py` has three interacting bugs that render it ineffective on 1M context windows: a fixed 85% activation threshold wastes ~550K tokens before detection, `reset()` wipes escalation on every user message requiring 1.65M tokens to reach KILL, and there is no progress-based detection for read-only dead loops. This design also covers dead code cleanup (stale test assertions, `_crash_to_cold()` references) and 6 deferred audit bugs (4 frontend, 2 backend) involving tab lifecycle cleanup, SSE connection management, and stop endpoint notification.

The fix strategy is minimal and surgical: add a dynamic threshold formula, remove one line from `reset()`, add a progress tracker to `record_tool_call()` and `check()`, update stale test assertions, remove dead references, and make targeted changes to the frontend hooks and backend SSE loop.

## Glossary

- **Bug_Condition (C)**: The set of conditions that trigger each bug — large context windows (≥200K), user interactions during loops (reset wipes escalation), and read-only tool call sequences (no progress detection)
- **Property (P)**: The desired behavior — dynamic threshold scaling, persistent escalation, progress-based detection at 15/30 consecutive non-productive calls
- **Preservation**: Existing behaviors that must remain unchanged — 200K threshold at 85%, PASSIVE phase returns MONITORING, `reset_all()` fully resets, set-overlap and single-tool detection, work_summary format, exception safety
- **CompactionGuard**: The per-session guard class in `compaction_guard.py` that detects compaction amnesia loops
- **EscalationLevel**: Enum (MONITORING → SOFT_WARN → HARD_WARN → KILL) representing graduated intervention severity
- **GuardPhase**: Enum (PASSIVE → ACTIVE) representing whether the guard is monitoring or intervening
- **Productive tools**: Tools that modify state — Edit, Write, MultiEdit, Bash, NotebookEdit
- **Non-productive tools**: Read-only tools — Read, Grep, Glob, Search, and any tool not in the productive set
- **`_compute_activation_pct()`**: New method that scales the activation threshold with context window size
- **`_consecutive_nonproductive`**: New counter tracking consecutive non-productive tool calls
- **`sse_with_heartbeat()`**: The SSE streaming wrapper in `chat.py` that yields heartbeats between messages
- **`_stop_event`**: New per-session `asyncio.Event` in SessionUnit for signaling SSE consumers

## Bug Details

### Bug Condition

The bugs manifest across three independent conditions that combine to make the guard ineffective on large context windows:

**Bug 1 (Fixed threshold):** On 1M context windows, the guard doesn't activate until 850K tokens (85%), wasting ~550K tokens per cycle.

**Bug 2 (Escalation reset):** Every user interaction (message, answer, permission) resets escalation to MONITORING, requiring 3 full cycles (~1.65M tokens) to reach KILL.

**Bug 3 (No progress detection):** Read-only dead loops (Read, Grep, Glob, Search) are invisible to the guard because detection is gated behind the context threshold.

**Formal Specification:**
```
FUNCTION isBugCondition_threshold(input)
  INPUT: input of type {window_size: int, context_pct: float}
  OUTPUT: boolean

  RETURN input.window_size > 200_000
         AND input.context_pct >= 40.0
         AND input.context_pct < 85.0
         AND guard.phase == ACTIVE
         AND loop_pattern_exists
         AND NOT detection_triggered
END FUNCTION

FUNCTION isBugCondition_reset(input)
  INPUT: input of type {escalation: EscalationLevel, action: str}
  OUTPUT: boolean

  RETURN input.escalation IN [SOFT_WARN, HARD_WARN]
         AND input.action IN ["send", "continue_with_answer", "continue_with_permission"]
         AND escalation_reset_to_MONITORING
END FUNCTION

FUNCTION isBugCondition_progress(input)
  INPUT: input of type {tool_sequence: list[str], consecutive_nonproductive: int}
  OUTPUT: boolean

  RETURN input.consecutive_nonproductive >= 15
         AND all_tools_are_nonproductive(input.tool_sequence[-15:])
         AND NOT escalation_triggered
END FUNCTION
```

### Examples

- **Bug 1**: Agent on Claude Opus 4.6 (1M window) enters a compaction loop at 400K tokens. Guard does nothing until 850K tokens. Expected: guard activates at ~400K (40% of 1M).
- **Bug 2**: Agent reaches SOFT_WARN at 850K tokens. User sends "keep going". `reset()` wipes escalation to MONITORING. Agent must waste another ~550K tokens to re-reach SOFT_WARN. Expected: escalation persists at SOFT_WARN after user message.
- **Bug 3**: Agent makes 30 consecutive Read/Grep calls without any Edit/Write. Guard is silent because context is at 60% (below 85% threshold). Expected: guard escalates to HARD_WARN after 30 non-productive calls regardless of context %.
- **Bug 1+2 combined**: On 1M window, reaching KILL requires 3 × 550K = 1.65M tokens across user interactions. Expected: KILL reachable within first compaction cycle (~400K tokens) with persistent escalation.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- 200K context windows activate loop detection at 85% (170K tokens) — original behavior preserved
- PASSIVE phase always returns MONITORING from `check()` without interference
- `reset_all()` fully resets all state including escalation, phase, context, and tool records
- Set-overlap detection (>60% match with min 5 calls) continues to detect loops
- Single-tool repetition detection (≥5 identical calls) continues to detect loops
- `work_summary()` generates structured summaries with "CRITICAL: Do NOT re-run" instructions
- `build_guard_event()` returns properly formatted SSE event dicts
- Heuristic compaction detection (≥30pt context drop) auto-activates the guard
- `record_tool_call()` never raises exceptions that block streaming
- All guard methods catch internal exceptions and return safe defaults
- `closeTab()` backend cleanup is best-effort — errors never block tab removal
- Tab restore creates a default tab when all saved sessions are expired
- SSE abort on tab switch transitions SessionUnit to IDLE (not DEAD)
- Tauri close handler is additive — existing shutdown path unchanged

**Scope:**
All inputs where the context window is ≤200K tokens should see identical guard behavior. All non-CompactionGuard code paths (work_summary, build_guard_event, activate, _detect_loop, _hash_input) are unchanged. Frontend changes are additive (new calls in existing functions), not replacements.

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are confirmed (not hypothesized — the code is directly readable):

1. **Fixed 85% threshold (Bug 1)**: `_CONTEXT_ACTIVATION_PCT = 85` is a module-level constant used in `check()` as `if self._context_pct < _CONTEXT_ACTIVATION_PCT: return MONITORING`. This was correct for 200K windows (170K tokens before activation) but catastrophic for 1M windows (850K tokens). The threshold needs to scale inversely with window size.

2. **Escalation reset in `reset()` (Bug 2)**: Line `self._escalation = EscalationLevel.MONITORING` in `reset()` is called by `session_unit.send()`, `continue_with_answer()`, and `continue_with_permission()` on every user interaction. This was a design oversight — per-turn tracking (sequence, pattern desc) should reset, but escalation state should persist across turns.

3. **No progress-based detection (Bug 3)**: The only detection mechanisms (`_detect_loop()` with set-overlap and single-tool repetition) are gated behind the 85% context threshold in `check()`. There is no independent check for non-productive tool call patterns. Adding a consecutive non-productive counter to `record_tool_call()` and checking it in `check()` before the context threshold gate provides context-independent detection.

4. **Stale test assertions (Bugs 5-6)**: Template files (MEMORY.md, SOUL.md) were updated but test assertions in `test_context_templates.py` were not updated to match. The `test_memory_agent_managed_marker` checks for `"🧠 MEMORY"` and `test_continuity_section` checks for `"## Continuity"` — these need to match current template content.

5. **Stale `_crash_to_cold()` references (Bug 7)**: The sync `_crash_to_cold()` method was replaced by `_crash_to_cold_async()` but references remain in docstrings (e.g., `_crash_to_cold_async` docstring says "Unlike the deleted sync `_crash_to_cold()`"), test names, and comments in `test_session_unit_properties.py` and `test_process_resource_management.py`.

6. **closeTab missing backend cleanup (Bug 8)**: `closeTab()` in `useUnifiedTabState.ts` aborts the `abortController` but never calls the backend to delete/cleanup the session. The `chatService.deleteSession()` or equivalent endpoint call is missing.

7. **Tab restore without validation (Bug 9)**: `restoreFromFile()` hydrates tabs from `open_tabs.json` without checking if sessionIds still exist in the backend. The `removeInvalidTabs()` method exists but is never called during restore.

8. **SSE linger on tab switch (Bug 10)**: `selectTab()` just updates `activeTabId` — it doesn't abort the previous tab's SSE connection. The previous tab's `abortController` should be aborted.

9. **No app close handler (Bug 11)**: `App.tsx` has no Tauri `close-requested` listener or `beforeunload` handler. The shutdown endpoint exists but is never called on app close.

10. **sessionStorage leak (Bug 12)**: `removePendingState()` exists in `useChatStreamingLifecycle.ts` but is not called on all error/completion paths.

11. **Stop endpoint doesn't notify SSE (Bug 13)**: `interrupt()` in SessionUnit sets `_interrupted = True` and calls `client.interrupt()`, but `sse_with_heartbeat()` in `chat.py` has no way to observe this — it only watches the message queue and heartbeat timeout.

## Correctness Properties

Property 1: Bug Condition — Dynamic Threshold Scaling

_For any_ context window size W where W > 200,000, the computed activation percentage SHALL be strictly less than 85.0, following the linear interpolation formula: `85.0 - (min((W - 200_000) / 800_000, 1.0) * 45.0)`. For W = 1,000,000 the result SHALL be 40.0. For W ≤ 200,000 the result SHALL be exactly 85.0.

**Validates: Requirements 2.1, 2.4**

Property 2: Bug Condition — Escalation Persistence Across reset()

_For any_ CompactionGuard instance with escalation level L where L ∈ {MONITORING, SOFT_WARN, HARD_WARN, KILL}, after calling `reset()`, the escalation level SHALL remain L. Only `reset_all()` SHALL reset escalation to MONITORING.

**Validates: Requirements 2.2, 2.4**

Property 3: Bug Condition — Progress-Based Detection

_For any_ sequence of N consecutive non-productive tool calls (where non-productive means tool_name ∉ {Edit, Write, MultiEdit, Bash, NotebookEdit}) with zero intervening productive calls, the guard SHALL escalate to at least SOFT_WARN when N ≥ 15 and to at least HARD_WARN when N ≥ 30, regardless of context usage percentage and regardless of guard phase.

**Validates: Requirements 2.3, 2.4**

Property 4: Preservation — 200K Window Threshold Unchanged

_For any_ context window size W where W ≤ 200,000, the computed activation percentage SHALL be exactly 85.0, preserving the original guard behavior for smaller context windows.

**Validates: Requirements 3.1**

Property 5: Preservation — PASSIVE Phase Non-Interference

_For any_ CompactionGuard in PASSIVE phase, `check()` SHALL return MONITORING regardless of context_pct value, tool call history, or consecutive non-productive count.

**Validates: Requirements 3.2**

Property 6: Preservation — reset_all() Full Reset

_For any_ CompactionGuard instance in any state (any phase, any escalation, any context_pct, any tool records, any consecutive_nonproductive count), after calling `reset_all()`, all state fields SHALL be at their initial values: phase=PASSIVE, escalation=MONITORING, context_pct=0.0, consecutive_nonproductive=0, has_productive_call=False, empty sets/lists.

**Validates: Requirements 3.3**

Property 7: Preservation — record_tool_call Exception Safety

_For any_ input to `record_tool_call()` (including None, empty strings, malformed dicts, extremely large inputs), the method SHALL never raise an exception. It SHALL either succeed silently or log the error and return without side effects.

**Validates: Requirements 3.9, 3.10**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/compaction_guard.py`

**Group A — All 3 CompactionGuard fixes:**

1. **Add `_compute_activation_pct()` method**: New instance method that takes a window size and returns the activation percentage using linear interpolation. For window ≤ 200K → 85%. For window > 200K → linearly interpolate from 85% down to 40% at 1M. Clamp at 40% for windows > 1M.

   ```python
   def _compute_activation_pct(self, window: int) -> float:
       if window <= 200_000:
           return 85.0
       ratio = min((window - 200_000) / 800_000, 1.0)
       return 85.0 - (ratio * 45.0)
   ```

2. **Store context window in `update_context_usage()`**: Save the resolved window size as `self._context_window` so `check()` can compute the dynamic threshold.

3. **Update `check()` to use dynamic threshold**: Replace `if self._context_pct < _CONTEXT_ACTIVATION_PCT` with `if self._context_pct < self._compute_activation_pct(self._context_window)`.

4. **Remove escalation reset from `reset()`**: Delete the line `self._escalation = EscalationLevel.MONITORING` from `reset()`. Keep it in `reset_all()`.

5. **Add progress tracking fields to `__init__()`**:
   ```python
   self._consecutive_nonproductive: int = 0
   self._has_productive_call: bool = False
   self._context_window: int = 200_000
   ```

6. **Update `record_tool_call()` to track progress**:
   ```python
   PRODUCTIVE_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "NotebookEdit"}
   if tool_name in PRODUCTIVE_TOOLS:
       self._consecutive_nonproductive = 0
       self._has_productive_call = True
   else:
       self._consecutive_nonproductive += 1
   ```

7. **Add progress-based detection to `check()` BEFORE the context threshold gate**: If `_consecutive_nonproductive >= 30` → escalate to at least HARD_WARN. If `>= 15` → escalate to at least SOFT_WARN. This fires in ACTIVE phase regardless of context_pct.

8. **Update `reset_all()` to clear new fields**: Add `self._consecutive_nonproductive = 0`, `self._has_productive_call = False`, `self._context_window = 200_000`.

---

**File**: `backend/tests/test_context_templates.py`

**Group B — Stale test assertions (Bugs 5-6):**

9. **Update `test_memory_agent_managed_marker`**: Read current MEMORY.md template, update the assertion marker string to match.

10. **Update `test_continuity_section`**: Read current SOUL.md template, update the assertion section heading and content string to match.

---

**File**: `backend/core/session_unit.py`

**Group B — Stale `_crash_to_cold()` references (Bug 7):**

11. **Update `_crash_to_cold_async()` docstring**: Remove the "Unlike the deleted sync `_crash_to_cold()`" reference. Describe the method on its own terms.

12. **Update `force_unstick_streaming()` docstring**: Remove the "Now async — uses `_crash_to_cold_async()`" reference to the old sync method.

**Group C — Stop event for SSE notification (Bug 13):**

13. **Add `_stop_event: asyncio.Event` to SessionUnit.__init__()**: Per-session event that signals SSE consumers to break.

14. **Set `_stop_event` in `interrupt()`**: Call `self._stop_event.set()` at the start of `interrupt()`.

15. **Expose `stop_event` property**: So `chat.py` can pass it to `sse_with_heartbeat()`.

---

**File**: `backend/routers/chat.py`

**Group C — SSE stop notification (Bug 13):**

16. **Update `sse_with_heartbeat()` signature**: Accept an optional `stop_event: asyncio.Event` parameter.

17. **Check `stop_event` in the heartbeat loop**: Use `asyncio.wait()` to race the message queue get against the stop event, breaking the loop when the event is set.

---

**File**: `desktop/src/hooks/useUnifiedTabState.ts`

**Group C — Frontend bug fixes (Bugs 8, 9, 10):**

18. **Bug 8 — closeTab backend cleanup**: After aborting the controller, add a best-effort `chatService.deleteSession(tab.sessionId)` call (fire-and-forget, catch errors silently).

19. **Bug 9 — Tab restore validation**: After `restoreFromFile()` hydrates tabs, validate sessionIds via the backend API. Call `removeInvalidTabs()` with the set of valid session IDs.

20. **Bug 10 — SSE abort on tab switch**: In `selectTab()`, before switching, abort the previous tab's `abortController` if it exists and the tab is streaming.

---

**File**: `desktop/src/App.tsx`

**Group C — App close handler (Bug 11):**

21. **Add Tauri `close-requested` event listener**: In a `useEffect`, listen for the Tauri close event, call `fetch('/shutdown', { method: 'POST' })`, then allow the window to close.

22. **Add `beforeunload` fallback**: For web/dev mode, add `window.addEventListener('beforeunload', ...)` that sends a beacon to `/shutdown`.

---

**File**: `desktop/src/hooks/useChatStreamingLifecycle.ts`

**Group C — sessionStorage cleanup (Bug 12):**

23. **Add `removePendingState()` calls**: Ensure `removePendingState(sessionId)` is called in both the success completion handler and the error handler of the SSE stream processing.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm the root cause analysis.

**Test Plan**: Write property-based tests using Hypothesis that exercise the CompactionGuard with large context windows, user interaction resets, and non-productive tool sequences. Run on UNFIXED code to observe failures.

**Test Cases**:
1. **Threshold Test**: Generate random window sizes > 200K, set context_pct to the expected dynamic threshold. Verify `check()` returns MONITORING on unfixed code (will fail — unfixed code uses 85% for all windows).
2. **Escalation Reset Test**: Set escalation to SOFT_WARN, call `reset()`, verify escalation is still SOFT_WARN (will fail — unfixed code resets to MONITORING).
3. **Progress Detection Test**: Record 15+ consecutive non-productive tool calls at 60% context, call `check()`. Verify escalation occurs (will fail — unfixed code gates behind 85%).
4. **Combined Test**: Simulate a full loop cycle on 1M window with user interaction mid-loop (will fail — unfixed code requires 1.65M tokens).

**Expected Counterexamples**:
- Window=1M, context_pct=50%: `check()` returns MONITORING instead of detecting the loop
- Escalation=SOFT_WARN after `reset()`: escalation is MONITORING instead of SOFT_WARN
- 20 consecutive Read calls at 60% context: no escalation triggered

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL window IN [200_001 .. 2_000_000] DO
  pct := _compute_activation_pct(window)
  ASSERT pct < 85.0
  ASSERT pct >= 40.0
  IF window == 1_000_000 THEN ASSERT pct == 40.0
END FOR

FOR ALL escalation IN [MONITORING, SOFT_WARN, HARD_WARN, KILL] DO
  guard._escalation := escalation
  guard.reset()
  ASSERT guard._escalation == escalation
END FOR

FOR ALL n IN [15 .. 100] DO
  guard := fresh CompactionGuard (ACTIVE phase)
  FOR i IN 1..n DO guard.record_tool_call("Read", {}) END
  level := guard.check()
  IF n >= 30 THEN ASSERT level >= HARD_WARN
  ELIF n >= 15 THEN ASSERT level >= SOFT_WARN
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL window IN [1 .. 200_000] DO
  ASSERT _compute_activation_pct(window) == 85.0
END FOR

FOR ALL guard IN PASSIVE phase DO
  ASSERT guard.check() == MONITORING
END FOR

FOR ALL guard after reset_all() DO
  ASSERT guard.phase == PASSIVE
  ASSERT guard.escalation == MONITORING
  ASSERT guard._consecutive_nonproductive == 0
END FOR
```

**Testing Approach**: Property-based testing with Hypothesis is recommended for the CompactionGuard fixes because:
- The threshold formula has a continuous input domain (window sizes from 1 to 2M+)
- The escalation persistence property must hold for all escalation states
- The progress tracker must work correctly for arbitrary tool call sequences
- Hypothesis can find edge cases at boundary values (200K, 1M) automatically

**Test Plan**: Write Hypothesis strategies for window sizes, escalation levels, and tool call sequences. Verify preservation properties hold for all generated inputs.

**Test Cases**:
1. **200K Threshold Preservation**: Generate window sizes ≤ 200K, verify threshold is exactly 85.0
2. **PASSIVE Phase Preservation**: Generate arbitrary context_pct and tool sequences, verify PASSIVE always returns MONITORING
3. **reset_all() Preservation**: Generate guards in arbitrary states, verify reset_all() returns to initial state
4. **Exception Safety Preservation**: Generate malformed inputs to record_tool_call(), verify no exceptions raised

### Unit Tests

- Test `_compute_activation_pct()` at boundary values: 0, 100K, 200K, 200_001, 500K, 1M, 2M
- Test `reset()` preserves each escalation level individually
- Test progress tracker: 14 non-productive → no escalation, 15 → SOFT_WARN, 29 → SOFT_WARN, 30 → HARD_WARN
- Test productive tool call resets consecutive counter
- Test stale test assertions pass after update (bugs 5-6)
- Test `_crash_to_cold()` references are removed (bug 7)
- Test closeTab calls backend cleanup (bug 8)
- Test tab restore validates sessionIds (bug 9)
- Test selectTab aborts previous SSE (bug 10)
- Test app close handler calls shutdown (bug 11)
- Test sessionStorage cleanup on stream completion/error (bug 12)
- Test stop event breaks SSE loop (bug 13)

### Property-Based Tests

- Generate random window sizes (1 to 5M) and verify threshold formula properties (monotonically decreasing, bounded [40, 85], exact at boundaries)
- Generate random escalation levels and verify reset() preserves them while reset_all() clears them
- Generate random tool call sequences (mix of productive/non-productive) and verify progress tracker counts correctly
- Generate random guard states and verify PASSIVE phase always returns MONITORING
- Generate malformed tool inputs and verify record_tool_call() never raises

### Integration Tests

- Full CompactionGuard lifecycle: PASSIVE → activate → record tools → check escalation → reset → verify persistence
- SSE stop event: start streaming, call stop endpoint, verify SSE loop breaks within 1 second
- Tab close → backend cleanup → verify SessionUnit is cleaned up
- App close → shutdown endpoint → verify graceful cleanup
