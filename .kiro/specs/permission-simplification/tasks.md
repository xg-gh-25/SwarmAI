# Implementation Plan: Permission Simplification

## Overview

Replace the 4-layer command permission system (~700 lines across 3 files) with a single "dangerous command gate" backed by a flat JSON pattern file and per-session in-memory approvals. Delete dead code, simplify PermissionManager, update wiring, and remove the frontend modal. Implementation follows a safe dependency order: delete dead code first, then simplify internals, then update wiring, then clean up tests.

## Tasks

- [x] 1. Delete dead code from security_hooks.py
  - [x] 1.1 Remove `DANGEROUS_PATTERNS` regex list, `check_dangerous_command()`, and `dangerous_command_blocker()` functions
    - Remove the `DANGEROUS_PATTERNS: list[tuple[str, str]]` constant (13 regex tuples)
    - Remove the `check_dangerous_command(command)` function
    - Remove the `dangerous_command_blocker()` async hook function
    - Remove unused imports: `from database import db`, `import re` (if no longer needed), `from .cmd_permission_manager import CmdPermissionManager`
    - Remove `from .permission_manager import PermissionManager` class-level import (gate receives it as parameter)
    - Retain `pre_tool_logger()`, `create_file_access_permission_handler()`, `create_skill_access_checker()` unchanged
    - _Requirements: 1.1, 1.2, 1.3, 15.1, 15.2, 15.3_

  - [x] 1.2 Remove `create_human_approval_hook()` function from security_hooks.py
    - Remove the entire `create_human_approval_hook()` factory function and its inner `human_approval_hook()` closure
    - This function is replaced by `create_dangerous_command_gate()` in task 2
    - _Requirements: 3.1, 11.1, 11.2_

- [x] 2. Simplify PermissionManager and create the dangerous command gate
  - [x] 2.1 Remove deprecated `get_permission_queue()` method from permission_manager.py
    - Delete the `get_permission_queue()` method (backward-compat shim that returns a throwaway queue)
    - Retain all HITL plumbing: `wait_for_permission_decision`, `set_permission_decision`, `get_session_queue`, `remove_session_queue`, `enqueue_permission_request`
    - Retain all pending request methods: `store_pending_request`, `get_pending_request`, `update_pending_request`, `remove_pending_request`
    - Retain all session approval methods: `approve_command`, `is_command_approved`, `clear_session_approvals`, `hash_command`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 2.2 Create `load_dangerous_patterns()` and `create_dangerous_command_gate()` in security_hooks.py
    - Add `DEFAULT_DANGEROUS_PATTERNS: list[str]` constant (20 glob patterns from cmd_permission_manager.py)
    - Add `load_dangerous_patterns()` function: loads from `~/.swarm-ai/dangerous_commands.json`, creates with defaults if missing, falls back to defaults on invalid JSON, uses `{"patterns": [...]}` format
    - Add `create_dangerous_command_gate(session_context, session_key, permission_mgr, enable_human_approval=True)` factory function
    - Gate extracts command from Bash tool input, uses `fnmatch.fnmatch()` for glob matching
    - Gate returns `{}` for non-dangerous commands, auto-denies when `enable_human_approval=False`
    - Gate checks `permission_mgr.is_command_approved()` for session approvals
    - Gate enqueues inline permission request via `permission_mgr.enqueue_permission_request()` and waits via `wait_for_permission_decision()`
    - On approve: calls `permission_mgr.approve_command()` and returns `{}`
    - On deny: returns deny dict with reason
    - Import `fnmatch`, `json`, `pathlib.Path`; use `config.get_app_data_dir` for file path
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 5.2_

  - [ ]* 2.3 Write property test for glob matching correctness (Property 1)
    - **Property 1: Glob matching correctness**
    - For any bash command and any list of glob patterns, the gate's "is dangerous" decision equals `any(fnmatch.fnmatch(command, p) for p in patterns)`
    - Use `hypothesis` with `st.text()` for commands, `st.lists(st.text())` for patterns
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 3.2, 3.3, 4.5**

  - [ ]* 2.4 Write property test for dangerous patterns file round-trip (Property 7)
    - **Property 7: Dangerous patterns file round-trip**
    - For any list of non-empty glob pattern strings, writing `{"patterns": [...]}` to a temp file and loading via `load_dangerous_patterns()` returns the exact same list
    - Use `hypothesis` with `st.lists(st.text(min_size=1), min_size=1)` and `tmp_path` fixture
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 4.1, 4.2, 4.4**

- [x] 3. Checkpoint — Verify gate and PermissionManager
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Update hook_builder.py to wire the new gate
  - [x] 4.1 Simplify `build_hooks()` to use `create_dangerous_command_gate`
    - Remove `cmd_permission_manager` parameter from `build_hooks()` signature
    - Remove `CmdPermissionManager` from TYPE_CHECKING import block
    - Replace `from .security_hooks import create_human_approval_hook` with `from .security_hooks import create_dangerous_command_gate`
    - Remove sandbox conditional skip logic (`sandbox_enabled` check that sets `enable_human_approval = False`)
    - Read `enable_human_approval` directly from `agent_config.get("enable_human_approval", True)`
    - Always attach the gate unconditionally: `HookMatcher(matcher="Bash", hooks=[gate])`
    - Call `create_dangerous_command_gate(hook_session_context, session_key, permission_manager, enable_human_approval=enable_human_approval)`
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4_

- [x] 5. Update agent_manager.py — remove CmdPermissionManager references
  - [x] 5.1 Clean up AgentManager class and module-level re-exports
    - Remove `from .cmd_permission_manager import CmdPermissionManager` import
    - Remove `cmd_permission_manager` parameter from `AgentManager.__init__()` and `AgentManager.configure()`
    - Remove `self._cmd_pm` attribute assignment in both `__init__` and `configure`
    - Remove re-exports of `DANGEROUS_PATTERNS`, `check_dangerous_command` if present
    - Update `_build_hooks()` to stop passing `self._cmd_pm` to `build_hooks()`
    - _Requirements: 1.4, 2.2, 2.3, 2.4, 12.3, 12.4, 12.5_

  - [x] 5.2 Simplify `continue_with_cmd_permission()` method
    - Remove `self._cmd_pm.approve(command)` call and its try/except block
    - On approve: call `_pm.approve_command(perm_session_id, command)` only
    - Retain all other logic: permission request lookup, decision update, SSE event contract
    - _Requirements: 9.1, 9.2, 9.3_

- [x] 6. Update chat.py — simplify `/cmd-permission-response` endpoint
  - [x] 6.1 Remove CmdPermissionManager usage from `handle_cmd_permission_response()`
    - Remove the `if request.decision == "approve":` block that calls `agent_manager._cmd_pm.approve(command)`
    - Remove the fallback `from core.agent_manager import approve_command as _legacy_approve`
    - On approve: call `_pm.approve_command(request.session_id, command)` only
    - Retain `set_permission_decision()` call and response format unchanged
    - Retain SSE event contract (`cmd_permission_request` event type)
    - _Requirements: 9.4, 9.5, 9.6, 9.7_

- [x] 7. Update main.py — startup wiring
  - [x] 7.1 Remove CmdPermissionManager from lifespan and add permissions.json generation
    - Remove `from core.cmd_permission_manager import CmdPermissionManager` import
    - Remove `cmd_perm = CmdPermissionManager()` and `cmd_perm.load()` lines
    - Remove `cmd_permission_manager=cmd_perm` from `agent_manager.configure()` call
    - Add: `from core.security_hooks import load_dangerous_patterns` and call it to get patterns
    - Add `_generate_permissions_json(workspace_path, patterns)` utility function that writes `SwarmWS/.claude/settings/permissions.json` with `description` and `dangerous_commands` fields
    - Create `.claude/settings/` directory with `mkdir(parents=True, exist_ok=True)`
    - Wrap permissions.json generation in try/except (non-critical, log warning on failure)
    - _Requirements: 12.1, 12.2, 13.1, 13.2, 13.3, 13.4, 13.5_

  - [x] 7.2 Add `.claude/settings/` category to auto_commit_hook.py
    - Add `".claude/settings/": "config"` entry to `COMMIT_CATEGORIES` dict in `backend/hooks/auto_commit_hook.py`
    - Insert before `"Knowledge/"` entry to maintain path-prefix ordering
    - _Requirements: 13.1_

- [x] 8. Checkpoint — Verify wiring changes
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Delete CmdPermissionManager and its test file
  - [x] 9.1 Delete `backend/core/cmd_permission_manager.py`
    - _Requirements: 2.1, 2.5, 2.6_

  - [x] 9.2 Delete `backend/tests/test_cmd_permission_manager.py`
    - _Requirements: 14.1_

- [x] 10. Delete frontend modal
  - [x] 10.1 Delete `desktop/src/components/chat/PermissionRequestModal.tsx` and remove any imports
    - Delete the file entirely
    - Search for and remove any imports of `PermissionRequestModal` in other frontend files
    - `InlinePermissionRequest.tsx` remains unchanged as the sole permission UI
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 11. Update remaining test files
  - [x] 11.1 Update `test_credential_validator_integration.py`
    - Remove `CmdPermissionManager` import and any mocks of it
    - Update `configure()` calls to not pass `cmd_permission_manager`
    - Use direct attribute assignment if needed instead of `configure()` with `cmd_permission_manager`
    - _Requirements: 14.2_

  - [x] 11.2 Update `helpers_parallel_session.py`
    - Remove `cmd_permission_manager=MagicMock()` from `AgentManager()` constructor call
    - _Requirements: 14.3_

  - [x] 11.3 Update `test_permission_manager.py`
    - Remove test for `get_permission_queue()` if present
    - Existing property tests for approve/check round-trip and decision set/wait remain valid
    - _Requirements: 14.5_

- [x] 12. Write property-based tests for PermissionManager
  - [ ]* 12.1 Write property test for approve/check round-trip (Property 2)
    - **Property 2: Approve/check round-trip per session**
    - For any session ID and command, `approve_command(sid, cmd)` then `is_command_approved(sid, cmd)` returns `True`; unapproved commands return `False`
    - Use `hypothesis` with `st.text(min_size=1)` for session IDs and commands
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 3.4, 3.6, 5.2, 6.5**

  - [ ]* 12.2 Write property test for session isolation (Property 3)
    - **Property 3: Session isolation of approvals**
    - For any two distinct session IDs and any command, approving in session A does not make `is_command_approved(session_B, cmd)` return `True`
    - Use `hypothesis` with `st.text(min_size=1)` and `assume(s1 != s2)`
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 5.1, 5.5**

  - [ ]* 12.3 Write property test for session cleanup (Property 4)
    - **Property 4: Session cleanup clears approvals**
    - For any session ID and set of approved commands, `clear_session_approvals(sid)` causes all `is_command_approved` calls to return `False`
    - Use `hypothesis` with `st.text(min_size=1)` and `st.lists(st.text(min_size=1), min_size=1)`
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 5.3**

  - [ ]* 12.4 Write property test for pending request round-trip (Property 5)
    - **Property 5: Pending request store/get/remove round-trip**
    - For any request dict with unique `id`, `store_pending_request` then `get_pending_request` returns original data; after `remove_pending_request`, returns `None`
    - Use `hypothesis` with `st.fixed_dictionaries({"id": st.text(min_size=1)})`
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 6.3**

  - [ ]* 12.5 Write property test for permission decision round-trip (Property 6)
    - **Property 6: Permission decision set/wait round-trip**
    - For any request ID and decision in `{"approve", "deny"}`, `set_permission_decision` resolves `wait_for_permission_decision` with the exact decision string
    - Use `hypothesis` with `st.text(min_size=1)` and `st.sampled_from(["approve", "deny"])`
    - Place in `backend/tests/test_permission_simplification.py`
    - **Validates: Requirements 3.5, 3.7**

- [x] 13. Final checkpoint — Run full test suite
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after major phases
- Property tests validate universal correctness properties from the design document
- Implementation order follows safe dependency ordering: dead code removal → internal simplification → wiring updates → deletions → test cleanup
- The `InlinePermissionRequest.tsx` component and SSE event contract remain unchanged throughout
