<!-- PE-REVIEWED -->
# ContextUsageRing Data Source Bugfix Design

## Overview

The `ContextUsageRing` displays stale or incorrect context usage because the backend's `check_context_usage()` function reads `.jsonl` transcript files from `~/.claude/projects/` (Claude Code's data) instead of using the `input_tokens` value already available in the SDK's `ResultMessage.usage` dict. Additionally, the check only fires on turns 1, 5, 10, 15... (`CHECK_INTERVAL_TURNS = 5`), leaving the ring frozen between checks.

The fix eliminates the filesystem-scanning approach entirely and instead extracts `input_tokens` from the `ResultMessage` that the SDK already delivers on every turn. The `context_warning` SSE event will be emitted after every turn, and the percentage will be computed as `input_tokens / model_context_window * 100`.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — `check_context_usage()` reads `.jsonl` files from `~/.claude/projects/` instead of using SDK-provided `input_tokens`
- **Property (P)**: The desired behavior — context usage percentage is computed from `input_tokens / model_context_window * 100` using the SDK's `ResultMessage.usage` data, emitted every turn
- **Preservation**: The ring's color thresholds (green/amber/red/gray), the `context_warning` SSE event shape, the frontend `ContextUsageRing` component behavior, and the `useChatStreamingLifecycle` handler must remain unchanged
- **`check_context_usage()`**: The function in `backend/core/context_monitor.py` that currently scans `.jsonl` transcript files to estimate context usage
- **`_run_query_on_client()`**: The method in `backend/core/agent_manager.py` (line ~1729) that processes SDK messages and yields SSE events, including the `result` event with `usage.input_tokens`
- **`run_conversation()`**: The method in `backend/core/agent_manager.py` (line ~1268) that orchestrates the response stream and currently calls `check_context_usage()` in the post-response section
- **`ResultMessage`**: SDK message type that carries `usage.input_tokens`, `usage.output_tokens`, `duration_ms`, `total_cost_usd`, etc.

## Bug Details

### Fault Condition

The bug manifests on every call to `check_context_usage()` because the function always reads from the wrong data source (`~/.claude/projects/*.jsonl` — Claude Code's transcripts) instead of using the `input_tokens` value from the SDK's `ResultMessage.usage`. A secondary fault is that the check only runs on turns where `turn == 1 or turn % 5 == 0`, leaving the ring stale for up to 4 consecutive turns.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ContextCheckInvocation
  OUTPUT: boolean

  // Primary fault: wrong data source
  LET usesFilesystemScan = input.dataSource = "jsonl_filesystem_scan"

  // Secondary fault: skipped turns
  LET isSkippedTurn = input.turnNumber > 1 AND input.turnNumber % CHECK_INTERVAL_TURNS != 0

  RETURN usesFilesystemScan OR isSkippedTurn
END FUNCTION
```

### Examples

- **Turn 1, SDK reports 15,000 input_tokens on 200K window**: Expected 8% (green ring). Actual: `check_context_usage()` scans `~/.claude/projects/`, finds an unrelated Claude Code transcript, reports 43% (amber ring) — wrong session's data.
- **Turn 2, SDK reports 28,000 input_tokens**: Expected 14% (green ring). Actual: no `context_warning` emitted (turn 2 is skipped by `CHECK_INTERVAL_TURNS = 5`), ring still shows 43% from turn 1's wrong data.
- **Turn 5, SDK 0.1.34+ with no .jsonl files on disk**: Expected usage based on actual `input_tokens`. Actual: `check_context_usage()` finds no transcript, returns 0% — ring drops to 0% despite real usage.
- **Turn 10, SDK reports 170,000 input_tokens (85% of 200K)**: Expected 85% (red ring, critical). Actual: filesystem scan returns stale/unrelated data, user gets no warning about approaching context limit.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The `ContextUsageRing` SVG component rendering, color thresholds, and tooltip text must remain identical (green < 70%, amber 70–84%, red ≥ 85%, gray for null)
- The `context_warning` SSE event shape (`type`, `level`, `pct`, `tokensEst`, `message`) must remain unchanged
- The `useChatStreamingLifecycle` handler that writes `contextWarning` to `tabMapRef` and mirrors to React state must remain unchanged
- The `ChatPage` → `ChatInput` → `ContextUsageRing` prop-passing chain (`contextWarning?.pct`) must remain unchanged
- The Toast notification behavior for warn/critical levels must remain unchanged
- Context monitoring must remain best-effort — errors must never break the response stream
- The `result` SSE event shape (including `usage.input_tokens`, `usage.output_tokens`, etc.) must remain unchanged

**Scope:**
All inputs that do NOT involve context usage calculation should be completely unaffected by this fix. This includes:
- All SDK message processing (AssistantMessage, SystemMessage, ToolUseBlock, etc.)
- Session management (init, resume, cleanup)
- Permission request handling
- Message persistence
- The `result` SSE event itself (we read from it, we don't change it)

## Hypothesized Root Cause

Based on the code analysis, the root causes are:

1. **Wrong Data Source**: `check_context_usage()` in `context_monitor.py` was designed for an earlier architecture where Claude Code persisted `.jsonl` transcripts to `~/.claude/projects/`. The SwarmAI app uses the Claude SDK via `ClaudeSDKClient`, which (a) doesn't share Claude Code's transcript directory, and (b) as of SDK 0.1.34+ no longer persists transcripts to disk at all. The function scans the filesystem for data that either belongs to a different application or doesn't exist.

2. **Available but Ignored SDK Data**: The `ResultMessage` from the SDK already carries `usage.input_tokens` (extracted at line ~2268 in `_run_query_on_client`), but `run_conversation()` ignores this data and instead calls the filesystem-based `check_context_usage()` in its post-response section (line ~1386).

3. **Interval-Based Checking**: `CHECK_INTERVAL_TURNS = 5` causes the monitor to skip turns 2, 3, 4, 6, 7, 8, 9, etc. This was a performance optimization for the expensive filesystem scan, but is unnecessary when reading a single integer from an already-available `ResultMessage`.

4. **No Model-Aware Window Size**: `check_context_usage()` defaults to `DEFAULT_WINDOW_TOKENS = 200_000` but doesn't know which model is in use. The `AgentManager` already has `_get_model_context_window()` which resolves the correct window size per model — this should be used instead.

## Correctness Properties

Property 1: Fault Condition - Context usage computed from SDK input_tokens

_For any_ turn where the SDK's `ResultMessage` carries `usage.input_tokens`, the fixed system SHALL compute context usage as `round(input_tokens / model_context_window * 100)` and emit a `context_warning` SSE event with the correct `pct`, `level`, and `tokensEst` values. The system SHALL NOT read from `.jsonl` transcript files.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 2: Fault Condition - Every-turn emission (when usage data available)

_For any_ turn in a session (turn 1, 2, 3, ... N) where the SDK's `ResultMessage` carries a valid `usage.input_tokens` (not None, > 0), the fixed system SHALL emit a `context_warning` SSE event after the `result` event, regardless of turn number. There SHALL be no `CHECK_INTERVAL_TURNS` gating. When `usage.input_tokens` is None or missing, no `context_warning` SHALL be emitted for that turn (to avoid flashing the ring to 0% incorrectly).

**Validates: Requirements 2.3**

Property 3: Preservation - Threshold levels unchanged

_For any_ context usage percentage value, the fixed system SHALL produce the same `level` classification as the original: `ok` when pct < 70, `warn` when 70 ≤ pct < 85, `critical` when pct ≥ 85. The `ContextUsageRing` color mapping (green/amber/red/gray) SHALL remain identical.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 4: Preservation - SSE event shape and frontend handling unchanged

_For any_ `context_warning` SSE event, the event SHALL contain the same fields (`type`, `level`, `pct`, `tokensEst`, `message`) as before. The frontend `useChatStreamingLifecycle` handler, `ChatPage` prop passing, and `ContextUsageRing` component SHALL require no changes.

**Validates: Requirements 3.5**

Property 5: Preservation - Error resilience

_For any_ error during context usage computation (e.g., missing `usage` field, division by zero), the system SHALL fail silently without breaking the response stream, consistent with the existing best-effort pattern.

**Validates: Requirements 3.6**

Property 6: Preservation - Multi-tab session isolation

_For any_ two concurrent sessions S1 and S2 (representing different tabs), the `context_warning` event emitted for S1 SHALL be computed exclusively from S1's own `ResultMessage.usage.input_tokens`, and SHALL NOT read from shared global state, filesystem paths, or S2's data. No new instance-level or module-level mutable state SHALL be introduced that is not keyed by session ID.

**Validates: Requirements 3.7**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**IMPORTANT**: There are **two identical context_warning blocks** that must both be updated:
1. `run_conversation()` (post-response section, lines ~1380–1410) — normal chat flow
2. `continue_with_answer()` (post-response section, lines ~2460–2490) — permission answer flow

Both follow the same pattern: iterate `_execute_on_session()`, then emit `context_warning`. Failing to update both means the ring will still show wrong data when the user answers a permission prompt.

**Specific Changes (apply to BOTH blocks)**:

1. **Capture `input_tokens` from the result event during streaming**: In the `async for event in self._execute_on_session(...)` loop (which yields events from `_run_query_on_client()`), capture `usage.input_tokens` when the `result` event passes through. Add a local variable before the loop:
   ```python
   last_input_tokens: Optional[int] = None
   last_model: Optional[str] = None
   ```
   Then inside the loop, when `event.get("type") == "result"`:
   ```python
   usage_data = event.get("usage")
   if usage_data:
       last_input_tokens = usage_data.get("input_tokens")
   last_model = event.get("model") or agent_model  # fallback to agent's configured model
   ```

2. **Remove `CHECK_INTERVAL_TURNS` gating**: Delete the `if turns == 1 or turns % CHECK_INTERVAL_TURNS == 0:` conditional. Emit `context_warning` on every turn where valid usage data is available.

3. **Replace `check_context_usage()` call with inline computation using null-safe guards**:
   ```python
   if last_input_tokens is not None and last_input_tokens > 0:
       window = self._get_model_context_window(last_model)
       pct = round((last_input_tokens / window) * 100) if window > 0 else 0
       level = "critical" if pct >= 85 else "warn" if pct >= 70 else "ok"
       tokens_k = last_input_tokens // 1000
       window_k = window // 1000
       # ... build message and yield context_warning
   ```
   Note: `last_input_tokens` can be `None` (SDK returned it explicitly as None) — always check `is not None` before arithmetic.

4. **Use `_get_model_context_window()`**: Pass the model to get the correct context window size. Note: `_MODEL_CONTEXT_WINDOWS` currently only has 3 entries and defaults to 200K. When new models with different window sizes are added (e.g., 1M context), this dict must be updated or the percentage will be incorrect.

5. **Build the message string**: Construct the human-readable message using the same format as the existing `ContextStatus` messages (e.g., "Context 42% full (~84K/200K tokens). Plenty of room.").

6. **Preserve logging**: Keep the existing `logger.info` / `logger.debug` calls with the same fields (level, session_id, pct, tokens_est) so debugging remains possible.

**File**: `backend/core/context_monitor.py`

**Specific Changes**:

7. **Deprecate (do not remove)**: Add a deprecation notice to the module docstring and `check_context_usage()` function docstring. The module has no external consumers (only imported by `agent_manager.py`), but keep it for reference. The `WARN_PCT` and `CRITICAL_PCT` constants should be inlined in `agent_manager.py` as the authoritative source.

8. **Update `agent_manager.py` imports**: Remove the `from .context_monitor import check_context_usage, CHECK_INTERVAL_TURNS` import since neither symbol is used after the fix.

**File**: `backend/tests/test_context_monitor.py`

**Specific Changes**:

9. **Preserve existing tests**: Do NOT delete `test_context_monitor.py`. The tests validate `check_context_usage()` which still exists (deprecated). Add a comment noting these tests cover the deprecated module. New tests for the fixed behavior go in a separate test file (e.g., `test_context_usage_inline.py`).

### No Frontend Changes Required

The frontend components (`ContextUsageRing`, `ChatInput`, `ChatPage`, `useChatStreamingLifecycle`) require zero changes. The `context_warning` SSE event shape is preserved, and the ring will simply receive accurate data more frequently.

### Multi-Tab Isolation Safety

This fix improves multi-tab isolation compared to the current broken implementation:

**Backend isolation (verified safe):**
- `_user_turn_counts` is keyed by `effective_sid` (per-session). No change to this keying.
- `last_input_tokens` and `last_model` are local variables scoped to a single `run_conversation()` / `continue_with_answer()` generator invocation — NOT instance-level or module-level state. Each tab's SSE stream has its own generator with its own locals.
- `_get_model_context_window()` is a pure function reading from a class-level immutable dict. No session state involved.
- The `context_warning` event is yielded into the same SSE stream as the `result` event — it goes to the specific tab's EventSource connection only. Tab A's context_warning cannot reach Tab B.
- No new shared mutable state is introduced (compliant with Global Anti-Pattern #1).

**Frontend isolation (preserved — no changes):**
- The existing `useChatStreamingLifecycle` handler for `context_warning` events uses `capturedTabId` (per Principle 3: Stream Handler Closure Capture) to write to `tabMapRef`, and mirrors to React state only if `isActiveTab` (per Principle 2: Active Tab = Display Mirror Only).
- Since the SSE event shape is unchanged and no frontend code is modified, all 7 isolation principles remain intact.

**Improvement over current code:**
- The old `check_context_usage()` was actually a multi-tab isolation violation: it scanned `~/.claude/projects/` globally without knowing which session was asking. If Tab A and Tab B both hit turn 5 simultaneously, they'd both scan the same directory and return the same (wrong) percentage. The fix makes context usage truly per-session by reading from each session's own `ResultMessage.usage.input_tokens`.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that exercise the actual bug path in `run_conversation()` and `continue_with_answer()`. Mock `_execute_on_session` to yield a `result` event with known `usage.input_tokens`, then verify the old code emits wrong/no `context_warning`. Also test `check_context_usage()` directly to confirm it reads from the wrong source.

**Test Cases**:
1. **Wrong Data Source Test**: Call `check_context_usage()` with no `.jsonl` files present — verify it returns 0% even when the SDK would report real usage (will demonstrate the bug)
2. **Stale Data Test**: Create a `.jsonl` file with known content, call `check_context_usage()` — verify it reads from the file instead of SDK data (will demonstrate wrong source)
3. **Skipped Turn Test**: Mock `run_conversation()` with turns 2, 3, 4 — verify no `context_warning` event is emitted despite `result` events carrying valid `usage.input_tokens` (will demonstrate the interval bug)
4. **Turn 5 Emission Test**: Mock turn 5 — verify `context_warning` IS emitted but with filesystem-derived data, not SDK data (will demonstrate wrong source even when emitted)
5. **Permission Answer Path Test**: Mock `continue_with_answer()` with turn 2 — verify the same skipped-turn bug exists in this second code path

**Expected Counterexamples**:
- `check_context_usage()` returns 0% when no `.jsonl` files exist, despite SDK reporting real token usage
- Turns 2–4 produce no `context_warning` event in both `run_conversation()` and `continue_with_answer()`
- Possible causes: filesystem scan finds nothing (SDK 0.1.34+), or finds unrelated Claude Code data

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := run_conversation_fixed(input)
  ASSERT result.context_warning.pct = round(input.usage.input_tokens / model_context_window * 100)
  ASSERT result.context_warning.dataSource = "sdk_input_tokens"
  ASSERT context_warning_emitted = true  // every turn, no interval gating
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT F(input).level_thresholds = F'(input).level_thresholds
  ASSERT F(input).sse_event_shape = F'(input).sse_event_shape
  ASSERT F(input).ring_colors = F'(input).ring_colors
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many random `(input_tokens, model_context_window)` pairs to verify threshold classification
- It catches edge cases (0 tokens, exactly 70%, exactly 85%, window size of 0, very large values)
- It provides strong guarantees that the level classification logic is unchanged

**Test Plan**: Observe the threshold behavior on UNFIXED code first (the `ContextStatus` level assignment), then write property-based tests verifying the same thresholds apply in the fixed code.

**Test Cases**:
1. **Threshold Preservation**: For random `pct` values 0–100, verify `level` is `ok` when pct < 70, `warn` when 70 ≤ pct < 85, `critical` when pct ≥ 85
2. **Percentage Calculation Preservation**: For random `(input_tokens, window)` pairs, verify `pct = round(input_tokens / window * 100)`
3. **SSE Event Shape Preservation**: Verify the `context_warning` event contains `type`, `level`, `pct`, `tokensEst`, `message` fields
4. **Null/Missing Usage Preservation**: When `ResultMessage.usage` is None, verify no crash and graceful handling

### Unit Tests

- Test the new inline context percentage computation with known `(input_tokens, window)` pairs
- Test threshold classification at boundary values (0, 69, 70, 84, 85, 100)
- Test graceful handling when `usage` is None or `input_tokens` is missing
- Test that `_get_model_context_window()` returns correct values for known models

### Property-Based Tests

- Generate random `(input_tokens, model_context_window)` pairs and verify `pct` and `level` are consistent with the threshold rules
- Generate random turn sequences and verify `context_warning` is emitted on every turn (no interval gating)
- Generate random `pct` values and verify the message string format matches the expected pattern

### Integration Tests

- Test full `run_conversation()` flow with a mock `ClaudeSDKClient` that returns a `ResultMessage` with known `usage.input_tokens` — verify the `context_warning` SSE event has the correct `pct`
- Test that the `result` SSE event is still emitted correctly (unchanged) alongside the new `context_warning`
- Test multi-turn conversation where `input_tokens` increases each turn — verify the ring percentage increases monotonically
