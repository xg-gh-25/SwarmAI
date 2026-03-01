# Requirements Document

## Introduction

This feature optimizes SwarmAI application startup time by implementing a "first-run vs subsequent-run" initialization strategy. Currently, the application performs full initialization (scanning skills, MCP servers, creating default agent/workspace) on every startup, causing slow startup times and potential "Failed to Load Agent" errors. The optimization ensures that full initialization only occurs on first run, while subsequent startups perform quick validation checks only.

## Glossary

- **Initialization_Manager**: The component responsible for tracking and managing application initialization state
- **SwarmAgent**: The default system agent created during first-time initialization
- **SwarmWorkspace**: The default workspace created during first-time initialization
- **Initialization_Flag**: A persistent flag in the database indicating whether first-time initialization has been completed
- **Quick_Validation**: A fast check that verifies default resources exist without performing full scanning/registration
- **Full_Initialization**: The complete initialization process including directory scanning, skill registration, MCP server registration, and default resource creation

## Requirements

### Requirement 1: Initialization State Tracking

**User Story:** As a user, I want the application to remember that it has been initialized, so that subsequent startups are fast.

#### Acceptance Criteria

1. THE Initialization_Manager SHALL store an initialization_complete flag in the app_settings database table
2. WHEN the application starts for the first time, THE Initialization_Manager SHALL set initialization_complete to false
3. WHEN full initialization completes successfully, THE Initialization_Manager SHALL set initialization_complete to true
4. THE Initialization_Manager SHALL persist the initialization_complete flag across application restarts

### Requirement 2: First-Time Startup Behavior

**User Story:** As a new user, I want the application to set up all default resources on first launch, so that I have a working environment.

#### Acceptance Criteria

1. WHEN initialization_complete is false, THE Initialization_Manager SHALL trigger full initialization
2. WHEN full initialization runs, THE Initialization_Manager SHALL scan the default-skills directory and register all skills
3. WHEN full initialization runs, THE Initialization_Manager SHALL read default-mcp-servers.json and register all MCP servers
4. WHEN full initialization runs, THE Initialization_Manager SHALL create the SwarmAgent with all system skills and MCP servers bound
5. WHEN full initialization runs, THE Initialization_Manager SHALL create the SwarmWorkspace with folder structure and context files
6. IF full initialization fails, THEN THE Initialization_Manager SHALL log the error and NOT set initialization_complete to true

### Requirement 3: Subsequent Startup Behavior

**User Story:** As a returning user, I want the application to start quickly, so that I can begin working immediately.

#### Acceptance Criteria

1. WHEN initialization_complete is true, THE Initialization_Manager SHALL perform quick validation only
2. WHEN performing quick validation, THE Initialization_Manager SHALL check if the default agent exists in the database
3. WHEN performing quick validation, THE Initialization_Manager SHALL check if the default workspace exists in the database
4. WHEN quick validation confirms resources exist, THE Initialization_Manager SHALL skip directory scanning and registration
5. THE Quick_Validation SHALL complete in under 2 seconds
6. IF quick validation finds missing resources, THEN THE Initialization_Manager SHALL trigger full initialization

### Requirement 4: Reset to Defaults Functionality

**User Story:** As a user, I want to be able to reset the application to its default state, so that I can recover from configuration issues.

#### Acceptance Criteria

1. THE System SHALL provide a reset_to_defaults API endpoint
2. WHEN reset_to_defaults is called, THE Initialization_Manager SHALL set initialization_complete to false
3. WHEN reset_to_defaults is called, THE Initialization_Manager SHALL trigger full initialization
4. WHEN reset_to_defaults completes, THE Initialization_Manager SHALL return success status
5. IF reset_to_defaults fails, THEN THE Initialization_Manager SHALL return an error with details

### Requirement 5: System Status Reporting

**User Story:** As a user, I want to see accurate startup status, so that I know when the application is ready.

#### Acceptance Criteria

1. THE System_Status endpoint SHALL report initialization mode (first_run or quick_validation)
2. THE System_Status endpoint SHALL report initialization_complete flag value
3. WHEN quick validation succeeds, THE System_Status endpoint SHALL report initialized as true
4. THE Frontend SHALL display startup overlay until initialized is true
5. WHEN subsequent startup completes, THE Frontend SHALL show success within 2 seconds

### Requirement 6: Error Handling

**User Story:** As a user, I want the application to handle initialization errors gracefully, so that I can understand and resolve issues.

#### Acceptance Criteria

1. IF database is unavailable during startup, THEN THE Initialization_Manager SHALL retry with exponential backoff
2. IF default agent creation fails, THEN THE Initialization_Manager SHALL log detailed error and report failure
3. IF default workspace creation fails, THEN THE Initialization_Manager SHALL log detailed error and report failure
4. IF quick validation fails unexpectedly, THEN THE Initialization_Manager SHALL fall back to full initialization
5. THE System SHALL NOT report initialized as true if any critical component fails
