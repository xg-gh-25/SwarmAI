# Requirements Document

## Introduction

SwarmAI's memory system (MEMORY.md + DailyActivity) is the persistent brain of the application. It currently suffers from five reliability concerns: concurrent write corruption from multi-tab sessions, unbounded DailyActivity token injection into the system prompt, fragile text-directive-only distillation, unreliable "processed" detection based on file moves, and session-end hooks that cannot fire reliably. This spec addresses all five concerns to make the memory system stable, robust, and data-loss-free.

## Glossary

- **Memory_System**: The combination of MEMORY.md and DailyActivity files that provide persistent context across SwarmAI sessions.
- **File_Lock_Manager**: A new Python module providing per-file advisory locking for concurrent write protection on MEMORY.md and DailyActivity files.
- **DailyActivity_File**: A Markdown file at `Knowledge/DailyActivity/YYYY-MM-DD.md` containing raw session observations for a single day, with optional YAML frontmatter.
- **MEMORY_File**: The curated long-term memory file at `.context/MEMORY.md`, loaded into the system prompt at priority 7.
- **Context_Directory_Loader**: The existing `ContextDirectoryLoader` class in `backend/core/context_directory_loader.py` responsible for assembling context files into the system prompt.
- **Agent_Manager**: The existing `AgentManager` class in `backend/core/agent_manager.py` that builds system prompts and manages agent sessions.
- **System_Prompt_Builder**: The component within `_build_system_prompt()` that assembles DailyActivity content ephemerally after L1 cache load.
- **Distillation_Skill**: A new built-in skill at `backend/skills/s_memory-distill/SKILL.md` providing structured instructions for promoting DailyActivity content into MEMORY.md.
- **Frontmatter**: YAML metadata block at the top of a Markdown file delimited by `---` lines, used to store processing state such as `distilled: true`.
- **Token_Cap**: A hard limit on the number of tokens injected from a single DailyActivity file into the system prompt (2000 tokens).
- **Archives_Directory**: The directory at `Knowledge/Archives/` where aged DailyActivity files are moved after 30 days.

## Requirements

### Requirement 1: Concurrent Write Protection for Memory Files

**User Story:** As a user running multiple SwarmAI chat sessions in parallel tabs, I want file-level locking on MEMORY.md and DailyActivity writes, so that concurrent sessions never overwrite each other's data.

#### Acceptance Criteria

1. WHEN two or more Agent_Manager sessions attempt to write to the same DailyActivity_File simultaneously, THE File_Lock_Manager SHALL serialize the writes using per-file advisory locks so that all appended content is preserved.
2. WHEN an Agent_Manager session writes to a DailyActivity_File, THE File_Lock_Manager SHALL use append-only mode so that existing content is never overwritten.
3. WHEN an Agent_Manager session writes to the MEMORY_File, THE File_Lock_Manager SHALL acquire an exclusive lock, read the current content, apply modifications, and write back within the same lock scope.
4. THE File_Lock_Manager SHALL use `fcntl.flock()` on Unix and `msvcrt.locking()` on Windows for cross-platform advisory locking.
5. THE File_Lock_Manager SHALL maintain independent locks per file path so that a lock on MEMORY_File does not block writes to any DailyActivity_File.
6. IF a lock cannot be acquired within 5 seconds, THEN THE File_Lock_Manager SHALL raise a timeout error and log the failure without corrupting the target file.
7. THE File_Lock_Manager SHALL release the lock in a `finally` block so that locks are released even when exceptions occur during the write operation.
8. THE File_Lock_Manager SHALL expose a context manager interface (`with file_lock(path):`) for use by all memory write call sites.

### Requirement 2: DailyActivity Token Cap in System Prompt

**User Story:** As a user, I want DailyActivity content in the system prompt to be capped at a fixed token budget, so that a busy day's log does not squeeze out higher-priority context.

#### Acceptance Criteria

1. WHEN the System_Prompt_Builder reads a DailyActivity_File for injection into the system prompt, THE System_Prompt_Builder SHALL enforce a Token_Cap of 2000 tokens per file.
2. WHEN a DailyActivity_File exceeds the Token_Cap, THE System_Prompt_Builder SHALL truncate from the head of the file, keeping the newest entries (tail) since DailyActivity is append-only.
3. WHEN truncation occurs, THE System_Prompt_Builder SHALL prepend a marker `[Truncated: kept newest ~2000 tokens]` to the injected content.
4. THE System_Prompt_Builder SHALL apply the Token_Cap only to the content injected into the system prompt, leaving the DailyActivity_File on disk unmodified.
5. THE System_Prompt_Builder SHALL use `ContextDirectoryLoader.estimate_tokens()` for consistent token estimation across the system.
6. FOR ALL DailyActivity_Files read by the System_Prompt_Builder, the injected token count per file SHALL be at most Token_Cap plus the overhead of the truncation marker.

### Requirement 3: Structured Distillation Skill

**User Story:** As a user, I want distillation of DailyActivity into MEMORY.md to be driven by a structured built-in skill rather than free-form text directives, so that distillation happens consistently and produces high-quality results.

#### Acceptance Criteria

1. THE Distillation_Skill SHALL exist at `backend/skills/s_memory-distill/SKILL.md` as a built-in skill available to all workspaces.
2. WHEN the agent detects more than 7 unprocessed DailyActivity_Files at session start, THE agent SHALL invoke the Distillation_Skill automatically.
3. THE Distillation_Skill SHALL scan all DailyActivity_Files in `Knowledge/DailyActivity/` and identify files without `distilled: true` in their Frontmatter as unprocessed.
4. THE Distillation_Skill SHALL extract key decisions, lessons learned, recurring themes, user corrections, and error resolutions from unprocessed DailyActivity_Files.
5. THE Distillation_Skill SHALL write distilled content to the appropriate sections of the MEMORY_File (Recent Context, Patterns and Preferences, Open Threads).
6. WHEN distillation of a DailyActivity_File is complete, THE Distillation_Skill SHALL add `distilled: true` and a `distilled_date: YYYY-MM-DD` entry to the file's Frontmatter.
7. THE Distillation_Skill SHALL run Archives auto-pruning: move DailyActivity_Files older than 30 days to Archives_Directory, and delete archived files older than 90 days.
8. THE Distillation_Skill SHALL acquire the File_Lock_Manager lock on MEMORY_File before writing distilled content.
9. THE Distillation_Skill SHALL perform all operations silently without announcing or requesting permission from the user.
10. THE Distillation_Skill SHALL preserve all existing MEMORY_File content and only append or update sections, never removing user-written content.

### Requirement 4: Frontmatter-Based Unprocessed Detection

**User Story:** As a user, I want DailyActivity processing state tracked via YAML frontmatter instead of file moves, so that detection is robust and files remain in place until auto-pruned.

#### Acceptance Criteria

1. THE Distillation_Skill SHALL determine a DailyActivity_File's processing state by reading its Frontmatter for a `distilled: true` field.
2. WHEN a DailyActivity_File has no Frontmatter or lacks a `distilled` field, THE Distillation_Skill SHALL treat the file as unprocessed.
3. WHEN the Distillation_Skill marks a file as processed, THE Distillation_Skill SHALL insert or update the Frontmatter block at the top of the file with `distilled: true` and `distilled_date: YYYY-MM-DD`.
4. THE Memory_System SHALL keep DailyActivity_Files in `Knowledge/DailyActivity/` until they are older than 30 days, at which point they are moved to Archives_Directory.
5. THE Memory_System SHALL delete files in Archives_Directory that are older than 90 days.
6. IF a DailyActivity_File's Frontmatter is malformed or unreadable, THEN THE Distillation_Skill SHALL treat the file as unprocessed and log a warning.
7. THE Distillation_Skill SHALL acquire the File_Lock_Manager lock on the target DailyActivity_File before modifying its Frontmatter.

### Requirement 5: Session-Start Open Threads Review

**User Story:** As a user, I want Open Threads in MEMORY.md reviewed at session start instead of session end, so that the review actually happens reliably since there is no session-end hook in the Claude Agent SDK.

#### Acceptance Criteria

1. WHEN a new agent session starts, THE agent SHALL review the MEMORY_File's "Open Threads" section and update it based on work completed since the last session.
2. THE AGENT.md template SHALL replace the directive "At session end: update MEMORY.md's Open Threads" with "At session start: review and update MEMORY.md's Open Threads based on completed work."
3. THE STEERING.md template SHALL replace the "At session end (if asked)" block with a "At session start" block that instructs the agent to review Open Threads and mark completed items.
4. WHEN the Distillation_Skill runs, THE Distillation_Skill SHALL also update the Open Threads section by cross-referencing recent DailyActivity_Files for thread completions.
5. THE agent SHALL perform Open Threads review silently without announcing the review to the user.

### Requirement 6: Frontmatter Parser and Printer

**User Story:** As a developer, I want a utility to parse and write YAML frontmatter in DailyActivity Markdown files, so that frontmatter-based state tracking is reliable.

#### Acceptance Criteria

1. THE Memory_System SHALL provide a `parse_frontmatter(content: str)` function that returns a tuple of (metadata_dict, body_str) from a Markdown file's content.
2. THE Memory_System SHALL provide a `write_frontmatter(metadata: dict, body: str)` function that produces a valid Markdown string with YAML frontmatter delimited by `---` lines.
3. WHEN the input content has no frontmatter block, THE `parse_frontmatter` function SHALL return an empty dict and the full content as body.
4. WHEN the input content has a valid `---` delimited frontmatter block, THE `parse_frontmatter` function SHALL parse the YAML and return the metadata dict and remaining body.
5. IF the YAML in the frontmatter block is malformed, THEN THE `parse_frontmatter` function SHALL return an empty dict, the full content as body, and log a warning.
6. FOR ALL valid metadata dicts and body strings, parsing then printing then parsing SHALL produce an equivalent metadata dict and body string (round-trip property).
7. THE `write_frontmatter` function SHALL produce output where the frontmatter block starts on line 1 with `---` and ends with `---` followed by a blank line before the body.
