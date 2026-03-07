# Requirements Document

## Introduction

SwarmAI's agent relies on soft text directives in AGENT.md and STEERING.md to write DailyActivity files and trigger memory distillation. In practice, these directives are routinely ignored because the agent's attention shifts to the user's actual request — the memory protocol competes for attention and loses. This feature moves memory persistence from "something the agent should remember to do" to "something the app enforces as a system-level behavior."

The solution centers on a general-purpose session lifecycle hook framework that fires when sessions truly close (TTL expiry, explicit delete, backend restart) — not on every turn. The first two consumers of this hook system are:
1. **Post-session DailyActivity extraction** — Automatically extract key points from the full conversation log into DailyActivity files (no agent involvement)
2. **Workspace auto-commit migration** — Move the existing per-turn `_auto_commit_workspace()` to fire once per session close, replacing the current noisy per-message commits

Users retain control via on-demand skills: `s_save-memory` for MEMORY.md writes (unchanged), a new `s_save-activity` skill for mid-conversation DailyActivity extraction, and `s_memory-distill` for promoting DailyActivity into MEMORY.md (now also triggered automatically by the hook when undistilled files accumulate).

Both the automatic hook and the on-demand skill share the same conversation summarization pipeline, ensuring consistent extraction logic.

## Glossary

- **Session_Lifecycle_Hook_Manager**: Backend component that manages registration and execution of hooks at session lifecycle events (post-session-close). General-purpose — not memory-specific.
- **Post_Session_Close**: A session lifecycle event that fires when a session ends. Triggers include: TTL expiry (12-hour idle timeout cleaned by the stale session reaper), explicit session deletion by the user via `DELETE /api/chat/sessions/{id}`, and backend restart/shutdown. This event does NOT fire on every turn or `ResultMessage`.
- **DailyActivity_Extractor**: Backend service that parses conversation logs and produces structured DailyActivity markdown files. Used by both the automatic post-session hook and the on-demand `s_save-activity` skill.
- **Summarization_Pipeline**: Shared extraction logic that converts a Conversation_Log into a structured summary (topics, decisions, files modified, open questions). Consumed by both DailyActivity_Extractor and on-demand skills.
- **Compliance_Tracker**: Backend component that tracks extraction metrics (sessions processed, files written, failures) for observability.
- **DailyActivity_File**: A markdown file at `Knowledge/DailyActivity/YYYY-MM-DD.md` inside SwarmWS containing session observations, decisions, and context for that date. Uses YAML frontmatter for metadata.
- **Conversation_Log**: The sequence of user and assistant messages stored in the database for a given session.
- **SwarmWS**: The SwarmAI workspace directory at `~/.swarm-ai/SwarmWS/`.
- **Distillation**: The process of promoting recurring themes and key decisions from DailyActivity files into MEMORY.md, performed by the `s_memory-distill` skill.


## Requirements

### Requirement 1: Session Lifecycle Hook Framework

**User Story:** As a SwarmAI developer, I want a general-purpose hook system for session close events, so that multiple post-session behaviors (DailyActivity extraction, workspace auto-commit, distillation triggers) can be registered and executed reliably without coupling them to specific application code.

#### Acceptance Criteria

1. THE Session_Lifecycle_Hook_Manager SHALL support registering hooks for the `post_session_close` lifecycle event
2. WHEN a Post_Session_Close event fires, THE Session_Lifecycle_Hook_Manager SHALL execute all registered hooks for that event in registration order
3. IF a registered hook raises an exception, THEN THE Session_Lifecycle_Hook_Manager SHALL catch the exception, log the error with the hook name and session ID, and continue executing remaining hooks
4. THE Session_Lifecycle_Hook_Manager SHALL pass the session context (session ID, agent ID, conversation message count, session start time) to each hook
5. WHILE post-session-close hooks are executing, THE Session_Lifecycle_Hook_Manager SHALL run them asynchronously so the session cleanup completes without blocking
6. WHEN the stale session reaper cleans up a session due to TTL expiry (12-hour idle timeout), THE Session_Lifecycle_Hook_Manager SHALL fire the `post_session_close` event before the session is removed
7. WHEN a user explicitly deletes a session via `DELETE /api/chat/sessions/{id}`, THE Session_Lifecycle_Hook_Manager SHALL fire the `post_session_close` event before the session data is deleted
7. WHEN the backend shuts down gracefully, THE Session_Lifecycle_Hook_Manager SHALL fire the `post_session_close` event for each active session before shutdown completes
8. WHEN `_cleanup_session` is called from an error-recovery path (conversation error, broken session), THE Session_Lifecycle_Hook_Manager SHALL NOT fire hooks because the conversation may be incomplete and the session may be recreated

### Requirement 2: Post-Session DailyActivity Extraction

**User Story:** As a SwarmAI user, I want the app to automatically extract DailyActivity entries when a session closes, so that my work history is captured without relying on the agent to remember.

#### Acceptance Criteria

1. WHEN a Post_Session_Close event fires, THE DailyActivity_Extractor SHALL be invoked as a registered `post_session_close` hook to extract a summary from the Conversation_Log
2. WHEN invoked, THE DailyActivity_Extractor SHALL retrieve the full Conversation_Log for the closed session from the database
3. WHEN the Conversation_Log is retrieved, THE DailyActivity_Extractor SHALL pass it to the Summarization_Pipeline to produce a structured summary
4. WHEN the summary is produced, THE DailyActivity_Extractor SHALL append the summary to the DailyActivity_File for the current date (`Knowledge/DailyActivity/YYYY-MM-DD.md`) inside SwarmWS
5. WHILE a DailyActivity_File for the current date already exists, THE DailyActivity_Extractor SHALL append a new session section with a timestamp header rather than overwriting existing content
6. IF the DailyActivity_Extractor fails to write the file, THEN THE Session_Lifecycle_Hook_Manager SHALL log the error with the session ID and continue executing remaining hooks
7. IF the Conversation_Log contains fewer than 3 messages, THEN THE DailyActivity_Extractor SHALL produce a minimal entry with only the `### Topics` section
8. THE DailyActivity_Extractor SHALL complete extraction within 10 seconds for conversations up to 100 messages

### Requirement 3: Smart Workspace Auto-Commit

**User Story:** As a SwarmAI user, I want workspace auto-commits to happen once per session close with intelligent commit messages derived from actual file changes, so that my git history is meaningful, noise-free, and categorized by change type.

#### Acceptance Criteria

1. WHEN a Post_Session_Close event fires, THE WorkspaceAutoCommitHook SHALL be invoked as a registered `post_session_close` hook
2. THE WorkspaceAutoCommitHook SHALL analyze changed files via `git diff --stat` and categorize changes by file path pattern into conventional commit prefixes: `framework:` (`.context/*`), `skills:` (`.claude/skills/*`), `content:` (`Knowledge/*`), `project:` (`Projects/*`), `output:` (`*.pdf`, `*.pptx`, `*.docx`), `chore:` (mixed/minor)
3. THE WorkspaceAutoCommitHook SHALL generate a commit message from the actual diff (e.g., `skills: config sync (18 files)` or `content: add comparative analysis`) instead of using the user's first message
4. THE WorkspaceAutoCommitHook SHALL replace the existing per-turn `_auto_commit_workspace()` call that currently fires after every `ResultMessage` at line 1796 of `agent_manager.py`
5. WHEN the workspace has no uncommitted changes, THE WorkspaceAutoCommitHook SHALL skip the commit silently
6. WHEN all changes are trivial (only skill config syncs with no user-initiated content changes), THE WorkspaceAutoCommitHook SHALL either skip the commit or use a `chore: session sync` message
7. IF the git operations fail, THEN THE WorkspaceAutoCommitHook SHALL log a warning and continue without blocking other hooks
5. IF the git commit operation fails, THEN THE workspace auto-commit hook SHALL log a warning and continue without blocking other hooks

### Requirement 4: On-Demand DailyActivity Save Skill

**User Story:** As a SwarmAI user, I want to trigger DailyActivity extraction mid-conversation by saying "save activity" or "save daily activity", so that I can capture important context without waiting for the session to close.

#### Acceptance Criteria

1. WHEN the user invokes the `s_save-activity` skill (via "save activity", "save daily activity", or similar triggers), THE DailyActivity_Extractor SHALL extract a summary from the current session's Conversation_Log up to that point
2. THE `s_save-activity` skill SHALL use the same Summarization_Pipeline as the automatic post-session hook to ensure consistent extraction logic
3. WHEN the extraction completes, THE `s_save-activity` skill SHALL append the summary to the DailyActivity_File for the current date inside SwarmWS
4. WHEN the on-demand extraction succeeds, THE `s_save-activity` skill SHALL confirm to the user that the DailyActivity entry was written, including the file path
5. IF the on-demand extraction fails, THEN THE `s_save-activity` skill SHALL report the error to the user with a descriptive message

### Requirement 5: Existing Skill Integration

**User Story:** As a SwarmAI user, I want the existing memory skills to continue working as-is, with the distillation skill additionally triggered automatically when undistilled files accumulate, so that MEMORY.md stays current without manual intervention.

#### Acceptance Criteria

1. THE `s_save-memory` skill SHALL continue to operate unchanged for direct MEMORY.md writes
2. THE `s_memory-distill` skill SHALL continue to operate unchanged when invoked manually by the user
3. WHEN the post-session DailyActivity extraction hook completes successfully, THE DistillationTriggerHook SHALL check the count of undistilled DailyActivity files (where the `distilled` frontmatter field is `false` or absent)
4. WHEN the count of undistilled DailyActivity files exceeds 7, THE DistillationTriggerHook SHALL write a `.needs_distillation` flag file in the DailyActivity directory so the next session's system prompt instructs the agent to run `s_memory-distill`
5. IF the flag file write fails, THEN THE DistillationTriggerHook SHALL log the error and continue without blocking

### Requirement 6: Conversation Summarization Pipeline

**User Story:** As a SwarmAI developer, I want a shared extraction pipeline that converts conversation logs into structured summaries, so that both the automatic hook and the on-demand skill produce consistent, high-quality DailyActivity entries.

#### Acceptance Criteria

1. THE Summarization_Pipeline SHALL accept a Conversation_Log (list of messages) and produce a structured summary containing: topics discussed, decisions made, files modified, and open questions
2. THE Summarization_Pipeline SHALL extract topics by identifying the primary subject of each user message and grouping related messages
3. THE Summarization_Pipeline SHALL extract decisions by identifying assistant responses that contain explicit choices, recommendations, or conclusions
4. THE Summarization_Pipeline SHALL extract file paths from tool_use events of type `Write`, `Edit`, `Read`, and `Bash` in the Conversation_Log
5. THE Summarization_Pipeline SHALL extract open questions from `ask_user_question` events and unresolved discussion threads
6. THE Summarization_Pipeline SHALL produce summaries that are at most 500 words per session entry
7. WHEN the Summarization_Pipeline is invoked by the automatic post-session hook, THE Summarization_Pipeline SHALL return the same structured output as when invoked by the on-demand `s_save-activity` skill

### Requirement 7: DailyActivity File Format

**User Story:** As a SwarmAI user, I want DailyActivity files to follow a consistent, parseable format with YAML frontmatter, so that both the agent and the distillation process can read them reliably.

#### Acceptance Criteria

1. THE DailyActivity_Extractor SHALL write DailyActivity files with YAML frontmatter containing: `date` (string, YYYY-MM-DD), `sessions_count` (integer), and `distilled` (boolean, default false)
2. THE DailyActivity_Extractor SHALL write each session entry under a level-2 heading with the format `## Session — HH:MM | {session_id[:8]} | {title}`
3. THE DailyActivity_Extractor SHALL include the following subsections in each session entry: `### What Happened`, `### Key Decisions`, `### Files Modified`, `### Open Questions`
4. WHEN a DailyActivity_File is parsed via `parse_frontmatter` and then formatted back via `write_frontmatter`, THE result SHALL produce an equivalent file (round-trip property)
5. THE DailyActivity_Extractor SHALL increment the `sessions_count` in the frontmatter each time a new session entry is appended
6. THE DailyActivity_Extractor SHALL use atomic read-modify-write with file locking (`fcntl.flock`) for all file writes to ensure concurrency safety when multiple sessions close simultaneously

### Requirement 8: Compliance Observability

**User Story:** As a SwarmAI developer, I want to track whether DailyActivity extractions are happening consistently, so that I can detect and debug enforcement failures.

#### Acceptance Criteria

1. THE Compliance_Tracker SHALL maintain a count of sessions processed and DailyActivity files written per day
2. THE Compliance_Tracker SHALL expose compliance metrics via a backend API endpoint at `/api/memory-compliance`
3. WHEN a DailyActivity write succeeds, THE Compliance_Tracker SHALL increment the success counter for the current date
4. WHEN a DailyActivity write fails, THE Compliance_Tracker SHALL increment the failure counter for the current date and record the error reason
5. THE Compliance_Tracker SHALL retain metrics for the most recent 30 days

### Requirement 9: DailyActivity Loading Improvement

**User Story:** As a SwarmAI user, I want the system to load the most recent DailyActivity files at session start regardless of date gaps, so that my context is preserved even when I skip days (weekends, holidays).

#### Acceptance Criteria

1. WHEN building the system prompt, THE `_build_system_prompt()` method SHALL load the last 2 DailyActivity files by filename date (YYYY-MM-DD.md sort order) instead of hardcoding today and yesterday
2. WHEN fewer than 2 DailyActivity files exist, THE `_build_system_prompt()` method SHALL load all available files
3. THE `_build_system_prompt()` method SHALL continue to apply the per-file token cap (`TOKEN_CAP_PER_DAILY_FILE`) to each loaded DailyActivity file
4. THE `_build_system_prompt()` method SHALL label each loaded DailyActivity section with its date (e.g., `## Daily Activity (2025-07-15)`)
