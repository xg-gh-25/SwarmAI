# Requirements Document

## Introduction

This feature unifies the working directory (cwd) for all SwarmAI agents to use a single, hardcoded SwarmWorkspace path (`~/.swarm-ai/SwarmWS`). Currently, global user mode agents use `Path.home()` and isolated mode agents use per-agent directories under `~/.swarm-ai/workspaces/{agent_id}/`. After this change, all agents share the single SwarmWorkspace directory as their cwd.

The design is aggressively simplified around a single-workspace model: there is only ever one workspace (`SwarmWS`), all heavy setup (folder structure, skill symlinks, templates, migration) happens at app startup, and per-session setup is lightweight (cached path read, config assembly, no filesystem I/O). The `swarm_workspaces` DB table is retained for future extensibility but all multi-workspace UI is removed.

## Glossary

- **Agent_Manager**: The backend component (`agent_manager.py`) responsible for resolving agent configuration, building runtime options via `_build_options()`, and launching agent sessions.
- **SwarmWorkspace**: The single persistent workspace directory at `~/.swarm-ai/SwarmWS` containing artifacts, context files, skills, and templates. Only one exists.
- **SwarmWorkspace_Manager**: The backend singleton (`swarm_workspace_manager.py`) that manages SwarmWorkspace lifecycle operations (folder structure, context files, path expansion).
- **Sandbox_Manager**: The backend component (`agent_sandbox_manager.py`) that manages skill symlinks and templates in the workspace.
- **Init_Manager**: The backend component (`initialization_manager.py`) that runs all heavy setup at app startup: folder structure creation, skill symlinks, templates, path migration, and default workspace DB record.
- **Default_Workspace**: The single SwarmWorkspace named "SwarmWS", located at `~/.swarm-ai/SwarmWS`.
- **Skills_Directory**: The `.claude/skills/` directory within the workspace where skill SKILL.md files are symlinked.
- **Templates_Directory**: The `.swarmai/` directory within the workspace containing agent template files.
- **App_Data_Dir**: The platform-specific application data directory, resolved to `~/.swarm-ai/` on all platforms.
- **Frontend_Chat**: The React ChatPage component that initiates chat sessions.
- **Workspace_Selection_Hook**: The `useWorkspaceSelection` React hook, simplified to return the single SwarmWS path.
- **Cached_Workspace_Path**: The in-memory cached string of the expanded SwarmWS filesystem path, set once at app init and read per-session without DB lookup.

## Requirements

### Requirement 1: Unified Working Directory from Cached Path

**User Story:** As a developer, I want all agents to use the single SwarmWorkspace path as their working directory, resolved from a cached string at session start with no DB lookup, so that per-session setup is lightweight and all agent outputs land in the workspace.

#### Acceptance Criteria

1. THE Agent_Manager SHALL cache the expanded Default_Workspace filesystem path as an in-memory string during app initialization.
2. WHEN a chat session is initiated, THE Agent_Manager SHALL read the Cached_Workspace_Path and set it as the agent working directory without performing a database lookup.
3. THE Agent_Manager SHALL use the Cached_Workspace_Path as the working directory for agents in global user mode.
4. THE Agent_Manager SHALL use the Cached_Workspace_Path as the working directory for agents in isolated mode.
5. THE Agent_Manager SHALL not accept a workspace_id parameter in the chat/session API, since only one workspace exists.

### Requirement 2: Default Workspace Path Flattening

**User Story:** As a developer, I want the default SwarmWorkspace path to be `~/.swarm-ai/SwarmWS` instead of `~/.swarm-ai/swarm-workspaces/SwarmWS`, so that the directory structure is simpler and flatter.

#### Acceptance Criteria

1. THE SwarmWorkspace_Manager SHALL use `{app_data_dir}/SwarmWS` as the default workspace file_path in DEFAULT_WORKSPACE_CONFIG.
2. WHEN the application starts and no default workspace exists in the database, THE Init_Manager SHALL create the Default_Workspace at `{app_data_dir}/SwarmWS`.
3. WHEN the application starts and a default workspace exists with the old path `{app_data_dir}/swarm-workspaces/SwarmWS`, THE Init_Manager SHALL migrate the database record to use the new path `{app_data_dir}/SwarmWS`.
4. WHEN migrating the default workspace path, THE Init_Manager SHALL move existing workspace contents from the old path to the new path if the old path exists on disk and the new path does not.
5. IF both old and new paths exist on disk during migration, THEN THE Init_Manager SHALL keep the new path, log a warning, and leave the old path untouched for manual cleanup.

### Requirement 3: Skill Symlinks at App Init and on CRUD Events

**User Story:** As a developer, I want all skills to be symlinked into the SwarmWorkspace at app startup and re-synced when skills are created, updated, or deleted, so that all agents see the same skills without per-session filesystem I/O.

#### Acceptance Criteria

1. WHEN the application starts, THE Init_Manager SHALL create the Skills_Directory at `{swarm_workspace_path}/.claude/skills/` if it does not exist.
2. WHEN the application starts, THE Init_Manager SHALL symlink all available skill files into the SwarmWorkspace Skills_Directory.
3. WHEN a skill is created, updated, or deleted via the API, THE Sandbox_Manager SHALL re-sync skill symlinks in the SwarmWorkspace Skills_Directory to match the current skill set.
4. WHEN skill symlinks are synced, THE Sandbox_Manager SHALL remove stale symlinks and add missing symlinks to match the current available skill set.
5. IF a skill source file does not exist at the expected path, THEN THE Sandbox_Manager SHALL skip that skill and log a warning.
6. THE Agent_Manager SHALL not perform skill symlinking during per-session setup.

### Requirement 4: Templates at App Init Only

**User Story:** As a developer, I want agent template files (`.swarmai/`) to be placed in the SwarmWorkspace at app startup only, so that per-session setup performs no filesystem I/O for templates.

#### Acceptance Criteria

1. WHEN the application starts, THE Init_Manager SHALL copy template files into the Templates_Directory at `{swarm_workspace_path}/.swarmai/` if they are not already present.
2. THE Init_Manager SHALL not overwrite existing template files in the SwarmWorkspace.
3. THE Agent_Manager SHALL not copy or check templates during per-session setup.

### Requirement 5: Remove Per-Agent Workspace Isolation

**User Story:** As a developer, I want to remove the per-agent isolated workspace directories, so that all agents share the SwarmWorkspace and the system is simpler.

#### Acceptance Criteria

1. THE Agent_Manager SHALL not create or reference per-agent workspace directories under `{app_data_dir}/workspaces/{agent_id}/`.
2. THE Agent_Manager SHALL not call `agent_sandbox_manager.get_agent_workspace()` for determining the working directory.
3. THE Agent_Manager SHALL not call `agent_sandbox_manager.rebuild_agent_workspace()` for per-agent directory rebuilds.
4. THE Agent_Manager SHALL set `setting_sources` to `['project']` for all agents regardless of workspace mode, since the working directory is the SwarmWorkspace.

### Requirement 6: Frontend Simplification

**User Story:** As a developer, I want the frontend to stop passing workspace-related parameters to the chat API and remove the workspace selector UI, since there is only one workspace.

#### Acceptance Criteria

1. THE Frontend_Chat SHALL not pass `addDirs` with the SwarmWorkspace filePath to the backend chat API.
2. THE Frontend_Chat SHALL not pass `workspaceId` to the backend chat API, since the backend uses the Cached_Workspace_Path.
3. THE Frontend_Chat SHALL remove the workspace selector dropdown from the UI.
4. THE Workspace_Selection_Hook SHALL return the single SwarmWS path for any UI component that needs the workspace path client-side (e.g., file browser sidebar).

### Requirement 7: File Access Control Adaptation

**User Story:** As a developer, I want file access control to use the SwarmWorkspace path as the base allowed directory, so that isolated-mode agents can read and write within the workspace.

#### Acceptance Criteria

1. WHILE an agent is running in isolated mode, THE Agent_Manager SHALL set the Cached_Workspace_Path as the primary allowed directory for file access control.
2. WHILE an agent is running in global user mode, THE Agent_Manager SHALL disable file access control (preserving current behavior for global user mode).
3. WHEN additional allowed directories are specified in the agent configuration, THE Agent_Manager SHALL include those directories alongside the SwarmWorkspace path in the allowed directories list.

### Requirement 8: Workspace Folder Structure at App Init Only

**User Story:** As a developer, I want the SwarmWorkspace folder structure (Artifacts/, ContextFiles/, Transcripts/) to be created at app startup only, so that per-session setup performs no folder creation I/O.

#### Acceptance Criteria

1. WHEN the application starts, THE Init_Manager SHALL verify that the standard folder structure (Artifacts/, ContextFiles/, Transcripts/ and subdirectories) exists in the SwarmWorkspace and create any missing directories.
2. IF the SwarmWorkspace path exists but is missing subdirectories, THEN THE Init_Manager SHALL create only the missing subdirectories without modifying existing content.
3. THE Agent_Manager SHALL not verify or create folder structure during per-session setup.

### Requirement 9: MCP Simplification

**User Story:** As a developer, I want MCP server configuration to be built directly from the agent's `mcp_ids` without workspace-level filtering, so that per-session MCP setup is a simple dict build with no workspace resolver dependency.

#### Acceptance Criteria

1. WHEN building MCP configuration for an agent session, THE Agent_Manager SHALL use the agent's `mcp_ids` directly to look up MCP server records from the database.
2. THE Agent_Manager SHALL not call `workspace_config_resolver.get_effective_mcps()` to filter MCP servers.
3. THE Agent_Manager SHALL not accept a `workspace_id` parameter in `_build_mcp_config()`.

### Requirement 10: Remove WorkspaceConfigResolver

**User Story:** As a developer, I want the `WorkspaceConfigResolver` class removed, with its initialization-time logic folded into the Init_Manager, so that there is no workspace-scoped configuration layer.

#### Acceptance Criteria

1. THE Init_Manager SHALL incorporate any workspace initialization logic previously handled by WorkspaceConfigResolver (skill registration, MCP registration) into the app startup flow.
2. THE Agent_Manager SHALL not import or reference `workspace_config_resolver` for MCP or skill filtering.
3. THE codebase SHALL not contain the `WorkspaceConfigResolver` class after this change.
