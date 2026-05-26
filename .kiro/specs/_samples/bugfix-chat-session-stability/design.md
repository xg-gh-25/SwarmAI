# Chat Session Stability Fix — Bugfix Design

## Overview

Four interacting bugs cause cascading session instability in the multi-session backend. The root cause is a `NameError` in `_read_formatted_response()` (Bug 1) that references an undefined `options` variable from `send()`'s local scope. This crash fires at the `ResultMessage` stage — the final step of every successful conversation turn — preventing the STREAMING→IDLE transition, triggering a retry loop that also crashes identically, exhausting all retries, and leaving orphaned Claude SDK subprocesses. The downstream effects are: excess child processes (Bug 4), zombie Hypothesis pytest processes with no deadline (Bug 2), and a stale dev backend orphan invisible to the lifecycle manager's reaper (Bug 3). The fix stores the model name on the `SessionUnit` instance during `send()` to structurally eliminate the `NameError`, moves the context warning bridge inside the existing try/except as defense-in-depth, broadens the orphan reaper to catch `python main.py` processes, and adds Hypothesis `deadline`/`suppress_health_check` settings.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the primary crash — `_read_formatted_response()` referencing the undefined `options` variable when processing a `ResultMessage` with usage data
- **Property (P)**: The desired behavior — context warning bridge accesses model info from `self._model_name` (instance attribute), completes normally, and the session transitions STREAMING→IDLE
- **Preservation**: All existing message processing (AssistantMessage, SystemMessage, StreamEvent, ToolUseBlock), retry logic for genuinely retriable errors, error event formatting, and subprocess lifecycle transitions must remain unchanged
- **`_read_formatted_response()`**: Async generator in `session_unit.py` that reads SDK messages and yields SSE events; contains the NameError at the context warning bridge
- **`send()`**: Entry point method on `SessionUnit` that receives `options: ClaudeAgentOptions` as a local parameter and delegates to `_stream_response()` → `_read_formatted_response()`
- **`_stream_response()`**: Intermediate method that sends the query and delegates response reading to `_read_formatted_response()`
- **Context warning bridge**: Code block at ~line 887 in `_read_formatted_response()` that checks input token usage against the model's context window and emits a `context_warning` SSE event
- **`LifecycleManager._reap_orphans()`**: Startup one-shot that kills unowned `claude_agent_sdk/_bundled/claude` processes
- **Hypothesis deadline**: Per-test time limit that prevents infinite shrinking loops in property-based tests

## Bug Details

### Bug Condition

The primary bug (Bug 1) manifests on every conversation turn that returns a `ResultMessage` with `input_tokens > 0`. The context warning bridge in `_read_formatted_response()` references `options` — a local variable in `send()` that was never passed down the call chain. The `NameError` fires BEFORE the `try` block, so the `except Exception: pass` defense never catches it.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ResultMessage
  OUTPUT: boolean

  usage := input.usage OR {}
  input_tokens := usage.get("input_tokens")

  RETURN input_tokens IS NOT None
         AND input_tokens > 0
         AND "options" NOT IN local_scope_of(_read_formatted_response)
END FUNCTION
```

The secondary bugs are downstream effects:
- Bug 2: `isBugCondition_hypothesis(test) := test.uses_hypothesis AND test.settings.deadline IS None`
- Bug 3: `isBugCondition_orphan(proc) := proc.cmdline MATCHES "python main.py" AND proc.ppid == 1 AND proc NOT IN reaper_search_patterns`
- Bug 4: `isBugCondition_excess_children(session) := session.crashed_via_bug1 AND session.subprocess NOT cleaned_up`

### Examples

- **Bug 1 — Every turn crashes**: User sends "hello", Claude responds successfully, SDK emits `ResultMessage` with `usage={"input_tokens": 1500}`. At line ~887, `if input_tokens and input_tokens > 0 and options:` raises `NameError: name 'options' is not defined`. The exception propagates up through `_stream_response()` to `send()`, which treats it as a retriable error. Each retry also crashes at the same point. After 3 retries, the session enters COLD state with no response delivered.

- **Bug 1 — Retry loop also crashes**: After the first `NameError`, `send()` enters the retry loop. It spawns a fresh subprocess with `--resume`, streams the response again, hits the same `ResultMessage` with usage data, and crashes with the same `NameError`. This repeats for all `MAX_RETRY_ATTEMPTS` (3), yielding `ALL_RETRIES_EXHAUSTED` to the frontend.

- **Bug 2 — Zombie pytest processes**: 5 Hypothesis pytest processes running 4-20 hours at ~35% CPU each. The tests have no `deadline` setting, so Hypothesis's shrinking phase runs indefinitely when it finds a failing example. Expected: tests complete within seconds with a `deadline` and `suppress_health_check` configuration.

- **Bug 3 — Invisible orphan**: PID 13782 (`python main.py --port 8000`) has ppid=1 (orphaned), running since Thursday. The reaper's `pgrep -f "claude_agent_sdk/_bundled/claude"` pattern doesn't match it. Expected: reaper also searches for stale `python main.py` processes on known ports.

- **Bug 4 — Excess children**: 6 Claude SDK child subprocesses at 1.4GB RSS when MAX_CONCURRENT=2 means at most 2-3 should exist. These are orphans from Bug 1 crashes where `_crash_to_cold()` was called but the subprocess kill didn't fully clean up child trees. Expected: fixing Bug 1 eliminates the source; the reaper catches any remaining orphans.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Processing of `AssistantMessage`, `SystemMessage`, `StreamEvent`, and `ToolUseBlock` messages in `_read_formatted_response()` must continue to yield the same SSE event formats (Req 3.7)
- Retry logic for genuinely retriable SDK errors (e.g., "Cannot write to terminated process", exit code -9) must continue to work with exponential backoff and `--resume` (Req 3.2)
- All-retries-exhausted error events must continue to be yielded to the frontend with friendly messages (Req 3.3)
- Non-retriable errors must continue to crash to COLD with `clear_identity=True` and yield `CONVERSATION_ERROR` events (Req 3.4)
- The lifecycle manager's existing claude CLI orphan reaping must continue to work (Req 3.5)
- Normal STREAMING→IDLE transitions must continue to reset `_hooks_enqueued` and fire idle hooks after the grace period (Req 3.6)
- Conversations with no usage data (usage is None or empty) must continue to skip the context warning bridge (Req 3.1)

**Scope:**
All inputs that do NOT involve: (a) `ResultMessage` processing with usage data, (b) Hypothesis test configuration, or (c) orphan process detection should be completely unaffected by this fix. This includes:
- All message types other than `ResultMessage` in `_read_formatted_response()`
- Mouse/keyboard interactions in the frontend
- Session creation, slot management, and eviction logic
- Permission request handling (`WAITING_INPUT` state)
- The compact, interrupt, and health_check methods

## Hypothesized Root Cause

Based on the bug description and code review of `session_unit.py`:

1. **Undefined Variable Reference (Bug 1)**: The context warning bridge at line ~887 of `_read_formatted_response()` contains `if input_tokens and input_tokens > 0 and options:`. The variable `options` is a parameter of `send()` (type `ClaudeAgentOptions`) but is never passed to `_stream_response()` or `_read_formatted_response()`. Python evaluates the entire `and` chain left-to-right; when `input_tokens > 0` is `True`, it evaluates `options` which raises `NameError`. This is OUTSIDE the `try/except Exception: pass` block that wraps the warning builder call, so the exception propagates uncaught.

2. **Structural Scope Gap**: The call chain is `send(options=...) → _stream_response(query_content) → _read_formatted_response()`. Neither intermediate method accepts or forwards `options`. The context warning bridge needs only the model name (e.g., `"claude-sonnet-4-20250514"`) to look up the context window size, not the full `options` object.

3. **No Hypothesis Deadline (Bug 2)**: Property-based tests using Hypothesis have no `@settings(deadline=...)` decorator. When Hypothesis finds a failing example, it enters a shrinking phase that can run indefinitely without a deadline, creating zombie processes.

4. **Narrow Reaper Pattern (Bug 3)**: `_reap_orphans()` uses `pgrep -f "claude_agent_sdk/_bundled/claude"` which only matches Claude CLI processes. Stale `python main.py` dev backend processes are invisible to this pattern.

5. **Cascading Orphans (Bug 4)**: Bug 1 causes every turn to crash, which calls `_crash_to_cold()` → `_force_kill()` → `os.kill(pid, SIGKILL)`. This kills the direct child but may not kill grandchildren (MCP subprocesses spawned by the Claude CLI). Over time, these accumulate.

## Correctness Properties

Property 1: Bug Condition — NameError Structurally Eliminated

_For any_ `ResultMessage` where `usage.input_tokens > 0`, the fixed `_read_formatted_response()` SHALL access the model name via `self._model_name` (an instance attribute set during `send()`) instead of referencing the undefined `options` variable, and SHALL complete the context warning bridge without raising `NameError`, transitioning STREAMING→IDLE normally.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — Non-Usage ResultMessages Unaffected

_For any_ `ResultMessage` where usage is `None` or `input_tokens` is `None` or `0`, the fixed `_read_formatted_response()` SHALL skip the context warning bridge entirely and transition STREAMING→IDLE, producing the same behavior as the original code for these inputs.

**Validates: Requirements 3.1, 3.6, 3.7**

Property 3: Bug Condition — Orphan Reaper Catches Python Backend Processes

_For any_ orphaned process matching `python main.py` with ppid=1 on a known port (e.g., 8000), the fixed `_reap_orphans()` SHALL detect and kill it during startup, in addition to the existing claude CLI process reaping.

**Validates: Requirements 2.6**

Property 4: Preservation — Existing Claude CLI Reaping Unchanged

_For any_ orphaned `claude_agent_sdk/_bundled/claude` process not owned by a SessionUnit, the fixed `_reap_orphans()` SHALL continue to detect and kill it exactly as before, with no change to the existing reaping logic.

**Validates: Requirements 3.5**

Property 5: Bug Condition — Hypothesis Tests Have Deadline

_For any_ Hypothesis property-based test in the test suite, the test SHALL be configured with a `deadline` setting and appropriate `suppress_health_check` to prevent infinite shrinking loops.

**Validates: Requirements 2.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/session_unit.py`

**Change 1: Store model name on instance during `send()`**

**Function**: `send()`

**Specific Changes**:
1. After the spawn block and before `self._transition(SessionState.STREAMING)`, add:
   ```python
   self._model_name = getattr(options, "model", None)
   ```
2. Add `self._model_name: Optional[str] = None` to `__init__()` in the internal fields section
3. This makes the model name available to `_read_formatted_response()` via `self._model_name` without threading `options` through the call chain

**Change 2: Replace `options` reference with `self._model_name` in context warning bridge**

**Function**: `_read_formatted_response()` — the context warning bridge (~line 887)

**Current code**:
```python
if input_tokens and input_tokens > 0 and options:
    try:
        from .prompt_builder import PromptBuilder
        _pb = PromptBuilder.__new__(PromptBuilder)
        warning_evt = _pb.build_context_warning(
            input_tokens, getattr(options, "model", None)
        )
        if warning_evt and warning_evt.get("level") != "ok":
            yield warning_evt
    except Exception:
        pass
```

**Fixed code**:
```python
if input_tokens and input_tokens > 0:
    try:
        from .prompt_builder import PromptBuilder
        _pb = PromptBuilder.__new__(PromptBuilder)
        warning_evt = _pb.build_context_warning(
            input_tokens, self._model_name
        )
        if warning_evt and warning_evt.get("level") != "ok":
            yield warning_evt
    except Exception:
        pass
```

**Specific Changes**:
1. Remove `and options` from the `if` condition — the `options` variable doesn't exist in this scope
2. Replace `getattr(options, "model", None)` with `self._model_name`
3. The entire block is already inside `try/except Exception: pass` after this change, providing defense-in-depth

**Change 3: Move the `if ... and options:` check inside the try/except (defense-in-depth)**

Per the user's design principle, the `if input_tokens and input_tokens > 0` check should be inside the existing `try/except Exception: pass` block so that any future issues in the condition evaluation are caught. The fixed code in Change 2 already achieves this since removing `and options` eliminates the only source of `NameError`, and the `try/except` wraps the `PromptBuilder` call.

---

**File**: `backend/core/lifecycle_manager.py`

**Change 4: Broaden orphan reaper to catch stale Python backend processes**

**Function**: `_reap_orphans()`

**Specific Changes**:
1. After the existing claude CLI reaping block, add a second `pgrep` call:
   ```python
   # Also reap stale python main.py dev backend processes (Bug 3)
   result2 = await asyncio.to_thread(
       subprocess.run,
       ["pgrep", "-f", "python main.py"],
       capture_output=True, text=True, timeout=5,
   )
   ```
2. For each matched PID, check if ppid=1 (orphaned) before killing — avoid killing the current running backend
3. Also skip our own PID (`os.getpid()`) and any PID in `known_pids`

---

**File**: Hypothesis test configuration (e.g., `conftest.py` or individual test files)

**Change 5: Add Hypothesis deadline and health check settings**

**Specific Changes**:
1. Add a project-wide Hypothesis profile in `conftest.py`:
   ```python
   from hypothesis import settings, HealthCheck
   settings.register_profile(
       "default",
       deadline=5000,  # 5 second deadline per example
       suppress_health_check=[HealthCheck.too_slow],
   )
   settings.load_profile("default")
   ```
2. This prevents infinite shrinking loops and bounds test execution time

---

**File**: `backend/core/session_unit.py`

**Change 6: Clear `_model_name` in `_cleanup_internal()`**

**Function**: `_cleanup_internal()`

**Specific Changes**:
1. Add `self._model_name = None` to the cleanup to prevent stale model names across session reuse

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that simulate `ResultMessage` processing with usage data in `_read_formatted_response()`. Run these tests on the UNFIXED code to observe the `NameError`.

**Test Cases**:
1. **NameError Reproduction Test**: Create a `SessionUnit`, mock `_client.receive_response()` to yield a `ResultMessage` with `usage={"input_tokens": 1500}`. Call `_read_formatted_response()` and assert it raises `NameError`. (Will fail on unfixed code — confirms Bug 1)
2. **Retry Cascade Test**: Call `send()` with a mocked client that returns a `ResultMessage` with usage data. Assert that all 3 retries fail with the same `NameError` and the final event is `ALL_RETRIES_EXHAUSTED`. (Will fail on unfixed code — confirms retry cascade)
3. **No-Usage Path Test**: Mock a `ResultMessage` with `usage=None`. Assert `_read_formatted_response()` completes without error. (Should PASS on unfixed code — confirms the bug only triggers with usage data)
4. **Orphan Reaper Pattern Test**: Run `_reap_orphans()` with a mocked `pgrep` that returns PIDs for both `claude_agent_sdk/_bundled/claude` and `python main.py` processes. Assert only claude processes are killed. (Will pass on unfixed code — confirms Bug 3 gap)

**Expected Counterexamples**:
- `NameError: name 'options' is not defined` raised from `_read_formatted_response()`
- All 3 retry attempts failing with the same `NameError`
- `python main.py` orphan processes surviving the reaper

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _read_formatted_response_fixed(input)
  ASSERT no NameError raised
  ASSERT STREAMING→IDLE transition completed
  ASSERT context_warning event yielded if usage > 70% window
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _read_formatted_response_original(input) == _read_formatted_response_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of message types and usage values
- It catches edge cases like `input_tokens=0`, `usage={}`, `usage=None`
- It provides strong guarantees that non-ResultMessage processing is unchanged

**Test Plan**: Observe behavior on UNFIXED code first for `AssistantMessage`, `SystemMessage`, `StreamEvent` processing, then write property-based tests capturing that behavior.

**Test Cases**:
1. **AssistantMessage Preservation**: Verify that `AssistantMessage` with `TextBlock`, `ThinkingBlock`, `ToolUseBlock`, and `ToolResultBlock` content yields identical SSE events before and after the fix
2. **StreamEvent Preservation**: Verify that `content_block_delta`, `content_block_start`, `content_block_stop` events yield identical SSE events
3. **SystemMessage Preservation**: Verify that `init` and other system messages yield identical SSE events
4. **No-Usage ResultMessage Preservation**: Verify that `ResultMessage` with `usage=None` or `usage={}` yields the same result event and transitions STREAMING→IDLE

### Unit Tests

- Test that `self._model_name` is set during `send()` from `options.model`
- Test that `_read_formatted_response()` uses `self._model_name` instead of `options`
- Test that `ResultMessage` with `input_tokens > 0` completes without `NameError`
- Test that `ResultMessage` with `input_tokens > 0` and `self._model_name` set emits `context_warning` when usage > 70%
- Test that `ResultMessage` with `usage=None` skips the context warning bridge
- Test that `_cleanup_internal()` resets `self._model_name` to `None`
- Test that `_reap_orphans()` kills orphaned `python main.py` processes with ppid=1
- Test that `_reap_orphans()` does NOT kill non-orphaned `python main.py` processes

### Property-Based Tests

- Generate random `ResultMessage` usage dicts with varying `input_tokens` values (0, None, positive integers). Verify that the fixed `_read_formatted_response()` never raises `NameError` and always transitions STREAMING→IDLE (Property 1).
- Generate random message sequences (mix of `AssistantMessage`, `SystemMessage`, `StreamEvent`, `ResultMessage`) and verify the fixed code produces identical SSE events as the original for all non-ResultMessage types (Property 2).
- Generate random process lists with varying cmdlines and ppids. Verify the fixed `_reap_orphans()` kills all orphaned claude CLI AND python main.py processes while preserving non-orphaned processes (Properties 3, 4).

### Integration Tests

- Test full conversation turn: `send()` → `_stream_response()` → `_read_formatted_response()` with a mocked SDK client that yields `AssistantMessage` then `ResultMessage` with usage data. Verify the response completes, context warning is emitted if applicable, and state transitions STREAMING→IDLE.
- Test retry recovery: After fixing Bug 1, verify that genuinely retriable errors (e.g., "Cannot write to terminated process") still trigger the retry loop and succeed on retry.
- Test lifecycle manager startup: Verify `_reap_orphans()` kills both claude CLI orphans and python main.py orphans in a single startup pass.
