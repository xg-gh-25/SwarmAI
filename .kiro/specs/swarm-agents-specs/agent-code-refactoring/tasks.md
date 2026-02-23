# Implementation Plan: Agent Code Refactoring

## Overview

Behavior-preserving decomposition of `backend/core/agent_manager.py` (~2,400 lines) into 5 focused modules, followed by internal decomposition of `_build_options` and conversation methods, documentation, cleanup, and type hints. Structured in 6 independent phases — each phase passes `pytest` before the next begins.

## Tasks

- [x] 1. Phase 1 — Extract modules from agent_manager.py
  - [x] 1.1 Extract `content_accumulator.py` module
    - Create `backend/core/content_accumulator.py` with the `ContentBlockAccumulator` class
    - Move `_get_key`, `add`, `extend`, `blocks` property, and `__bool__` methods
    - Add module-level docstring describing O(1) deduplication
    - Zero external dependencies — pure utility class
    - Update `agent_manager.py` to import from `content_accumulator`
    - Add re-export of `ContentBlockAccumulator` in `agent_manager.py`
    - _Requirements: 5.1, 5.2, 11.1_

  - [x] 1.2 Write property test for ContentBlockAccumulator deduplication
    - **Property 4: Content block deduplication equivalence**
    - **Validates: Requirements 5.2, 5.3**
    - Create `backend/tests/test_content_accumulator.py`
    - Use Hypothesis with custom strategy generating content block dicts (text, tool_use, tool_result, unknown types)
    - Verify duplicate text blocks (same text), duplicate tool_use (same id), duplicate tool_result (same tool_use_id) are each added only once
    - Verify blocks with unknown types or missing IDs are always added

  - [x] 1.3 Extract `permission_manager.py` module
    - Create `backend/core/permission_manager.py` with `PermissionManager` class
    - Move `_approved_commands`, `_permission_events`, `_permission_results`, `_permission_request_queue` from module-level globals into class attributes
    - Implement methods: `hash_command`, `approve_command`, `is_command_approved`, `clear_session_approvals`, `wait_for_permission_decision`, `set_permission_decision`, `get_permission_queue`
    - Create module-level singleton: `permission_manager = PermissionManager()`
    - Add module-level docstring describing permission state management
    - Update `agent_manager.py` to import singleton and re-export functions: `approve_command`, `is_command_approved`, `set_permission_decision`, `wait_for_permission_decision`, `_permission_request_queue`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 11.1_

  - [x] 1.4 Write property tests for PermissionManager
    - **Property 2: Permission approve/check round-trip**
    - **Property 3: Permission decision set/wait round-trip**
    - **Validates: Requirements 2.4, 2.5, 2.6**
    - Create `backend/tests/test_permission_manager.py`
    - Property 2: Use Hypothesis `st.text(min_size=1)` for session IDs and commands; verify `approve_command` then `is_command_approved` returns `True`, and unapproved commands return `False`
    - Property 3: Use Hypothesis `st.text(min_size=1)` + `st.sampled_from(["approve", "deny"])`; verify `set_permission_decision` then `wait_for_permission_decision` returns the exact decision

  - [x] 1.5 Extract `security_hooks.py` module
    - Create `backend/core/security_hooks.py` with `DANGEROUS_PATTERNS`, `check_dangerous_command`, `dangerous_command_blocker`, `pre_tool_logger`, `create_human_approval_hook`, `create_file_access_permission_handler`, `create_skill_access_checker`
    - Change `create_human_approval_hook` to accept `PermissionManager` instance as parameter instead of referencing module-level globals
    - Add module-level docstring describing the 4-layer defense model
    - Update `agent_manager.py` to import from `security_hooks` and re-export `DANGEROUS_PATTERNS`, `check_dangerous_command`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 11.1_

  - [x] 1.6 Write property test for dangerous command detection
    - **Property 1: Dangerous command detection equivalence**
    - **Validates: Requirements 1.2**
    - Add test to `backend/tests/test_security_hooks.py`
    - Use Hypothesis `st.text()` + `st.sampled_from(DANGEROUS_PATTERNS)` to generate random strings including substrings from dangerous patterns
    - Verify `check_dangerous_command` returns matching reason string or `None` consistently

  - [x] 1.7 Extract `claude_environment.py` module
    - Create `backend/core/claude_environment.py` with `_configure_claude_environment` and `_ClaudeClientWrapper`
    - Add module-level docstring describing Claude SDK env config
    - Update `agent_manager.py` to import from `claude_environment`
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 1.8 Extract `agent_defaults.py` module
    - Create `backend/core/agent_defaults.py` with `DEFAULT_AGENT_ID`, `SWARM_AGENT_NAME`, `_get_resources_dir`, `_get_templates_dir`, `get_default_agent`, `ensure_default_agent`, `_register_default_skills`, `_register_default_mcp_servers`, `expand_skill_ids_with_plugins`
    - Add module-level docstring explaining the default agent bootstrap process
    - Update `agent_manager.py` to import and re-export `ensure_default_agent`, `get_default_agent`, `expand_skill_ids_with_plugins`, `DEFAULT_AGENT_ID`, `SWARM_AGENT_NAME`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 11.1_

  - [x] 1.9 Write re-export and structure verification tests
    - **Property 7: All required symbols re-exported from agent_manager**
    - **Validates: Requirements 11.1, 11.2**
    - Create `backend/tests/test_agent_refactoring_structure.py`
    - Verify all required symbols (`ensure_default_agent`, `get_default_agent`, `approve_command`, `is_command_approved`, `set_permission_decision`, `wait_for_permission_decision`, `_permission_request_queue`, `DEFAULT_AGENT_ID`, `SWARM_AGENT_NAME`, `AgentManager`, `DANGEROUS_PATTERNS`, `check_dangerous_command`, `expand_skill_ids_with_plugins`, `ContentBlockAccumulator`) are importable from `backend.core.agent_manager`
    - Verify `PermissionManager` singleton is the same instance across imports

- [x] 2. Checkpoint — Phase 1 complete
  - Ensure all tests pass with `pytest backend/tests/`, ask the user if questions arise.
  - Verify the application starts and handles chat conversations identically to before.
  - _Requirements: 12.1, 12.2_

- [x] 3. Phase 2 — Decompose `_build_options` into focused helpers
  - [x] 3.1 Extract `_resolve_allowed_tools` helper
    - Move tool resolution logic from `_build_options` into `AgentManager._resolve_allowed_tools`
    - _Requirements: 6.1, 6.4_

  - [x] 3.2 Extract `_build_mcp_config` helper
    - Move MCP server configuration logic into `AgentManager._build_mcp_config`
    - _Requirements: 6.1, 6.4_

  - [x] 3.3 Extract `_build_hooks` helper
    - Move hook composition logic into `AgentManager._build_hooks`
    - Compose `security_hooks` functions with `PermissionManager` singleton
    - _Requirements: 6.1, 6.3, 6.4_

  - [x] 3.4 Extract `_resolve_workspace_mode`, `_build_sandbox_config`, and `_inject_channel_mcp` helpers
    - Move workspace resolution, sandbox config, and channel MCP injection into separate helpers
    - _Requirements: 6.1, 6.4_

    > **⚠️ Post-Refactor Update**: `_resolve_workspace_mode` was later removed entirely by the unified-swarm-workspace-cwd refactor. Its logic was inlined into `_build_options()`, reducing the helpers from 6 to 5 + inline workspace logic.

  - [x] 3.5 Refactor `_build_options` to orchestrate helpers
    - Replace the ~400-line body with calls to the 6 extracted helpers
    - Assemble and return `ClaudeAgentOptions` from helper results
    - _Requirements: 6.1, 6.2_

  - [x] 3.6 Write unit tests for `_build_options` decomposition
    - Create `backend/tests/test_agent_manager_decomposition.py`
    - Verify all 6 helper methods exist on `AgentManager` class
    - Verify `_build_options` still produces valid `ClaudeAgentOptions`
    - _Requirements: 6.1, 6.2_

- [x] 4. Phase 3 — Extract shared conversation execution pattern
  - [x] 4.1 Implement `_execute_on_session` helper
    - Extract shared session setup, query execution, and response streaming pattern from `run_conversation` and `continue_with_answer`
    - Handle: reusing existing clients, creating new clients, fallback to fresh sessions, storing clients, error handling and cleanup
    - _Requirements: 7.1, 7.4_

  - [x] 4.2 Refactor `run_conversation` to delegate to `_execute_on_session`
    - Make `run_conversation` a thin wrapper that prepares inputs and delegates
    - _Requirements: 7.2_

  - [x] 4.3 Refactor `continue_with_answer` to delegate to `_execute_on_session`
    - Make `continue_with_answer` a thin wrapper that prepares inputs and delegates
    - _Requirements: 7.3_

  - [x] 4.4 Write unit tests for conversation deduplication
    - Add tests to `backend/tests/test_agent_manager_decomposition.py`
    - Verify `_execute_on_session` method exists on `AgentManager`
    - Verify `run_conversation` and `continue_with_answer` still exist as public methods
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 5. Checkpoint — Phases 2 & 3 complete
  - Ensure all tests pass with `pytest backend/tests/`, ask the user if questions arise.
  - Verify the application starts and handles chat conversations identically to before.
  - _Requirements: 12.1, 12.2_

- [x] 6. Phase 4 — Add documentation to workspace_manager.py
  - [x] 6.1 Add docstrings to workspace_manager.py public methods
    - Add docstrings to `get_agent_skills_dir`, `get_skill_name_by_id`, `_get_skill_by_name`, `_get_skill_source_path`, `get_all_skill_names`, `get_allowed_skill_names`, `rebuild_agent_workspace`
    - Clarify that workspace_manager handles symlink management while skill_manager handles skill file lifecycle
    - _Requirements: 8.1, 8.2_

  - [x] 6.2 Write docstring presence tests
    - **Property 5: All refactored modules and public methods have docstrings**
    - **Validates: Requirements 8.1, 10.1**
    - Add tests to `backend/tests/test_agent_refactoring_structure.py`
    - Verify all 5 new modules have non-empty `__doc__` attributes
    - Verify all public methods on workspace_manager skill-related methods have non-empty `__doc__`

- [x] 7. Phase 5 — Dead code and cleanup
  - [x] 7.1 Move inline `import traceback` to top-level imports
    - Find all inline `import traceback` statements in `agent_manager.py` and move to top-level
    - _Requirements: 9.1_

  - [x] 7.2 Change `agent_config` logging from `info` to `debug` level
    - _Requirements: 9.2_

  - [x] 7.3 Remove commented-out code blocks
    - _Requirements: 9.3_

  - [x] 7.4 Extract `run_skill_creator_conversation` system prompt to template
    - Move the hardcoded multi-line system prompt string to a template file or constant
    - _Requirements: 9.4_

- [x] 8. Phase 6 — Type hints and inline documentation
  - [x] 8.1 Add type hints to all function signatures in new modules
    - Add type annotations on all parameters (except `self`) and return types in `security_hooks.py`, `permission_manager.py`, `agent_defaults.py`, `claude_environment.py`, `content_accumulator.py`
    - _Requirements: 10.2_

  - [x] 8.2 Add inline comments to `_run_query_on_client`
    - Add comments explaining the message loop, content block accumulation, and SSE event dispatch logic
    - _Requirements: 10.3_

  - [x] 8.3 Write type annotation verification tests
    - **Property 6: All functions in new modules have type-annotated signatures**
    - **Validates: Requirements 10.2**
    - Add tests to `backend/tests/test_agent_refactoring_structure.py`
    - Use `inspect.signature` and `typing.get_type_hints` to verify all parameters and return types are annotated

- [x] 9. Final checkpoint — All phases complete
  - Ensure all tests pass with `pytest backend/tests/`, ask the user if questions arise.
  - Verify the application starts and handles chat conversations identically to before.
  - Verify no existing test files were modified.
  - _Requirements: 11.3, 12.1, 12.2, 12.3_

- [x] 10. Update AGENT_ARCHITECTURE_DEEP_DIVE.md to reflect refactored structure
  - Revise `.kiro/specs/AGENT_ARCHITECTURE_DEEP_DIVE.md` to reflect the new module structure after refactoring
  - Update the "Execution Engine" section to reference the extracted modules (`security_hooks.py`, `permission_manager.py`, `agent_defaults.py`, `claude_environment.py`, `content_accumulator.py`)
  - Update the "Security Model" section to reference `security_hooks.py` and `permission_manager.py` as the canonical locations
  - Update the "Agent Lifecycle & Initialization" section to reference `agent_defaults.py` for default agent bootstrap
  - Update the "File Reference Map" section with the new module paths and their responsibilities
  - Update the architecture diagrams and dependency descriptions to show the decomposed module structure
  - Remove any references to monolithic `agent_manager.py` containing security hooks, permission state, etc.
  - Ensure the doc accurately describes the post-refactoring codebase as the single source of truth for agent architecture

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each phase is independently testable — the test suite passes after each phase
- Property tests use Hypothesis library with `@given` decorators
- Properties 5, 6, 7 are exhaustive checks implemented as parameterized tests rather than Hypothesis
- All new test files go in `backend/tests/`
- No functional changes — this is purely structural refactoring
- Backward-compatible re-exports ensure zero import changes for callers


## Post-Refactor Note (unified-swarm-workspace-cwd)

> **This spec was written before the unified-swarm-workspace-cwd refactor.** The following changes from that refactor affect elements described in this document:
>
> - **`_resolve_workspace_mode()` eliminated** (Task 3.4): Its logic was inlined into `_build_options()`. The `_build_options` decomposition now has 5 helpers + inline workspace logic instead of 6 helpers.
> - **`workspace_id` parameter removed**: Removed from `_build_options()`, `_build_mcp_config()`, `_build_hooks()`, and `_build_system_prompt()` signatures, and from chat/session APIs.
> - **Per-agent workspace isolation removed**: All agents now use a single SwarmWorkspace at `~/.swarm-ai/SwarmWS`.
>
> See `.kiro/specs/unified-swarm-workspace-cwd/` for the full refactor specification.
