# Requirements Document

## Introduction

Enhance SwarmAI's context file system based on learnings from OpenClaw. This covers modifications to existing context file templates (STEERING.md, AGENT.md, USER.md, KNOWLEDGE.md), introduction of new context files (TOOLS.md, BOOTSTRAP.md), and new mechanisms (daily notes, channel behavior rules). Changes span three priority tiers: P0 (immediate template edits), P1 (new files/mechanisms), and P2 (code changes for daily notes and channel behavior).

Context files live as templates in `backend/context/` and are deployed to `~/.swarm-ai/SwarmWS/.context/` on startup via `ContextDirectoryLoader.ensure_directory()`. System-default files (⚙️) are overwritten on startup and should be readonly for users; user-customized files (👤) are never overwritten and are freely editable. The `CONTEXT_FILES` list in `context_directory_loader.py` controls which files are loaded into the system prompt with priority-based ordering and token budget enforcement.

## Glossary

- **Context_Directory**: The `~/.swarm-ai/SwarmWS/.context/` directory containing all context files loaded into the agent's system prompt.
- **Templates_Directory**: The `backend/context/` directory containing built-in template files that are copied to the Context_Directory on startup.
- **ContextDirectoryLoader**: The Python class in `backend/core/context_directory_loader.py` responsible for loading, assembling, and caching context files.
- **CONTEXT_FILES**: The ordered list of `ContextFileSpec` entries in `context_directory_loader.py` that defines which files are loaded, their priority, section name, and truncatability.
- **ContextFileSpec**: A named tuple defining a context file's metadata: filename, priority (0=highest), section_name, truncatable flag, and user_customized flag.
- **System_Default_File**: A context file marked with ⚙️ that is managed by SwarmAI and overwritten from templates on every startup.
- **User_Customized_File**: A context file marked with 👤 that belongs to the user and is never overwritten by SwarmAI.
- **Token_Budget**: The maximum token count (default 25,000) for assembled context output.
- **L1_Cache**: The full concatenation cache file (`L1_SYSTEM_PROMPTS.md`) used for models with context windows >= 64K tokens.
- **SwarmWS**: The SwarmAI workspace root directory at `~/.swarm-ai/SwarmWS/`.
- **Knowledge_Directory**: The `SwarmWS/Knowledge/` directory tree (top-level name unchanged). Current subdirectories created by `KNOWLEDGE_SUBDIRS` in `swarm_workspace_manager.py`: `Knowledge Base/` and `Notes/`. This spec replaces them with: `Notes/`, `Reports/`, `Meetings/`, `Library/`, `Archives/`, `DailyActivity/`.
- **DailyActivity_Directory**: The `SwarmWS/Knowledge/DailyActivity/` directory for auto-created daily activity logs (replaces the DailyNotes concept).
- **Onboarding_Flow**: The first-run conversational process triggered by BOOTSTRAP.md to populate USER.md with user preferences.
- **Channel**: A communication interface (e.g., Feishu, CLI, Web) through which the agent interacts with the user.

## Requirements

### Requirement 1: Rewrite Memory Protocol in STEERING.md

**User Story:** As the SwarmAI creator, I want the Memory Protocol section in STEERING.md to emphasize writing things down over mental notes, so that the agent reliably persists important discoveries to files instead of relying on ephemeral in-context memory.

#### Acceptance Criteria

1. THE STEERING.md default template in the Templates_Directory SHALL contain a Memory Protocol section that instructs the agent to write discoveries to MEMORY.md or `Knowledge/Notes/` instead of noting them mentally. This applies to new installations; existing users retain their current STEERING.md per Requirement 10.
2. THE STEERING.md template SHALL include the directive "写下来。文件 > 大脑。如果值得记住，就写到 MEMORY.md 或 Knowledge/Notes/" in the Memory Protocol section.
3. THE STEERING.md template SHALL remove the phrase "note important discoveries mentally" from the Memory Protocol section.
4. THE STEERING.md template SHALL retain the existing Memory Protocol behaviors for session start (read MEMORY.md silently), explicit "remember this" commands (update MEMORY.md immediately), and session end persistence.

### Requirement 2: Add Trash-Over-Delete Safety Principle to AGENT.md

**User Story:** As the SwarmAI creator, I want AGENT.md to include a `trash > rm` safety principle, so that the agent prefers recoverable deletion over permanent deletion by default.

#### Acceptance Criteria

1. THE AGENT.md template SHALL include a safety rule stating that the agent prefers `trash` or `mv` operations over `rm` for file deletion.
2. THE AGENT.md Safety Rules section SHALL state the principle as "recoverable > permanent" for destructive file operations.
3. WHEN the agent needs to delete a file, THE AGENT.md directive SHALL instruct the agent to move the file to a trash location or use a recoverable method before resorting to permanent deletion.

### Requirement 3: Add Humanistic Footer to USER.md

**User Story:** As the SwarmAI creator, I want USER.md to include a humanistic footer reminding the agent that it is getting to know a person rather than building a dossier, so that the agent approaches user information with respect and empathy.

#### Acceptance Criteria

1. THE USER.md template SHALL include a footer section containing the text "你是在了解一个人，而不是在建立档案。尊重这两者之间的区别。"
2. THE USER.md footer SHALL appear after the existing closing guidance text at the bottom of the file.
3. THE USER.md template SHALL retain its User_Customized_File designation (👤 marker) so that user edits are never overwritten.

### Requirement 4: Create BOOTSTRAP.md First-Run Onboarding

**User Story:** As a new SwarmAI user, I want a conversational onboarding experience on first run that helps me fill in my USER.md, so that the agent can personalize responses from the very first session.

#### Acceptance Criteria

1. WHEN `ensure_directory()` runs and USER.md in the Context_Directory contains only the empty template placeholders, THE ContextDirectoryLoader SHALL create a BOOTSTRAP.md file in the Context_Directory.
2. THE BOOTSTRAP.md file SHALL contain instructions for the agent to initiate a conversational onboarding flow that gathers user preferences (name, timezone, language, role, work context, communication style).
3. WHEN the onboarding flow completes and USER.md has been populated, THE agent SHALL delete or mark BOOTSTRAP.md as completed so that the onboarding flow does not trigger again.
4. THE BOOTSTRAP.md file SHALL NOT be included in the CONTEXT_FILES list because it is a one-time onboarding artifact, not a persistent context file.
5. WHEN BOOTSTRAP.md exists in the Context_Directory, THE agent SHALL detect its presence at session start and prioritize the onboarding conversation before other tasks.
6. IF USER.md already contains user-provided content beyond the empty template, THEN THE ContextDirectoryLoader SHALL NOT create BOOTSTRAP.md.

### Requirement 5: Refocus KNOWLEDGE.md on the Knowledge Directory

**User Story:** As the SwarmAI creator, I want KNOWLEDGE.md to serve as an index and guide for the `Knowledge/` folder (notes, reports, reference materials) rather than a place for tech stack information, so that the agent uses KNOWLEDGE.md to navigate the Knowledge_Directory and stores domain knowledge in the appropriate subfolder files.

#### Acceptance Criteria

1. THE KNOWLEDGE.md template SHALL describe its purpose as an index of the Knowledge_Directory contents (`SwarmWS/Knowledge/`), including Notes, Reports, Meetings, Library, Archives, and DailyActivity subfolders.
2. THE KNOWLEDGE.md template SHALL remove the current "Tech Stack", "Coding Conventions", "Architecture Notes", and "Reference" placeholder sections.
3. THE KNOWLEDGE.md template SHALL include guidance for the agent to add an entry to KNOWLEDGE.md whenever a new file is created in the Knowledge_Directory.
4. THE KNOWLEDGE.md template SHALL include a section structure for indexing files by subfolder (Notes, Reports, Meetings, Library, Archives, DailyActivity).
5. THE KNOWLEDGE.md template SHALL retain its User_Customized_File designation (👤 marker) so that user edits are never overwritten.

### Requirement 6: Introduce TOOLS.md Context File

**User Story:** As a SwarmAI user, I want a dedicated TOOLS.md context file for environment-specific configuration (device names, SSH hosts, local tool preferences, network paths), so that this information is separated from Skills and available to the agent in every session.

#### Acceptance Criteria

1. THE Templates_Directory SHALL contain a new TOOLS.md template file with placeholder sections for device names, SSH hosts, local tool preferences, network paths, and environment-specific configuration.
2. THE CONTEXT_FILES list in context_directory_loader.py SHALL include a ContextFileSpec entry for TOOLS.md with a priority between STEERING.md (priority 5) and MEMORY.md (priority 6).
3. THE TOOLS.md ContextFileSpec SHALL have `truncatable` set to `True`.
4. THE TOOLS.md template SHALL be designated as a User_Customized_File (👤 marker) so that user edits are never overwritten.
5. WHEN `ensure_directory()` runs, THE ContextDirectoryLoader SHALL copy the TOOLS.md template to the Context_Directory only if TOOLS.md does not already exist (copy-only-if-missing per Requirement 10, since TOOLS.md is a User_Customized_File).
6. THE effective Token_Budget (25,000–40,000 depending on model per Requirement 11) SHALL remain sufficient to accommodate the additional TOOLS.md file alongside all other context files.

### Requirement 7: Daily Activity Mechanism

**User Story:** As a SwarmAI user, I want the agent to auto-create a daily activity file in `Knowledge/DailyActivity/YYYY-MM-DD.md` at the start of each day's first session, so that session-level observations, decisions, and context accumulate in a structured daily log and can be periodically distilled into MEMORY.md.

#### Acceptance Criteria

1. WHEN the agent starts a new session and no file exists at `Knowledge/DailyActivity/YYYY-MM-DD.md` for the current date, THE agent SHALL create the daily activity file with a standard template including date header and empty sections for observations, decisions, and open questions.
2. THE agent SHALL create the DailyActivity_Directory (`Knowledge/DailyActivity/`) if it does not already exist.
3. WHILE a daily activity file exists for the current date, THE agent SHALL append noteworthy observations, decisions, and context to that file during the session rather than relying on in-context memory alone.
4. THE STEERING.md Memory Protocol section SHALL include instructions for periodic distillation: the agent reviews recent daily activity files and promotes recurring themes, key decisions, and lessons learned into MEMORY.md.
5. THE daily activity template SHALL include YAML frontmatter with `title`, `date`, and `tags` fields.
6. IF the DailyActivity_Directory contains more than 30 daily activity files, THEN THE agent SHALL automatically archive older entries to `Knowledge/Archives/` without prompting the user.

### Requirement 8: Channel Behavior Rules

**User Story:** As the SwarmAI creator, I want channel-specific behavior guidance in the agent's context, so that the agent adapts its communication style, verbosity, and interaction patterns based on the active channel (e.g., Feishu vs CLI vs Web).

#### Acceptance Criteria

1. THE AGENT.md template SHALL include a "Channel Behavior" section that defines per-channel rules for communication style, formatting, and interaction patterns.
2. THE Channel Behavior section SHALL include rules for the Feishu channel specifying when to speak, when to stay silent, and formatting differences (e.g., shorter messages, no markdown headers, emoji reactions for acknowledgment).
3. THE Channel Behavior section SHALL include rules for the CLI channel specifying concise output, minimal formatting, and direct answers.
4. THE Channel Behavior section SHALL include rules for the Web channel specifying full markdown formatting, structured responses, and interactive suggestions.
5. WHEN the agent detects the active Channel, THE agent SHALL apply the corresponding channel-specific behavior rules from the Channel Behavior section.
6. IF the active Channel is not recognized, THEN THE agent SHALL fall back to the Web channel behavior rules as the default.

### Requirement 9: Enforce Readonly on System Default Context Files

**User Story:** As the SwarmAI creator, I want System_Default_Files (SWARMAI.md, IDENTITY.md, SOUL.md, AGENT.md) to be readonly for users in the workspace explorer and file editor, so that users cannot accidentally edit files that will be overwritten on the next startup, and are guided to use STEERING.md for customization instead.

#### Acceptance Criteria

1. WHEN `ensure_directory()` copies a System_Default_File from the Templates_Directory to the Context_Directory, THE ContextDirectoryLoader SHALL set the file's filesystem permissions to readonly (e.g., `0o444` on Unix).
2. THE CONTEXT_FILES list SHALL mark SWARMAI.md (P0), IDENTITY.md (P1), SOUL.md (P2), and AGENT.md (P3) as `user_customized=False` (System_Default_File, readonly).
3. THE CONTEXT_FILES list SHALL mark USER.md (P4), STEERING.md (P5), TOOLS.md, MEMORY.md (P6), KNOWLEDGE.md (P7), and PROJECTS.md (P8) as `user_customized=True` (User_Customized_File, read-write).
4. WHEN the backend workspace file API (`GET /workspace/file`) returns a file with `user_customized=False`, THE response SHALL include a `readonly: true` field so the frontend can disable editing.
5. WHEN the frontend file editor opens a file with `readonly: true`, THE editor SHALL display the file in read-only mode with a visible banner explaining "⚙️ System Default — This file is managed by SwarmAI and refreshed on every startup. Use STEERING.md to customize behavior."
6. WHEN `ensure_directory()` overwrites a System_Default_File, THE method SHALL re-apply readonly permissions after writing the new content.
7. THE User_Customized_Files SHALL retain normal read-write permissions (e.g., `0o644` on Unix) so users can freely edit them.

### Requirement 10: User-Customized Files with Default Templates and No-Override Protection

**User Story:** As a SwarmAI user, I want user-customized context files (USER.md, STEERING.md, TOOLS.md, MEMORY.md, KNOWLEDGE.md, PROJECTS.md) to ship with sensible default templates on initial install, and once I have customized them, I want SwarmAI to never overwrite my changes on subsequent startups.

#### Acceptance Criteria

1. THE ContextFileSpec named tuple SHALL include a new `user_customized` boolean field that indicates whether the file is a User_Customized_File (user_customized=True) or a System_Default_File (user_customized=False). This single field drives both copy behavior (Req 10) and readonly enforcement (Req 9).
2. THE CONTEXT_FILES list SHALL mark USER.md, STEERING.md, TOOLS.md, MEMORY.md, KNOWLEDGE.md, and PROJECTS.md as `user_customized=True`.
3. WHEN `ensure_directory()` runs and a User_Customized_File does NOT exist in the Context_Directory, THE ContextDirectoryLoader SHALL copy the default template from the Templates_Directory to create the initial file.
4. WHEN `ensure_directory()` runs and a User_Customized_File already EXISTS in the Context_Directory, THE ContextDirectoryLoader SHALL NOT overwrite it, regardless of whether the template has changed.
5. THE `ensure_directory()` method SHALL use the `user_customized` field from CONTEXT_FILES to determine copy behavior: always-overwrite for System_Default_Files (`user_customized=False`), copy-only-if-missing for User_Customized_Files (`user_customized=True`).
6. THE Templates_Directory SHALL contain sensible default templates for all User_Customized_Files with placeholder sections, inline guidance comments, and the 👤 USER-CUSTOMIZED marker.
7. WHEN a User_Customized_File template is updated in the Templates_Directory (e.g., new sections added in a SwarmAI release), THE ContextDirectoryLoader SHALL NOT retroactively apply those template changes to existing user files — the user's version takes precedence.

### Requirement 11: Model-Aware Dynamic Token Budget

**User Story:** As the SwarmAI creator, I want the context token budget to scale dynamically based on the model's context window size, so that larger models (200K) get more room for rich context files while smaller models stay within safe limits.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL accept the model's context window size and compute the token budget as a percentage of that window, rather than using a fixed constant.
2. FOR models with context windows >= 200K tokens, THE token budget SHALL be set to 40,000 tokens (20% of 200K).
3. FOR models with context windows >= 64K and < 200K tokens, THE token budget SHALL be set to 25,000 tokens (current default).
4. FOR models with context windows < 64K tokens, THE existing L0 compact cache behavior SHALL apply (current behavior unchanged).
5. THE DEFAULT_TOKEN_BUDGET constant (25,000) SHALL be retained as the fallback when the model context window is unknown.
6. THE dynamic budget calculation SHALL be performed in `load_all()` before calling `_assemble_from_sources()`, passing the computed budget to the assembly and truncation logic.
7. THE `_enforce_token_budget()` method SHALL use the dynamically computed budget instead of `self.token_budget` when a model-specific budget has been calculated.
8. THE TSCC system prompt viewer SHALL display the current effective token budget alongside per-file token counts so users can see how much headroom remains.

### Requirement 12: Update Knowledge Directory Structure

**User Story:** As the SwarmAI creator, I want the Knowledge directory subdirectories to be updated with proper names (no spaces) and new folders for reports, meetings, reference, archives, and daily activity, so that the folder layout matches the context file conventions defined in STEERING.md and KNOWLEDGE.md.

#### Acceptance Criteria

1. THE `KNOWLEDGE_SUBDIRS` constant in `swarm_workspace_manager.py` SHALL be updated to `["Notes", "Reports", "Meetings", "Library", "Archives", "DailyActivity"]`.
2. THE legacy `Knowledge Base/` subdirectory (with space) SHALL be renamed to `Library/` during `_cleanup_legacy_content()`, preserving any existing user files inside it.
3. THE `create_folder_structure()` method SHALL create all six Knowledge subdirectories (`Notes/`, `Reports/`, `Meetings/`, `Library/`, `Archives/`, `DailyActivity/`) on workspace initialization.
4. THE `_cleanup_legacy_content()` method SHALL move `Knowledge Base/` contents to `Library/` and then remove the empty `Knowledge Base/` directory, rather than deleting user files.
5. ALL references to `Knowledge Base` in context templates (STEERING.md, KNOWLEDGE.md), tests, and documentation SHALL be updated to `Library`.
6. THE STEERING.md SwarmWS Directory Structure section SHALL reflect the updated Knowledge subdirectory names.
7. THE top-level `Knowledge/` directory name SHALL remain unchanged to avoid regressions.
8. THE `SYSTEM_MANAGED_FOLDERS` set SHALL be expanded to include all six Knowledge subdirectory paths: `Knowledge/Notes`, `Knowledge/Reports`, `Knowledge/Meetings`, `Knowledge/Library`, `Knowledge/Archives`, `Knowledge/DailyActivity`.
9. WHEN a user attempts to delete or rename a system-managed Knowledge subdirectory via the workspace API or file explorer, THE backend SHALL reject the operation with an error message explaining that system-managed directories cannot be deleted or renamed.
10. THE `verify_integrity()` method SHALL check that all six Knowledge subdirectories exist and recreate any missing ones, ensuring the structure self-heals on startup.

### Requirement 13: Revise Default Context File Templates with OpenClaw Learnings

**User Story:** As the SwarmAI creator, I want all default context file templates to be revised with best practices learned from OpenClaw's template design, so that the agent has richer, more human, and more actionable guidance out of the box.

#### Acceptance Criteria

**SOUL.md (System Default):**
1. THE SOUL.md template SHALL adopt OpenClaw's "你不是聊天机器人，你正在成为某个人" framing — emphasizing that the agent is developing a real working relationship, not just answering questions.
2. THE SOUL.md template SHALL include explicit "Good vs Bad" response examples (concise vs verbose, direct vs sycophantic) as demonstrated in OpenClaw's SOUL.md.
3. THE SOUL.md template SHALL include a "Continuity" section instructing the agent that context files ARE its memory and it should read and update them proactively.

**IDENTITY.md (System Default):**
4. THE IDENTITY.md template SHALL include an avatar field (workspace-relative path, URL, or data URI) following OpenClaw's IDENTITY.md pattern.
5. THE IDENTITY.md template SHALL encourage the agent to evolve its identity over time through the onboarding flow (Req 4), rather than being static metadata.

**AGENT.md (System Default):**
6. THE AGENT.md template SHALL include a "写下来，不要心理笔记" directive — explicitly instructing the agent to write observations to files rather than relying on in-context memory, following OpenClaw's "文件 > 大脑 📝" principle.
7. THE AGENT.md template SHALL include a "trash > rm" safety rule per Requirement 2.
8. THE AGENT.md template SHALL include a "Channel Behavior" section per Requirement 8.

**USER.md (User Customized):**
9. THE USER.md template SHALL include a "Background" section (following OpenClaw's pattern) asking what the user cares about, what projects they're working on, and what makes them tick — going beyond just role/timezone metadata.
10. THE USER.md template SHALL end with the humanistic footer per Requirement 3, plus OpenClaw's guidance: "你了解得越多，就越能提供更好的帮助。但请记住——你是在了解一个人，而不是在建立档案。"

**STEERING.md (User Customized):**
11. THE STEERING.md template SHALL include the revised Memory Protocol per Requirement 1, with "写下来。文件 > 大脑" as the core directive.
12. THE STEERING.md template SHALL include the updated SwarmWS Directory Structure reflecting the new Knowledge subdirectories (Notes, Reports, Meetings, Library, Archives, DailyActivity) per Requirement 12.
13. THE STEERING.md template SHALL include the File Saving & Knowledge Organization rules specifying default save locations per subfolder.

**MEMORY.md (Agent Managed):**
14. THE MEMORY.md template SHALL include guidance distinguishing daily activity logs (`Knowledge/DailyActivity/`) from curated long-term memory (MEMORY.md itself), following OpenClaw's two-tier memory model.
15. THE MEMORY.md template SHALL instruct the agent to periodically distill daily activity files into MEMORY.md, promoting recurring themes and key decisions.

**KNOWLEDGE.md (User Customized):**
16. THE KNOWLEDGE.md template SHALL be restructured as a Knowledge Directory index per Requirement 5, with sections for each subfolder (Notes, Reports, Meetings, Library, Archives, DailyActivity).

**PROJECTS.md (User Customized):**
17. THE PROJECTS.md template SHALL retain its current structure but add guidance for linking project folders in `SwarmWS/Projects/` to entries in PROJECTS.md, so the agent can navigate between the index and the filesystem.

**Cross-cutting:**
18. ALL revised templates SHALL preserve their existing inline comment markers (⚙️ SYSTEM DEFAULT, 👤 USER-CUSTOMIZED, 🤖 AGENT-MANAGED) at the top of each file.
19. ALL revised templates SHALL be written in English as the primary language, with Chinese translations included inline for key directives (e.g., "写下来。文件 > 大脑") where they add clarity.
20. THE revised templates SHALL NOT increase the total token count of all system-default files beyond 3,000 tokens (current ~1,500t), to leave headroom for user-customized files within the token budget.

### Requirement 14: Align System Prompt Assembly Pipeline with New Context Architecture

**User Story:** As the SwarmAI creator, I want the system prompt assembly pipeline (`ContextDirectoryLoader`, `SystemPromptBuilder`, `_build_system_prompt()`) to be fully aligned with the new context file architecture (TOOLS.md, `user_customized` field, dynamic token budget, BOOTSTRAP.md detection), so that all new context files and behaviors are correctly loaded, assembled, and reported.

#### Acceptance Criteria

**ContextDirectoryLoader updates:**
1. THE `CONTEXT_FILES` list SHALL be updated to include the new TOOLS.md entry (priority between STEERING and MEMORY) with `user_customized=True` and `truncatable=True`.
2. THE `ContextFileSpec` named tuple SHALL be extended with the `user_customized` boolean field, and all existing entries SHALL be annotated accordingly (SWARMAI/IDENTITY/SOUL/AGENT = False; USER/STEERING/TOOLS/MEMORY/KNOWLEDGE/PROJECTS = True).
3. THE `ensure_directory()` method SHALL implement the two-mode copy logic: always-overwrite for `user_customized=False` files, copy-only-if-missing for `user_customized=True` files (per Requirement 10).
4. THE `ensure_directory()` method SHALL set filesystem permissions to `0o444` (readonly) for `user_customized=False` files and `0o644` (read-write) for `user_customized=True` files after copying (per Requirement 9).
5. THE `ensure_directory()` method SHALL detect whether USER.md is an empty template and create BOOTSTRAP.md if so (per Requirement 4).
6. THE `load_all()` method SHALL compute the dynamic token budget based on model context window size before calling `_assemble_from_sources()` (per Requirement 11).

**SystemPromptBuilder updates:**
7. THE `SystemPromptBuilder` SHALL remain unchanged in its responsibility (non-file sections only: identity, safety, workspace, datetime, runtime). No context file content shall be duplicated in SystemPromptBuilder.
8. IF the `SystemPromptBuilder._section_safety()` method contains safety rules that overlap with the revised AGENT.md template (e.g., trash > rm), THE SystemPromptBuilder SHALL defer to AGENT.md and remove duplicated rules to avoid conflicting or redundant instructions.

**_build_system_prompt() updates:**
9. THE `_build_system_prompt()` method in `agent_manager.py` SHALL pass the model context window to `ContextDirectoryLoader` so the dynamic token budget is applied (currently it passes `DEFAULT_TOKEN_BUDGET` from config; it should also pass the model window for Req 11 budget calculation).
10. THE per-file metadata collection loop in `_build_system_prompt()` SHALL include the `user_customized` field in each file's metadata dict, so the TSCC system prompt viewer can display which files are system-managed vs user-customized.
11. THE prompt metadata SHALL include the effective token budget used for the current session, so the TSCC viewer can show budget vs actual usage.

**L1 Cache invalidation:**
12. WHEN the `CONTEXT_FILES` list changes (new TOOLS.md entry, updated priorities), THE L1 cache (`L1_SYSTEM_PROMPTS.md`) SHALL be automatically invalidated on the next `load_all()` call, because the `_is_l1_fresh()` git-status check will detect the changed files.
13. IF a new context file (TOOLS.md) is added to the Context_Directory but the L1 cache was generated before TOOLS.md existed, THE `_is_l1_fresh()` method SHALL correctly detect the cache as stale (the new file's presence triggers git status change).

**BOOTSTRAP.md integration:**
14. THE `_build_system_prompt()` method SHALL check for BOOTSTRAP.md existence in the Context_Directory and, if present, prepend the BOOTSTRAP.md content to the system prompt so the agent prioritizes the onboarding flow.
15. THE BOOTSTRAP.md content SHALL NOT be included in the L1 cache (it is ephemeral and should be read fresh each session until deleted).

### Requirement 15: Two-Tier Memory Coordination Protocol

**User Story:** As a SwarmAI user, I want a clear coordination protocol between MEMORY.md (long-term curated memory loaded into every session) and DailyActivity files (raw daily logs), so that important context is reliably preserved without MEMORY.md bloating or daily details being lost.

#### Acceptance Criteria

**Writing rules — what goes where:**
1. DURING a session, THE agent SHALL write raw observations, decisions, context, and open questions to the current day's `Knowledge/DailyActivity/YYYY-MM-DD.md` file.
2. THE agent SHALL NOT write raw session-level details directly to MEMORY.md during a session. MEMORY.md is for curated, distilled content only.
3. THE only user-initiated memory operation SHALL be explicit verbal commands during a chat session (e.g., "remember this", "save to memory", "remember the key decisions", "save the lessons learned"). WHEN the user issues such a command, THE agent SHALL write the specified content to MEMORY.md immediately. All other memory operations are fully automatic.
4. THE agent SHALL update MEMORY.md's "Open Threads" section at session end if there are unfinished tasks or pending decisions.

**Reading rules — what gets loaded when:**
5. AT session start, THE agent SHALL read MEMORY.md (loaded into system prompt via P6 priority). DailyActivity files are NOT loaded into the system prompt.
6. AT session start, THE agent SHALL also read today's and yesterday's DailyActivity files (if they exist) for recent context. This is a direct file read, not a system prompt injection.
7. IF the user asks about something from a previous day, THE agent SHALL search DailyActivity files for the relevant date range.

**Distillation rules — promoting daily to long-term:**
8. WHEN the DailyActivity_Directory contains more than 7 unprocessed daily files, THE agent SHALL automatically perform a distillation pass at the start of the next session without prompting or notifying the user.
9. DURING distillation, THE agent SHALL review recent DailyActivity files and promote to MEMORY.md: recurring themes, key decisions with rationale, lessons learned, user corrections, and important context that will matter in future sessions.
10. DURING distillation, THE agent SHALL NOT promote to MEMORY.md: one-off observations, transient context, information already captured in KNOWLEDGE.md or PROJECTS.md, or raw session logs.
11. AFTER distillation, THE agent SHALL move processed DailyActivity files to `Knowledge/Archives/` with a `distilled: true` marker in their YAML frontmatter.

**Size management:**
12. THE DailyActivity files SHALL have no individual size limit, but THE agent SHALL keep each day's file focused and avoid duplicating content already written to other Knowledge subfolders.
13. ALL memory coordination operations (DailyActivity writing, distillation, archival, MEMORY.md consolidation) SHALL be performed silently and automatically. The agent SHALL NOT announce, prompt, or request permission for these housekeeping operations — they are invisible system maintenance.

**STEERING.md Memory Protocol alignment:**
14. THE STEERING.md Memory Protocol section SHALL be updated to document this two-tier model, replacing the current single-tier protocol with the full coordination rules (write to DailyActivity during session, distill to MEMORY.md periodically, read both at session start).

### Requirement 16: Recency-Preserving Truncation for MEMORY.md

**User Story:** As the SwarmAI creator, I want MEMORY.md to be truncated from the top (oldest content) when it exceeds the token budget, so that the most recent and relevant memories are always preserved in the system prompt.

#### Acceptance Criteria

1. THE `_enforce_token_budget()` method SHALL support a `truncate_from` parameter on `ContextFileSpec` that specifies whether truncation removes content from the head (oldest first) or the tail (newest first).
2. THE `ContextFileSpec` named tuple SHALL be extended with a `truncate_from` field with values `"head"` (remove oldest, keep newest) or `"tail"` (remove newest, keep oldest — current default behavior).
3. THE MEMORY.md entry in `CONTEXT_FILES` SHALL be configured with `truncate_from="head"`, so that when MEMORY.md exceeds its allocated token share, the oldest entries at the top of the file are removed first and the most recent entries at the bottom are preserved.
4. ALL other truncatable context files (AGENT.md, USER.md, STEERING.md, TOOLS.md, KNOWLEDGE.md, PROJECTS.md) SHALL retain the default `truncate_from="tail"` behavior (truncate from the end).
5. WHEN truncating from the head, THE `_enforce_token_budget()` method SHALL keep the last N words of the content (most recent) rather than the first N words, and prepend the `[Truncated]` indicator at the top.
6. THE DailyActivity files are NOT loaded into the system prompt and therefore are NOT subject to token budget truncation.
