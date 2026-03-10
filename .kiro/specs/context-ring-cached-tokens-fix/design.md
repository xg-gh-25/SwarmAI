<!-- PE-REVIEWED -->
# Context Ring Cached Tokens Fix — Bugfix Design

## Overview

The context usage ring reports near-0% during heavy sessions because the previous fix (`context-usage-ring-fix`) only reads `usage.input_tokens` from the SDK's `ResultMessage`. With Anthropic's prompt caching enabled, `input_tokens` reflects only the non-cached portion (often single digits), while the bulk of context consumption lives in `cache_read_input_tokens` and `cache_creation_input_tokens`. The total context window usage should be the sum of all three fields. A secondary issue is that the `model` field is never present in the `result` SSE event, so `_get_model_context_window(None)` always falls back to the default 200K window.

The fix is surgical: in both `run_conversation()` and `continue_with_answer()`, sum all three token fields from the `usage` dict before passing to `_build_context_warning()`, and resolve the model from `agent_config.get("model")` instead of from the result event.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — `last_input_tokens` is set to only `usage.input_tokens` (non-cached tokens) instead of the sum of `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
- **Property (P)**: The desired behavior — context usage percentage is computed from the total of all three token fields divided by the correct model's context window
- **Preservation**: The threshold levels (ok/warn/critical), the `context_warning` SSE event shape, the `_build_context_warning()` function signature and logic, and the `result` SSE event shape must remain unchanged
- **`_build_context_warning()`**: Method in `AgentManager` (line ~931) that takes `(input_tokens, model)` and returns a `context_warning` event dict with `pct`, `level`, `tokensEst`, `message`
- **`run_conversation()`**: Method in `AgentManager` (line ~1318) that orchestrates the chat response stream and emits `context_warning` post-response
- **`continue_with_answer()`**: Method in `AgentManager` (line ~2447) that continues conversation after permission/question answers, same post-response context monitor pattern
- **`_run_query_on_client()`**: Method in `AgentManager` (line ~1781) that builds the `result` SSE event including the full `usage` dict with all three token fields

## Bug Details

### Fault Condition

The bug manifests whenever the SDK returns a `ResultMessage` with prompt caching active. Both `run_conversation()` and `continue_with_answer()` capture only `_usage.get("input_tokens")` as `last_input_tokens`, ignoring the `cache_read_input_tokens` and `cache_creation_input_tokens` fields that are already present in the same usage dict. Additionally, `last_model = event.get("model")` always resolves to `None` because the `result` SSE event (built in `_run_query_on_client()`) does not include a `model` field.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ResultEvent (the "result" SSE event from _run_query_on_client)
  OUTPUT: boolean

  LET usage = input.usage
  IF usage IS None THEN RETURN false  // no usage data at all, nothing to miscompute

  LET cached = (usage.cache_read_input_tokens OR 0) + (usage.cache_creation_input_tokens OR 0)
  LET model_from_event = input.model  // always None — result event has no model field

  // Bug condition: cached tokens exist but are ignored, OR model is unresolvable
  RETURN cached > 0 OR model_from_event IS None
END FUNCTION
```

Note: Since `model_from_event` is always `None` in the current code, `isBugCondition` is `true` for every turn. This means preservation checking must target `_build_context_warning()` directly (which is unchanged by this fix), not the full pipeline — because the full pipeline always hits the bug condition.

### Examples

- **Typical cached session**: `input_tokens: 3`, `cache_read_input_tokens: 98,599`, `cache_creation_input_tokens: 948` on a 200K window. Expected: `round((3 + 98599 + 948) / 200000 * 100) = 50%`. Actual: `round(3 / 200000 * 100) = 0%`.
- **Over-window session**: `input_tokens: 11,337`, `cache_read_input_tokens: 661,568`, `cache_creation_input_tokens: 66,889` on a 200K window. Expected: `round(739794 / 200000 * 100) = 370%` (critical). Actual: `round(11337 / 200000 * 100) = 6%` (ok) — user gets no warning.
- **Moderate cached session**: `input_tokens: 97`, `cache_read_input_tokens: 145,000`, `cache_creation_input_tokens: 5,000` on a 200K window. Expected: `round(150097 / 200000 * 100) = 75%` (warn). Actual: `round(97 / 200000 * 100) = 0%` (ok).
- **No caching (edge case)**: `input_tokens: 140,000`, `cache_read_input_tokens: 0`, `cache_creation_input_tokens: 0`. Expected: `round(140000 / 200000 * 100) = 70%` (warn). Actual: same — bug does not manifest when caching is inactive.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `_build_context_warning()` function signature and internal logic must remain identical — it already correctly computes `pct`, `level`, and `message` from `(input_tokens, model)`. The fix is upstream of this function.
- The `context_warning` SSE event shape (`type`, `level`, `pct`, `tokensEst`, `message`) must remain unchanged.
- The `result` SSE event shape (including individual `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` fields in `usage`) must remain unchanged.
- Threshold levels: `ok` when pct < 70, `warn` when 70 ≤ pct < 85, `critical` when pct ≥ 85.
- When all three token fields are `None` or sum to 0, no `context_warning` event should be emitted (same as current behavior where `_build_context_warning` returns `None` for `input_tokens <= 0`).
- Error resilience: context monitoring must remain best-effort, never breaking the response stream.
- Multi-tab isolation: `last_input_tokens` and `last_model` remain local variables in each generator — no new shared state.

**Scope:**
All inputs that do NOT involve the token summation or model resolution should be completely unaffected by this fix. This includes:
- All SDK message processing (AssistantMessage, SystemMessage, ToolUseBlock, etc.)
- Session management (init, resume, cleanup)
- Permission request handling
- Message persistence
- The `result` SSE event itself (we read from it, we don't change it)
- The `_build_context_warning()` function (we change what we pass to it, not the function itself)

## Hypothesized Root Cause

Based on the code analysis, the root causes are:

1. **Incomplete Token Summation**: When the previous fix (`context-usage-ring-fix`) was implemented, it captured `last_input_tokens = _usage.get("input_tokens")` — the correct field name from the SDK, but only one of three fields that contribute to total context consumption. With Anthropic's prompt caching, `input_tokens` represents only the non-cached portion. The `cache_read_input_tokens` and `cache_creation_input_tokens` fields were already present in the `usage` dict (included in the `result` event by `_run_query_on_client()`) but were not summed.

2. **Missing Model in Result Event**: The `result` SSE event built in `_run_query_on_client()` does not include a `model` field. Both `run_conversation()` and `continue_with_answer()` attempt `last_model = event.get("model")`, which always returns `None`. This causes `_get_model_context_window(None)` to return the default 200K window. While all current models happen to have 200K windows, this is fragile and will break silently when models with different window sizes are added.

3. **Duplicated Code Path**: The same bug exists in both `run_conversation()` (line ~1427) and `continue_with_answer()` (line ~2510). Both must be fixed identically.

## Correctness Properties

Property 1: Fault Condition - Total Token Summation

_For any_ `result` SSE event where the `usage` dict contains `input_tokens`, `cache_read_input_tokens`, and/or `cache_creation_input_tokens`, the fixed code SHALL compute `last_input_tokens` as the sum of all three fields (treating `None` as 0), and pass this sum to `_build_context_warning()`. The resulting `context_warning.pct` SHALL equal `round(sum / model_context_window * 100)`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Fault Condition - Model Resolution from Agent Config

_For any_ conversation turn, the fixed code SHALL resolve the model from `agent_config.get("model")` (available as a local variable in both `run_conversation()` and `continue_with_answer()`) instead of from `event.get("model")` on the result event. The resolved model SHALL be passed to `_build_context_warning()` so that `_get_model_context_window()` receives the correct model identifier.

**Validates: Requirements 2.5**

Property 3: Preservation - Threshold Levels Unchanged

_For any_ context usage percentage value, the fixed system SHALL produce the same `level` classification as the original: `ok` when pct < 70, `warn` when 70 ≤ pct < 85, `critical` when pct ≥ 85. The `_build_context_warning()` function is NOT modified by this fix.

**Validates: Requirements 3.1, 3.2, 3.3**

Property 4: Preservation - No-Data Suppression Unchanged

_For any_ turn where all three token fields are `None` or sum to 0, the fixed code SHALL NOT emit a `context_warning` event, consistent with `_build_context_warning()` returning `None` for `input_tokens <= 0`.

**Validates: Requirements 3.4**

Property 5: Preservation - SSE Event Shapes Unchanged

_For any_ `context_warning` event, the event SHALL contain the same fields (`type`, `level`, `pct`, `tokensEst`, `message`). _For any_ `result` event, the `usage` dict SHALL continue to include the individual `input_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens` fields.

**Validates: Requirements 3.5, 3.6**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**IMPORTANT**: There are **two identical code paths** that must both be updated:
1. `run_conversation()` — the `async for event in self._execute_on_session(...)` loop and post-response section
2. `continue_with_answer()` — the same pattern

**Specific Changes (apply to BOTH methods)**:

1. **Sum all three token fields**: Replace:
   ```python
   last_input_tokens = _usage.get("input_tokens")
   ```
   With:
   ```python
   last_input_tokens = (
       (_usage.get("input_tokens") or 0)
       + (_usage.get("cache_read_input_tokens") or 0)
       + (_usage.get("cache_creation_input_tokens") or 0)
   )
   ```
   The `or 0` pattern handles `None` values from the SDK gracefully.

2. **Resolve model from agent_config**: Replace:
   ```python
   last_model = event.get("model")
   ```
   With:
   ```python
   last_model = agent_config.get("model")
   ```
   The `agent_config` dict is already available as a local variable in both methods (fetched via `await db.agents.get(agent_id)` at the top of each method).

### No Other Changes Required

- `_build_context_warning()` — **no changes**. It already correctly computes `pct`, `level`, and `message` from `(input_tokens, model)`. The fix is upstream. Note: the `tokensEst` field in the returned event dict is set to the `input_tokens` parameter — after this fix, that parameter will contain the **sum** of all three token fields, so `tokensEst` will correctly reflect total context consumption. This is the desired behavior.
- `_run_query_on_client()` — **no changes**. The `result` event already includes all three token fields in the `usage` dict. No need to add a `model` field to the result event.
- `_get_model_context_window()` — **no changes**. It already handles `None` model by returning the default window. It also strips Bedrock prefixes (`us.anthropic.`) and suffixes (`:0`, `-v1`) from model strings, so `agent_config.get("model")` values like `us.anthropic.claude-opus-4-6-v1` will resolve correctly.
- Frontend — **no changes**. The `context_warning` SSE event shape is preserved.

### Edge Case: Negative Token Values

The `or 0` pattern in `(_usage.get("input_tokens") or 0)` treats both `None` and `0` as `0`. If the SDK were to return a negative value (unlikely), e.g., `(-5 or 0)` evaluates to `-5` since `-5` is truthy. This is safely handled by `_build_context_warning()`'s existing guard: `if input_tokens is None or input_tokens <= 0: return None`.

### Multi-Tab Isolation Safety

- `last_input_tokens` and `last_model` are local variables scoped to each generator invocation — NOT instance-level or module-level state. Each tab's SSE stream has its own generator with its own locals.
- No new shared mutable state is introduced (compliant with Global Anti-Pattern #1).
- The `context_warning` event is yielded into the same SSE stream as the `result` event — it goes to the specific tab's EventSource connection only.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior. Tests will be added to the existing `backend/tests/test_context_usage_inline.py` file.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that exercise the token capture logic in `run_conversation()` and `continue_with_answer()`. Mock `_execute_on_session` to yield a `result` event with known `usage` values including cached token fields, then verify the old code passes only `input_tokens` (not the sum) to `_build_context_warning()`.

**Test Cases**:
1. **Cached Tokens Ignored Test**: Yield a result event with `input_tokens: 3`, `cache_read_input_tokens: 98599`, `cache_creation_input_tokens: 948`. Verify `last_input_tokens` is set to 3 (not 99550) on unfixed code — demonstrating the bug.
2. **Over-Window Not Detected Test**: Yield a result event with `input_tokens: 11337`, `cache_read_input_tokens: 661568`, `cache_creation_input_tokens: 66889`. Verify the context_warning shows ~6% instead of ~370% on unfixed code.
3. **Model Always None Test**: Verify that `last_model` captured from `event.get("model")` is always `None` because the result event has no model field.

**Expected Counterexamples**:
- `_build_context_warning()` receives only the non-cached `input_tokens` value (3, 11337, etc.) instead of the full sum
- `_build_context_warning()` receives `None` for model, always falling back to 200K default window

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  LET total = (input.usage.input_tokens OR 0)
            + (input.usage.cache_read_input_tokens OR 0)
            + (input.usage.cache_creation_input_tokens OR 0)
  LET model = agent_config.model
  result := _build_context_warning(total, model)
  ASSERT result.pct = round(total / _get_model_context_window(model) * 100)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Important**: Since `isBugCondition` is true for every turn (model is always `None` in the result event), preservation checking must target `_build_context_warning()` directly — which is unchanged by this fix — rather than the full pipeline. The preservation tests verify that the function's threshold classification, event shape, and null suppression behavior remain identical regardless of what value is passed in.

**Pseudocode:**
```
// Preservation targets _build_context_warning() directly (unchanged function)
FOR ALL (total_tokens, model) pairs DO
  ASSERT _build_context_warning(total_tokens, model) produces same level/pct/shape
  // When cache fields are 0/None, the sum equals input_tokens — same as before
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many random `(input_tokens, cache_read, cache_creation, window)` tuples
- It catches edge cases (all None, all 0, mixed None/0, very large values)
- It provides strong guarantees that the summation formula is correct

**Test Plan**: Observe behavior on UNFIXED code first for non-cached inputs, then write property-based tests verifying the same behavior after the fix.

**Test Cases**:
1. **No-Cache Preservation**: For inputs where `cache_read_input_tokens` and `cache_creation_input_tokens` are both 0, verify the result is identical to the original code (sum equals `input_tokens`).
2. **Threshold Preservation**: For random `pct` values, verify `level` classification is unchanged (ok/warn/critical boundaries at 70/85).
3. **Event Shape Preservation**: Verify `context_warning` event contains `type`, `level`, `pct`, `tokensEst`, `message` fields.
4. **Null Suppression Preservation**: When all three fields are None, verify no `context_warning` is emitted.

### Unit Tests

- Test the three-field summation with known values (cached session, over-window, no-cache, all-None, mixed-None)
- Test model resolution from `agent_config` vs from result event
- Test boundary values for threshold classification (69%, 70%, 84%, 85%)
- Test that `_build_context_warning()` is unchanged (existing tests in `test_context_usage_inline.py` already cover this)

### Property-Based Tests

- Generate random `(input_tokens, cache_read, cache_creation)` triples and verify the sum formula: `total = (input_tokens or 0) + (cache_read or 0) + (cache_creation or 0)`
- Generate random totals and window sizes, verify `pct = round(total / window * 100)` and correct level classification
- Generate inputs where `cache_read = 0` and `cache_creation = 0`, verify result matches original single-field behavior (preservation)

### Integration Tests

- Test full `run_conversation()` flow with a mock `_execute_on_session` that yields a result event with cached tokens — verify the `context_warning` SSE event has the correct summed percentage
- Test `continue_with_answer()` with the same mock — verify both code paths produce identical results
- Test that the `result` SSE event is still emitted correctly (unchanged) alongside the corrected `context_warning`
