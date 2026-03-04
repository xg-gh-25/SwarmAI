# Requirements: SwarmWS Restructure + Git Init

## Introduction

Simplify SwarmWS to a clean, git-backed user workspace. Remove all legacy system-managed files and context loaders. Context is fully owned by `~/.swarm-ai/.context/` via `ContextDirectoryLoader`. SwarmWS becomes a minimal workspace with `Knowledge/`, `Projects/`, and git history.

## Requirements

### Requirement 1: Simplified Folder Structure

1. `FOLDER_STRUCTURE` SHALL be `["Knowledge", "Projects"]`
2. `SYSTEM_MANAGED_FOLDERS` SHALL be `{"Knowledge", "Projects"}`
3. `SYSTEM_MANAGED_ROOT_FILES` SHALL be empty
4. `SYSTEM_MANAGED_SECTION_FILES` SHALL be empty
5. `PROJECT_SYSTEM_FILES` SHALL be `{".project.json"}`
6. `PROJECT_SYSTEM_FOLDERS` SHALL be empty
7. `create_folder_structure()` SHALL only create `Knowledge/` and `Projects/` + `.gitignore`
8. `verify_integrity()` SHALL only check `Knowledge/` and `Projects/` exist

### Requirement 2: Git Repository

1. `ensure_default_workspace()` SHALL call `git init` if `.git/` does not exist
2. A `.gitignore` SHALL be created with entries for DB, caches, and OS files
3. An initial commit SHALL be created with all existing files
4. If `git` is not available, log warning and continue (non-blocking)

### Requirement 3: Session Auto-Commit

1. After a chat session completes (ResultMessage), auto-commit workspace changes
2. Commit message format: `Session: {title_first_50_chars}`
3. Run in background thread (non-blocking)
4. Failures are logged and ignored

### Requirement 4: Context Refresh (Background, Non-Disruptive)

1. Context refresh SHALL run in the background and SHALL NOT affect any running chat session
2. When `.context/` source files change, the L1 cache (`L1_SYSTEM_PROMPTS.md`) SHALL be regenerated
3. Running sessions SHALL keep their existing SDK client and frozen system prompt — no interruption
4. Only NEW sessions SHALL pick up refreshed context (via `ContextDirectoryLoader.load_all()` at session creation)
5. The refresh SHALL be invisible to the user — no UI indication, no session restart

### Requirement 5: Delete All Legacy Context Code

1. Delete `backend/core/context_assembler.py`
2. Delete `backend/core/context_snapshot_cache.py`
3. Delete `backend/core/context_manager.py`
4. Delete `backend/core/tscc_snapshot_manager.py`
5. Delete `backend/core/telemetry_emitter.py`
6. Remove all imports and invocations of deleted modules
7. Remove ContextAssembler invocation from `_build_system_prompt()`
8. Delete all template constants from `swarm_workspace_manager.py`
9. Delete all tests that import deleted modules

### Requirement 6: Simplify TSCC to System Prompt Viewer

1. THE TSCC popover SHALL display the assembled system prompt metadata: loaded context files, token counts per file, truncation status, and total token usage
2. THE TSCC popover SHALL provide a "View Full Prompt" action that shows the complete assembled system prompt text
3. THE following TSCC modules SHALL be deleted: `WhatAIDoingModule`, `ActiveSourcesModule`, `ActiveAgentsModule`, `KeySummaryModule`
4. THE `TSCCStateManager` SHALL be simplified to only track system prompt metadata (context files loaded, token counts) — remove agent_activity, tool_invocation, sources_updated, capability_activated event handling
5. THE `telemetry_emitter.py` SHALL be deleted — TSCC no longer tracks tool activity
6. THE TSCC snapshot endpoints SHALL be removed from `backend/routers/tscc.py`
7. A new endpoint `GET /api/chat/{session_id}/system-prompt` SHALL return the assembled system prompt for a session
8. THE `useTSCCState` hook SHALL be simplified to fetch system prompt metadata instead of processing telemetry events
