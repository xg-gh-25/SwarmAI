# Tasks: SwarmWS Restructure + Git Init

## Tasks

- [ ] 1. Simplify swarm_workspace_manager.py
  - [ ] 1.1 Replace all constants (FOLDER_STRUCTURE, SYSTEM_MANAGED_*, PROJECT_SYSTEM_*)
  - [ ] 1.2 Delete template constants (CONTEXT_L0_TEMPLATE, CONTEXT_L1_TEMPLATE, SYSTEM_PROMPTS_TEMPLATE, all related strings)
  - [ ] 1.3 Rewrite `create_folder_structure()` — only create Knowledge/, Projects/, .gitignore
  - [ ] 1.4 Rewrite `verify_integrity()` — only check Knowledge/ and Projects/ exist
  - [ ] 1.5 Delete `_populate_sample_data()` and all sample file creation code
  - [ ] 1.6 Delete `KNOWLEDGE_SECTIONS` constant and related depth checks
  - _Requirements: 1.1-1.8_

- [ ] 2. Add git initialization
  - [ ] 2.1 Add `GITIGNORE_CONTENT` constant and `_ensure_git_repo()` method
  - [ ] 2.2 Call from `ensure_default_workspace()` after folder structure creation
  - [ ] 2.3 Handle git-not-installed gracefully
  - _Requirements: 2.1-2.4_

- [ ] 3. Checkpoint — verify workspace creation
  - Verify SwarmWS creates with only Knowledge/, Projects/, .git/, .gitignore

- [ ] 4. Add session auto-commit
  - [ ] 4.1 Add `_auto_commit_workspace()` to AgentManager
  - [ ] 4.2 Call after ResultMessage in `_run_query_on_client()`
  - _Requirements: 3.1-3.4_

- [ ] 5. Verify context refresh works for new sessions
  - [ ] 5.1 Verify that `ContextDirectoryLoader.load_all()` detects stale L1 cache when source files change
  - [ ] 5.2 Verify that running sessions are NOT affected when `.context/` files change
  - [ ] 5.3 Verify that a new session after a `.context/` file edit picks up the fresh content
  - _Requirements: 4.1-4.5_

- [ ] 6. Delete legacy context code
  - [ ] 6.1 Delete `backend/core/context_assembler.py`
  - [ ] 6.2 Delete `backend/core/context_snapshot_cache.py`
  - [ ] 6.3 Delete `backend/core/context_manager.py`
  - [ ] 6.4 Delete `backend/core/tscc_snapshot_manager.py`
  - [ ] 6.5 Remove all imports of deleted modules from production code
  - [ ] 6.6 Remove ContextAssembler invocation from `_build_system_prompt()`
  - [ ] 6.7 Remove TSCC snapshot manager wiring from `main.py`
  - _Requirements: 5.1-5.8_

- [ ] 7. Delete legacy tests
  - [ ] 7.1 Delete all test files that import deleted modules
  - [ ] 7.2 Fix any remaining test imports that reference deleted code
  - _Requirements: 5.9_

- [ ] 8. Simplify TSCC to system prompt viewer
  - [ ] 8.1 Add system prompt metadata storage in `_build_system_prompt()` (file list, token counts, full text)
  - [ ] 8.2 Add `GET /api/chat/{session_id}/system-prompt` endpoint in tscc.py
  - [ ] 8.3 Simplify `TSCCStateManager` — remove agent_activity, tool_invocation, sources_updated, capability_activated handling
  - [ ] 8.4 Delete `backend/core/telemetry_emitter.py` and all imports
  - [ ] 8.5 Remove TSCC snapshot endpoints from tscc.py
  - [ ] 8.6 Remove telemetry event emission from `_run_query_on_client()` in agent_manager.py
  - _Requirements: 6.1-6.8_

- [ ] 9. Simplify TSCC frontend
  - [ ] 9.1 Replace `TSCCModules.tsx` (5 modules) with single `SystemPromptModule` showing file list + token counts
  - [ ] 9.2 Add "View Full Prompt" modal fetching from `/api/chat/{session_id}/system-prompt`
  - [ ] 9.3 Simplify `useTSCCState` hook — fetch metadata from endpoint, remove telemetry event processing
  - [ ] 9.4 Remove telemetry event handling from `useChatStreamingLifecycle`
  - _Requirements: 6.1-6.8_

- [ ] 10. Final checkpoint
  - Run `pytest` and `npm test -- --run`
  - Verify no imports of deleted modules remain
  - Verify SwarmWS creates cleanly with new structure + git
  - Verify TSCC popover shows system prompt metadata
