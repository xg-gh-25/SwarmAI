# Bugfix Requirements Document

## Introduction

When a chat tab is evicted due to the MAX_CONCURRENT=2 concurrency cap (another tab needs the slot while this tab is IDLE), the evicted tab permanently loses all conversation context. Returning to the evicted tab starts a fresh conversation with zero history instead of resuming where the user left off via the SDK's `--resume` flag.

The root cause is a two-part interaction: `_cleanup_internal()` in `session_unit.py` erases `_sdk_session_id` (the only key needed to resume), and `run_conversation()` in `session_router.py` gates the resume ID on `unit.is_alive` which is always `False` after eviction. Together, these guarantee that an evicted tab can never resume its conversation.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a SessionUnit is evicted (killed to free a concurrency slot) and `_cleanup_internal()` runs THEN the system sets `_sdk_session_id = None`, permanently destroying the only identifier needed to resume the conversation via `--resume`

1.2 WHEN a user returns to an evicted tab and `run_conversation()` builds SDK options THEN the system evaluates `unit._sdk_session_id if unit.is_alive else None`, which always yields `None` because a killed unit is never alive (`is_alive` requires state in IDLE/STREAMING/WAITING_INPUT)

1.3 WHEN `resume_session_id` is `None` due to 1.1 and 1.2 THEN the system spawns a fresh subprocess with no `--resume` flag, resulting in a blank conversation with zero prior context visible to the user

### Expected Behavior (Correct)

2.1 WHEN a SessionUnit is evicted and `_cleanup_internal()` runs THEN the system SHALL preserve `_sdk_session_id` across the cleanup, clearing only transient subprocess resources (`_client`, `_wrapper`, `_interrupted`, `_retry_count`)

2.2 WHEN a user returns to an evicted tab and `run_conversation()` builds SDK options THEN the system SHALL use `unit._sdk_session_id` unconditionally (if it exists), regardless of whether the unit's subprocess is currently alive

2.3 WHEN `resume_session_id` is provided to the subprocess via `--resume` THEN the system SHALL restore the full prior conversation context so the user sees all previous messages and can continue seamlessly

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a SessionUnit subprocess is alive and IDLE and `send()` is called THEN the system SHALL CONTINUE TO reuse the existing subprocess without spawning a new one

3.2 WHEN a SessionUnit is in COLD state with no prior `_sdk_session_id` (brand new tab) THEN the system SHALL CONTINUE TO spawn a fresh subprocess without a `--resume` flag

3.3 WHEN a non-retriable error causes DEAD → COLD transition (crash, not eviction) THEN the system SHALL CONTINUE TO clean up all internal state including `_sdk_session_id` so the next conversation starts fresh

3.4 WHEN the retry loop in `send()` captures `resume_session_id` before `_cleanup_internal()` THEN the system SHALL CONTINUE TO pass `--resume` on retry attempts using the captured value

3.5 WHEN `disconnect_all()` is called during shutdown THEN the system SHALL CONTINUE TO fully clean up all units including clearing `_sdk_session_id`
