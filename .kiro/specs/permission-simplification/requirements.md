# Requirements Document

## Introduction

The SwarmAI command permission system currently has 4 overlapping layers across 3 files (~700 lines): a regex-based `dangerous_command_blocker` (dead code), a glob-based `human_approval_hook` (via `CmdPermissionManager`), an in-memory per-session `PermissionManager`, and a filesystem-backed cross-session `CmdPermissionManager`. This creates two pattern lists in two formats, two approval stores, sandbox-conditional skips, and persistent approvals that should be per-session.

This feature replaces all 4 layers with a single "dangerous command gate" that uses glob matching against a flat JSON pattern file, per-session in-memory approvals only, and the existing HITL asyncio plumbing (queue/event/SSE) for inline chat prompts. No database involvement, no modal popups, no persistent approval files.

## Glossary

- **Dangerous_Command_Gate**: The single PreToolUse hook function that checks Bash commands against dangerous glob patterns and prompts the user inline when a match is found
- **Dangerous_Patterns_File**: The flat JSON file at `~/.swarm-ai/dangerous_commands.json` containing glob patterns for dangerous commands
- **Session_Approval_Set**: The per-session in-memory set of approved command hashes, cleared when the session ends
- **Permission_Manager**: The simplified PermissionManager class retaining only HITL asyncio plumbing (queues, events, wait/set) and pending request storage
- **Inline_Permission_Prompt**: The existing `InlinePermissionRequest.tsx` component that renders permission prompts inline in the chat stream via SSE events
- **Hook_Builder**: The `hook_builder.py` module that composes security hooks into the Claude Agent SDK's hook system
- **HITL_Plumbing**: The human-in-the-loop asyncio infrastructure: per-session queues, asyncio.Event signaling, and SSE event dispatch for permission request/response flow
- **Permission_Settings_File**: The `SwarmWS/.claude/settings/permissions.json` file that gives users visibility into the current dangerous command patterns that require approval

## Requirements

### Requirement 1: Remove Dead Code — dangerous_command_blocker

**User Story:** As a maintainer, I want dead code removed from the permission system, so that the codebase is easier to understand and there are no misleading code paths.

#### Acceptance Criteria

1. WHEN the codebase is built, THE Security_Hooks module SHALL NOT contain the `dangerous_command_blocker` function
2. WHEN the codebase is built, THE Security_Hooks module SHALL NOT contain the `DANGEROUS_PATTERNS` regex list
3. WHEN the codebase is built, THE Security_Hooks module SHALL NOT contain the `check_dangerous_command` function
4. WHEN the codebase is built, THE Agent_Manager module SHALL NOT re-export `DANGEROUS_PATTERNS` or `check_dangerous_command`

### Requirement 2: Delete CmdPermissionManager and Persistent Approval Storage

**User Story:** As a maintainer, I want the filesystem-backed cross-session approval system removed, so that approvals are per-session only and there is a single approval mechanism.

#### Acceptance Criteria

1. WHEN the codebase is built, THE Backend SHALL NOT contain the `cmd_permission_manager.py` module
2. WHEN the codebase is built, THE Agent_Manager module SHALL NOT import or instantiate CmdPermissionManager
3. WHEN the codebase is built, THE Agent_Manager module SHALL NOT accept a `cmd_permission_manager` constructor parameter
4. WHEN the codebase is built, THE Hook_Builder module SHALL NOT accept a `cmd_permission_manager` parameter
5. THE Backend SHALL NOT create or read from the `~/.swarm-ai/cmd_permissions/` directory
6. THE Backend SHALL NOT create or read from `approved_commands.json`

### Requirement 3: Consolidate to Single Dangerous Command Gate

**User Story:** As a developer, I want a single permission gate for dangerous commands, so that there is one clear code path for command approval instead of four overlapping layers.

#### Acceptance Criteria

1. THE Security_Hooks module SHALL expose a single `create_dangerous_command_gate` factory function that returns an async PreToolUse hook
2. WHEN a Bash command is intercepted, THE Dangerous_Command_Gate SHALL check the command against glob patterns loaded from the Dangerous_Patterns_File
3. WHEN a Bash command does not match any dangerous pattern, THE Dangerous_Command_Gate SHALL return an empty dict to allow execution
4. WHEN a Bash command matches a dangerous pattern AND the command is in the Session_Approval_Set, THE Dangerous_Command_Gate SHALL return an empty dict to allow execution
5. WHEN a Bash command matches a dangerous pattern AND the command is NOT in the Session_Approval_Set, THE Dangerous_Command_Gate SHALL enqueue an inline permission request via the HITL_Plumbing and suspend execution until the user responds
6. WHEN the user approves a dangerous command, THE Dangerous_Command_Gate SHALL add the command hash to the Session_Approval_Set and return an empty dict to allow execution
7. WHEN the user denies a dangerous command, THE Dangerous_Command_Gate SHALL return a deny decision with the reason

### Requirement 4: Dangerous Patterns File Management

**User Story:** As a user, I want dangerous command patterns stored in a single human-editable JSON file, so that I can customize which commands require approval.

#### Acceptance Criteria

1. THE Dangerous_Command_Gate SHALL load glob patterns from `~/.swarm-ai/dangerous_commands.json` at startup
2. WHEN `~/.swarm-ai/dangerous_commands.json` does not exist, THE Dangerous_Command_Gate SHALL create the file with the default dangerous patterns from the existing `DEFAULT_DANGEROUS_PATTERNS` list
3. WHEN `~/.swarm-ai/dangerous_commands.json` contains invalid JSON, THE Dangerous_Command_Gate SHALL fall back to the default dangerous patterns and log a warning
4. THE Dangerous_Patterns_File SHALL use the format `{"patterns": ["glob1", "glob2", ...]}` with one glob pattern per entry
5. THE Dangerous_Command_Gate SHALL use `fnmatch` glob matching to compare commands against patterns

### Requirement 5: Per-Session In-Memory Approvals Only

**User Story:** As a user, I want command approvals to last only for the current session, so that dangerous commands require re-approval in new sessions.

#### Acceptance Criteria

1. THE Permission_Manager SHALL maintain a per-session set of approved command hashes keyed by session ID
2. WHEN a user approves a dangerous command, THE Permission_Manager SHALL store the command hash in the Session_Approval_Set for that session
3. WHEN a session is cleaned up, THE Permission_Manager SHALL clear the Session_Approval_Set for that session
4. THE Permission_Manager SHALL NOT persist approved commands to the filesystem
5. THE Permission_Manager SHALL NOT share approved commands across sessions

### Requirement 6: Simplify PermissionManager to HITL Plumbing Only

**User Story:** As a maintainer, I want the PermissionManager to contain only the asyncio HITL plumbing, so that its responsibility is clear and minimal.

#### Acceptance Criteria

1. THE Permission_Manager SHALL retain the per-session asyncio.Queue infrastructure for permission request routing
2. THE Permission_Manager SHALL retain the asyncio.Event-based wait/set mechanism for permission decisions
3. THE Permission_Manager SHALL retain the pending request in-memory store (store, get, update, remove)
4. THE Permission_Manager SHALL retain the `enqueue_permission_request` method for SSE event dispatch
5. THE Permission_Manager SHALL retain the per-session approval tracking (`approve_command`, `is_command_approved`, `clear_session_approvals`, `hash_command`)
6. THE Permission_Manager SHALL NOT contain any filesystem I/O operations
7. THE Permission_Manager SHALL NOT contain the deprecated `get_permission_queue` method

### Requirement 7: Remove Sandbox Conditional Skip

**User Story:** As a developer, I want the dangerous command gate to fire regardless of sandbox mode, so that the user always has visibility into dangerous commands.

#### Acceptance Criteria

1. THE Hook_Builder SHALL NOT skip the dangerous command gate when sandbox is enabled
2. THE Hook_Builder SHALL always attach the Dangerous_Command_Gate as a PreToolUse hook for Bash commands
3. WHEN sandbox is enabled, THE Dangerous_Command_Gate SHALL still prompt the user for dangerous commands via the Inline_Permission_Prompt

### Requirement 8: Simplify Hook Builder Wiring

**User Story:** As a maintainer, I want the hook builder to wire a single gate function with fewer parameters, so that the hook composition is straightforward.

#### Acceptance Criteria

1. THE Hook_Builder `build_hooks` function SHALL accept `permission_manager` as a parameter but SHALL NOT accept `cmd_permission_manager`
2. THE Hook_Builder SHALL call `create_dangerous_command_gate` instead of `create_human_approval_hook`
3. THE Hook_Builder SHALL NOT import `CmdPermissionManager` or reference it in type hints
4. THE Hook_Builder SHALL attach the Dangerous_Command_Gate hook with `HookMatcher(matcher="Bash", hooks=[...])` unconditionally (no sandbox check)


### Requirement 9: Simplify Permission API Endpoints

**User Story:** As a frontend developer, I want both permission endpoints to work with the simplified backend, so that the inline permission flow continues to function without CmdPermissionManager.

#### Acceptance Criteria

1. THE `continue_with_cmd_permission` method in Agent_Manager SHALL set the permission decision via Permission_Manager only (no CmdPermissionManager involvement)
2. THE `continue_with_cmd_permission` method SHALL NOT call `self._cmd_pm.approve()` or reference CmdPermissionManager
3. WHEN the user approves a command via `continue_with_cmd_permission`, THE method SHALL store the approval in the Session_Approval_Set via Permission_Manager only
4. THE `/cmd-permission-response` endpoint in chat.py SHALL NOT call `agent_manager._cmd_pm.approve()` or reference CmdPermissionManager
5. WHEN the user approves a command via `/cmd-permission-response`, THE endpoint SHALL store the approval via Permission_Manager only
6. BOTH endpoints SHALL preserve the existing SSE event contract (`cmd_permission_request` event type)
7. BOTH endpoints SHALL preserve the existing request/response format (requestId, sessionId, decision)

### Requirement 10: Delete Dead Frontend Modal

**User Story:** As a frontend maintainer, I want the unused PermissionRequestModal component removed, so that there is no confusion about which UI is active.

#### Acceptance Criteria

1. WHEN the codebase is built, THE Frontend SHALL NOT contain the `PermissionRequestModal.tsx` file
2. WHEN the codebase is built, THE Frontend SHALL NOT contain any imports of `PermissionRequestModal`
3. THE Inline_Permission_Prompt component (`InlinePermissionRequest.tsx`) SHALL remain unchanged as the sole permission UI

### Requirement 11: Preserve Untouched Concerns

**User Story:** As a developer, I want the file access handler and skill access checker to remain unchanged, so that this refactor has a minimal blast radius.

#### Acceptance Criteria

1. THE Security_Hooks module SHALL retain the `create_file_access_permission_handler` function unchanged
2. THE Security_Hooks module SHALL retain the `create_skill_access_checker` function unchanged
3. THE `bypassPermissions` SDK-level permission mode SHALL continue to function unchanged
4. THE SSE event format for `cmd_permission_request` events SHALL remain unchanged
5. THE `InlinePermissionRequest.tsx` component SHALL remain unchanged

### Requirement 12: Update Startup Wiring and AgentManager Interface

**User Story:** As a maintainer, I want the startup code and AgentManager interface cleaned up so there are no references to the deleted CmdPermissionManager.

#### Acceptance Criteria

1. THE `main.py` lifespan function SHALL NOT import, instantiate, or call `.load()` on CmdPermissionManager
2. THE `main.py` lifespan function SHALL NOT pass `cmd_permission_manager` to `agent_manager.configure()`
3. THE `AgentManager.configure()` method SHALL NOT accept a `cmd_permission_manager` parameter
4. THE `AgentManager.__init__()` SHALL NOT store a `_cmd_pm` attribute
5. THE `AgentManager._build_hooks()` SHALL NOT pass `self._cmd_pm` to the hook builder

### Requirement 13: Permission Visibility in Workspace

**User Story:** As a user, I want to see the current permission configuration (allowed tools and dangerous command patterns) in my workspace, so that I have visibility into what the agent can and cannot do.

#### Acceptance Criteria

1. THE Backend SHALL generate a `SwarmWS/.claude/settings/permissions.json` file at agent startup that reflects the current dangerous command patterns
2. THE Permission_Settings_File SHALL contain a `dangerous_commands` section listing the current glob patterns that require user approval
3. THE Permission_Settings_File SHALL contain a `description` field explaining the permission model in plain language
4. THE Permission_Settings_File SHALL be regenerated at each agent startup to reflect any changes to the dangerous patterns file
5. THE Permission_Settings_File SHALL be read-only for informational purposes — editing it SHALL NOT change the actual permission behavior (the source of truth is `~/.swarm-ai/dangerous_commands.json`)

### Requirement 14: Update and Delete Test Files

**User Story:** As a maintainer, I want test files updated to reflect the simplified permission system, so that tests pass and don't reference deleted modules.

#### Acceptance Criteria

1. THE `test_cmd_permission_manager.py` test file SHALL be deleted (tests the deleted module)
2. THE `test_credential_validator_integration.py` SHALL NOT import or mock CmdPermissionManager
3. THE `helpers_parallel_session.py` SHALL NOT pass `cmd_permission_manager` when constructing AgentManager
4. THE `test_parallel_session_safety.py` SHALL continue to pass with the simplified PermissionManager (queue isolation tests remain valid)
5. THE `test_permission_manager.py` SHALL be updated to remove tests for any deleted methods (e.g. `get_permission_queue`)

### Requirement 15: Clean Up Dead Imports in Security Hooks

**User Story:** As a maintainer, I want unused imports removed from security_hooks.py so the module is clean and doesn't reference deleted modules.

#### Acceptance Criteria

1. THE Security_Hooks module SHALL NOT import `from database import db` (currently unused)
2. THE Security_Hooks module SHALL NOT import `CmdPermissionManager`
3. THE Security_Hooks module SHALL NOT import `PermissionManager` as a type (the gate receives it as a parameter, typed inline or via TYPE_CHECKING)
