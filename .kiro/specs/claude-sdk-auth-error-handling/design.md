# Claude SDK Auth Error Handling Bugfix Design

## Overview

The Claude Agent SDK returns `ResultMessage` objects with `is_error=True` for authentication failures (e.g., "Not logged in · Please run /login"), but the current `_run_query_on_client` method in `agent_manager.py` only checks for `subtype == 'error_during_execution'`. This causes auth error text to be yielded as a normal `assistant` SSE event, misleading the user into thinking the error message is a valid response. The failed session is also stored in `_active_sessions`, wasting resources.

The fix adds `is_error` detection to the `ResultMessage` handling path, classifies auth-specific errors with user-friendly messages, and prevents failed sessions from being stored. An optional pre-flight auth check in `_configure_claude_environment()` provides early failure before the SDK round-trip.

## Glossary

- **Bug_Condition (C)**: A `ResultMessage` with `is_error=True` that is not already caught by the `subtype == 'error_during_execution'` check
- **Property (P)**: Such messages should be yielded as SSE `error` events (not `assistant` events), and the session should not be stored in `_active_sessions`
- **Preservation**: All existing behavior for `is_error=False` messages, `error_during_execution` subtype handling, normal session storage, and Bedrock auth flows must remain unchanged
- **`_run_query_on_client`**: The method in `backend/core/agent_manager.py` that processes SDK messages and yields SSE events to the frontend
- **`_configure_claude_environment`**: The function in `backend/core/claude_environment.py` that reads API settings and sets environment variables before creating a `ClaudeSDKClient`
- **`_active_sessions`**: A dict on `AgentManager` that stores long-lived `ClaudeSDKClient` instances keyed by session ID for reuse across HTTP requests
- **`ResultMessage`**: A Claude Agent SDK message type that carries final result text, error flags (`is_error`, `subtype`), and usage metrics (`total_cost_usd`, `duration_ms`)

## Bug Details

### Fault Condition

The bug manifests when the Claude SDK returns a `ResultMessage` with `is_error=True` and the result text contains an authentication error. The `_run_query_on_client` method checks only `message.subtype == 'error_during_execution'` but does not check `message.is_error == True`, so the error text falls through to the normal `result_text` yield path and is emitted as an `assistant` message.

**Formal Specification:**
```
FUNCTION isBugCondition(message)
  INPUT: message of type ResultMessage
  OUTPUT: boolean

  RETURN message.is_error == True
         AND message.subtype != 'error_during_execution'
END FUNCTION
```

A secondary bug condition exists at the pre-flight level:

```
FUNCTION isMissingAuthCondition(api_settings, env)
  INPUT: api_settings from database, env from os.environ
  OUTPUT: boolean

  has_api_key := api_settings.get("anthropic_api_key") OR env.get("ANTHROPIC_API_KEY")
  use_bedrock := api_settings.get("use_bedrock", False)

  RETURN NOT has_api_key AND NOT use_bedrock
END FUNCTION
```

### Examples

- User sends a chat message with no API key configured → SDK returns `ResultMessage(is_error=True, result="Not logged in · Please run /login", subtype=None, total_cost_usd=0)` → Currently yielded as `{"type": "assistant", "content": [{"type": "text", "text": "Not logged in · Please run /login"}]}` → Should be `{"type": "error", "error": "Authentication failed. Please configure your API key in Settings."}`
- User sends a chat message with an expired/invalid API key → SDK returns `ResultMessage(is_error=True, result="Invalid API key", subtype=None)` → Currently yielded as a normal assistant message → Should be yielded as an error event
- User sends a chat message with a valid API key → SDK returns `ResultMessage(is_error=False, result="Here is the answer...")` → Correctly yielded as assistant message → Must remain unchanged
- SDK encounters a runtime error during tool execution → `ResultMessage(subtype='error_during_execution', result="Tool failed")` → Already handled correctly as error event → Must remain unchanged


## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Normal `ResultMessage` with `is_error=False` and non-empty `result` text must continue to be yielded as `assistant` SSE events and persisted to the database
- `ResultMessage` with `subtype='error_during_execution'` must continue to be handled as error events with session cleanup (existing code path)
- Successful conversations must continue to store sessions in `_active_sessions` for future resume calls
- Bedrock authentication flows (credentials or bearer token) must continue to work without requiring an `ANTHROPIC_API_KEY`
- All non-ResultMessage SDK message types (AssistantMessage, ToolUseMessage, SystemMessage, etc.) must continue to be processed identically
- The `result` SSE event at conversation end (with `duration_ms`, `total_cost_usd`, `num_turns`) must continue to be emitted for all conversations

**Scope:**
All inputs where `message.is_error` is `False` (or not set) should be completely unaffected by this fix. This includes:
- Normal successful conversations
- Tool use and tool result messages
- Permission request flows
- Ask-user-question flows
- Slash command handling
- Session resume via `_active_sessions`

## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is clear:

1. **Missing `is_error` Check in ResultMessage Handling**: The `_run_query_on_client` method at line ~1065 checks `message.subtype == 'error_during_execution'` but does not check `message.is_error == True`. The Claude SDK uses `is_error=True` as the primary error flag on `ResultMessage`, while `subtype='error_during_execution'` is a specific sub-category. Auth failures set `is_error=True` but do NOT set `subtype='error_during_execution'`, so they fall through to the normal result text path.

2. **No Auth-Specific Error Classification**: Even if `is_error` were checked, there is no logic to distinguish authentication errors from other error types (rate limiting, server errors). Auth errors need a specific user-friendly message directing users to the Settings page.

3. **Session Stored Despite Error**: In `_execute_on_session` (line ~904), after `_run_query_on_client` completes, the session is unconditionally stored in `_active_sessions` if `final_session_id` is set. There is no check for whether the session ended in an error state, so failed auth sessions pollute the reuse pool.

4. **No Pre-Flight Auth Validation**: `_configure_claude_environment()` sets environment variables but does not validate that at least one auth method is configured. The missing auth is only discovered after the full SDK round-trip, adding unnecessary latency.

## Correctness Properties

Property 1: Fault Condition - Error ResultMessages Yield Error SSE Events

_For any_ `ResultMessage` where `is_error == True` and `subtype != 'error_during_execution'`, the fixed `_run_query_on_client` method SHALL yield an SSE event with `type: "error"` containing the error text, and SHALL NOT yield the result text as a `type: "assistant"` event.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Non-Error ResultMessages Unchanged

_For any_ `ResultMessage` where `is_error == False` (or `is_error` is not set) and `result` text is non-empty, the fixed `_run_query_on_client` method SHALL produce the same SSE events as the original function, preserving the `type: "assistant"` yield and content accumulation behavior.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**Function**: `_run_query_on_client`

**Specific Changes**:

1. **Add `is_error` Check Before Result Text Yield**: After the existing `subtype == 'error_during_execution'` block and before the `result_text` yield, add a check for `message.is_error == True`. If true, classify the error and yield as an `error` SSE event instead of an `assistant` event.

2. **Auth Error Detection**: Check if the error text matches known auth patterns (e.g., contains "not logged in", "please run /login", "invalid api key", "authentication"). Yield a user-friendly message: `"Authentication failed. Please configure your API key in Settings or run /login."`.

3. **General Error Fallback**: For `is_error=True` messages that don't match auth patterns, yield the raw error text as a `type: "error"` event.

4. **Skip Content Accumulation on Error**: When `is_error=True`, do not call `assistant_content.add()` with the error text, since it should not be persisted as an assistant message.

5. **Signal Error State to Caller**: Set a flag in `session_context` (e.g., `session_context["had_error"] = True`) so `_execute_on_session` can skip storing the session in `_active_sessions`.

**File**: `backend/core/agent_manager.py`

**Function**: `_execute_on_session`

**Specific Changes**:

6. **Conditional Session Storage**: After the `_run_query_on_client` loop, check `session_context.get("had_error")` before storing in `_active_sessions`. If an error occurred, disconnect the wrapper instead of storing.

**File**: `backend/core/claude_environment.py`

**Function**: `_configure_claude_environment`

**Specific Changes**:

7. **Pre-Flight Auth Validation (Optional Enhancement)**: After reading settings, check if at least one auth method is configured (API key or Bedrock). If not, raise a specific exception (e.g., `AuthenticationNotConfiguredError`) that `_execute_on_session` can catch and yield as an error event before creating the `ClaudeSDKClient`. This avoids the SDK round-trip entirely.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that construct mock `ResultMessage` objects with `is_error=True` and various `subtype` values, then pass them through the `_run_query_on_client` message processing logic. Run these tests on the UNFIXED code to observe that auth errors are yielded as `assistant` events.

**Test Cases**:
1. **Auth Error as Assistant Message**: Create `ResultMessage(is_error=True, result="Not logged in · Please run /login", subtype=None)` and verify it is yielded as `type: "assistant"` (will demonstrate bug on unfixed code)
2. **General is_error as Assistant Message**: Create `ResultMessage(is_error=True, result="Rate limit exceeded", subtype=None)` and verify it is yielded as `type: "assistant"` (will demonstrate bug on unfixed code)
3. **Error Session Stored**: Verify that after an `is_error=True` ResultMessage, the session is stored in `_active_sessions` (will demonstrate bug on unfixed code)
4. **No Pre-Flight Check**: Call `_execute_on_session` with no API key and no Bedrock configured, verify it proceeds to create a client (will demonstrate bug on unfixed code)

**Expected Counterexamples**:
- Auth error text "Not logged in · Please run /login" appears as a normal assistant message in the SSE stream
- Possible causes: missing `is_error` check in ResultMessage handling path

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL message WHERE isBugCondition(message) DO
  events := collect(_run_query_on_client_fixed(message))
  ASSERT no event has type == "assistant" with error text
  ASSERT at least one event has type == "error"
  ASSERT session NOT in _active_sessions
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL message WHERE NOT isBugCondition(message) DO
  ASSERT _run_query_on_client_original(message) == _run_query_on_client_fixed(message)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for normal `ResultMessage` objects with `is_error=False`, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Normal Result Preservation**: Verify `ResultMessage(is_error=False, result="Hello world")` continues to yield `type: "assistant"` with the result text
2. **Error During Execution Preservation**: Verify `ResultMessage(subtype='error_during_execution', result="Tool failed")` continues to yield `type: "error"` and clean up session
3. **Session Storage Preservation**: Verify successful conversations continue to store sessions in `_active_sessions`
4. **Bedrock Auth Preservation**: Verify Bedrock-configured environments continue to work without `ANTHROPIC_API_KEY`

### Unit Tests

- Test `is_error=True` with auth error patterns yields `type: "error"` with user-friendly message
- Test `is_error=True` with non-auth error text yields `type: "error"` with raw error text
- Test `is_error=False` with result text yields `type: "assistant"` (unchanged)
- Test `subtype='error_during_execution'` continues to work as before
- Test session is not stored in `_active_sessions` when `is_error=True`
- Test pre-flight auth validation raises error when no auth configured
- Test pre-flight auth validation passes when Bedrock is configured (no API key needed)

### Property-Based Tests

- Generate random `ResultMessage` objects with `is_error=False` and arbitrary result text, verify all yield `type: "assistant"` events (preservation)
- Generate random `ResultMessage` objects with `is_error=True` and random error text, verify none yield `type: "assistant"` events (fix checking)
- Generate random auth configuration combinations (API key present/absent, Bedrock on/off), verify pre-flight validation correctly identifies missing auth

### Integration Tests

- Test full chat flow with no API key configured: user sends message → receives error event → no session stored
- Test full chat flow with valid API key: user sends message → receives assistant response → session stored (preservation)
- Test that frontend correctly renders error events from auth failures as error UI, not as chat messages
