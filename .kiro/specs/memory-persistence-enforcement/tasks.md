# Implementation Plan: Memory Persistence Enforcement

## Overview

This plan implements a session lifecycle hook framework and its three consumers (DailyActivity extraction, workspace auto-commit migration, distillation trigger), plus shared infrastructure (summarization pipeline, DailyActivity writer, compliance tracker), an on-demand skill, and DailyActivity loading improvements. Tasks are ordered so each step builds on the previous, with regression-prevention tasks and property-based tests woven throughout.

## Tasks

- [x] 1. Core infrastructure: Hook framework and data models
  - [x] 1.1 Create `backend/core/session_hooks.py` with `HookContext`, `SessionLifecycleHook` protocol, and `SessionLifecycleHookManager`
    - Define `HookContext` frozen dataclass with fields: `session_id`, `agent_id`, `message_count`, `session_start_time`, `session_title`
    - Define `SessionLifecycleHook` as a `typing.Protocol` with `name` property and `async execute(context)` method
    - Implement `SessionLifecycleHookManager` with `register()` and `fire_post_session_close()` methods
    - `fire_post_session_close` must execute hooks sequentially in registration order, catch exceptions per-hook, and enforce per-hook timeout via `asyncio.wait_for`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.2 Write property test: Hooks execute in registration order (Property 1)
    - **Property 1: Hooks execute in registration order**
    - Generate random lists of mock hooks, fire event, verify execution log matches registration order
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 1.3 Write property test: Error isolation preserves remaining hook execution (Property 2)
    - **Property 2: Error isolation preserves remaining hook execution**
    - Generate hook lists with random failure positions, verify non-failing hooks all execute and no exception propagates to caller
    - **Validates: Requirements 1.3, 2.6, 3.5, 5.5**

  - [ ]* 1.4 Write property test: Hook context is passed faithfully to all hooks (Property 3)
    - **Property 3: Hook context is passed faithfully to all hooks**
    - Generate random `HookContext` fields, verify each hook receives the identical `HookContext` instance
    - **Validates: Requirements 1.4**


- [x] 2. Core infrastructure: Summarization Pipeline
  - [x] 2.1 Create `backend/core/summarization.py` with `StructuredSummary` dataclass and `SummarizationPipeline` class
    - Define `StructuredSummary` dataclass with fields: `topics`, `decisions`, `files_modified`, `open_questions`, `session_title`, `timestamp`
    - Implement `SummarizationPipeline` with rule-based extraction (no LLM): `_extract_topics` (first sentence of user messages), `_extract_decisions` (regex patterns: "decided to", "chose", "will use", "going with", "recommend", "the approach is", "selected"), `_extract_files` (from tool_use events of type Write/Edit/Read/Bash), `_extract_open_questions` (from ask_user_question events)
    - Implement `summarize()` for 3+ message conversations and `minimal_summary()` for <3 messages (topics only)
    - Enforce 500-word cap via `MAX_WORDS_PER_ENTRY`
    - Deduplicate topics and files by exact string match; preserve chronological order
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 2.2 Write property test: Short conversations produce minimal entries (Property 5)
    - **Property 5: Short conversations produce minimal entries**
    - Generate conversations with 0-2 messages, verify only `topics` is non-empty and `decisions`, `files_modified`, `open_questions` are empty lists
    - **Validates: Requirements 2.7**

  - [ ]* 2.3 Write property test: Tool-use file paths and ask_user_question events are fully captured (Property 11)
    - **Property 11: Tool-use file paths and ask_user_question events are fully captured**
    - Generate conversations with random tool_use events (Write/Edit/Read/Bash) and ask_user_question events, verify all file paths appear in `files_modified` and all questions appear in `open_questions`
    - **Validates: Requirements 6.4, 6.5**

  - [ ]* 2.4 Write property test: Summary word count does not exceed 500 (Property 12)
    - **Property 12: Summary word count does not exceed 500**
    - Generate large conversation logs, verify total word count across all `StructuredSummary` fields ≤ 500
    - **Validates: Requirements 6.6**

  - [ ]* 2.5 Write property test: Summarization is deterministic (Property 13)
    - **Property 13: Summarization is deterministic**
    - Generate random conversation logs, call `summarize()` twice with the same input, verify identical `StructuredSummary` output
    - **Validates: Requirements 6.7**


- [x] 3. Core infrastructure: DailyActivity file writer
  - [x] 3.1 Create `backend/core/daily_activity_writer.py` with `write_daily_activity()`, `parse_frontmatter()`, and `write_frontmatter()`
    - Implement `parse_frontmatter(content)` → `(dict, str)` with value normalization (booleans to Python bool, integers to int)
    - Implement `write_frontmatter(frontmatter, body)` → `str` that serializes frontmatter dict and body back to file content
    - Implement `write_daily_activity(summary, context)` that: creates file with YAML frontmatter if new (`date`, `sessions_count: 1`, `distilled: false`); appends `## Session — HH:MM` section with subsections `### Topics`, `### Decisions`, `### Files Modified`, `### Open Questions`; increments `sessions_count` on append; uses atomic read-modify-write with `fcntl.flock` for concurrency safety
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 3.2 Write property test: DailyActivity write appends and preserves existing content (Property 4)
    - **Property 4: DailyActivity write appends and preserves existing content**
    - Generate random existing DailyActivity file content + new `StructuredSummary`, verify original content is preserved and new entry is appended
    - **Validates: Requirements 2.4, 2.5, 4.3**

  - [ ]* 3.3 Write property test: DailyActivity file structure is correct (Property 8)
    - **Property 8: DailyActivity file structure is correct**
    - Generate random summaries, write to file, parse and verify: YAML frontmatter with `date`, `sessions_count`, `distilled`; each session under `## Session — HH:MM`; subsections `### Topics`, `### Decisions`, `### Files Modified`, `### Open Questions`
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [ ]* 3.4 Write property test: Frontmatter round-trip (Property 9)
    - **Property 9: Frontmatter round-trip**
    - Generate random frontmatter dicts + body content, verify `parse_frontmatter(write_frontmatter(fm, body))` produces semantically equal output
    - **Validates: Requirements 7.4**

  - [ ]* 3.5 Write property test: Sessions count increments on append (Property 10)
    - **Property 10: Sessions count increments on append**
    - Generate DailyActivity files with random `sessions_count = N`, append one entry, verify resulting `sessions_count == N + 1`
    - **Validates: Requirements 7.5**

  - [ ]* 3.6 Write unit test: Concurrent DailyActivity writes do not corrupt file
    - Use `threading` to simulate multiple sessions closing simultaneously, each calling `write_daily_activity`
    - Verify final file has correct `sessions_count` and all session entries are present and intact
    - _Requirements: 7.6_


- [x] 4. Core infrastructure: Compliance Tracker
  - [x] 4.1 Create `backend/core/compliance.py` with `DailyMetrics` dataclass and `ComplianceTracker` class
    - Implement `record_success(session_id)`, `record_failure(session_id, reason)`, `get_metrics(days=30)`, and `_prune_old()`
    - In-memory dict keyed by date string, 30-day retention with automatic pruning
    - _Requirements: 8.1, 8.3, 8.4, 8.5_

  - [x] 4.2 Create `/api/memory-compliance` endpoint in `backend/routers/memory.py`
    - Expose `GET /api/memory-compliance` returning JSON with `metrics` array and `retention_days`
    - Register the router in `backend/main.py`
    - _Requirements: 8.2_

  - [ ]* 4.3 Write property test: Compliance counters accurately reflect operations (Property 14)
    - **Property 14: Compliance counters accurately reflect operations**
    - Generate random sequences of `record_success` and `record_failure` calls, verify `sessions_processed == total calls`, `files_written == success count`, `failures == failure count`, and each failure reason is recorded
    - **Validates: Requirements 8.1, 8.3, 8.4**

  - [ ]* 4.4 Write property test: Compliance metrics retain only 30 days (Property 15)
    - **Property 15: Compliance metrics retain only 30 days**
    - Generate metrics across 40+ distinct dates, verify `get_metrics()` returns at most 30 most recent dates
    - **Validates: Requirements 8.5**

- [x] 5. Checkpoint — Verify core infrastructure
  - Ensure all tests pass, ask the user if questions arise.


- [x] 6. Hook implementations
  - [x] 6.1 Create `backend/hooks/__init__.py` and `backend/hooks/daily_activity_hook.py` with `DailyActivityExtractionHook`
    - Implement `SessionLifecycleHook` protocol with `name = "daily_activity_extraction"`
    - `execute()`: retrieve conversation log via `db.messages.list_by_session(session_id, limit=500)`, call `SummarizationPipeline.summarize()` or `minimal_summary()` based on message count, call `write_daily_activity()`, record success/failure in `ComplianceTracker`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 6.2 Create `backend/hooks/auto_commit_hook.py` with `WorkspaceAutoCommitHook`
    - Implement `SessionLifecycleHook` protocol with `name = "workspace_auto_commit"`
    - Implement `COMMIT_CATEGORIES` dict mapping file path prefixes to conventional commit prefixes: `.context/` → `framework:`, `.claude/skills/` → `skills:`, `Knowledge/` → `content:`, `Projects/` → `project:`
    - Implement `EXTENSION_CATEGORIES` dict mapping file extensions to prefixes: `.pdf`/`.pptx`/`.docx` → `output:`
    - `execute()`: run `git status --porcelain` to check for changes, `git add -A` to stage, `git diff --cached --stat` to analyze, then generate smart commit message
    - `_parse_diff_stat()`: extract file paths from `git diff --stat` output
    - `_categorize_file()`: map file path to conventional commit category via prefix/extension matching
    - `_is_trivial()`: return `True` if all changes are `skills` or `chore` category (skip or use `chore: session sync`)
    - `_generate_commit_message()`: find dominant category, build descriptive message like `content: update 3 files` or `framework: update MEMORY.md`
    - Skip silently if no changes; log warning if git fails; check `git add` return code before committing
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 3.7_

  - [ ]* 6.3 Write property test: Auto-commit generates conventional commit messages from diffs (Property 6)
    - **Property 6: Auto-commit generates conventional commit messages from diffs**
    - Generate random sets of file paths matching various path patterns, verify commit message starts with a valid conventional prefix (`framework:`, `skills:`, `content:`, `project:`, `output:`, `chore:`) and the prefix matches the dominant file category
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 6.3.1 Write unit test: Smart commit message generation for known file patterns
    - Test `.context/MEMORY.md` change → `framework: update MEMORY.md`
    - Test multiple `Knowledge/` files → `content: update 5 files`
    - Test `Projects/` files → `project:` prefix
    - Test mixed `.context/` + `Knowledge/` → dominant category prefix with breakdown
    - Test only `.claude/skills/` config syncs → `chore: session sync (N files)` (trivial)
    - Test single `.pdf` output → `output: update report.pdf`
    - _Requirements: 3.2, 3.3, 3.6_

  - [x] 6.4 Create `backend/hooks/distillation_hook.py` with `DistillationTriggerHook`
    - Implement `SessionLifecycleHook` protocol with `name = "distillation_trigger"`
    - `execute()`: scan `Knowledge/DailyActivity/*.md` files from last 30 days, parse frontmatter to count files where `distilled != true`, if count > 7 write `.needs_distillation` flag file with `undistilled_count` and `flagged_at`
    - _Requirements: 5.3, 5.4, 5.5_

  - [ ]* 6.5 Write property test: Distillation triggers iff undistilled count exceeds threshold (Property 7)
    - **Property 7: Distillation triggers iff undistilled count exceeds threshold**
    - Generate sets of DailyActivity files with random `distilled` frontmatter values, verify flag file is written iff undistilled count > 7
    - **Validates: Requirements 5.3, 5.4**

  - [ ]* 6.6 Write unit test: Distillation flag file approach end-to-end
    - Test that `DistillationTriggerHook` writes `.needs_distillation` flag when threshold exceeded
    - Test that `_build_system_prompt()` reads the flag and injects "Memory Maintenance Required" instruction
    - Test that flag is not written when undistilled count ≤ 7
    - _Requirements: 5.3, 5.4_


- [x] 7. Integration: Hook registration and `AgentManager` wiring
  - [x] 7.1 Register hooks in `backend/main.py` lifespan startup
    - Create `SummarizationPipeline`, `ComplianceTracker`, and `SessionLifecycleHookManager` instances
    - Register hooks in order: `DailyActivityExtractionHook`, `WorkspaceAutoCommitHook`, `DistillationTriggerHook`
    - Inject `hook_manager` into `AgentManager` via `set_hook_manager()`
    - _Requirements: 1.1, 1.2_

  - [x] 7.2 Add `set_hook_manager()` method and `_build_hook_context()` helper to `AgentManager`
    - Add `self._hook_manager: SessionLifecycleHookManager | None = None` attribute
    - Implement `set_hook_manager(hook_manager)` setter
    - Implement `_build_hook_context(session_id, info)` that builds `HookContext` using `db.messages.count_by_session()` (NOT `list_by_session()`) for the message count
    - _Requirements: 1.4_

  - [x] 7.3 Add `count_by_session()` method to the DB messages layer
    - Add `count_by_session(session_id) -> int` method that executes `SELECT COUNT(*) FROM messages WHERE session_id = ?`
    - This is used by `_build_hook_context` to avoid loading all messages just for a count
    - _Requirements: 1.4 (efficient context building)_

  - [ ]* 7.4 Write unit test: `_build_hook_context` uses `count_by_session()` not `list_by_session()`
    - Mock both DB methods, call `_build_hook_context`, verify `count_by_session` was called and `list_by_session` was NOT called
    - _Requirements: 1.4_

- [x] 8. Integration: Modify `_cleanup_session` in `AgentManager` (REGRESSION-CRITICAL)
  - [x] 8.1 Reorder `_cleanup_session` to use `get` before hooks, `pop` after
    - **REGRESSION: `_cleanup_session` pop-before-hooks**
    - Current code does `self._active_sessions.pop(session_id)` as the FIRST line
    - Change to: `info = self._active_sessions.get(session_id)` BEFORE hooks, then `self._active_sessions.pop(session_id, None)` AFTER hooks complete
    - Add `skip_hooks: bool = False` parameter (default `False`)
    - When `skip_hooks=False` and `info` exists and `self._hook_manager` is set: build `HookContext` and call `await self._hook_manager.fire_post_session_close(context)`
    - _Requirements: 1.6, 1.8_

  - [x] 8.2 Update all error-path calls to `_cleanup_session` to pass `skip_hooks=True`
    - **REGRESSION: Error-path `skip_hooks=True`**
    - Update the call at line ~1361 (conversation error recovery) to `await self._cleanup_session(session_id, skip_hooks=True)`
    - Update the call at line ~1532 (broken session recovery) to `await self._cleanup_session(session_id, skip_hooks=True)`
    - Update the call at line ~2327 (error recovery) to `await self._cleanup_session(session_id, skip_hooks=True)`
    - All three error-path calls MUST pass `skip_hooks=True` because the conversation may be incomplete and the session may be recreated
    - _Requirements: 1.8_

  - [ ]* 8.3 Write unit test: Hooks can access session info during `_cleanup_session`
    - Register a mock hook that reads `context.session_id` and `context.message_count`
    - Call `_cleanup_session(session_id)` (without `skip_hooks`)
    - Verify the hook received valid session info (not `None`, not already popped)
    - Verify `_active_sessions` no longer contains the session AFTER hooks complete
    - _Requirements: 1.6_

  - [ ]* 8.4 Write unit test: Hooks do NOT fire on error-path cleanup
    - Register a mock hook that records whether it was called
    - Simulate an error-recovery call: `_cleanup_session(session_id, skip_hooks=True)`
    - Verify the hook was NOT called
    - Test all three error-path call sites by mocking the error conditions at lines ~1361, ~1532, ~2327
    - _Requirements: 1.8_


- [x] 9. Integration: Modify `delete_session` endpoint in `chat.py` (REGRESSION-CRITICAL)
  - [x] 9.1 Reorder `delete_session` to fire hooks before deleting data
    - **REGRESSION: `delete_session` endpoint reordering**
    - Current code deletes messages BEFORE hooks could read them
    - New order: (1) build `HookContext` from DB, (2) fire `post_session_close` hooks, (3) call `_cleanup_session(session_id, skip_hooks=True)` if session is active in `_active_sessions` (prevents stale reaper double-fire), (4) delete messages via `db.messages.delete_by_session()`, (5) delete session
    - Add `_build_hook_context_from_db()` helper function in `chat.py` that builds `HookContext` from the database (for when session is not in `_active_sessions`)
    - _Requirements: 1.7_

  - [ ]* 9.2 Write unit test: `delete_session` fires hooks before data deletion
    - Mock hook manager and DB delete methods
    - Call `delete_session`, verify `fire_post_session_close` was called BEFORE `delete_by_session` and `delete_session`
    - Verify hooks receive valid `HookContext` with correct message count
    - _Requirements: 1.7_

  - [ ]* 9.3 Write unit test: `delete_session` calls `_cleanup_session(skip_hooks=True)` for active sessions
    - Set up a session that exists in both DB and `_active_sessions`
    - Call `delete_session`, verify `_cleanup_session` is called with `skip_hooks=True`
    - This prevents the stale reaper from firing hooks again later for the same session
    - _Requirements: 1.7_

- [x] 10. Integration: Modify `disconnect_all` in `AgentManager` (REGRESSION-CRITICAL)
  - [x] 10.1 Update `disconnect_all` to fire hooks in outer loop, then cleanup with `skip_hooks=True`
    - **REGRESSION: Double hook execution on shutdown**
    - Iterate over `list(self._active_sessions.keys())`
    - For each session: build `HookContext`, call `await self._hook_manager.fire_post_session_close(context)`
    - Then call `await self._cleanup_session(session_id, skip_hooks=True)` for resource cleanup only
    - This prevents double hook execution: hooks fire once in the outer loop, `_cleanup_session` skips them
    - _Requirements: 1.7 (shutdown trigger)_

  - [ ]* 10.2 Write unit test: Hooks fire exactly once per session during shutdown
    - Register a mock hook that counts invocations per session_id
    - Set up 3 active sessions in `_active_sessions`
    - Call `disconnect_all()`
    - Verify each session's hook was called exactly once (not zero, not twice)
    - _Requirements: 1.7_

- [x] 11. Integration: Remove per-turn `_auto_commit_workspace()` call (REGRESSION-CRITICAL)
  - [x] 11.1 Remove the `_auto_commit_workspace()` call from `_run_query_on_client()` at line ~1796
    - **REGRESSION: `_auto_commit_workspace()` removal from per-turn path**
    - Delete the `await self._auto_commit_workspace()` call that fires after every `ResultMessage`
    - Keep the `_auto_commit_workspace()` method itself (mark as deprecated) for backward compatibility
    - The auto-commit behavior is now handled by `WorkspaceAutoCommitHook` at session close
    - _Requirements: 3.3_

  - [ ]* 11.2 Write unit test: `_auto_commit_workspace()` no longer fires per-turn
    - Mock `_auto_commit_workspace` and run a simulated query via `_run_query_on_client`
    - Verify `_auto_commit_workspace` was NOT called during the query processing
    - Verify `WorkspaceAutoCommitHook` IS called when the session closes with a smart commit message (not `Session: {title}`)
    - _Requirements: 3.3, 3.4_


- [x] 12. Checkpoint — Verify integration points and regression prevention
  - Ensure all tests pass, ask the user if questions arise.
  - Specifically verify: `_cleanup_session` get-before-pop ordering, all 3 error-path calls use `skip_hooks=True`, `delete_session` reordering, `disconnect_all` single-fire, `_auto_commit_workspace` removal from per-turn path.

- [x] 13. Integration: DailyActivity loading improvement and distillation flag in `_build_system_prompt`
  - [x] 13.1 Modify `_build_system_prompt()` to load last 2 DailyActivity files by filename date
    - **Change from**: hardcoded today + yesterday loading
    - **Change to**: scan `Knowledge/DailyActivity/` directory, sort `*.md` files by filename (YYYY-MM-DD.md), take the 2 most recent
    - If fewer than 2 files exist, load all available
    - Continue to apply `TOKEN_CAP_PER_DAILY_FILE` per file
    - Label each section with its date: `## Daily Activity (YYYY-MM-DD)`
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 13.2 Add `.needs_distillation` flag check to `_build_system_prompt()`
    - After loading DailyActivity files, check if `Knowledge/DailyActivity/.needs_distillation` exists
    - If flag exists, append "Memory Maintenance Required" section instructing agent to run `s_memory-distill`
    - _Requirements: 5.3, 5.4_

  - [ ]* 13.3 Write property test: DailyActivity loading selects last 2 files by date (Property 16)
    - **Property 16: DailyActivity loading selects last 2 files by date**
    - Generate sets of DailyActivity files with YYYY-MM-DD.md filenames including date gaps (weekends, holidays), verify exactly the 2 most recent files by filename sort are loaded
    - **Validates: Requirements 9.1, 9.2**

  - [ ]* 13.4 Write unit test: DailyActivity loading with date gaps (e.g., skip weekend)
    - Create files for Monday, Wednesday, Friday (skipping Tue/Thu)
    - Verify `_build_system_prompt()` loads Friday and Wednesday (the 2 most recent), not today/yesterday
    - Create files for Friday only, verify it loads just that one file
    - Create no files, verify graceful handling (no crash, no DailyActivity section)
    - _Requirements: 9.1, 9.2_

  - [ ]* 13.5 Write property test: Distillation flag file triggers system prompt injection (Property 17)
    - **Property 17: Distillation flag file triggers system prompt injection**
    - When `.needs_distillation` flag exists, verify `_build_system_prompt()` output contains "Memory Maintenance Required" section
    - When flag does not exist, verify the section is absent
    - **Validates: Requirements 5.3, 5.4**


- [x] 14. On-demand skill: `s_save-activity`
  - [x] 14.1 Create `backend/skills/s_save-activity/SKILL.md` skill definition
    - Define trigger phrases: "save activity", "save daily activity"
    - Describe the skill's behavior: extract DailyActivity from current conversation using `SummarizationPipeline`, append to today's DailyActivity file, confirm to user
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 14.2 Implement `s_save-activity` skill handler
    - Reuse `SummarizationPipeline` and `write_daily_activity()` from shared infrastructure
    - Retrieve current session's conversation log up to the current point
    - On success: confirm to user with file path
    - On failure: report descriptive error to user
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 14.3 Write unit test: `s_save-activity` uses same `SummarizationPipeline` as automatic hook
    - Verify both the skill and the `DailyActivityExtractionHook` use the same `SummarizationPipeline` class
    - Feed identical conversation logs to both paths, verify identical `StructuredSummary` output
    - _Requirements: 4.2_

- [x] 15. Verify existing skills remain unchanged
  - [x] 15.1 Verify `s_save-memory` skill is not modified
    - Confirm no changes to `s_save-memory` skill files
    - _Requirements: 5.1_

  - [x] 15.2 Verify `s_memory-distill` skill is not modified
    - Confirm no changes to `s_memory-distill` skill files
    - _Requirements: 5.2_

- [x] 16. Checkpoint — Verify all hooks, skills, and integration
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 17. Regression prevention: Comprehensive integration tests
  - [ ]* 17.1 Write integration test: Full TTL expiry flow
    - Simulate a session that expires via the stale session reaper
    - Verify: hooks fire in order (extraction → auto-commit → distillation check), DailyActivity file is written, session is cleaned up after hooks complete
    - _Requirements: 1.6, 2.1_

  - [ ]* 17.2 Write integration test: Full explicit delete flow
    - Simulate `DELETE /api/chat/sessions/{id}` for a session with messages
    - Verify: hooks fire before data deletion, messages are readable by hooks, then messages and session are deleted
    - _Requirements: 1.7_

  - [ ]* 17.3 Write integration test: Full shutdown flow
    - Set up 3 active sessions, call `disconnect_all()`
    - Verify: hooks fire exactly once per session, all sessions are cleaned up, no double-fire
    - _Requirements: 1.7_

  - [ ]* 17.4 Write integration test: Error-path cleanup does not fire hooks
    - Simulate conversation errors at the 3 error-recovery call sites (lines ~1361, ~1532, ~2327)
    - Verify: `_cleanup_session(skip_hooks=True)` is called, hooks are NOT fired, session is still cleaned up properly
    - _Requirements: 1.8_

  - [ ]* 17.5 Write integration test: `delete_session` for active session prevents stale reaper double-fire
    - Set up a session in both `_active_sessions` and DB
    - Call `delete_session` endpoint
    - Verify: hooks fire once via `delete_session`, session is removed from `_active_sessions` with `skip_hooks=True`, stale reaper finds nothing to clean
    - _Requirements: 1.7_

  - [ ]* 17.6 Write integration test: Auto-commit fires only at session close with smart messages
    - Run a simulated multi-turn conversation (3+ messages)
    - Verify: `_auto_commit_workspace()` is NOT called during any turn
    - Close the session, verify: `WorkspaceAutoCommitHook` fires exactly once
    - Verify: commit message uses conventional prefix from file categories, NOT the user's first message
    - Test with `.context/` changes → verify `framework:` prefix
    - Test with `Knowledge/` changes → verify `content:` prefix
    - Test with only `.claude/skills/` config syncs → verify `chore: session sync` or skip
    - _Requirements: 3.3, 3.2, 3.6_

  - [ ]* 17.7 Write integration test: Concurrent session closes produce correct DailyActivity
    - Use `asyncio.gather` to close 5 sessions simultaneously
    - Verify: DailyActivity file has correct `sessions_count`, all 5 session entries are present, no corruption from concurrent `fcntl.flock` writes
    - _Requirements: 7.6_

- [x] 18. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Run full test suite: `cd backend && pytest`
  - Run property tests only: `cd backend && pytest -m hypothesis`

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (17 properties from design)
- Regression-prevention tasks (8, 9, 10, 11) are explicitly called out as REGRESSION-CRITICAL and must not be skipped
- All code is Python (FastAPI backend), test framework is pytest + Hypothesis
- Test files go in `backend/tests/` following the organization in the design document
