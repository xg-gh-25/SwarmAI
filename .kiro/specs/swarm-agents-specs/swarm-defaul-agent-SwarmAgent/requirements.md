# Requirements Document

## Introduction

This document specifies the requirements for the SwarmAgent System Default feature. The feature creates a protected, always-available system agent named "SwarmAgent" that automatically binds all system skills and MCP servers at runtime. This differs from the existing default agent feature by focusing on automatic runtime binding of system resources without requiring code changes when new system skills/MCPs are added.

## Glossary

- **SwarmAgent**: The hardcoded system agent name that cannot be edited by users (brand protection)
- **System_Skill**: A skill file stored in `desktop/resources/default-skills/` with `is_system=true` flag
- **System_MCP**: An MCP server defined in `desktop/resources/default-mcp-servers.json` with `is_system=true` flag
- **User_Skill**: A skill created by the user that can be optionally bound to SwarmAgent
- **User_MCP**: An MCP server created by the user that can be optionally bound to SwarmAgent
- **System_Resources_Folder**: The `desktop/resources/` directory containing default skills and MCP configurations
- **Runtime_Binding**: The automatic association of system skills/MCPs to SwarmAgent on application startup
- **Agents_Page**: The agent management interface for viewing and editing agents

## Requirements

### Requirement 1: SwarmAgent Auto-Creation and Protection

**User Story:** As a user, I want a protected system agent named "SwarmAgent" that is always available and cannot be deleted, so that I always have a fully-capable agent ready to use.

#### Acceptance Criteria

1. WHEN the application starts for the first time, THE System SHALL create an agent named "SwarmAgent"
2. THE System SHALL hardcode the agent name as "SwarmAgent" and users SHALL NOT be able to edit the name
3. WHEN a delete request is made for SwarmAgent, THE System SHALL reject the request with an appropriate error message
4. WHEN displaying SwarmAgent in the UI, THE Agents_Page SHALL show a "System" badge to indicate its protected status
5. WHEN displaying SwarmAgent in the UI, THE Agents_Page SHALL disable the delete button
6. WHEN displaying SwarmAgent in the UI, THE Agents_Page SHALL make the name field readonly/disabled

### Requirement 2: System Skill Detection and Binding

**User Story:** As a developer, I want system skills to be automatically detected from the resources folder and bound to SwarmAgent at runtime, so that new system skills work without code changes.

#### Acceptance Criteria

1. WHEN the application starts, THE System SHALL scan `desktop/resources/default-skills/` for skill files
2. WHEN a skill file is found in the system resources folder, THE System SHALL mark it with `is_system=true`
3. WHEN SwarmAgent is initialized, THE System SHALL automatically bind all system skills to SwarmAgent
4. WHEN a new system skill file is added to the resources folder, THE System SHALL detect and bind it on next application restart
5. THE System SHALL add an `is_system` boolean field to the skill schema with default value `false`
6. WHEN displaying skills bound to SwarmAgent, THE UI SHALL show a "System" indicator for system skills

> **⚠️ Post-Refactor Update**: Skill symlinks are now shared in `SwarmWS/.claude/skills/` via `AgentSandboxManager.setup_workspace_skills()` rather than per-agent directories. This method is called at app init and on skill CRUD events.

### Requirement 3: System MCP Server Detection and Binding

**User Story:** As a developer, I want system MCP servers to be automatically detected from the configuration file and bound to SwarmAgent at runtime, so that new system MCPs work without code changes.

#### Acceptance Criteria

1. WHEN the application starts, THE System SHALL load MCP configurations from `desktop/resources/default-mcp-servers.json`
2. WHEN an MCP server is defined in the system configuration file, THE System SHALL mark it with `is_system=true`
3. WHEN SwarmAgent is initialized, THE System SHALL automatically bind all system MCP servers to SwarmAgent
4. WHEN a new MCP server is added to the configuration file, THE System SHALL detect and bind it on next application restart
5. THE System SHALL add an `is_system` boolean field to the MCP schema with default value `false`
6. WHEN displaying MCP servers bound to SwarmAgent, THE UI SHALL show a "System" indicator for system MCPs

> **⚠️ Post-Refactor Update**: MCP binding now uses the agent's `mcp_ids` directly — no workspace intersection model. `WorkspaceConfigResolver` was removed entirely.

### Requirement 4: System Resource Unbind Protection

**User Story:** As a user, I want system skills and MCPs to be permanently bound to SwarmAgent, so that the system agent always has full capabilities.

#### Acceptance Criteria

1. WHEN a user attempts to unbind a system skill from SwarmAgent, THE System SHALL reject the request
2. WHEN a user attempts to unbind a system MCP from SwarmAgent, THE System SHALL reject the request
3. WHEN displaying system skills on the SwarmAgent management page, THE UI SHALL NOT show an unbind option
4. WHEN displaying system MCPs on the SwarmAgent management page, THE UI SHALL NOT show an unbind option

### Requirement 5: User Resource Binding to SwarmAgent

**User Story:** As a user, I want to optionally bind my own custom skills to SwarmAgent, so that I can extend its capabilities with my own tools.

#### Acceptance Criteria

1. THE System SHALL allow users to bind their own custom skills to SwarmAgent
2. THE System SHALL allow users to unbind their own custom skills from SwarmAgent
3. WHEN displaying user skills bound to SwarmAgent, THE UI SHALL show an unbind option
4. THE System SHALL allow users to bind their own custom MCP servers to SwarmAgent
5. THE System SHALL allow users to unbind their own custom MCP servers from SwarmAgent
6. WHEN displaying user MCPs bound to SwarmAgent, THE UI SHALL show an unbind option

### Requirement 6: Schema Extensions for System Flag

**User Story:** As a developer, I want the skill and MCP schemas to include an `is_system` field, so that the system can distinguish between system and user resources.

#### Acceptance Criteria

1. THE System SHALL add an `is_system` boolean field to the SkillMetadata Pydantic model with default value `false`
2. THE System SHALL add an `is_system` boolean field to the SkillResponse Pydantic model
3. THE System SHALL add an `is_system` boolean field to the MCPConfig Pydantic model with default value `false`
4. THE System SHALL add an `is_system` boolean field to the MCPResponse Pydantic model
5. THE System SHALL add an `isSystem` field to the TypeScript Skill interface
6. THE System SHALL add an `isSystem` field to the TypeScript MCPServer interface
7. WHEN converting API responses, THE skills service SHALL map `is_system` to `isSystem` in the case conversion function
8. WHEN converting API responses, THE MCP service SHALL map `is_system` to `isSystem` in the case conversion function

### Requirement 7: Runtime Binding Mechanism

**User Story:** As a developer, I want the system binding to happen at runtime during application startup, so that the binding is always current with the resources folder contents.

#### Acceptance Criteria

1. WHEN the application starts, THE System SHALL execute the system resource detection before SwarmAgent initialization
2. WHEN system resources are detected, THE System SHALL register them in the database with `is_system=true`
3. WHEN SwarmAgent is initialized, THE System SHALL query for all resources with `is_system=true` and bind them
4. IF a system resource already exists in the database, THEN THE System SHALL update it rather than create a duplicate
5. THE System SHALL log the number of system skills and MCPs bound to SwarmAgent on startup

### Requirement 8: SwarmAgent Management Page UI

**User Story:** As a user, I want to see all bound skills and MCPs on the SwarmAgent management page with clear indicators of which are system vs user resources.

#### Acceptance Criteria

1. WHEN viewing SwarmAgent details, THE UI SHALL display all bound skills in a list
2. WHEN viewing SwarmAgent details, THE UI SHALL display all bound MCP servers in a list
3. WHEN displaying a system skill, THE UI SHALL show a "System" badge and no unbind button
4. WHEN displaying a user skill, THE UI SHALL show an unbind button
5. WHEN displaying a system MCP, THE UI SHALL show a "System" badge and no unbind button
6. WHEN displaying a user MCP, THE UI SHALL show an unbind button


## Post-Refactor Note (unified-swarm-workspace-cwd)

> **This spec was written before the unified-swarm-workspace-cwd refactor.** The following changes from that refactor affect elements described in this document:
>
> - **Per-agent workspace isolation removed** (Requirements 2, 3): All agents now use a single SwarmWorkspace at `~/.swarm-ai/SwarmWS`. Per-agent directories under `workspaces/{agent_id}/` no longer exist.
> - **Skill symlinks shared** (Requirement 2): All skills are now symlinked into `SwarmWS/.claude/skills/` via `AgentSandboxManager.setup_workspace_skills()` rather than per-agent directories. This method is called at app init and on skill CRUD events.
> - **MCP binding is direct** (Requirement 3): MCP configuration uses agent's `mcp_ids` directly with no workspace intersection model. `WorkspaceConfigResolver` was removed entirely.
> - **`rebuild_agent_workspace()` replaced**: `AgentSandboxManager.setup_workspace_skills()` now handles skill symlinks at app init and on skill CRUD events, shared across all agents.
>
> See `.kiro/specs/unified-swarm-workspace-cwd/` for the full refactor specification.
