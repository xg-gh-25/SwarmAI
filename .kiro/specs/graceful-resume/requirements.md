# Requirements Document

## Introduction

When a user closes and reopens the SwarmAI desktop app, then sends a message on an existing chat tab, Claude starts a fresh SDK session with no memory of the previous conversation. The tab's messages are visible in the UI (loaded from SQLite), but Claude's context is blank — it only has the system prompt, context files, and MEMORY.md.

This feature adds conversation context injection on session resume. When the backend detects a resumed session with no active SDK client, it loads the last N messages from SQLite and injects them as a "Previous Conversation Context" section into the system prompt, giving Claude awareness of what was discussed before the app restart.

## Glossary

- **Resume_Detector**: The logic in `_execute_on_session_inner` that identifies when a session is resuming without an active SDK client (PATH A with `is_resuming=True` but no reusable client).
- **Context_Injector**: The component responsible for loading previous messages from SQLite, formatting them, and injecting them into the system prompt for resumed sessions.
- **System_Prompt_Builder**: The existing `_build_system_prompt` method in `AgentManager` that assembles the full system prompt from context files, daily activity, and non-file sections.
- **Message_Store**: The SQLite-backed message persistence layer (`db.messages`) that stores all chat messages with session ID, role, content blocks, and timestamps.
- **Token_Budget**: The maximum number of estimated tokens allocated for injected conversation context (capped at 2000 tokens).
- **Session_Manager**: The existing `SessionManager` class that tracks session metadata (creation time, last access, title) in SQLite.
- **SDK_Client**: The Claude Agent SDK subprocess client (`ClaudeSDKClient`) that maintains Claude's live conversation state in memory.
- **App_Session_ID**: The stable tab-level session identifier that persists across app restarts, used to look up previous messages.

## Requirements

### Requirement 1: Detect Resume-Without-Client Condition

**User Story:** As a user, I want the system to detect when I'm resuming a conversation after an app restart, so that it can restore conversation context automatically.

#### Acceptance Criteria

1. WHEN a chat request arrives with `is_resuming=True` AND the Resume_Detector finds no active SDK_Client for the session, THE Resume_Detector SHALL set a `needs_context_injection` flag to `True`.
2. WHEN a chat request arrives with `is_resuming=True` AND the Resume_Detector finds an active SDK_Client (PATH B), THE Resume_Detector SHALL set the `needs_context_injection` flag to `False`.
3. WHEN a chat request arrives with `is_resuming=False` (new session), THE Resume_Detector SHALL set the `needs_context_injection` flag to `False`.

### Requirement 2: Load Previous Conversation Messages

**User Story:** As a user, I want the system to retrieve my recent conversation history from the database, so that Claude can understand what we previously discussed.

#### Acceptance Criteria

1. WHEN `needs_context_injection` is `True`, THE Context_Injector SHALL load the most recent messages from the Message_Store using the App_Session_ID.
2. THE Context_Injector SHALL load up to 30 messages from the Message_Store to account for tool-only messages being filtered out, then retain a maximum of 10 human-readable messages in the final output.
3. THE Context_Injector SHALL return messages in chronological order (oldest first).
4. WHEN the Message_Store contains zero messages for the App_Session_ID, THE Context_Injector SHALL return an empty list and skip injection.
5. THE Context_Injector SHALL filter out messages that contain only tool-use or tool-result content blocks, retaining only messages with human-readable text content.

### Requirement 3: Format Messages for System Prompt Injection

**User Story:** As a user, I want my previous conversation to be formatted clearly in Claude's context, so that Claude can distinguish between current and previous conversation turns.

#### Acceptance Criteria

1. THE Context_Injector SHALL format each message as a labeled turn with the role prefix ("User:" or "Assistant:") followed by the text content.
2. THE Context_Injector SHALL wrap all formatted messages in a section header: `## Previous Conversation Context`.
3. THE Context_Injector SHALL include a preamble stating: "The following is a summary of the previous conversation in this chat session. You did not experience these turns directly — they are provided for context only. Do not repeat or re-execute any actions described below."
4. WHEN a message contains multiple text content blocks, THE Context_Injector SHALL concatenate the text blocks with newline separators.
5. WHEN a message contains image or document content blocks, THE Context_Injector SHALL replace the non-text block with a placeholder: "[image attachment]" or "[document attachment]".

### Requirement 4: Enforce Token Budget

**User Story:** As a user, I want the injected context to stay within a reasonable size, so that it does not crowd out other important context (system prompt, context files, MEMORY.md).

#### Acceptance Criteria

1. THE Context_Injector SHALL estimate the token count of the formatted conversation context using the existing `ContextDirectoryLoader.estimate_tokens` method.
2. WHEN the formatted context exceeds the Token_Budget of 2000 tokens, THE Context_Injector SHALL remove the oldest messages first until the total is within budget.
3. WHEN the formatted context exceeds the Token_Budget after truncation, THE Context_Injector SHALL prepend a note: "[Earlier messages truncated to fit token budget]".
4. THE Context_Injector SHALL reserve the Token_Budget from the existing ephemeral headroom calculation in the System_Prompt_Builder.

### Requirement 5: Inject Context into System Prompt

**User Story:** As a user, I want the previous conversation context to appear in Claude's system prompt only for resumed sessions, so that fresh sessions remain unaffected.

#### Acceptance Criteria

1. WHEN `needs_context_injection` is `True` AND the Context_Injector produces non-empty formatted context, THE System_Prompt_Builder SHALL append the formatted context after the DailyActivity section and before the SystemPromptBuilder non-file sections.
2. WHEN `needs_context_injection` is `False`, THE System_Prompt_Builder SHALL not include any previous conversation context section.
3. THE System_Prompt_Builder SHALL read the `needs_context_injection` flag and the `resume_app_session_id` from the `agent_config` dict (following the existing pattern where `agent_config` carries `system_prompt`, `context_token_budget`, etc.).

### Requirement 6: Session Isolation

**User Story:** As a user, I want my conversation context to remain private to my session, so that no cross-session data leakage occurs.

#### Acceptance Criteria

1. THE Context_Injector SHALL load messages exclusively using the App_Session_ID associated with the current request.
2. THE Context_Injector SHALL not cache loaded messages across different session requests.
3. WHEN the App_Session_ID is `None`, THE Context_Injector SHALL skip context injection entirely.

### Requirement 7: Support All Session Types

**User Story:** As a user, I want context injection to work for all session types (regular chat, skill creator), so that resume behavior is consistent.

#### Acceptance Criteria

1. WHEN a regular chat session resumes without an active SDK_Client, THE Context_Injector SHALL inject previous conversation context following the same rules as Requirements 2–6.
2. WHEN a skill creator session resumes without an active SDK_Client, THE Context_Injector SHALL inject previous conversation context following the same rules as Requirements 2–6.

### Requirement 8: Graceful Error Handling

**User Story:** As a user, I want the system to continue working even if context injection fails, so that a resume failure does not block my conversation.

#### Acceptance Criteria

1. IF the Message_Store query fails with a database error, THEN THE Context_Injector SHALL log the error and proceed with an empty context (no injection).
2. IF the token estimation fails, THEN THE Context_Injector SHALL log the error and proceed with an empty context (no injection).
3. IF the formatting of a single message fails, THEN THE Context_Injector SHALL skip that message and continue formatting the remaining messages.
