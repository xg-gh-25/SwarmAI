# Requirements Document

## Introduction

Replace the current scattered context injection system in SwarmAI with a single centralized directory at `~/.swarm-ai/.context/`. The directory contains 9 user-editable source files and 2 auto-generated cache files, all assembled into the system prompt at session start. This eliminates the hybrid DB/filesystem storage for context, makes all context files human-editable and version-controllable, and adds model-aware compaction (L0/L1) for different context window sizes.

## Glossary

- **Context_Directory**: The `~/.swarm-ai/.context/` directory containing all context source files and auto-generated cache files
- **ContextDirectoryLoader**: The new Python module (`backend/core/context_directory_loader.py`) responsible for reading, assembling, and caching context files from the Context_Directory
- **Source_File**: One of the 9 user-editable markdown files in the Context_Directory (SWARMAI.md, IDENTITY.md, SOUL.md, AGENT.md, USER.md, STEERING.md, MEMORY.md, KNOWLEDGE.md, PROJECTS.md)
- **L1_Cache**: The auto-generated `L1_SYSTEM_PROMPTS.md` file containing the full concatenation of all Source_Files with section headers, used for models with 128K+ context windows
- **L0_Cache**: The auto-generated `L0_SYSTEM_PROMPTS.md` file containing a compact/compressed version of all Source_Files, used for models with 32K–64K context windows
- **Token_Budget**: The maximum number of tokens allocated for the system prompt context assembly (default 25,000 tokens)
- **Priority**: An integer (0–8) assigned to each Source_File that determines assembly order and truncation order; lower numbers indicate higher priority
- **SystemPromptBuilder**: The existing module (`backend/core/system_prompt.py`) that builds non-file system prompt sections (safety principles, datetime, runtime metadata)
- **ContextAssembler**: The existing 8-layer module (`backend/core/context_assembler.py`) that handles project-scoped context injection; coexists with the ContextDirectoryLoader
- **AgentSandboxManager**: The existing module (`backend/core/agent_sandbox_manager.py`) that copies template files into workspace directories
- **Mtime**: File modification timestamp used to determine cache freshness

## Requirements

### Requirement 1: Context Directory Initialization

**User Story:** As a SwarmAI user, I want the context directory to be automatically created with default templates on first startup, so that I have a working set of context files without manual setup.

#### Acceptance Criteria

1. WHEN SwarmAI starts and the Context_Directory does not exist, THE ContextDirectoryLoader SHALL create the `~/.swarm-ai/.context/` directory and copy all 9 Source_File defaults from `backend/context/` into the Context_Directory
2. WHEN SwarmAI starts and the Context_Directory already exists, THE ContextDirectoryLoader SHALL preserve all existing files and only copy templates for Source_Files that are missing
3. THE ContextDirectoryLoader SHALL copy the L0_Cache and L1_Cache template files into the Context_Directory when they do not already exist
4. IF a template file cannot be copied due to a filesystem error, THEN THE ContextDirectoryLoader SHALL log a warning and continue initializing the remaining files

### Requirement 2: Context File Loading and Assembly

**User Story:** As a SwarmAI user, I want all my context files assembled into the system prompt in a predictable priority order, so that the agent has consistent access to my identity, preferences, and knowledge.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL read all 9 Source_Files from the Context_Directory and assemble them in ascending Priority order (SWARMAI.md at Priority 0 first, PROJECTS.md at Priority 8 last)
2. WHEN a Source_File exists and contains non-empty content, THE ContextDirectoryLoader SHALL include the file content in the assembled output with a section header matching the file's section name
3. WHEN a Source_File does not exist or is empty, THE ContextDirectoryLoader SHALL skip the file and continue assembling the remaining Source_Files
4. THE ContextDirectoryLoader SHALL return the assembled context as a single string with section headers separated by double newlines
5. FOR ALL valid sets of Source_Files, loading then serializing to L1_Cache then loading from L1_Cache SHALL produce equivalent assembled output (round-trip property)

### Requirement 3: Token Budget Enforcement

**User Story:** As a SwarmAI developer, I want the context assembly to respect a configurable token budget, so that the system prompt does not consume excessive context window space.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL enforce a configurable Token_Budget (default 25,000 tokens) on the total assembled context output
2. WHEN the total assembled context exceeds the Token_Budget, THE ContextDirectoryLoader SHALL truncate Source_Files starting from the lowest Priority (Priority 8 PROJECTS.md first, then Priority 7 KNOWLEDGE.md, and so on)
3. THE ContextDirectoryLoader SHALL mark SWARMAI.md (Priority 0), IDENTITY.md (Priority 1), and SOUL.md (Priority 2) as non-truncatable and never remove content from these files during budget enforcement
4. WHEN a Source_File is truncated, THE ContextDirectoryLoader SHALL append a truncation indicator showing the original and truncated token counts for that section
5. FOR ALL assembled outputs after budget enforcement, the total token count SHALL be less than or equal to the Token_Budget plus the combined token count of non-truncatable files (when non-truncatable files alone exceed the budget)

### Requirement 4: L1 Cache Generation and Freshness

**User Story:** As a SwarmAI user, I want the full system prompt to be cached so that session startup is fast when my context files have not changed.

#### Acceptance Criteria

1. WHEN the ContextDirectoryLoader assembles context from Source_Files for a model with a context window of 64K tokens or more, THE ContextDirectoryLoader SHALL write the assembled output to the L1_Cache file in the Context_Directory
2. WHEN a session starts and the L1_Cache file exists, THE ContextDirectoryLoader SHALL compare the Mtime of the L1_Cache against the Mtime of each Source_File to determine freshness
3. WHILE the L1_Cache Mtime is newer than or equal to the Mtime of every Source_File, THE ContextDirectoryLoader SHALL return the L1_Cache content directly without re-reading Source_Files
4. WHEN any Source_File has an Mtime newer than the L1_Cache, THE ContextDirectoryLoader SHALL re-assemble from Source_Files and regenerate the L1_Cache
5. IF the L1_Cache file cannot be written due to a filesystem error, THEN THE ContextDirectoryLoader SHALL log a warning and return the assembled content without caching

### Requirement 5: L0 Compact Cache for Small Models

**User Story:** As a SwarmAI user running smaller models, I want a compact version of my context that fits within limited context windows, so that the agent still has access to essential context.

#### Acceptance Criteria

1. WHEN the ContextDirectoryLoader loads context for a model with a context window less than 64K tokens, THE ContextDirectoryLoader SHALL return the L0_Cache content instead of the full Source_File assembly
2. WHEN the L0_Cache file does not exist and a small model is requested, THE ContextDirectoryLoader SHALL fall back to assembling from Source_Files with aggressive truncation (skipping KNOWLEDGE.md and PROJECTS.md for models under 32K)
3. THE L0_Cache SHALL contain a compressed representation of all 9 Source_Files where each file is reduced to its essential directives without examples or prose
4. WHEN the L0_Cache is regenerated, THE ContextDirectoryLoader SHALL record a generation timestamp in the L0_Cache file content

### Requirement 6: Model-Aware Context Selection

**User Story:** As a SwarmAI developer, I want the loader to automatically select the right context level based on the model's context window, so that each model gets the most context it can handle.

#### Acceptance Criteria

1. WHEN the model context window is 128K tokens or more, THE ContextDirectoryLoader SHALL use the L1_Cache or load Source_Files directly with the full Token_Budget
2. WHEN the model context window is between 64K and 128K tokens, THE ContextDirectoryLoader SHALL use the L1_Cache with aggressive truncation of KNOWLEDGE.md and PROJECTS.md to fit within a reduced Token_Budget
3. WHEN the model context window is between 32K and 64K tokens, THE ContextDirectoryLoader SHALL use the L0_Cache
4. WHEN the model context window is less than 32K tokens, THE ContextDirectoryLoader SHALL use the L0_Cache and exclude KNOWLEDGE.md and PROJECTS.md content entirely

### Requirement 7: Integration with System Prompt Builder

**User Story:** As a SwarmAI developer, I want the ContextDirectoryLoader to integrate cleanly with the existing SystemPromptBuilder, so that the final system prompt includes both file-based context and runtime metadata.

#### Acceptance Criteria

1. THE AgentManager SHALL invoke the ContextDirectoryLoader before the SystemPromptBuilder during system prompt construction in the `_build_system_prompt` method
2. WHEN the ContextDirectoryLoader returns assembled context, THE AgentManager SHALL append the context to the agent configuration's `system_prompt` field before passing the configuration to the SystemPromptBuilder
3. THE SystemPromptBuilder SHALL continue to provide non-file sections (safety principles, datetime, runtime metadata, workspace path) independently of the ContextDirectoryLoader
4. THE SystemPromptBuilder SHALL NOT load any files from the `.swarmai/` directory or any other filesystem path — all file-based context is provided exclusively by the ContextDirectoryLoader. The methods `_section_user_identity()`, `_section_project_context()`, `_section_extra_prompt()`, and `_load_workspace_file()` SHALL be removed.
5. THE ContextDirectoryLoader output and the ContextAssembler output (project-scoped context) SHALL coexist in the final system prompt without conflict, with the ContextDirectoryLoader output appearing before the ContextAssembler output

### Requirement 8: No Legacy Migration

**User Story:** As a SwarmAI developer, I want a clean break from legacy context sources with no migration logic, so that the codebase stays simple and there is a single source of truth.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL NOT read from `.swarmai/` directory, `Knowledge/Memory/` directory, or DB `agents.system_prompt` field for any purpose
2. WHEN SwarmAI starts with the new ContextDirectoryLoader active, THE system SHALL use only the files in `~/.swarm-ai/.context/` as context sources — legacy sources are ignored entirely
3. THE ContextDirectoryLoader SHALL NOT contain any migration logic, file-copying from legacy paths, or DB-to-filesystem extraction code

### Requirement 9: Delete AgentSandboxManager

**User Story:** As a SwarmAI developer, I want the AgentSandboxManager removed entirely since its responsibilities are fully replaced by ContextDirectoryLoader.

#### Acceptance Criteria

1. THE `AgentSandboxManager` class and its module (`backend/core/agent_sandbox_manager.py`) SHALL be deleted
2. ALL callers of `AgentSandboxManager` (template copying, `main_workspace` property, `ensure_templates_in_directory`) SHALL be updated to use `ContextDirectoryLoader.ensure_directory()` or `initialization_manager.get_cached_workspace_path()` as appropriate
3. THE global `agent_sandbox_manager` singleton import SHALL be removed from all modules that reference it

### Requirement 10: Token Estimation

**User Story:** As a SwarmAI developer, I want accurate token estimation for context files, so that budget enforcement produces predictable results.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL estimate token counts using a consistent estimation method (approximately 4 characters per token) for all Source_Files and assembled output
2. WHEN estimating tokens for budget enforcement, THE ContextDirectoryLoader SHALL include section headers and separator whitespace in the token count
3. FOR ALL text inputs, the token estimation function SHALL return a positive integer proportional to the input length
4. THE ContextDirectoryLoader SHALL expose the token estimation function as a static or class method for use by other modules

### Requirement 11: Error Resilience

**User Story:** As a SwarmAI user, I want the agent to start successfully even if context files are corrupted or missing, so that context issues never block my work.

#### Acceptance Criteria

1. IF the Context_Directory cannot be created due to a permissions error, THEN THE ContextDirectoryLoader SHALL log an error and return an empty string for the assembled context
2. IF a Source_File contains invalid UTF-8 encoding, THEN THE ContextDirectoryLoader SHALL skip the file, log a warning, and continue assembling the remaining files
3. IF the entire context loading process raises an unexpected exception, THEN THE AgentManager SHALL catch the exception, log the error, and proceed with agent execution using only the SystemPromptBuilder output
4. THE ContextDirectoryLoader SHALL complete context loading within 500 milliseconds for a typical set of Source_Files (total size under 100KB) on local filesystem

### Requirement 12: Filesystem-Only Storage

**User Story:** As a SwarmAI user, I want all context stored exclusively on the filesystem, so that I can edit files with any text editor, version-control them with git, and copy them between machines.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL read context exclusively from the filesystem at `~/.swarm-ai/.context/` and SHALL NOT query the database for any context content
2. THE AgentManager SHALL NOT read the `agents.system_prompt` DB field for context content — the field SHALL be removed from the prompt assembly pipeline entirely
3. THE Context_Directory SHALL contain only markdown files that are human-readable and editable with any text editor
4. FOR ALL Source_Files in the Context_Directory, the file content SHALL be the single source of truth for that context category with no secondary copy in the database

### Requirement 13: Token Estimation Consistency (PE Fix)

**User Story:** As a SwarmAI developer, I want token estimation to be consistent between the ContextDirectoryLoader and the existing ContextAssembler, so that combined token counts are accurate when both contribute to the same system prompt.

#### Acceptance Criteria

1. THE ContextDirectoryLoader SHALL use the same word-based token estimation formula as `ContextAssembler.estimate_tokens()`: `tokens = max(1, int(word_count * 4 / 3))` for non-empty text, `0` for empty/whitespace-only text
2. THE ContextDirectoryLoader SHALL NOT use a character-based estimation formula (e.g., `len(text) // 4`) as this would produce inconsistent counts when combined with ContextAssembler output

### Requirement 14: L1 Cache TOCTOU Safety (PE Fix)

**User Story:** As a SwarmAI developer, I want the L1 cache to be safe against time-of-check-to-time-of-use races, so that the agent always sees the latest context even if a file is edited during session startup.

#### Acceptance Criteria

1. AFTER reading L1_Cache content, THE ContextDirectoryLoader SHALL re-verify that no Source_File Mtime has changed since the initial freshness check
2. IF any Source_File Mtime changed during the L1_Cache read, THEN THE ContextDirectoryLoader SHALL discard the cached content and re-assemble from Source_Files

### Requirement 16: Delete backend/templates/ and Legacy Bootstrap Code

**User Story:** As a SwarmAI developer, I want the old templates folder and its associated bootstrap code removed, so that there is a single source of truth for context files with no dead code.

#### Acceptance Criteria

1. THE `backend/templates/` directory and all files within it (AGENTS.md, BOOTSTRAP.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, SWARMAI.md, USER.md) SHALL be deleted
2. THE `AgentSandboxManager` class SHALL stop referencing `backend/templates/` for template file copying and SHALL be updated to use `backend/context/` as the source for default context files
3. THE `AgentSandboxManager.TEMPLATE_FILES` list and `_copy_templates()` method SHALL be removed or refactored to only handle context directory initialization via `ContextDirectoryLoader`
4. THE agent bootstrap code in `agent_defaults.py` that reads `SWARMAI.md` from `backend/templates/` and writes it to the DB `agents.system_prompt` field SHALL be removed
5. THE `SystemPromptBuilder._load_workspace_file()` method SHALL stop reading from `.swarmai/` directory since those files are superseded by `~/.swarm-ai/.context/`
6. ALL references to `backend/templates/` in production code SHALL be replaced with references to `backend/context/`
