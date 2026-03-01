# Bugfix Requirements Document

## Introduction

Chat messages disappear from chat tabs after the app is restarted. The root cause is a session ID replacement cascade: when the backend restarts, it loses its in-memory `_active_sessions` dict. When a tab tries to resume with its saved `session_id`, the backend cannot find an active client, falls back to creating a fresh SDK session with a NEW ID, and emits a second `session_start` event. The user message gets saved under both the old and new session IDs while the assistant response is only saved under the new one. On the next restart, the cycle repeats with yet another new session ID, orphaning all previous messages.

The correct behavior is: one tab = one session ID. When the backend restarts and cannot resume the SDK client, it should create a fresh SDK client but continue using the ORIGINAL session ID for all message persistence and frontend communication. The SDK's internal session ID is an implementation detail that should not leak into the app's session model.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the app restarts and a tab resumes a conversation with a previously valid session_id THEN the system emits a `session_start` event with the OLD session_id and saves the user message under the OLD session_id BEFORE discovering that the resume will fail (no active in-memory client exists)

1.2 WHEN the backend falls back to a fresh SDK session after a failed resume attempt THEN the system creates a NEW session_id via the SDK init handler and emits a SECOND `session_start` event with the NEW session_id, causing the frontend tab to silently switch to the new session_id and abandon the original one

1.3 WHEN the session_id replacement occurs THEN the user message is duplicated (saved under both old and new session_ids) and the assistant response is saved ONLY under the new session_id, splitting the conversation across two database sessions

1.4 WHEN the app restarts a second time THEN the tab attempts to resume with the most recently assigned session_id, which again has no active in-memory client, repeating the replacement cycle and orphaning all messages from the previous session_id

### Expected Behavior (Correct)

2.1 WHEN the app restarts and a tab resumes a conversation with a previously valid session_id THEN the system SHALL use the ORIGINAL session_id for all message persistence, session storage, and frontend communication — regardless of what internal session_id the SDK assigns

2.2 WHEN the backend cannot find an active in-memory client for a resumed session_id THEN the system SHALL create a fresh SDK client but SHALL continue to use the original session_id as the app-level session identifier for saving messages, emitting `session_start`, and storing the session record

2.3 WHEN a fresh SDK client is created after a failed resume THEN the user message SHALL be saved exactly once under the ORIGINAL session_id, and the assistant response SHALL also be saved under that same ORIGINAL session_id, keeping the entire conversation history in a single session

2.4 WHEN the app restarts multiple times THEN the tab SHALL always resume with its one session_id, and all messages (past and future) SHALL be stored under that same session_id — no session ID replacement, no orphaned messages, no duplicate user messages

2.5 WHEN the backend emits a `session_start` event for a resumed conversation (whether the SDK client was reused or freshly created) THEN the event SHALL contain the ORIGINAL session_id that the frontend tab already holds, NOT the SDK's internal session_id

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a tab sends a message on a brand-new conversation (no prior session_id) THEN the system SHALL CONTINUE TO create a new session via the SDK init handler, emit a single `session_start` event with the SDK-assigned session_id, and save both user and assistant messages under that session_id

3.2 WHEN a tab resumes a conversation and the backend still has the active in-memory client (no restart occurred) THEN the system SHALL CONTINUE TO reuse the existing client, emit a single `session_start` event with the original session_id, and save messages under that same session_id

3.3 WHEN the user sends multiple messages within a single session without any restart THEN the system SHALL CONTINUE TO save each user message and assistant response pair under the same session_id in chronological order

3.4 WHEN the frontend loads messages for a tab on app startup THEN the system SHALL CONTINUE TO fetch and display messages by session_id from the database — the session_id in localStorage matches the session_id in the messages table

3.5 WHEN `continue_with_cmd_permission` is called to handle a permission decision THEN the system SHALL CONTINUE TO save the decision message under the app session_id and return immediately — no resume-fallback can occur because this method does not create a new SDK client
