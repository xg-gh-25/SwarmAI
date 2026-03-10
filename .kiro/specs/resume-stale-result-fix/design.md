<!-- PE-REVIEWED -->
# Resume Stale Result Fix — Bugfix Design

## Overview

During `--resume`, the Claude SDK replays old messages from previous turns before processing the new query. The message loop in `_run_query_on_client()` exits on the first `ResultMessage` it encounters, returning stale results from a previous turn. The existing stale-result detection has three interrelated flaws: a race condition in the queue drain, wasted retries on repeated replays, and no positive identification of fresh results.

The fix replaces the current "detect → cancel → drain → retry" approach with a **generation counter** pattern. Each SDK reader task is assigned a monotonically increasing generation number. Queue items are tagged with their generation. The main loop ignores items from old generations. This eliminates the race condition entirely — no queue draining needed, no cancellation timing issues, no wasted retries.

## Glossary

- **Bug_Condition (C)**: A `ResultMessage` arrives during a resume session from an SDK reader whose generation is older than the current generation — i.e., it is a replayed result from a previous turn, not the response to the current query.
- **Property (P)**: The system discards all stale `ResultMessage`s and only yields the `ResultMessage` produced by the current-generation SDK reader to the frontend.
- **Preservation**: All non-resume sessions, error handling, permission forwarding, tool-use tracking, DB persistence, and SSE event emission continue to work identically.
- **`_run_query_on_client()`**: The async generator method in `AgentManager` (line ~1729 of `agent_manager.py`) that sends a query to the Claude SDK client and yields SSE events to the frontend.
- **`sdk_message_reader()`**: The inner async task that drains `client.receive_response()` into the `combined_queue`.
- **Generation**: A monotonically increasing integer assigned to each SDK reader task. Items in the `combined_queue` are tagged with the generation of the reader that produced them.
- **`combined_queue`**: The `asyncio.Queue` that merges SDK messages and permission requests into a single consumer stream.

## Bug Details

### Fault Condition

The bug manifests when a session is resumed (`is_resuming=True`) and the SDK replays old messages including a `ResultMessage` from a previous turn. The current stale-detection logic attempts to catch this but fails due to three compounding issues: (1) the queue drain races with the old SDK reader, (2) retries are wasted on repeated replays, and (3) there is no positive signal to identify fresh results.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type QueueItem (containing a message and session context)
  OUTPUT: boolean

  RETURN input.session.is_resuming == True
         AND input.message IS ResultMessage
         AND input.message.is_error == False
         AND input.message.subtype != 'error_during_execution'
         AND input.reader_generation < current_generation
END FUNCTION
```

Note: In the current (unfixed) code, there is no `reader_generation` concept. The bug condition is that the code lacks a reliable mechanism to distinguish stale from fresh `ResultMessage`s. The heuristic (`not _saw_tool_use AND _num_turns <= 1`) is necessary but insufficient — it fails when retries replay the same messages, and the queue drain races with the old reader.

### Examples

- **Example 1 (basic stale result)**: User resumes a session and sends "refactor the auth module". The SDK replays the previous turn's `ResultMessage` with `result="I've updated the README"` and `num_turns=1`. The loop exits immediately, showing the old response. The actual refactoring work happens invisibly in the background.

- **Example 2 (race condition)**: Stale detection fires, cancels the old reader, drains the queue. But the old reader pushes one more message between `combined_queue.empty()` returning `True` and the new reader starting. The stale message from the old stream is consumed by the main loop as if it came from the new stream.

- **Example 3 (wasted retry)**: After the first retry, the SDK replays the same old messages again. The heuristic matches a second time (`_stale_retry_count` goes from 1 to 2). On the third replay, `_stale_retry_count == _MAX_STALE_RETRIES`, so the stale `ResultMessage` is accepted as fresh.

- **Example 4 (edge case — tool use in previous turn)**: The previous turn involved tool use, so the replayed stream includes `ToolUseBlock`s. With the generation counter fix, these replayed `ToolUseBlock`s arrive tagged with generation 0. When the first stale `ResultMessage` (also gen 0) triggers the heuristic, the generation is bumped to 1. All subsequent gen-0 items — including the replayed `ToolUseBlock`s — are filtered out. The `_saw_tool_use` flag is reset on each generation bump, so replayed tool use from previous turns cannot poison the heuristic for the new generation. **Note**: The very first `ResultMessage` (gen 0, current gen 0) still relies on the heuristic (`not _saw_tool_use AND _num_turns <= 1`). If the replayed stream delivers `ToolUseBlock`s BEFORE the first `ResultMessage`, `_saw_tool_use` will be `True` and the heuristic won't fire. This is addressed by the two-layer detection model described in the Fix Implementation section.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Non-resume sessions (`is_resuming=False`) must yield `ResultMessage`s to the frontend exactly as before — no generation tagging or filtering applies.
- `ResultMessage` with `subtype='error_during_execution'` or `is_error=True` must continue to be handled as error events in both resume and non-resume sessions.
- `AssistantMessage` processing, `ToolUseBlock` tracking, `TextBlock` rendering, and `_format_message` dispatch must remain unchanged.
- Permission request forwarding via `permission_request_forwarder()` must continue to work — permission items are not tagged with a generation and are always processed.
- `SystemMessage` handling (init, task_started) must remain unchanged.
- `ask_user_question` and `cmd_permission_request` early-return paths must continue to persist assistant content and return.
- DB persistence of assistant content on conversation completion must remain unchanged.
- The `finally` block cleanup of background tasks must remain unchanged.
- SSE event structure (`session_start`, `assistant`, `result`, `context_compacted`, `agent_activity`) must remain unchanged.

**Scope:**
All inputs where `is_resuming=False`, or where the `ResultMessage` is an error, are completely unaffected by this fix. The fix only changes behavior for non-error `ResultMessage`s during resume sessions.

## Hypothesized Root Cause

Based on the bug description and code analysis, the three root causes are:

1. **Race condition in queue drain (lines ~1988-1993)**: After cancelling the old `sdk_reader_task`, the code calls `combined_queue.get_nowait()` in a loop until `combined_queue.empty()` returns `True`. But `asyncio.Queue.empty()` is unreliable with concurrent producers — the old reader's `finally` block or a pending `await combined_queue.put()` can push items after the drain completes. The new reader then starts, and the main loop processes these leftover stale items as if they came from the new stream.

2. **Wasted retries on repeated replays (lines ~1959-2010)**: After re-sending the query via `client.query()`, the SDK may replay the same old messages again before processing the new query. The heuristic (`not _saw_tool_use AND _num_turns <= 1`) matches again, incrementing `_stale_retry_count` without making progress. With `_MAX_STALE_RETRIES=2`, only two retries are allowed, and the third stale result is accepted as fresh.

3. **No positive identification of fresh results**: The heuristic uses negative signals (no tool_use, low num_turns) to guess staleness. There is no mechanism to positively identify that a `ResultMessage` belongs to the current query. This means any `ResultMessage` that happens to have `num_turns > 1` or follows a replayed `ToolUseBlock` will be incorrectly accepted as fresh.

## Correctness Properties

Property 1: Fault Condition — Stale ResultMessages Are Discarded During Resume

_For any_ queue item where `is_resuming` is `True` and the item contains a non-error `ResultMessage` from an SDK reader whose generation is less than the current generation, the fixed `_run_query_on_client()` SHALL discard that item (not yield it to the frontend) and SHALL continue processing the queue until a `ResultMessage` from the current-generation reader arrives.

**Validates: Requirements 2.1, 2.3, 2.4**

Property 2: Preservation — Non-Resume and Error ResultMessages Are Unaffected

_For any_ input where `is_resuming` is `False`, or where the `ResultMessage` has `is_error=True` or `subtype='error_during_execution'`, the fixed `_run_query_on_client()` SHALL produce exactly the same SSE events as the original function, preserving all existing behavior for non-resume sessions and error handling.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Retry Budget Safety Net — Stale Retries Are Bounded

_For any_ resume session, the number of stale-detection retries (generation bumps + query re-sends) SHALL NOT exceed `_MAX_STALE_RETRIES`. When the retry budget is exhausted, the next `ResultMessage` from the current-generation reader SHALL be accepted as-is and yielded to the frontend, preventing infinite retry loops.

**Validates: Requirement 2.3 (bounded retries)**

Property 4: Two-Layer Detection — First ResultMessage Uses Heuristic, Subsequent Use Generation

_For any_ resume session, the FIRST non-error `ResultMessage` at generation 0 (where `item["gen"] == _generation`) SHALL be evaluated by the stale heuristic (`not _saw_tool_use AND _num_turns <= 1`). If the heuristic identifies it as stale, the generation is bumped and subsequent stale items are filtered by generation. This two-layer model ensures that even the initial stale result (before any generation bump) is caught.

**Validates: Requirements 2.1, 2.3**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**Function**: `_run_query_on_client()` (line ~1729)

**Specific Changes**:

1. **Add generation counter state**: Before the main loop, initialize `_generation = 0`. This counter tracks which SDK reader task is "current".

2. **Tag queue items with generation**: Modify `sdk_message_reader()` to capture the current `_generation` value at task creation time and include it in every item pushed to `combined_queue`:
   ```python
   async def sdk_message_reader(gen: int):
       try:
           async for message in client.receive_response():
               await combined_queue.put({"source": "sdk", "message": message, "gen": gen})
       except Exception as e:
           ...
           await combined_queue.put({"source": "error", "error": str(e), "detail": error_traceback, "gen": gen})
       finally:
           await combined_queue.put({"source": "sdk_done", "gen": gen})
   ```

3. **Filter stale items in the main loop**: At the top of the `while True` loop, after `item = await combined_queue.get()`, add a generation check for SDK-sourced items:
   ```python
   if item.get("gen") is not None and item["gen"] < _generation:
       continue  # Discard item from an old reader generation
   ```
   Permission items (which have no `gen` key) pass through unconditionally. Note: this filter applies to ALL SDK-sourced item types including `sdk_done` and `error` sentinels. An `sdk_done` from an old generation is correctly discarded — the main loop only breaks on `sdk_done` from the current generation. This is critical: if an old-generation `sdk_done` were processed, the loop would exit before the current reader delivers its result.

4. **Replace the stale-detection + drain + retry block**: When a non-error `ResultMessage` arrives during resume with no `_saw_tool_use` and `_num_turns <= 1`:
   - Increment `_generation`.
   - Do NOT cancel the old reader task or drain the queue — old items will be filtered by generation.
   - Re-send the query via `client.query()`.
   - Start a new `sdk_message_reader(_generation)` task.
   - Reset `_saw_tool_use`, `_saw_new_text_block`, `message_count`, and `assistant_content`.
   - `continue` back to the main loop.
   - Note: `_saw_tool_use` is effectively scoped to the current generation because (a) it is reset on each generation bump, and (b) old-generation `AssistantMessage` items (which would set `_saw_tool_use`) are filtered out by the generation check before reaching the tracking code. This means replayed `ToolUseBlock`s from previous turns cannot set `_saw_tool_use` for the new generation.

5. **Cancel old reader before starting new one**: Although the generation filter makes old items harmless, the old SDK reader task MUST still be cancelled before re-sending the query. This is because the Claude SDK client does NOT support multiple concurrent `receive_response()` iterators — the old reader holds an open iterator on the client's response stream, and starting a new `receive_response()` while the old one is active may cause undefined behavior or block. The cancellation sequence is:
   - Cancel the old reader task and `await` it (to ensure it's fully stopped)
   - THEN re-send the query via `client.query()`
   - THEN start the new `sdk_message_reader(_generation)` task
   - Any items the old reader pushed to the queue before cancellation are harmlessly filtered by generation
   - This is different from the current code's approach: we cancel the reader but do NOT drain the queue (generation filtering handles stale items)

6. **Update finally block**: Track all spawned reader tasks (not just the latest) so the `finally` block can cancel all of them. Use a list `_reader_tasks: list[asyncio.Task]` and append each new task. In `finally`, cancel all tasks in the list. Note: in practice, only the current-generation reader should be alive at `finally` time (old readers are cancelled in step 5), but the list provides defense-in-depth.

7. **Keep retry budget**: Retain `_MAX_STALE_RETRIES` and `_stale_retry_count` as a safety net to prevent infinite retry loops. But now each retry is effective because old messages are filtered by generation rather than by unreliable queue draining.

8. **Two-layer stale detection model**: The fix uses two complementary mechanisms:
   - **Layer 1 — Heuristic (first stale result)**: When the first `ResultMessage` arrives at generation 0 during resume, the generation check passes (`gen == _generation`), so the heuristic (`not _saw_tool_use AND _num_turns <= 1`) evaluates it. If stale, the generation is bumped and a retry is initiated.
   - **Layer 2 — Generation filter (subsequent stale items)**: After a generation bump, ALL items from old generations are silently discarded before reaching any dispatch logic. This handles the race condition and repeated replays.
   - **Edge case — replayed tool use before first ResultMessage**: If the SDK replays `ToolUseBlock`s from the previous turn before the first `ResultMessage`, `_saw_tool_use` becomes `True` and the heuristic won't fire. In this case, the stale `ResultMessage` is accepted. This is an acceptable trade-off: if the SDK replayed tool use blocks, the result likely corresponds to real work from the previous turn, and accepting it is safer than discarding a potentially valid result. The user can re-send their query if needed.

9. **SystemMessage handling during retry**: When a retry stream starts, the SDK may send a new `SystemMessage(init)` with the same session ID. This is harmless — `session_context["sdk_session_id"]` is overwritten with the same value. The `SystemMessage` processing code already handles the `is_resuming` case by skipping session bootstrap (no duplicate `session_start` event or user message save). Old-generation `SystemMessage` items are filtered by the generation check and never reach the dispatch logic.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that mock the SDK client to replay old messages (including a stale `ResultMessage`) before delivering fresh messages. Run these tests on the UNFIXED code to observe failures and understand the root cause.

**Test Cases**:
1. **Basic Stale Result Test**: Mock `client.receive_response()` to yield `[SystemMessage(init), ResultMessage(stale, num_turns=1), AssistantMessage(fresh), ResultMessage(fresh)]`. Assert the yielded SSE events contain the fresh result, not the stale one. (Will fail on unfixed code — the loop exits on the first `ResultMessage`.)
2. **Race Condition Test**: Mock two overlapping SDK reader streams where the old reader pushes a message after the queue drain. Assert the stale message is not yielded. (Will fail on unfixed code — the drained message arrives after the drain.)
3. **Repeated Replay Test**: Mock the SDK to replay the same stale messages on retry. Assert the system does not exhaust retries and eventually yields the fresh result. (Will fail on unfixed code — retries are wasted.)
4. **Tool Use in Previous Turn Test**: Mock a replayed stream that includes `ToolUseBlock`s from the previous turn followed by a stale `ResultMessage`. Assert the stale result is not accepted as fresh. (May fail on unfixed code — `_saw_tool_use` becomes `True`.)

**Expected Counterexamples**:
- The first `ResultMessage` in a resume stream is yielded to the frontend even though it's stale.
- After retry, the same stale `ResultMessage` is yielded because `_stale_retry_count` reaches `_MAX_STALE_RETRIES`.
- Possible causes: no generation tagging, unreliable queue drain, heuristic based on negative signals only.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _run_query_on_client_fixed(input)
  ASSERT result contains only fresh ResultMessage (from current generation)
  ASSERT stale ResultMessages are not yielded as SSE events
  ASSERT SSE stream remains open until fresh result arrives
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _run_query_on_client_original(input) == _run_query_on_client_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (varying `is_error`, `subtype`, `is_resuming`, `num_turns`, message sequences)
- It catches edge cases that manual unit tests might miss (e.g., error results during resume, tool use with low num_turns)
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for non-resume sessions and error results, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Non-Resume Session Preservation**: Verify that new sessions (`is_resuming=False`) yield `ResultMessage`s to the frontend exactly as before, with no generation filtering applied.
2. **Error Result Preservation**: Verify that `ResultMessage` with `is_error=True` or `subtype='error_during_execution'` continues to yield error SSE events in both resume and non-resume sessions.
3. **Permission Forwarding Preservation**: Verify that permission requests continue to be forwarded to the frontend regardless of generation or resume state.
4. **Tool Use Tracking Preservation**: Verify that `_saw_tool_use` tracking on `AssistantMessage` with `ToolUseBlock`s continues to work, and that results following real tool use are accepted as fresh.
5. **DB Persistence Preservation**: Verify that assistant content is persisted to the DB on conversation completion, `ask_user_question`, and `cmd_permission_request` early returns.

### Unit Tests

- Test generation counter increments correctly on stale detection
- Test queue items from old generations are silently discarded
- Test permission items (no `gen` key) are always processed
- Test `_MAX_STALE_RETRIES` safety net prevents infinite retry loops
- Test error `ResultMessage`s bypass stale detection entirely
- Test `finally` block cancels all spawned reader tasks

### Property-Based Tests

- Generate random message sequences (varying counts of `AssistantMessage`, `ToolUseBlock`, `ResultMessage`) and verify that during resume, only the last-generation `ResultMessage` is yielded
- Generate random `is_resuming` / `is_error` / `subtype` combinations and verify preservation: non-resume and error inputs produce identical SSE events before and after the fix
- Generate random interleaving of SDK messages and permission requests and verify permission forwarding is unaffected by generation filtering

### Integration Tests

- Test full resume flow: create a session, send a query, resume the session, verify the fresh result is returned
- Test resume with tool use: verify that tool calls in the fresh stream are processed correctly
- Test resume with error: verify that SDK errors during resume are handled correctly
- Test resume with permission request: verify that permission requests during resume are forwarded correctly
