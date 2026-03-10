# Implementation Plan: Graceful Resume

## Overview

Add conversation context injection on session resume. A new stateless module `backend/core/context_injector.py` loads the last 30 messages from SQLite, filters tool-only turns, takes the last 10 human-readable messages, formats them with role prefixes, enforces a 2000-token budget, and returns a formatted string. The string is injected into the system prompt via the existing `agent_config` dict pattern. Two integration sites in `agent_manager.py` set the `needs_context_injection` flag (regular chat PATH A and skill creator PATH A).

## Tasks

- [x] 1. Create `backend/core/context_injector.py` with core helpers
  - [x] 1.1 Implement `_filter_tool_only_messages(messages)` helper
    - Accept a list of message dicts, return only messages that have at least one non-tool content block (type not in `{"tool_use", "tool_result"}`)
    - _Requirements: 2.5_
  - [x] 1.2 Implement `_format_message(message)` helper
    - Extract role prefix ("User:" for role "user", "Assistant:" for role "assistant")
    - Concatenate text blocks with newline separators
    - Replace image blocks with `[image attachment]`, document blocks with `[document attachment]`
    - Skip tool_use/tool_result blocks silently
    - Wrap in try/except: on any error, log warning and return `None` (caller skips)
    - _Requirements: 3.1, 3.4, 3.5, 8.3_
  - [x] 1.3 Implement `_apply_token_budget(formatted_messages, token_budget)` helper
    - Use `ContextDirectoryLoader.estimate_tokens` for token estimation
    - Remove oldest messages first until total fits within budget
    - Return `(surviving_messages, was_truncated)` tuple
    - Wrap in try/except: on estimation error, log warning and return `([], False)`
    - _Requirements: 4.1, 4.2, 8.2_
  - [x] 1.4 Implement `_assemble_context(messages, was_truncated)` helper
    - Wrap messages in `## Previous Conversation Context` header
    - Include preamble disclaimer text per Requirement 3.3
    - Prepend truncation note `[Earlier messages truncated to fit token budget]` only when `was_truncated` is True
    - Return empty string for empty message list
    - _Requirements: 3.2, 3.3, 4.3_
  - [x] 1.5 Implement `build_resume_context(app_session_id, max_messages=10, db_fetch_limit=30, token_budget=2000)` public async function
    - Return `""` immediately if `app_session_id` is None
    - Import `db` from `backend.database` and call `db.messages.list_by_session_paginated(app_session_id, limit=db_fetch_limit)`
    - Filter with `_filter_tool_only_messages`, take last `max_messages`
    - Format each with `_format_message`, skip None results
    - Apply token budget with `_apply_token_budget`
    - Assemble with `_assemble_context`
    - Wrap entire body in try/except: on any error, log warning and return `""`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.1, 6.3, 8.1_

- [ ]* 1.6 Write unit tests for context_injector helpers (`backend/tests/test_context_injector.py`)
    - Test `_filter_tool_only_messages`: empty list → empty; all tool-only → empty; mixed → text-containing retained
    - Test `_format_message`: user role prefix, assistant role prefix, multi-text-block concatenation, image/document placeholders, malformed message returns None
    - Test `_apply_token_budget`: all fit → no truncation; over budget → oldest dropped, `was_truncated=True`
    - Test `_assemble_context`: non-empty → has header + preamble; truncated → has truncation note; empty → returns `""`
    - Test `build_resume_context`: `app_session_id=None` → `""`; DB error → `""`; zero messages → `""`; happy path with mixed messages
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 4.2, 4.3, 6.3, 8.1, 8.2, 8.3_

- [x] 2. Checkpoint — Verify context_injector module
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Integrate resume detection flag into `agent_manager.py`
  - [x] 3.1 Set `needs_context_injection` flag in `_execute_on_session_inner` PATH A
    - In the `else` branch (PATH A) of `_execute_on_session_inner`, after the `if is_resuming:` block that logs "No active client" and resets `is_resuming = False`, set `agent_config["needs_context_injection"] = True` and `agent_config["resume_app_session_id"] = app_session_id`
    - Ensure the flag is set BEFORE `_build_system_prompt` is called (it's called inside `_build_options` → no, it's called later in `_run_query_on_client` flow — verify the call site)
    - In PATH B (reused client), explicitly set `agent_config["needs_context_injection"] = False`
    - For non-resuming requests (`is_resuming=False` initially), set `agent_config["needs_context_injection"] = False`
    - _Requirements: 1.1, 1.2, 1.3_
  - [x] 3.2 Set `needs_context_injection` flag in `run_skill_creator_conversation`
    - Same pattern: in the `else` branch (no active client) after the `if is_resuming:` block, set `agent_config["needs_context_injection"] = True` and `agent_config["resume_app_session_id"] = session_id`
    - In the reused-client branch, set `agent_config["needs_context_injection"] = False`
    - For non-resuming requests, set `agent_config["needs_context_injection"] = False`
    - _Requirements: 1.1, 1.2, 1.3, 7.1, 7.2_

- [x] 4. Inject resume context into `_build_system_prompt`
  - [x] 4.1 Expand `EPHEMERAL_HEADROOM` to include resume context budget
    - Change `EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE` to `RESUME_CONTEXT_BUDGET = 2000` and `EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE + RESUME_CONTEXT_BUDGET`
    - Update the corresponding constant in `backend/tests/test_system_prompt_e2e.py` to match
    - _Requirements: 4.4_
  - [x] 4.2 Add context injection call site in `_build_system_prompt`
    - After the DailyActivity injection block (after the distillation flag check), add: if `agent_config.get("needs_context_injection")` and `agent_config.get("resume_app_session_id")`, call `await build_resume_context(agent_config["resume_app_session_id"])` and append result to `context_text`
    - Import `build_resume_context` from `backend.core.context_injector`
    - Log info: number of messages injected and estimated token count, or "skipped: no injectable messages"
    - Note: `_build_system_prompt` is already async, so the await is fine
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 5. Checkpoint — Verify integration
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 6. Write property-based tests (`backend/tests/test_property_context_injector.py`)
  - [ ]* 6.1 Property 1: Resume detection flag is correctly derived
    - **Property 1: Resume detection flag equals `is_resuming AND NOT has_active_client`**
    - Generate all combinations of `is_resuming` (bool) and `has_active_client` (bool), verify `needs_context_injection` matches the formula
    - **Validates: Requirements 1.1, 1.2, 1.3**
  - [ ]* 6.2 Property 2: Messages loaded exclusively for requested session
    - **Property 2: Output only contains content from the requested session_id**
    - Generate messages for multiple session IDs, verify `build_resume_context(target_id)` output only contains text from `target_id` messages
    - **Validates: Requirements 2.1, 6.1**
  - [ ]* 6.3 Property 3: Output respects message count limit and chronological ordering
    - **Property 3: At most 10 human-readable messages, in chronological order**
    - Generate random message lists (0–30), verify filtered output has ≤ 10 messages and timestamps are non-decreasing
    - **Validates: Requirements 2.2, 2.3**
  - [ ]* 6.4 Property 4: Tool-only messages are excluded
    - **Property 4: No surviving message consists exclusively of tool_use/tool_result blocks**
    - Generate messages with random content block types, verify no tool-only message survives filtering
    - **Validates: Requirements 2.5**
  - [ ]* 6.5 Property 5: Message formatting preserves content with correct role prefixes
    - **Property 5: Formatted output has correct role prefix and all text content**
    - Generate messages with random roles and content blocks, verify prefix and text extraction
    - **Validates: Requirements 3.1, 3.4, 3.5**
  - [ ]* 6.6 Property 6: Non-empty output includes section header and preamble
    - **Property 6: Non-empty output contains header and preamble; empty input → empty output**
    - Generate random formatted message lists, verify structural invariants
    - **Validates: Requirements 3.2, 3.3**
  - [ ]* 6.7 Property 7: Token budget enforcement with oldest-first truncation
    - **Property 7: Final output ≤ token budget; truncated messages are the oldest**
    - Generate messages with varying lengths and budgets, verify budget compliance and truncation ordering
    - **Validates: Requirements 4.2, 4.3**
  - [ ]* 6.8 Property 8: No injection when flag is False
    - **Property 8: System prompt with `needs_context_injection=False` never contains the context header**
    - Generate random agent_config with flag=False, verify absence of `## Previous Conversation Context`
    - **Validates: Requirements 5.2**
  - [ ]* 6.9 Property 9: Error resilience — failures produce empty context
    - **Property 9: DB/estimation/formatting errors return empty string without raising**
    - Inject random failures (mock DB errors, malformed messages), verify empty string returned
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [x] 7. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.


## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1–9)
- Unit tests validate specific examples and edge cases
- The module is stateless — no module-level mutable state, no caching across sessions (per SwarmAI anti-pattern rules)
- `_build_system_prompt` is already async, so the `await build_resume_context()` call requires no signature changes
