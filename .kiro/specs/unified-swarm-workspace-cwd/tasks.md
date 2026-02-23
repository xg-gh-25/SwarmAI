# Implementation Plan: Unified SwarmWorkspace CWD

## Overview

Unify all agent working directories to a single cached SwarmWorkspace path (`~/.swarm-ai/SwarmWS`). All heavy setup (folder structure, skills, templates, migration) moves to app init; per-session setup becomes a lightweight cached-path read. Remove `WorkspaceConfigResolver`, `_resolve_workspace_mode()`, per-agent workspace isolation, and frontend workspace selection.

Implementation order: data layer changes first (SwarmWorkspaceManager path + migration), then init-time setup (InitializationManager, AgentSandboxManager), then per-session simplification (AgentManager), then removals, then frontend, then cleanup.

## Tasks

- [x] 1. Update SwarmWorkspaceManager default path and add migration
  - [x] 1.1 Update `DEFAULT_WORKSPACE_CONFIG` path in `backend/core/swarm_workspace_manager.py`
    - Change `file_path` from `"{app_data_dir}/swarm-workspaces/SwarmWS"` to `"{app_data_dir}/SwarmWS"`
    - _Requirements: 2.1_

  - [x] 1.2 Add `_migrate_default_workspace_path()` to `SwarmWorkspaceManager`
    - Implement migration logic in `backend/core/swarm_workspace_manager.py`
    - Use `shutil.move()` when old path exists and new path does not
    - Log warning and keep new path when both exist
    - Update DB record to new `file_path` pattern
    - _Requirements: 2.3, 2.4, 2.5_

  - [x] 1.3 Modify `ensure_default_workspace()` to trigger migration
    - Check if existing default workspace has old path pattern `{app_data_dir}/swarm-workspaces/SwarmWS`
    - Call `_migrate_default_workspace_path()` when old path detected
    - _Requirements: 2.2, 2.3_

  - [x] 1.4 Write property test for migration preserves workspace contents (Property 10)
    - **Property 10: Migration preserves workspace contents**
    - Generate random file trees at old path, run migration, verify all files present at new path and DB record updated
    - Create `backend/tests/test_property_workspace_migration.py`
    - **Validates: Requirements 2.3, 2.4, 2.5**

  - [x] 1.5 Write unit tests for migration edge cases
    - Test: old path exists, new does not → moved
    - Test: both paths exist → new kept, warning logged
    - Test: neither exists → DB updated, no crash
    - Test: `DEFAULT_WORKSPACE_CONFIG["file_path"]` equals `"{app_data_dir}/SwarmWS"`
    - Add tests to `backend/tests/test_swarm_workspace_manager.py`
    - _Requirements: 2.1, 2.3, 2.4, 2.5_

- [x] 2. Implement `setup_workspace_skills()` in AgentSandboxManager
  - [x] 2.1 Add `setup_workspace_skills()` method to `backend/core/agent_sandbox_manager.py`
    - Create `.claude/skills/` directory if missing
    - Query all available skill names from DB
    - Remove stale symlinks not in current skill set
    - Add missing symlinks for skills not yet linked
    - Skip and log warning for missing skill source files
    - _Requirements: 3.1, 3.2, 3.4, 3.5_

  - [x] 2.2 Write property test for skill symlink set equals all available skills (Property 4)
    - **Property 4: Skill symlink set equals all available skills**
    - Generate random sets of skill names, call `setup_workspace_skills()`, verify symlink names match exactly
    - Create `backend/tests/test_property_skill_symlinks.py`
    - **Validates: Requirements 3.2, 3.4**

  - [x] 2.3 Write property test for skill symlink idempotence (Property 5)
    - **Property 5: Skill symlink idempotence**
    - Generate random skill sets, call `setup_workspace_skills()` twice, verify filesystem state identical after both calls
    - Add to `backend/tests/test_property_skill_symlinks.py`
    - **Validates: Requirements 3.2, 3.4**

  - [x] 2.4 Write unit tests for skill edge cases
    - Test: empty skill set removes all existing symlinks
    - Test: missing skill source file logs warning, other skills still linked
    - Add to `backend/tests/test_property_skill_symlinks.py`
    - _Requirements: 3.4, 3.5_

- [x] 3. Wire skill re-sync into skill CRUD API routes
  - [x] 3.1 Call `setup_workspace_skills()` after skill create/update/delete in `backend/routers/skills.py`
    - Get cached workspace path from `initialization_manager`
    - Call `agent_sandbox_manager.setup_workspace_skills(Path(workspace_path))` after each CRUD operation
    - _Requirements: 3.3_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Expand `InitializationManager.run_full_initialization()` with workspace setup
  - [x] 5.1 Add workspace setup steps to `backend/core/initialization_manager.py`
    - After `ensure_default_workspace()`: get expanded workspace path
    - Call `agent_sandbox_manager.setup_workspace_skills(path)` to symlink all skills
    - Call `agent_sandbox_manager.ensure_templates_in_directory(path)` to copy templates
    - Cache the expanded path as `self._cached_workspace_path`
    - _Requirements: 1.1, 3.2, 4.1, 8.1_

  - [x] 5.2 Add `get_cached_workspace_path()` accessor to `InitializationManager`
    - Return `self._cached_workspace_path`
    - Include fallback: compute from `DEFAULT_WORKSPACE_CONFIG` if not set
    - _Requirements: 1.1, 1.2_

  - [x] 5.3 Write property test for cached path equals expanded default (Property 1)
    - **Property 1: Cached path equals expanded default workspace path**
    - Verify `get_cached_workspace_path()` returns `expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"])`
    - Create `backend/tests/test_property_cached_workspace_path.py`
    - **Validates: Requirements 1.1, 1.2, 2.1**

  - [x] 5.4 Write property test for template idempotence (Property 6)
    - **Property 6: Template idempotence**
    - Generate random template content modifications, call `ensure_templates_in_directory()`, verify modified files preserved and missing files created
    - Create `backend/tests/test_property_template_idempotence.py`
    - **Validates: Requirements 4.1, 4.2**

  - [x] 5.5 Write property test for non-destructive folder structure integrity (Property 8)
    - **Property 8: Non-destructive folder structure integrity**
    - Generate random file trees in workspace, call `create_folder_structure()`, verify standard folders exist and pre-existing files untouched
    - Create `backend/tests/test_property_folder_structure.py`
    - **Validates: Requirements 8.1, 8.2**

- [x] 6. Simplify `AgentManager._build_options()` — inline workspace resolution
  - [x] 6.1 Modify `_build_options()` in `backend/core/agent_manager.py`
    - Replace `_resolve_workspace_mode()` call with inline logic
    - Read `initialization_manager.get_cached_workspace_path()` for `working_directory`
    - Set `setting_sources = ["project"]` unconditionally
    - Build `file_access_handler` inline: `None` for global_user_mode, permission handler for isolated
    - Remove `workspace_id` parameter from method signature
    - Set `add_dirs=None` in `ClaudeAgentOptions`
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 5.1, 5.2, 5.3, 5.4, 7.1, 7.2, 7.3_

  - [x] 6.2 Write property test for unified cwd regardless of workspace mode (Property 2)
    - **Property 2: Unified cwd regardless of workspace mode**
    - Generate random agent configs with varying `global_user_mode`, verify `cwd` is always cached SwarmWS path
    - Create `backend/tests/test_property_unified_cwd.py`
    - **Validates: Requirements 1.3, 1.4, 5.1**

  - [x] 6.3 Write property test for setting sources always project-only (Property 3)
    - **Property 3: Setting sources always project-only**
    - Generate random agent configs, verify `setting_sources` is always `['project']`
    - Add to `backend/tests/test_property_unified_cwd.py`
    - **Validates: Requirement 5.4**

  - [x] 6.4 Write property test for file access control determined by workspace mode (Property 7)
    - **Property 7: File access control determined by workspace mode**
    - Generate random agent configs, verify `can_use_tool` is `None` for global_user_mode=True and a handler for False
    - Create `backend/tests/test_property_file_access_control.py`
    - **Validates: Requirements 7.1, 7.2, 7.3**

- [x] 7. Simplify `AgentManager._build_mcp_config()` — remove workspace filtering
  - [x] 7.1 Modify `_build_mcp_config()` in `backend/core/agent_manager.py`
    - Remove `workspace_id` parameter
    - Remove `workspace_config_resolver.get_effective_mcps()` call
    - Iterate over `agent_config["mcp_ids"]` directly, look up each MCP server from DB
    - Handle name deduplication and connection types (stdio, sse, http)
    - _Requirements: 9.1, 9.2, 9.3_

  - [x] 7.2 Write property test for MCP config built without workspace filtering (Property 9)
    - **Property 9: MCP config built without workspace filtering**
    - Generate random agent configs with `mcp_ids`, verify all valid MCP IDs appear in result dict with no workspace filtering
    - Create `backend/tests/test_property_mcp_config.py`
    - **Validates: Requirements 9.1, 9.2, 9.3**

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Remove deprecated backend components
  - [x] 9.1 Delete `WorkspaceConfigResolver` class
    - Remove `backend/core/workspace_config_resolver.py`
    - Remove `backend/tests/test_workspace_config_resolver.py`
    - Remove all imports of `workspace_config_resolver` from other modules
    - _Requirements: 10.1, 10.2, 10.3_

  - [x] 9.2 Remove `_resolve_workspace_mode()` from `AgentManager`
    - Delete the method from `backend/core/agent_manager.py`
    - Verify no remaining references
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 9.3 Remove per-agent workspace methods from `AgentSandboxManager`
    - Remove `rebuild_agent_workspace()`, `get_agent_workspace()`, `delete_agent_workspace()` from `backend/core/agent_sandbox_manager.py`
    - Remove any callers of these methods
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 9.4 Deprecate `agent_workspaces_dir` in `backend/config.py`
    - Remove or comment out the `agent_workspaces_dir` config setting
    - Remove any references to it in other modules
    - _Requirements: 5.1_

- [x] 10. Simplify frontend — remove workspace selection and chat API params
  - [x] 10.1 Simplify `useWorkspaceSelection` hook in `desktop/src/hooks/useWorkspaceSelection.ts`
    - Return single default SwarmWS path, remove workspace switching logic
    - Remove `setSelectedWorkspace` and related state
    - _Requirements: 6.3, 6.4_

  - [x] 10.2 Remove `addDirs` and `workspaceId` from chat service in `desktop/src/services/chat.ts`
    - Remove `add_dirs` from the `streamChat` request body
    - Remove `workspace_id` from the `streamChat` request body
    - _Requirements: 6.1, 6.2_

  - [x] 10.3 Update `ChatPage.tsx` to remove workspace selector and params
    - Remove workspace selector dropdown component from `desktop/src/pages/ChatPage.tsx`
    - Remove `addDirs: workDir ? [workDir] : undefined` from `streamChat()` call
    - Remove `workspaceId` from `streamChat()` call
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 10.4 Update frontend tests
    - Update `desktop/src/pages/ChatPage.test.tsx` to remove workspace-related assertions
    - Update `desktop/src/hooks/useWorkspaceSelection.test.ts` to match simplified hook
    - Verify `streamChat` request body has no `add_dirs` or `workspace_id`
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 11. Remove backend `workspace_id` from chat/session API
  - [x] 11.1 Remove `workspace_id` parameter from chat API endpoint
    - Update the chat router to not accept `workspace_id`
    - Update `_build_hooks()` and `_build_system_prompt()` to not accept `workspace_id`
    - _Requirements: 1.5, 9.3_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1-10)
- Property 11 (no per-session filesystem I/O) is validated structurally by code review — the refactored `_build_options()` contains no filesystem calls
- Backend: Python with `hypothesis` for property-based tests, `pytest` for unit tests
- Frontend: TypeScript with existing test framework
- All property tests use `@settings(max_examples=100)` and `tmp_path` fixture for filesystem isolation
