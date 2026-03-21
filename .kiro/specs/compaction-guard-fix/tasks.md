# Implementation Plan

- [x] 1. Write bug condition exploration tests (Group A — CompactionGuard)
  - **Property 1: Bug Condition** — CompactionGuard Three-Bug Exploration
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate all three Group A bugs
  - **Scoped PBT Approach**: Use Hypothesis to generate window sizes, escalation levels, and tool sequences
  - Sub-property 1a (Dynamic Threshold): For window sizes W > 200K, generate W ∈ [200_001, 2_000_000]. Compute expected threshold via `85.0 - (min((W - 200_000) / 800_000, 1.0) * 45.0)`. Assert `_compute_activation_pct(W)` returns this value. On unfixed code: method doesn't exist → FAIL
  - Sub-property 1b (Escalation Persistence): For each L ∈ {MONITORING, SOFT_WARN, HARD_WARN, KILL}, set `guard._escalation = L`, call `reset()`, assert `guard.escalation == L`. On unfixed code: `reset()` wipes to MONITORING → FAIL for SOFT_WARN, HARD_WARN, KILL
  - Sub-property 1c (Progress Detection): Generate N ∈ [15, 100] consecutive non-productive tool calls (Read, Grep, Glob, Search). Set guard to ACTIVE phase. Call `check()`. Assert escalation ≥ SOFT_WARN when N ≥ 15, ≥ HARD_WARN when N ≥ 30. On unfixed code: no progress tracker exists → FAIL
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: All three sub-properties FAIL (this is correct — proves the bugs exist)
  - Document counterexamples found (e.g., "window=1M → no `_compute_activation_pct` method", "SOFT_WARN reset to MONITORING", "20 Read calls → no escalation")
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — CompactionGuard Existing Behavior
  - **IMPORTANT**: Follow observation-first methodology — run UNFIXED code, observe outputs, encode as properties
  - Sub-property 2a (200K Threshold Unchanged): For window sizes W ∈ [1, 200_000], observe that the guard uses 85% threshold. Write property: `_CONTEXT_ACTIVATION_PCT == 85` for all W ≤ 200K. Verify on unfixed code → PASS
  - Sub-property 2b (PASSIVE Phase Non-Interference): Create guard in PASSIVE phase, set arbitrary context_pct ∈ [0, 100], record arbitrary tool calls. Assert `check()` returns MONITORING. Verify on unfixed code → PASS
  - Sub-property 2c (reset_all Full Reset): Create guard in arbitrary state (any phase, escalation, context_pct, tool records). Call `reset_all()`. Assert phase=PASSIVE, escalation=MONITORING, context_pct=0.0, empty sets/lists. Verify on unfixed code → PASS
  - Sub-property 2d (Exception Safety): Generate malformed inputs to `record_tool_call()` (None, empty strings, huge dicts, nested objects). Assert no exception raised. Verify on unfixed code → PASS
  - Sub-property 2e (Set-Overlap Detection): Create ACTIVE guard with baseline, record >60% overlapping post-compaction calls (min 5), set context_pct ≥ 85. Assert `check()` escalates. Verify on unfixed code → PASS
  - Sub-property 2f (Single-Tool Repetition): Create ACTIVE guard, record same (tool, input) pair 5+ times, set context_pct ≥ 85. Assert `check()` escalates. Verify on unfixed code → PASS
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: All sub-properties PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.9, 3.10_

- [ ] 3. Group A — CompactionGuard fixes (compaction_guard.py)

  - [x] 3.1 Add dynamic activation threshold
    - Add `_context_window: int = 200_000` field to `__init__()`
    - Add `_compute_activation_pct(self, window: int) -> float` method: `if window <= 200_000: return 85.0` else `85.0 - (min((window - 200_000) / 800_000, 1.0) * 45.0)`
    - Store resolved window size as `self._context_window` in `update_context_usage()`
    - Replace `if self._context_pct < _CONTEXT_ACTIVATION_PCT` in `check()` with `if self._context_pct < self._compute_activation_pct(self._context_window)`
    - _Bug_Condition: isBugCondition_threshold — window > 200K AND context_pct ∈ [40%, 85%) AND detection not triggered_
    - _Expected_Behavior: _compute_activation_pct(1M) == 40.0, scales linearly from 85% at 200K to 40% at 1M_
    - _Preservation: _compute_activation_pct(W) == 85.0 for all W ≤ 200K_
    - _Requirements: 1.1, 2.1, 2.4, 3.1_

  - [x] 3.2 Remove escalation reset from reset()
    - Delete the line `self._escalation = EscalationLevel.MONITORING` from `reset()`
    - Keep `self._escalation = EscalationLevel.MONITORING` in `reset_all()` (unchanged)
    - _Bug_Condition: isBugCondition_reset — escalation ∈ {SOFT_WARN, HARD_WARN} AND action ∈ {send, answer, permission}_
    - _Expected_Behavior: reset() preserves escalation level L for all L ∈ {MONITORING, SOFT_WARN, HARD_WARN, KILL}_
    - _Preservation: reset_all() still resets escalation to MONITORING_
    - _Requirements: 1.2, 2.2, 2.4, 3.3_

  - [x] 3.3 Add progress-based detection
    - Add `_consecutive_nonproductive: int = 0` and `_has_productive_call: bool = False` to `__init__()`
    - Define `PRODUCTIVE_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "NotebookEdit"}` as module constant
    - In `record_tool_call()`: if tool_name in PRODUCTIVE_TOOLS → reset counter to 0, set _has_productive_call=True; else → increment counter
    - In `check()` BEFORE the context threshold gate (but after PASSIVE and KILL checks): if `_consecutive_nonproductive >= 30` → escalate to at least HARD_WARN; elif `>= 15` → escalate to at least SOFT_WARN
    - Update `reset_all()` to clear: `_consecutive_nonproductive = 0`, `_has_productive_call = False`, `_context_window = 200_000`
    - _Bug_Condition: isBugCondition_progress — consecutive_nonproductive ≥ 15 AND all tools non-productive AND no escalation triggered_
    - _Expected_Behavior: 15 non-productive → SOFT_WARN, 30 → HARD_WARN, regardless of context_pct or phase_
    - _Preservation: Productive tool call resets counter; reset_all() clears all new fields_
    - _Requirements: 1.3, 2.3, 2.4, 3.3_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — CompactionGuard Three-Bug Fix Validation
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior for all three bugs
    - When this test passes, it confirms: dynamic threshold works, escalation persists, progress detection fires
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms all three bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** — CompactionGuard Existing Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — 200K threshold, PASSIVE phase, reset_all, exception safety, set-overlap, single-tool repetition all unchanged)
    - Confirm all preservation tests still pass after Group A fixes

- [ ] 4. Group B — Dead code and test cleanup

  - [x] 4.1 Fix test_memory_agent_managed_marker assertion
    - Read current `backend/context/MEMORY.md` template to find the actual marker string
    - Update assertion in `backend/tests/test_context_templates.py::test_memory_agent_managed_marker` to match current template
    - Run the test to confirm it passes
    - _Requirements: 1.5, 2.5_

  - [x] 4.2 Fix test_continuity_section assertion
    - Read current `backend/context/SOUL.md` template to find the actual section heading
    - Update assertion in `backend/tests/test_context_templates.py::TestSoulTemplate::test_continuity_section` to match current template
    - Run the test to confirm it passes
    - _Requirements: 1.6, 2.6_

  - [x] 4.3 Clean _crash_to_cold() stale docstring references
    - Update `_crash_to_cold_async()` docstring in `backend/core/session_unit.py` — remove "Unlike the deleted sync `_crash_to_cold()`" reference
    - Update `force_unstick_streaming()` docstring in `backend/core/session_unit.py` — remove "Now async" reference to old sync method
    - Search for any remaining `_crash_to_cold` references in test files (`test_session_unit_properties.py`, `test_process_resource_management.py`) and update/remove
    - _Requirements: 1.7, 2.7_

- [ ] 5. Group C backend — Stop endpoint SSE notification

  - [x] 5.1 Add _stop_event to SessionUnit
    - Add `self._stop_event = asyncio.Event()` to `SessionUnit.__init__()`
    - Add `@property stop_event` to expose it for `chat.py`
    - Set `self._stop_event.set()` at the start of `interrupt()` method
    - Reset `self._stop_event = asyncio.Event()` in appropriate reset paths (new session start)
    - _Bug_Condition: stop endpoint called → SSE consumer not notified → 15s delay_
    - _Expected_Behavior: stop_event.set() breaks SSE loop within 1 second_
    - _Preservation: Existing interrupt() behavior (state transition, client.interrupt()) unchanged_
    - _Requirements: 1.13, 2.13, 3.14_

  - [x] 5.2 Update sse_with_heartbeat() to check stop_event
    - Add optional `stop_event: asyncio.Event` parameter to `sse_with_heartbeat()` in `backend/routers/chat.py`
    - Use `asyncio.wait()` to race the message queue `get()` against `stop_event.wait()`, breaking the loop when the event is set
    - Pass `unit.stop_event` from the streaming endpoint caller
    - _Requirements: 1.13, 2.13_

- [ ] 6. Group C frontend — Tab lifecycle and cleanup fixes

  - [ ] 6.1 closeTab backend cleanup (Bug 1.8)
    - In `closeTab()` in `desktop/src/hooks/useUnifiedTabState.ts`, after aborting the controller:
    - Add best-effort `chatService.deleteSession(tab.sessionId)` call (fire-and-forget)
    - Wrap in try/catch — errors never block tab removal
    - _Bug_Condition: closeTab on IDLE tab → no backend cleanup → SessionUnit lives until 12hr TTL_
    - _Expected_Behavior: Backend notified of tab closure for prompt cleanup_
    - _Preservation: Backend unreachable → error silently caught, local tab removal proceeds (3.11)_
    - _Requirements: 1.8, 2.8, 3.11_

  - [ ] 6.2 Tab restore sessionId validation (Bug 1.9)
    - After `restoreFromFile()` hydrates tabs in `useUnifiedTabState.ts`:
    - Validate each sessionId against the backend API (e.g., `GET /api/chat/sessions/{id}`)
    - Remove tabs whose sessions no longer exist via `removeInvalidTabs()`
    - If ALL sessions are expired, create a fresh default tab
    - _Bug_Condition: App restores tabs from open_tabs.json → sessionIds may reference deleted/expired sessions_
    - _Expected_Behavior: Ghost tabs removed, valid tabs preserved, empty state → fresh default tab_
    - _Preservation: All sessions expired → fresh default tab created, not empty tab bar (3.12)_
    - _Requirements: 1.9, 2.9, 3.12_

  - [ ] 6.3 SSE abort on tab switch (Bug 1.10)
    - In `selectTab()` in `useUnifiedTabState.ts`, before switching active tab:
    - Check if previous tab has an active `abortController` and is streaming
    - If so, abort the previous tab's `abortController` to free the backend SSE slot
    - _Bug_Condition: Tab switch while SSE active → connection lingers 45s → wastes backend slot_
    - _Expected_Behavior: SSE fetch aborted within 2s, backend transitions STREAMING → IDLE_
    - _Preservation: SessionUnit transitions to IDLE (not DEAD) — conversation resumable via cold-start (3.13)_
    - _Requirements: 1.10, 2.10, 3.13_

  - [ ] 6.4 App close handler (Bug 1.11)
    - In `desktop/src/App.tsx`, add a `useEffect` with:
    - Tauri `close-requested` event listener → call `fetch('/shutdown', { method: 'POST' })` → allow window close
    - `beforeunload` fallback for web/dev mode → `navigator.sendBeacon('/shutdown')`
    - _Bug_Condition: App close → no shutdown call → orphaned subprocesses until next startup cleanup_
    - _Expected_Behavior: /shutdown endpoint called, graceful cleanup of sessions/subprocesses/MCP servers_
    - _Preservation: Existing disconnect_all() → kill() → hook firing sequence unchanged — close handler is additive (3.14)_
    - _Requirements: 1.11, 2.11, 3.14_

  - [ ] 6.5 sessionStorage cleanup (Bug 1.12)
    - In `desktop/src/hooks/useChatStreamingLifecycle.ts`:
    - Add `removePendingState(sessionId)` call in the SSE stream success completion handler
    - Add `removePendingState(sessionId)` call in the SSE stream error handler
    - Ensure cleanup fires on disconnect/tab close paths as well
    - _Bug_Condition: SSE stream errors or user navigates away → sessionStorage entries never cleaned_
    - _Expected_Behavior: Pending state cleaned on completion, error, disconnect, or tab close_
    - _Requirements: 1.12, 2.12_

- [ ] 7. Verification — Re-run all tests

  - [ ] 7.1 Re-run Group A exploration tests (Property 1)
    - Run the bug condition exploration test from task 1
    - All three sub-properties (threshold, escalation persistence, progress detection) must PASS
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ] 7.2 Re-run Group A preservation tests (Property 2)
    - Run the preservation property tests from task 2
    - All sub-properties (200K threshold, PASSIVE phase, reset_all, exception safety, set-overlap, single-tool) must PASS
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.9, 3.10_

  - [ ] 7.3 Run existing test suite
    - Run `pytest backend/tests/` to verify no regressions across the full backend test suite
    - Fix any failures introduced by the changes

- [ ] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Verify: exploration tests (Property 1) PASS, preservation tests (Property 2) PASS, existing test suite PASS
  - Confirm Group B test fixes (4.1, 4.2) pass in the full suite
  - Confirm no import errors or lint issues in modified files
