# Requirements Document

## Introduction

The SwarmAI backend `agent_manager.py` module has grown to ~2,300 lines handling 6+ distinct responsibilities: security hooks, permission state management, environment configuration, default agent setup, content accumulation, and the core AgentManager class. This refactoring decomposes the monolith into focused, single-responsibility modules while preserving all existing behavior. The goal is improved maintainability, testability, and clarity without any functional changes.

## Glossary

- **Agent_Manager**: The `AgentManager` class in `backend/core/agent_manager.py` that manages agent lifecycle using Claude Agent SDK
- **Security_Hooks**: The collection of hook factory functions (`dangerous_command_blocker`, `create_human_approval_hook`, `create_file_access_permission_handler`, `create_skill_access_checker`, `pre_tool_logger`) and related constants (`DANGEROUS_PATTERNS`) that implement the 4-layer defense model
- **Permission_Manager**: The encapsulated state and functions managing command approval tracking and permission request/response flow (`_approved_commands`, `_permission_events`, `_permission_results`, `_permission_request_queue`, and their accessor functions)
- **Agent_Defaults**: The functions responsible for default agent creation, skill registration, and MCP server registration (`get_default_agent`, `ensure_default_agent`, `_register_default_skills`, `_register_default_mcp_servers`, `expand_skill_ids_with_plugins`)
- **Claude_Environment**: The `_configure_claude_environment` function and `_ClaudeClientWrapper` class that set up the Claude SDK runtime
- **Content_Accumulator**: The `ContentBlockAccumulator` utility class for O(1) deduplication of content blocks
- **Build_Options**: The `_build_options` method on Agent_Manager (~400 lines) that assembles `ClaudeAgentOptions` from agent configuration
- **Caller**: Any module that imports from `agent_manager.py`, including `backend/routers/chat.py` and `backend/core/initialization_manager.py`
- **Session_Context**: A dictionary containing `sdk_session_id` and related metadata passed through hook closures for permission tracking

## Requirements

### Requirement 1: Extract Security Hooks Module

**User Story:** As a developer, I want security hook functions isolated in their own module, so that I can review, test, and modify the 4-layer defense model independently from agent lifecycle logic.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Security_Hooks module at `backend/core/security_hooks.py` SHALL contain `dangerous_command_blocker`, `check_dangerous_command`, `DANGEROUS_PATTERNS`, `create_human_approval_hook`, `create_file_access_permission_handler`, `create_skill_access_checker`, and `pre_tool_logger`
2. THE Security_Hooks module SHALL preserve the exact function signatures, return values, and side effects of all extracted functions
3. WHEN a Caller imports security hook functions, THE Caller SHALL be able to import from `backend.core.security_hooks` with no change in behavior
4. THE Security_Hooks module SHALL include a module-level docstring describing the 4-layer defense model it implements
5. WHEN `create_human_approval_hook` is called, THE Security_Hooks module SHALL accept Permission_Manager methods as parameters rather than referencing module-level globals directly

### Requirement 2: Extract Permission Manager Module

**User Story:** As a developer, I want permission state encapsulated in a class, so that module-level mutable globals are eliminated and permission logic is testable in isolation.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Permission_Manager module at `backend/core/permission_manager.py` SHALL contain a `PermissionManager` class encapsulating `_approved_commands`, `_permission_events`, `_permission_results`, and `_permission_request_queue`
2. THE Permission_Manager class SHALL expose methods: `hash_command`, `approve_command`, `is_command_approved`, `clear_session_approvals`, `wait_for_permission_decision`, `set_permission_decision`, and `get_permission_queue`
3. THE Permission_Manager module SHALL provide a module-level singleton instance for use by Callers
4. WHEN `approve_command` is called with a session ID and command, THE Permission_Manager SHALL store the command hash in the session's approval set identically to the current behavior
5. WHEN `wait_for_permission_decision` is called, THE Permission_Manager SHALL block until a decision is set or the timeout expires, matching the current 300-second default timeout
6. IF `wait_for_permission_decision` times out, THEN THE Permission_Manager SHALL return "deny" identically to the current behavior

### Requirement 3: Extract Agent Defaults Module

**User Story:** As a developer, I want default agent setup logic in its own module, so that initialization and skill/MCP registration can be understood and modified without navigating the full agent lifecycle code.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Agent_Defaults module at `backend/core/agent_defaults.py` SHALL contain `_get_resources_dir`, `_get_templates_dir`, `get_default_agent`, `ensure_default_agent`, `_register_default_skills`, `_register_default_mcp_servers`, and `expand_skill_ids_with_plugins`
2. THE Agent_Defaults module SHALL preserve the exact behavior of `ensure_default_agent` including database interactions, skill registration, and MCP server registration
3. WHEN `initialization_manager.py` calls `ensure_default_agent`, THE Agent_Defaults module SHALL be importable as a drop-in replacement with no behavioral change
4. THE Agent_Defaults module SHALL include a module-level docstring explaining the default agent bootstrap process

### Requirement 4: Extract Claude Environment Module

**User Story:** As a developer, I want Claude SDK environment configuration separated, so that API key resolution, model selection, and client wrapper logic are isolated from agent orchestration.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Claude_Environment module at `backend/core/claude_environment.py` SHALL contain `_configure_claude_environment` and `_ClaudeClientWrapper`
2. THE Claude_Environment module SHALL preserve the exact environment variable configuration, API key resolution, and model ID selection logic
3. WHEN Agent_Manager creates a Claude SDK client, THE Claude_Environment module SHALL provide `_ClaudeClientWrapper` with identical context manager behavior

### Requirement 5: Extract Content Accumulator Module

**User Story:** As a developer, I want the content block accumulator in its own module, so that this utility can be reused and tested independently.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Content_Accumulator module at `backend/core/content_accumulator.py` SHALL contain the `ContentBlockAccumulator` class
2. THE Content_Accumulator class SHALL preserve the exact O(1) deduplication behavior, including `add`, `extend`, `blocks`, and `__bool__` methods
3. FOR ALL sequences of content blocks, adding blocks via the extracted Content_Accumulator then reading via `blocks()` SHALL produce the same result as the current implementation (round-trip equivalence)

### Requirement 6: Decompose Build Options Method

**User Story:** As a developer, I want the ~400-line `_build_options` method broken into focused helpers, so that each concern (tools, MCP, hooks, workspace, sandbox, channel injection) is readable and testable on its own.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Agent_Manager class SHALL delegate Build_Options assembly to private helper methods: `_resolve_allowed_tools`, `_build_mcp_config`, `_build_hooks`, `_resolve_workspace_mode`, `_build_sandbox_config`, and `_inject_channel_mcp`
2. WHEN `_build_options` is called with an agent configuration, THE Agent_Manager SHALL produce an identical `ClaudeAgentOptions` object as the current monolithic implementation
3. THE `_build_hooks` helper SHALL compose Security_Hooks functions with Permission_Manager state to produce the same hook list as the current implementation
4. WHEN a new hook or tool resolution rule is added in the future, THE developer SHALL only need to modify the relevant helper method rather than the full `_build_options` body

> **⚠️ Post-Refactor Update**: The unified-swarm-workspace-cwd refactor later removed `_resolve_workspace_mode()` entirely (its logic was inlined into `_build_options()`), reducing the helpers from 6 to 5 + inline workspace logic. The `workspace_id` parameter was also removed from `_build_options()`, `_build_mcp_config()`, `_build_hooks()`, and `_build_system_prompt()` signatures.

### Requirement 7: Reduce Duplication in Conversation Methods

**User Story:** As a developer, I want the shared pattern between `run_conversation` and `continue_with_answer` extracted into a common helper, so that conversation execution logic is defined once.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Agent_Manager class SHALL contain an `_execute_on_session` helper that encapsulates the shared session setup, query execution, and response streaming pattern
2. WHEN `run_conversation` is called, THE Agent_Manager SHALL delegate to `_execute_on_session` and produce identical SSE event streams as the current implementation
3. WHEN `continue_with_answer` is called, THE Agent_Manager SHALL delegate to `_execute_on_session` and produce identical SSE event streams as the current implementation
4. THE `_execute_on_session` helper SHALL handle session lookup, client retrieval, error handling, and message formatting identically to the current duplicated logic

### Requirement 8: Add Workspace Manager Documentation

**User Story:** As a developer, I want clear docstrings on workspace_manager.py skill methods, so that the boundary between workspace symlink management and skill file lifecycle is documented.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE `workspace_manager.py` module SHALL have docstrings on all public methods explaining their responsibility boundary
2. THE docstrings on skill-related methods (`get_agent_skills_dir`, `get_skill_name_by_id`, `_get_skill_by_name`, `_get_skill_source_path`, `get_all_skill_names`, `get_allowed_skill_names`, `rebuild_agent_workspace`) SHALL clarify that workspace_manager handles symlink management while skill_manager handles skill file lifecycle

> **⚠️ Post-Refactor Update**: The unified-swarm-workspace-cwd refactor replaced `rebuild_agent_workspace()` with `AgentSandboxManager.setup_workspace_skills()`. Several methods listed here (`get_agent_workspace`, `delete_agent_workspace`) were removed entirely. The workspace_manager was refactored into `AgentSandboxManager` with a unified workspace model.

### Requirement 9: Dead Code and Cleanup

**User Story:** As a developer, I want dead code, misplaced imports, and noisy logging cleaned up, so that the codebase is consistent and maintainable.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE Agent_Manager module SHALL have all `import traceback` statements at the top-level rather than inline
2. WHEN the refactoring is complete, THE Agent_Manager module SHALL log `agent_config` at `debug` level rather than `info` level
3. WHEN the refactoring is complete, THE Agent_Manager module SHALL contain no commented-out code blocks
4. THE `run_skill_creator_conversation` method SHALL load its system prompt from a template file or constant rather than containing a hardcoded multi-line string

### Requirement 10: Type Hints and Inline Documentation

**User Story:** As a developer, I want type hints on all function signatures and inline comments on complex logic, so that the codebase is self-documenting and IDE-friendly.

#### Acceptance Criteria

1. WHEN the refactoring is complete, each new module (`security_hooks.py`, `permission_manager.py`, `agent_defaults.py`, `claude_environment.py`, `content_accumulator.py`) SHALL have a module-level docstring describing its purpose
2. THE refactored code SHALL have type hints on all public and private function signatures in the new modules
3. THE `_run_query_on_client` method in Agent_Manager SHALL have inline comments explaining the message loop, content block accumulation, and SSE event dispatch logic

### Requirement 11: Backward-Compatible Imports

**User Story:** As a developer, I want existing callers to continue working without import changes during the transition, so that the refactoring does not break any dependent code.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE `backend/core/agent_manager.py` module SHALL re-export key symbols (`ensure_default_agent`, `get_default_agent`, `approve_command`, `is_command_approved`, `set_permission_decision`, `wait_for_permission_decision`, `_permission_request_queue`) so that existing Callers continue to work
2. WHEN `backend/routers/chat.py` imports from `agent_manager`, THE imports SHALL resolve to the same functions as before the refactoring
3. THE existing test suite SHALL pass with zero modifications after the refactoring is complete

### Requirement 12: Phase Independence

**User Story:** As a developer, I want each refactoring phase to be independently testable, so that I can verify correctness incrementally rather than all at once.

#### Acceptance Criteria

1. WHEN any single phase (module extraction, build_options decomposition, conversation dedup, documentation, cleanup, or type hints) is completed, THE existing test suite SHALL pass
2. WHEN a phase is completed, THE application SHALL start and handle chat conversations identically to before that phase
3. IF a phase introduces a regression, THEN THE developer SHALL be able to revert that phase independently without affecting other completed phases


## Post-Refactor Note (unified-swarm-workspace-cwd)

> **This spec was written before the unified-swarm-workspace-cwd refactor.** The following changes from that refactor affect elements described in this document:
>
> - **`_resolve_workspace_mode()` eliminated** (Requirement 6): Its logic was inlined into `_build_options()`. The decomposition now has 5 helpers + inline workspace logic instead of 6 helpers. The `workspace_id` parameter was removed from helper signatures.
> - **`rebuild_agent_workspace()` replaced** (Requirement 8): `AgentSandboxManager.setup_workspace_skills()` now handles skill symlinks at app init and on skill CRUD events. Methods `get_agent_workspace()` and `delete_agent_workspace()` were removed entirely.
> - **`WorkspaceConfigResolver` removed**: MCP configuration uses agent's `mcp_ids` directly with no workspace-level filtering.
> - **Per-agent workspace isolation removed**: All agents now use a single SwarmWorkspace at `~/.swarm-ai/SwarmWS`.
>
> See `.kiro/specs/unified-swarm-workspace-cwd/` for the full refactor specification.
