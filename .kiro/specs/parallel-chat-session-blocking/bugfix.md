# Bugfix Requirements Document

## Introduction

Users cannot start a new chat session in parallel when another session is actively streaming. The per-session concurrency lock in `_execute_on_session()` uses a fallback lock key of `agent_id` when `session_id` is `None`, causing all new (not-yet-assigned) sessions for the same agent to share a single lock. The second new session is immediately rejected with a `SESSION_BUSY` error, even though it is a completely independent conversation.

The lock was designed to prevent double-send corruption on the *same* session (e.g., double-clicking Send or frontend retry). It was never intended to block *different* sessions from running concurrently.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a user sends a message in a new chat tab (session_id=None) while another new chat tab for the same agent is already streaming THEN the system immediately rejects the request with a SESSION_BUSY error, because both sessions compute lock_key as `agent_id` (e.g., "default")

1.2 WHEN a user sends a message in a new chat tab (session_id=None) while an existing (resumed) session for the same agent is streaming AND that existing session's lock key also fell back to agent_id THEN the system rejects the new session with SESSION_BUSY

1.3 WHEN the `run_skill_creator_conversation()` method runs without `_execute_on_session()` THEN it has no per-session concurrency guard at all, meaning it is susceptible to double-send corruption on the same skill-creator session

### Expected Behavior (Correct)

2.1 WHEN a user sends a message in a new chat tab (session_id=None) while another chat session for the same agent is actively streaming THEN the system SHALL generate a unique lock key for the new session and allow it to proceed independently without blocking

2.2 WHEN a user sends a message in a new chat tab (session_id=None) while an existing (resumed) session for the same agent is streaming THEN the system SHALL allow the new session to proceed independently, since it is a distinct conversation

2.3 WHEN the `run_skill_creator_conversation()` method handles concurrent requests on the same skill-creator session THEN the system SHALL apply the same per-session concurrency guard pattern used by `_execute_on_session()` to prevent double-send corruption

### Additional Requirements (from PE Review)

2.4 WHEN the system generates an ephemeral lock key for a new session THEN the system SHALL clean up that ephemeral key from `_session_locks` after execution completes (via `finally` block) to prevent unbounded memory growth

2.5 WHEN the system generates an ephemeral lock key THEN the system SHALL log the ephemeral UUID and agent_id for observability and production debugging

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user double-clicks Send or the frontend retries on the same active session (same session_id) THEN the system SHALL CONTINUE TO reject the duplicate request with SESSION_BUSY to prevent double-send corruption

3.2 WHEN a resumed session (session_id is not None) is actively streaming and the same session_id sends another request THEN the system SHALL CONTINUE TO reject the concurrent request with SESSION_BUSY

3.3 WHEN a session completes normally THEN the system SHALL CONTINUE TO release the session lock and clean it up via `_cleanup_session()`

3.4 WHEN a session encounters an error THEN the system SHALL CONTINUE TO clean up the session from the reuse pool and release associated resources
