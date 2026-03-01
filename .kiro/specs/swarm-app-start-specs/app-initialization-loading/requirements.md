# Requirements Document

## Introduction

This document specifies the requirements for ensuring the SwarmAI desktop application is fully initialized before displaying the main chat window to users. The feature ensures that the default system SwarmAgent and system SwarmWorkspace are completely ready before transitioning from the loading state to the main UI. This prevents users from interacting with an incompletely initialized system and provides clear feedback during the initialization process.

## Glossary

- **SwarmAgent**: The default system agent that is automatically created and configured during application startup with all system skills and MCP servers bound.
- **SwarmWorkspace**: The protected system workspace that is always present and used for SwarmAI's internal operations.
- **Backend_Startup_Overlay**: The React component (BackendStartupOverlay.tsx) that displays during app startup showing initialization status.
- **System_Status_API**: The backend endpoint (`/api/system/status`) that provides system initialization status information.
- **Initialization_Gate**: The mechanism that blocks the main UI from displaying until all required system components are ready.
- **Loading_Progress_Indicator**: The visual component that shows initialization progress to users during startup.
- **Main_Chat_Window**: The primary three-column layout interface where users interact with SwarmAgent.
- **Initialization_Timeout**: The maximum time allowed for initialization before showing an error state.

## Requirements

### Requirement 1: SwarmAgent Readiness Gate

**User Story:** As a user, I want the app to wait until SwarmAgent is fully ready before showing the main chat window, so that I can immediately start working without encountering initialization errors.

#### Acceptance Criteria

1. THE Initialization_Gate SHALL prevent the Main_Chat_Window from displaying until the System_Status_API returns `agent.ready` as `true`
2. WHEN the SwarmAgent is not ready, THE Backend_Startup_Overlay SHALL remain visible and display the current initialization status
3. THE System SHALL poll the System_Status_API at regular intervals (every 1 second) until SwarmAgent is ready
4. WHEN the SwarmAgent becomes ready, THE System SHALL proceed to check SwarmWorkspace readiness before displaying the Main_Chat_Window
5. IF the SwarmAgent fails to become ready within the Initialization_Timeout (60 seconds), THEN THE System SHALL display an error state with retry option

### Requirement 2: SwarmWorkspace Readiness Gate

**User Story:** As a user, I want the app to wait until SwarmWorkspace is fully ready before showing the main chat window, so that file operations work correctly from the start.

#### Acceptance Criteria

1. THE Initialization_Gate SHALL prevent the Main_Chat_Window from displaying until the System_Status_API returns `swarmWorkspace.ready` as `true`
2. WHEN the SwarmWorkspace is not ready, THE Backend_Startup_Overlay SHALL remain visible and display the current initialization status
3. THE System SHALL verify SwarmWorkspace readiness after SwarmAgent readiness is confirmed
4. WHEN the SwarmWorkspace becomes ready, THE System SHALL proceed to display the Main_Chat_Window
5. IF the SwarmWorkspace fails to become ready within the Initialization_Timeout, THEN THE System SHALL display an error state with retry option

### Requirement 3: Loading Progress Indicator

**User Story:** As a user, I want to see a clear loading progress indicator during initialization, so that I know the app is working and approximately how long I need to wait.

#### Acceptance Criteria

1. THE Loading_Progress_Indicator SHALL display a visual progress bar during initialization
2. THE Loading_Progress_Indicator SHALL show the current initialization step being executed
3. THE Loading_Progress_Indicator SHALL display checkmarks (✓) for completed initialization steps
4. THE Loading_Progress_Indicator SHALL display the step name and status for each initialization component (Database, SwarmAgent, Channel Gateway, SwarmWorkspace)
5. WHEN an initialization step is in progress, THE Loading_Progress_Indicator SHALL display a spinner next to that step
6. THE Loading_Progress_Indicator SHALL use a CLI-style monospace font for consistency with existing design

### Requirement 4: Initialization Error Handling

**User Story:** As a user, I want clear error messages when initialization fails, so that I can understand what went wrong and take appropriate action.

#### Acceptance Criteria

1. IF any initialization step fails, THEN THE System SHALL display a red X (✗) next to the failed step
2. IF any initialization step fails, THEN THE System SHALL display a descriptive error message explaining the failure
3. WHEN an initialization error occurs, THE System SHALL display a "Retry" button to attempt initialization again
4. WHEN an initialization error occurs, THE System SHALL display the log file path for troubleshooting
5. IF the backend health check fails, THEN THE System SHALL display "Failed to connect to backend" with retry option
6. IF the SwarmAgent initialization fails, THEN THE System SHALL display "SwarmAgent initialization failed" with the specific error

### Requirement 5: Smooth Transition to Main UI

**User Story:** As a user, I want a smooth visual transition from the loading screen to the main chat window, so that the app feels polished and professional.

#### Acceptance Criteria

1. WHEN all initialization steps complete successfully, THE Backend_Startup_Overlay SHALL fade out over 500 milliseconds
2. THE Main_Chat_Window SHALL only become visible after the Backend_Startup_Overlay fade-out completes
3. THE System SHALL wait 500 milliseconds after all initialization steps are shown before starting the fade-out
4. THE transition SHALL not cause any visual flickering or layout shifts
5. THE Main_Chat_Window SHALL be fully rendered and ready for interaction when it becomes visible

### Requirement 6: Initialization State Persistence

**User Story:** As a developer, I want the initialization state to be tracked consistently, so that the app can reliably determine when to show the main UI.

#### Acceptance Criteria

1. THE System SHALL track initialization state using a state machine with states: 'starting', 'connecting', 'fetching_status', 'waiting_for_ready', 'connected', 'error'
2. THE System SHALL only transition to 'connected' state when both SwarmAgent and SwarmWorkspace are ready
3. THE System SHALL persist the initialization completion status to prevent re-showing the loading screen on hot reloads
4. WHEN the app is in development mode, THE System SHALL skip the Backend_Startup_Overlay (existing behavior)
5. THE System SHALL log initialization state transitions for debugging purposes

### Requirement 7: Timeout and Retry Mechanism

**User Story:** As a user, I want the app to handle slow initialization gracefully, so that I'm not stuck on a loading screen indefinitely.

#### Acceptance Criteria

1. THE System SHALL implement a 60-second timeout for the entire initialization process
2. IF initialization times out, THEN THE System SHALL display a timeout error with retry option
3. WHEN the user clicks "Retry", THE System SHALL restart the entire initialization process from the beginning
4. THE System SHALL reset the timeout counter when retry is initiated
5. THE System SHALL display elapsed time or a progress indicator during long initialization periods

### Requirement 8: Internationalization Support

**User Story:** As a user in a non-English locale, I want all loading screen text to be translated, so that I can understand the initialization status in my language.

#### Acceptance Criteria

1. THE Backend_Startup_Overlay SHALL use i18n translation keys for all status messages
2. THE Translation_File SHALL include keys for "Waiting for SwarmAgent to be ready" and "Waiting for SwarmWorkspace to be ready"
3. THE Translation_File SHALL include keys for timeout and retry-related messages
4. THE Translation_File SHALL include keys for all error messages displayed during initialization
5. THE existing translation keys for initialization steps SHALL be reused where applicable
