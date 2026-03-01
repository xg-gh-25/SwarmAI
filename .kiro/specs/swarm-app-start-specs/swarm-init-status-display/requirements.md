# Requirements Document

## Introduction

This feature adds a system initialization status display to the BackendStartupOverlay (app starting page/splash screen) of the SwarmAI desktop application. When the application starts, the backend initializes the database, creates/updates the SwarmAgent with system skills and MCP servers, starts the channel gateway, and initializes the default SwarmWorkspace. Currently, the startup overlay only shows a generic "Starting..." message. This feature will display the detailed initialization process in a CLI-like format, similar to how Kiro CLI shows its initialization status, providing users with visibility into each startup step.

## Glossary

- **SwarmAgent**: The default system agent that is automatically created and configured during application startup
- **SwarmWorkspace**: The default system workspace that is automatically created during application startup for organizing user work
- **System_Status_API**: The backend endpoint that provides initialization status information
- **Backend_Startup_Overlay**: The existing React component (BackendStartupOverlay.tsx) that displays during app startup
- **System_Skills**: Skills that are bundled with the application and automatically registered during startup
- **System_MCP_Servers**: MCP servers that are bundled with the application and automatically registered during startup
- **Channel_Gateway**: The backend service that manages communication channels for agents

## Requirements

### Requirement 1: System Status API Endpoint

**User Story:** As a frontend developer, I want a backend API endpoint that returns system initialization status, so that I can display the current state of the system to users on the startup screen.

#### Acceptance Criteria

1. THE System_Status_API SHALL expose a GET endpoint at `/api/system/status`
2. WHEN the endpoint is called, THE System_Status_API SHALL return the database health status as a boolean
3. WHEN the endpoint is called, THE System_Status_API SHALL return the SwarmAgent information including name, bound skills count, and bound MCP servers count
4. WHEN the endpoint is called, THE System_Status_API SHALL return the channel gateway running status as a boolean
5. WHEN the endpoint is called, THE System_Status_API SHALL return the SwarmWorkspace status including ready state, name, and path
6. WHEN the endpoint is called, THE System_Status_API SHALL return an overall initialization status indicating whether all components are ready
7. IF the database is unavailable, THEN THE System_Status_API SHALL return database status as false with an appropriate error message
8. IF the SwarmAgent does not exist, THEN THE System_Status_API SHALL return agent status as not ready with an appropriate message
9. IF the SwarmWorkspace does not exist, THEN THE System_Status_API SHALL return workspace status as not ready with an appropriate message

### Requirement 2: System Status Response Schema

**User Story:** As a frontend developer, I want a well-defined response schema from the status endpoint, so that I can reliably parse and display the initialization information.

#### Acceptance Criteria

1. THE System_Status_API SHALL return a JSON response with a `database` object containing `healthy` boolean and optional `error` string
2. THE System_Status_API SHALL return a JSON response with an `agent` object containing `ready` boolean, `name` string, `skillsCount` number, and `mcpServersCount` number
3. THE System_Status_API SHALL return a JSON response with a `channelGateway` object containing `running` boolean
4. THE System_Status_API SHALL return a JSON response with a `swarmWorkspace` object containing `ready` boolean, `name` string, and `path` string
5. THE System_Status_API SHALL return a JSON response with an `initialized` boolean indicating overall system readiness
6. THE System_Status_API SHALL return a JSON response with a `timestamp` string in ISO format

### Requirement 3: Frontend System Service

**User Story:** As a frontend developer, I want a TypeScript service to fetch system status, so that I can integrate the status display into the startup overlay.

#### Acceptance Criteria

1. THE Frontend_System_Service SHALL provide a `getStatus()` function that calls the `/api/system/status` endpoint
2. THE Frontend_System_Service SHALL convert snake_case response fields to camelCase for TypeScript consumption
3. THE Frontend_System_Service SHALL convert the `swarm_workspace` snake_case response fields to camelCase (`swarmWorkspace`)
4. WHEN the API call fails, THE Frontend_System_Service SHALL propagate the error to the caller

### Requirement 4: Startup Overlay Initialization Status Display

**User Story:** As a user, I want to see the system initialization status on the app startup screen, so that I know what the system is doing while it starts up.

#### Acceptance Criteria

1. THE Backend_Startup_Overlay SHALL display initialization status items below the logo and app name during startup
2. WHILE the backend health check is in progress, THE Backend_Startup_Overlay SHALL show "Connecting to backend..." as the first status item with a spinner
3. WHEN the backend health check succeeds, THE Backend_Startup_Overlay SHALL fetch the system status from the API
4. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display a green checkmark (✓) next to "Database initialized"
5. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display a green checkmark (✓) next to "SwarmAgent ready"
6. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display the bound skills count as a nested item under SwarmAgent (e.g., "└─ 3 system skills bound")
7. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display the bound MCP servers count as a nested item under SwarmAgent (e.g., "└─ 2 system MCP servers bound")
8. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display a green checkmark (✓) next to "Channel gateway started"
9. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display a green checkmark (✓) next to "Swarm Workspace initialized" when ready
10. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display the workspace path as a nested item under Swarm Workspace (e.g., "└─ ~/.swarm-ai/swarm-workspaces/SwarmWS")
11. IF the Swarm_Workspace is not ready, THEN THE Backend_Startup_Overlay SHALL display a red X (✗) next to "Swarm Workspace initialized" with an error message
12. IF any initialization step fails, THEN THE Backend_Startup_Overlay SHALL display a red X (✗) next to the failed step with an error message

### Requirement 5: CLI-Style Visual Formatting

**User Story:** As a user, I want the initialization status to look like a CLI output, so that it feels familiar and professional.

#### Acceptance Criteria

1. THE Backend_Startup_Overlay SHALL use a monospace font for the initialization status display
2. THE Backend_Startup_Overlay SHALL use tree-style indentation with "└─" characters for nested items
3. THE Backend_Startup_Overlay SHALL use green color for success checkmarks
4. THE Backend_Startup_Overlay SHALL use red color for failure indicators
5. THE Backend_Startup_Overlay SHALL animate status items appearing sequentially for a CLI-like feel

### Requirement 6: Internationalization Support

**User Story:** As a user in a non-English locale, I want the initialization status text to be translated, so that I can understand the system state in my language.

#### Acceptance Criteria

1. THE Backend_Startup_Overlay SHALL use i18n translation keys for all initialization status text
2. THE Translation_File SHALL include keys for "Connecting to backend", "Database initialized", "SwarmAgent ready", "Channel gateway started", "Swarm Workspace initialized", and related messages
3. THE Translation_File SHALL include keys for nested items like "system skills bound", "system MCP servers bound", and workspace path display
4. THE Translation_File SHALL include keys for error messages

### Requirement 7: Startup Flow Integration

**User Story:** As a user, I want the initialization status to integrate smoothly with the existing startup flow, so that the app starts reliably.

#### Acceptance Criteria

1. THE Backend_Startup_Overlay SHALL maintain the existing health check polling mechanism
2. THE Backend_Startup_Overlay SHALL only fetch system status after the health check succeeds
3. THE Backend_Startup_Overlay SHALL proceed to fade out and show the main app after all initialization steps complete successfully
4. IF the system status API call fails, THEN THE Backend_Startup_Overlay SHALL still proceed to the main app (graceful degradation)
5. THE Backend_Startup_Overlay SHALL not block app startup if system status fetch times out after 5 seconds
