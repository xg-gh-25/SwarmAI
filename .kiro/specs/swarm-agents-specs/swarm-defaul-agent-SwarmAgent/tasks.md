# Implementation Plan: SwarmAgent System Default

## Overview

This implementation plan breaks down the SwarmAgent System Default feature into discrete coding tasks. The approach builds on the existing default agent infrastructure, adding system resource detection, binding protection, and UI indicators.

## Tasks

- [x] 1. Backend Schema and Database Changes
  - [x] 1.1 Add `is_system_agent` field to agent Pydantic models
    - Add `is_system_agent: bool = Field(default=False)` to `AgentConfig` in `backend/schemas/agent.py`
    - Add `is_system_agent: bool = False` to `AgentResponse` model
    - Add `is_system_agent: bool | None = None` to `AgentUpdateRequest` model (should not be updatable by users)
    - _Requirements: 1.1, 1.2_
  
  - [x] 1.2 Add `is_system_agent` column to database schema
    - Add `is_system_agent INTEGER DEFAULT 0` to agents table in `backend/database/sqlite.py`
    - Add migration logic to add column if not exists
    - _Requirements: 1.1_

  - [x] 1.3 Add database query methods for system resources
    - Add `list_by_system()` method to `SkillsTable` class
    - Add `list_by_system()` method to `MCPServersTable` class
    - Both methods should return records where `is_system=1`
    - _Requirements: 2.3, 3.3, 7.3_

- [x] 2. Update Agent Manager for System Resource Binding
  - [x] 2.1 Add SWARM_AGENT_NAME constant
    - Define `SWARM_AGENT_NAME = "SwarmAgent"` as hardcoded constant
    - _Requirements: 1.2_
  
  - [x] 2.2 Modify `ensure_default_agent()` to set `is_system_agent=True`
    - Update agent creation to include `is_system_agent=True`
    - Ensure name is always set to `SWARM_AGENT_NAME`
    - _Requirements: 1.1, 1.2_
  
  - [x] 2.3 Implement runtime system resource binding
    - After registering default skills/MCPs, query ALL system resources from database
    - Use `db.skills.list_by_system()` and `db.mcp_servers.list_by_system()`
    - Bind all system resource IDs to SwarmAgent
    - Log the count of bound system skills and MCPs
    - _Requirements: 2.3, 3.3, 7.3, 7.5_

- [x] 3. Checkpoint - Backend Initialization
  - Ensure backend starts successfully
  - Verify SwarmAgent is created with `is_system_agent=True`
  - Verify all system skills and MCPs are bound
  - Ask the user if questions arise

- [x] 4. Implement API Protections
  - [x] 4.1 Add name change protection for SwarmAgent
    - Modify `update_agent` endpoint in `backend/routers/agents.py`
    - Check if agent has `is_system_agent=True`
    - Reject name changes with ValidationException
    - _Requirements: 1.2_
  
  - [x] 4.2 Add system resource unbind protection
    - In `update_agent` endpoint, check if agent is system agent
    - Query system skills and MCPs using `list_by_system()`
    - Verify all system resource IDs remain in the update request
    - Reject updates that would remove system resources
    - _Requirements: 4.1, 4.2_
  
  - [x] 4.3 Ensure delete protection includes system agent check
    - Verify existing delete protection covers `is_system_agent` flag
    - Add explicit check if not already present
    - _Requirements: 1.3_

  - [x] 4.4 Write unit tests for API protections
    - Test name update rejection for SwarmAgent
    - Test system skill unbind rejection
    - Test system MCP unbind rejection
    - Test user skill bind/unbind success
    - Test delete rejection for SwarmAgent
    - _Requirements: 1.2, 1.3, 4.1, 4.2, 5.1, 5.2_

- [x] 5. Write property tests for backend
  - [x] 5.1 Write property test for name update protection
    - **Property 1: Name Update Protection**
    - **Validates: Requirements 1.2**
    - Generate random valid agent names
    - Verify all name updates to SwarmAgent are rejected
  
  - [x] 5.2 Write property test for system resource unbind protection
    - **Property 4: System Resource Unbind Protection**
    - **Validates: Requirements 4.1, 4.2**
    - Generate random subsets excluding system resources
    - Verify all such updates are rejected
  
  - [x] 5.3 Write property test for initialization idempotence
    - **Property 5: Initialization Idempotence**
    - **Validates: Requirements 7.4**
    - Run ensure_default_agent() multiple times
    - Verify no duplicate resources created

- [x] 6. Checkpoint - API Layer Complete
  - Ensure all API protections work correctly
  - Verify name changes are blocked
  - Verify system resource unbinding is blocked
  - Verify user resource binding works
  - Ask the user if questions arise

- [x] 7. Frontend Type and Service Updates
  - [x] 7.1 Add `isSystemAgent` to TypeScript Agent interface
    - Add `isSystemAgent: boolean` to Agent interface in `desktop/src/types/index.ts`
    - _Requirements: 6.5, 6.6 (analogous for agent)_
  
  - [x] 7.2 Update agents service case conversion
    - Add `is_system_agent` to `isSystemAgent` mapping in `toCamelCase` function
    - Update `toSnakeCase` if needed (should not send is_system_agent in updates)
    - _Requirements: 6.7, 6.8 (analogous for agent)_

- [x] 8. Update Agents Page UI
  - [x] 8.1 Add "System" badge for SwarmAgent
    - Show "System" badge next to agent name when `isSystemAgent` is true
    - Style badge with primary color similar to existing "Default" badge
    - _Requirements: 1.4_
  
  - [x] 8.2 Disable delete button for system agent
    - Extend existing delete button disable logic to include `isSystemAgent`
    - Add visual indication (opacity, cursor)
    - _Requirements: 1.5_
  
  - [x] 8.3 Disable name field for system agent
    - Make name input readonly when `isSystemAgent` is true
    - Add visual styling to indicate disabled state
    - _Requirements: 1.6_

- [x] 9. Update Agent Detail/Edit Page
  - [x] 9.1 Add system indicator for bound skills
    - Show "System" badge next to system skills in the skills list
    - Hide unbind button for system skills
    - Show unbind button for user skills
    - _Requirements: 2.6, 4.3, 5.3_
  
  - [x] 9.2 Add system indicator for bound MCPs
    - Show "System" badge next to system MCPs in the MCPs list
    - Hide unbind button for system MCPs
    - Show unbind button for user MCPs
    - _Requirements: 3.6, 4.4, 5.6_

- [x] 10. Checkpoint - Frontend Complete
  - Ensure UI correctly shows System badge for SwarmAgent
  - Verify delete button is disabled for SwarmAgent
  - Verify name field is disabled for SwarmAgent
  - Verify system skills/MCPs show System indicator
  - Verify user skills/MCPs show unbind option
  - Ask the user if questions arise

- [x] 11. Write property tests for frontend
  - [x] 11.1 Write property test for case conversion
    - **Property 2: System Resource Detection** (frontend aspect)
    - **Validates: Requirements 6.7, 6.8**
    - Generate random agent responses with is_system_agent
    - Verify toCamelCase produces correct isSystemAgent value

- [x] 12. Final Checkpoint
  - Ensure all tests pass
  - Verify end-to-end flow works:
    - Fresh start creates SwarmAgent with all system resources
    - SwarmAgent shows "System" badge
    - SwarmAgent name cannot be changed
    - SwarmAgent cannot be deleted
    - System skills/MCPs cannot be unbound
    - User skills/MCPs can be bound/unbound
    - Adding new system skill to resources folder binds on restart
  - Ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Follow existing code patterns in the SwarmAI codebase
- CRITICAL: Always update both `toSnakeCase` AND `toCamelCase` in services when adding fields
- The `is_system` field already exists on skills and MCPs - no schema changes needed there
- This feature builds on the existing default agent infrastructure


## Post-Refactor Note (unified-swarm-workspace-cwd)

> **This spec was written before the unified-swarm-workspace-cwd refactor.** The following changes from that refactor supersede elements described in this document:
>
> - **Per-agent workspace isolation removed**: All agents now use a single SwarmWorkspace at `~/.swarm-ai/SwarmWS`. Per-agent directories under `workspaces/{agent_id}/` no longer exist.
> - **Skill symlinks shared**: Tasks related to skill binding (Tasks 2.3, 9.1) now operate via `AgentSandboxManager.setup_workspace_skills()` which symlinks all skills into `SwarmWS/.claude/skills/`, not per-agent directories.
> - **MCP binding is direct**: Tasks related to MCP binding (Tasks 2.3, 9.2) now use agent's `mcp_ids` directly with no workspace intersection model. `WorkspaceConfigResolver` was removed entirely.
> - **`rebuild_agent_workspace()` replaced**: `AgentSandboxManager.setup_workspace_skills()` handles skill symlinks at app init and on skill CRUD events.
>
> See `.kiro/specs/unified-swarm-workspace-cwd/` for the full refactor specification.
