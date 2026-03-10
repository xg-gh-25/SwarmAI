# Bugfix Requirements Document

## Introduction

During `--resume`, the Claude SDK replays old messages from previous conversation turns before processing the new query. The message loop in `_run_query_on_client()` exits on the first `ResultMessage` it encounters, returning stale results from a previous turn to the frontend instead of waiting for the fresh result from the current query. This causes the user to see the wrong response, and the actual work (tool calls, etc.) happens after the SSE stream has already closed — making the real result invisible until the next interaction.

The existing stale-result detection logic (lines ~1959-2024) attempts to handle this but has a race condition in the recovery flow: the old SDK reader task may still be producing messages when the queue is drained, and the `asyncio.Queue.empty()` check is unreliable with concurrent producers. Additionally, after retry the SDK may replay the same old messages again, causing the heuristic to match a second time and waste the retry budget without ever reaching the fresh result.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a session is resumed (`is_resuming=True`) and the SDK replays old messages including a `ResultMessage` from the previous turn THEN the message loop exits on that stale `ResultMessage`, yielding the old response text to the frontend and closing the SSE stream before the new query's actual work begins

1.2 WHEN the stale-result detection fires and cancels the old SDK reader task, drains the queue, and re-sends the query THEN messages from the old SDK stream may still arrive in the queue between the drain and the new reader starting (race condition with `asyncio.Queue.empty()` and concurrent producer), causing the new stream to process leftover stale messages

1.3 WHEN the SDK replays the same old messages on the retry stream (second `client.query()` call) THEN the stale detection heuristic (`not _saw_tool_use` and `_num_turns <= 1`) matches again, consuming a retry attempt without making progress, and if `_stale_retry_count` reaches `_MAX_STALE_RETRIES` the next stale `ResultMessage` is treated as fresh and yielded to the frontend

1.4 WHEN the SSE stream closes prematurely due to a stale result being accepted THEN any subsequent tool calls and the real response from the SDK execute silently in the background with no way to deliver them to the frontend, causing the user to see the correct response one turn late (on their next message)

### Expected Behavior (Correct)

2.1 WHEN a session is resumed and the SDK replays old messages including a `ResultMessage` from a previous turn THEN the system SHALL reliably identify and discard all stale `ResultMessage`s, and SHALL only yield the `ResultMessage` produced by the current query to the frontend

2.2 WHEN the stale-result recovery flow re-sends the query THEN the system SHALL ensure the old SDK reader task is fully terminated before starting the new reader (since the SDK client does not support concurrent `receive_response()` iterators), and SHALL use generation-based filtering to discard any residual items from the old reader that were already in the queue, eliminating the race condition without relying on `asyncio.Queue.empty()`

2.3 WHEN the SDK replays old messages on a retry stream THEN the system SHALL not waste retry attempts on repeated replays, and SHALL use a generation counter to positively identify which SDK reader produced each message, filtering stale items regardless of how many times the SDK replays old content. The total number of retries SHALL be bounded by `_MAX_STALE_RETRIES` to prevent infinite retry loops.

2.4 WHEN the system detects and discards a stale result THEN the SSE stream SHALL remain open and the message loop SHALL continue processing until the fresh `ResultMessage` for the current query arrives, ensuring the frontend always receives the correct response in the same turn it was requested

2.5 WHEN the previous turn involved tool use and the SDK replays `ToolUseBlock`s before the first `ResultMessage` during resume THEN the system SHALL accept the `ResultMessage` as potentially valid (since replayed tool use is indistinguishable from fresh tool use at the heuristic level), and the user can re-send their query if the result is stale. This is an acceptable trade-off to avoid discarding valid results.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a new (non-resume) session sends a query and receives a `ResultMessage` THEN the system SHALL CONTINUE TO yield the result to the frontend and close the SSE stream normally

3.2 WHEN a `ResultMessage` with `subtype='error_during_execution'` or `is_error=True` arrives (in either resume or non-resume sessions) THEN the system SHALL CONTINUE TO handle it as an error event, yielding the appropriate error to the frontend

3.3 WHEN the message loop receives `AssistantMessage`s with `ToolUseBlock`s followed by a `ResultMessage` during a resume THEN the system SHALL CONTINUE TO treat the result as fresh (since tool use indicates real work was done) and yield it normally

3.4 WHEN the message loop receives permission requests or `ask_user_question` events THEN the system SHALL CONTINUE TO forward them to the frontend and pause/return as appropriate, regardless of whether the session is resumed

3.5 WHEN the message loop completes normally THEN the system SHALL CONTINUE TO persist the assistant content to the database, emit the `result` SSE event with usage metrics, and clean up background tasks in the `finally` block
