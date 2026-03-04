# Tasks: SwarmWS Restructure + Git Init

## Tasks

- [x] 1. Simplify swarm_workspace_manager.py
  - [x] 1.1 Replace all constants (FOLDER_STRUCTURE, SYSTEM_MANAGED_*, PROJECT_SYSTEM_*)
  - [x] 1.2 Delete template constants (CONTEXT_L0_TEMPLATE, CONTEXT_L1_TEMPLATE, SYSTEM_PROMPTS_TEMPLATE, all related strings)
  - [x] 1.3 Rewrite `create_folder_structure()` — only create Knowledge/, Projects/, .gitignore
  - [x] 1.4 Rewrite `verify_integrity()` — only check Knowledge/ and Projects/ exist
  - [x] 1.5 Delete `_populate_sample_data()` and all sample file creation code
  - [x] 1.6 Delete `KNOWLEDGE_SECTIONS` constant and related depth checks
  - [x] 1.7 Update `_should_include()` in `workspace_api.py` to allow `.context` directory
  - [x] 1.8 Remove `is_system_managed` field from workspace tree endpoint response and delete `is_system_managed()` method from `swarm_workspace_manager.py`
  - _Requirements: 1.1-1.10_

- [x] 2. Add git initialization
  - [x] 2.1 Add `GITIGNORE_CONTENT` constant and `_ensure_git_repo()` method
  - [x] 2.2 Call from `ensure_default_workspace()` after folder structure creation
  - [x] 2.3 Handle git-not-installed gracefully
  - _Requirements: 2.1-2.4_

- [x] 3. Checkpoint — verify workspace creation
  - Verify SwarmWS creates with only Knowledge/, Projects/, .git/, .gitignore

- [x] 4. Add session auto-commit
  - [x] 4.1 Add `_auto_commit_workspace()` to AgentManager
  - [x] 4.2 Call after ResultMessage in `_run_query_on_client()`
  - _Requirements: 3.1-3.4_

- [x] 5. Update ContextDirectoryLoader and AgentManager for SwarmWS/.context/
  - [x] 5.1 Update `_is_l1_fresh()` in ContextDirectoryLoader to use `git diff --quiet .context/` with mtime fallback
  - [x] 5.2 Remove TOCTOU double-check in `_load_l1_if_fresh()` (git is atomic)
  - [x] 5.3 Update `_build_system_prompt()` in agent_manager.py: change `context_dir` from `get_app_data_dir() / ".context"` to `Path(working_directory) / ".context"`
  - [x] 5.4 Verify new sessions pick up fresh context after `.context/` file edits
  - [x] 5.5 Verify running sessions are NOT affected by `.context/` changes
  - _Requirements: 4.1-4.5_

- [x] 6. Delete legacy context code
  - [x] 6.1 Delete `backend/core/context_assembler.py`
  - [x] 6.2 Delete `backend/core/context_snapshot_cache.py`
  - [x] 6.3 Delete `backend/core/context_manager.py`
  - [x] 6.4 Delete `backend/core/tscc_snapshot_manager.py`
  - [x] 6.5 Remove all imports of deleted modules from production code
  - [x] 6.6 Remove ContextAssembler invocation from `_build_system_prompt()`
  - [x] 6.7 Remove TSCC snapshot manager wiring from `main.py`
  - _Requirements: 5.1-5.8_

- [x] 7. Delete legacy tests
  - [x] 7.1 Delete all test files that import deleted modules
  - [x] 7.2 Fix any remaining test imports that reference deleted code
  - _Requirements: 5.9_

- [x] 8. Simplify TSCC to system prompt viewer
  - [x] 8.1 Add system prompt metadata storage in `_build_system_prompt()` (file list, token counts, full text)
  - [x] 8.2 Add `GET /api/chat/{session_id}/system-prompt` endpoint in tscc.py
  - [x] 8.3 Simplify `TSCCStateManager` — remove agent_activity, tool_invocation, sources_updated, capability_activated handling
  - [x] 8.4 Delete `backend/core/telemetry_emitter.py` and all imports
  - [x] 8.5 Remove TSCC snapshot endpoints from tscc.py
  - [x] 8.6 Remove telemetry event emission from `_run_query_on_client()` in agent_manager.py
  - _Requirements: 6.1-6.8_

- [x] 9. Simplify TSCC frontend
  - [x] 9.1 Replace `TSCCModules.tsx` (5 modules) with single `SystemPromptModule` showing file list + token counts
  - [x] 9.2 Add "View Full Prompt" modal fetching from `/api/chat/{session_id}/system-prompt`
  - [x] 9.3 Simplify `useTSCCState` hook — fetch metadata from endpoint, remove telemetry event processing
  - [x] 9.4 Remove telemetry event handling from `useChatStreamingLifecycle`
  - _Requirements: 6.1-6.8_

- [x] 10. Final checkpoint
  - Run `pytest` and `npm test -- --run`
  - Verify no imports of deleted modules remain
  - Verify SwarmWS creates cleanly with new structure + git
  - Verify TSCC popover shows system prompt metadata
