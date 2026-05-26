# Bugfix Requirements Document

## Introduction

Chat sessions crash at the end of every successful conversation turn due to a `NameError` in `_read_formatted_response()` that references an undefined `options` variable. This crash fires at the `ResultMessage` stage — the very end of the streaming path — preventing the normal STREAMING→IDLE state transition and leaving sessions in a broken state. The crash triggers the retry loop (which also crashes the same way), exhausting all retries and leaving orphaned Claude SDK subprocesses that are never cleaned up. Compounding this, zombie Hypothesis pytest processes with no deadline/shrink limits accumulate unbounded CPU usage, and the lifecycle manager's orphan reaper does not catch stale `python main.py` dev backend processes or excess Claude SDK children from crashed sessions.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a conversation turn completes successfully and the `ResultMessage` contains usage data with `input_tokens > 0` THEN the system crashes with `NameError: name 'options' is not defined` in `_read_formatted_response()` because the context warning bridge at line ~887 references `options` which is a local variable in `send()` that was never passed to `_read_formatted_response()` or `_stream_response()`

1.2 WHEN the `NameError` crashes `_read_formatted_response()` THEN the exception propagates up through `_stream_response()` to `send()`, which treats it as a retriable error and enters the retry loop — but each retry also crashes with the same `NameError` at the `ResultMessage` stage, exhausting all `MAX_RETRY_ATTEMPTS` (3) retries and leaving the session in COLD state with no successful response delivered to the user

1.3 WHEN the `NameError` crash prevents the normal STREAMING→IDLE transition THEN the SessionUnit never reaches IDLE state for that turn, which means the `_hooks_enqueued` flag is never properly cycled and the subprocess cleanup path is entered via `_crash_to_cold()` instead of the graceful IDLE path, potentially leaving Claude SDK child subprocesses orphaned

1.4 WHEN multiple sessions crash via Bug 1.1 over time THEN orphaned Claude SDK child subprocesses accumulate beyond the expected MAX_CONCURRENT (2) limit, consuming excessive memory (observed: 6 children at 1.4GB RSS total when at most 2-3 should be alive)

1.5 WHEN Hypothesis property-based tests run without a `deadline` setting or with unbounded `max_examples` THEN the shrinking phase can run indefinitely, creating zombie pytest processes that consume 35%+ CPU each (observed: 5 processes at ~180% CPU combined, running 4-20 hours)

1.6 WHEN a `python main.py --port 8000` dev backend process is started and then orphaned (ppid=1) THEN the lifecycle manager's startup orphan reaper does not detect or kill it because the reaper only searches for `claude_agent_sdk/_bundled/claude` processes, not stale Python backend processes

### Expected Behavior (Correct)

2.1 WHEN a conversation turn completes successfully and the `ResultMessage` contains usage data THEN the system SHALL access the model's context window information without referencing the undefined `options` variable — either by passing `options` (or just the model name) into `_read_formatted_response()` as a parameter, or by storing the model name on the SessionUnit instance during `send()` so it is available when the context warning bridge executes

2.2 WHEN the context warning bridge executes at the `ResultMessage` stage THEN the system SHALL emit a `context_warning` SSE event if usage exceeds 70% of the model window, and SHALL complete the normal STREAMING→IDLE transition regardless of whether the warning succeeds or fails

2.3 WHEN a conversation turn completes and the `ResultMessage` is processed THEN the system SHALL transition STREAMING→IDLE, update `last_used`, reset `_retry_count`, and return normally — the `NameError` SHALL no longer occur because the variable reference is structurally eliminated

2.4 WHEN Claude SDK child subprocesses are orphaned from crashed sessions THEN the lifecycle manager SHALL detect and reap them during its maintenance loop or startup orphan reaper, keeping the alive subprocess count within the MAX_CONCURRENT bound

2.5 WHEN Hypothesis property-based tests are configured THEN they SHALL include a `deadline` setting and appropriate `suppress_health_check` configuration to prevent infinite shrinking loops and unbounded test execution time

2.6 WHEN the lifecycle manager's startup orphan reaper runs THEN it SHALL also detect and kill stale `python main.py` backend processes that are orphaned (ppid=1) and holding known ports (e.g., port 8000), in addition to the existing claude CLI process reaping

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a conversation turn completes with no usage data (usage is None or empty) THEN the system SHALL CONTINUE TO skip the context warning bridge and transition STREAMING→IDLE normally

3.2 WHEN a retriable SDK error occurs during streaming (e.g., "Cannot write to terminated process", exit code -9) THEN the system SHALL CONTINUE TO enter the retry loop with exponential backoff and `--resume` flag for conversation context restoration

3.3 WHEN all retry attempts are exhausted for a genuinely retriable error THEN the system SHALL CONTINUE TO yield an `ALL_RETRIES_EXHAUSTED` error event to the frontend with a friendly message and suggested action

3.4 WHEN a non-retriable error occurs during streaming THEN the system SHALL CONTINUE TO crash to COLD with `clear_identity=True` and yield a `CONVERSATION_ERROR` event to the frontend

3.5 WHEN the lifecycle manager's orphan reaper runs at startup THEN it SHALL CONTINUE TO kill unowned `claude_agent_sdk/_bundled/claude` processes that are not tracked by any SessionUnit

3.6 WHEN a SessionUnit transitions from STREAMING to IDLE normally THEN the system SHALL CONTINUE TO reset `_hooks_enqueued` on the next STREAMING entry and fire idle hooks after the grace period

3.7 WHEN the `_read_formatted_response()` method processes `AssistantMessage`, `SystemMessage`, `StreamEvent`, and `ToolUseBlock` messages THEN the system SHALL CONTINUE TO yield the same SSE event formats as today with no changes to the message processing pipeline
