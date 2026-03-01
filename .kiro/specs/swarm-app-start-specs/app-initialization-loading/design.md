# Design Document: App Initialization Loading

## Overview

This design document describes the technical implementation for ensuring the SwarmAI desktop application is fully initialized before displaying the main chat window. The feature enhances the existing `BackendStartupOverlay` component to implement a proper initialization gate that waits for both SwarmAgent and SwarmWorkspace to be ready before transitioning to the main UI.

The design prioritizes:
- **Reliability**: Ensures users never interact with an incompletely initialized system
- **User Experience**: Provides clear visual feedback during initialization
- **Graceful Degradation**: Handles errors and timeouts appropriately
- **Consistency**: Builds on existing initialization patterns and components

## Architecture

### High-Level Initialization Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        App Startup Sequence                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  1. Tauri App Launch                                                     │
│     - Initialize Tauri runtime                                           │
│     - Start Python FastAPI sidecar                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  2. BackendStartupOverlay (Enhanced)                                     │
│     ┌─────────────────────────────────────────────────────────────────┐ │
│     │  State: 'starting'                                               │ │
│     │  - Display logo and app name                                     │ │
│     │  - Initialize backend connection                                 │ │
│     └─────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│     ┌─────────────────────────────────────────────────────────────────┐ │
│     │  State: 'connecting'                                             │ │
│     │  - Poll /health endpoint                                         │ │
│     │  - Show "Connecting to backend..." spinner                       │ │
│     └─────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│     ┌─────────────────────────────────────────────────────────────────┐ │
│     │  State: 'fetching_status'                                        │ │
│     │  - Fetch /api/system/status                                      │ │
│     │  - Build initialization steps                                    │ │
│     └─────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│     ┌─────────────────────────────────────────────────────────────────┐ │
│     │  State: 'waiting_for_ready' (NEW)                                │ │
│     │  - Check agent.ready === true                                    │ │
│     │  - Check swarmWorkspace.ready === true                           │ │
│     │  - Poll until both ready OR timeout                              │ │
│     └─────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              ▼                                           │
│     ┌─────────────────────────────────────────────────────────────────┐ │
│     │  State: 'connected'                                              │ │
│     │  - All systems ready                                             │ │
│     │  - Animate step completion                                       │ │
│     │  - Fade out overlay                                              │ │
│     └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  3. Main Chat Window (ThreeColumnLayout)                                 │
│     - Fully rendered and ready for interaction                           │
│     - SwarmAgent available for chat                                      │
│     - SwarmWorkspace accessible in explorer                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### State Machine Diagram

```
                    ┌──────────────┐
                    │   starting   │
                    └──────┬───────┘
                           │ initializeBackend()
                           ▼
                    ┌──────────────┐
         ┌─────────│  connecting  │◄────────────┐
         │         └──────┬───────┘             │
         │                │ health check OK     │ retry
         │                ▼                     │
         │         ┌──────────────────┐         │
         │         │ fetching_status  │         │
         │         └──────┬───────────┘         │
         │                │ status fetched      │
         │                ▼                     │
         │         ┌──────────────────┐         │
         │         │ waiting_for_ready│─────────┤
         │         └──────┬───────────┘         │
         │                │ all ready           │
         │                ▼                     │
         │         ┌──────────────┐             │
         │         │  connected   │             │
         │         └──────┬───────┘             │
         │                │ fade out            │
         │                ▼                     │
         │         ┌──────────────┐             │
         │         │   (hidden)   │             │
         │         └──────────────┘             │
         │                                      │
         │ timeout/error                        │
         ▼                                      │
  ┌──────────────┐                              │
  │    error     │──────────────────────────────┘
  └──────────────┘
```

## Components and Interfaces

### Component Hierarchy

```
App
├── QueryClientProvider
├── ThemeProvider
├── BackendStartupOverlay (ENHANCED)
│   ├── LogoSection
│   ├── InitializationSteps
│   │   ├── InitStepItem (Database)
│   │   ├── InitStepItem (SwarmAgent)
│   │   │   ├── InitStepItem (Skills - child)
│   │   │   └── InitStepItem (MCP Servers - child)
│   │   ├── InitStepItem (Channel Gateway)
│   │   └── InitStepItem (SwarmWorkspace)
│   │       └── InitStepItem (Path - child)
│   ├── ProgressBar
│   ├── ErrorDisplay
│   │   ├── ErrorIcon
│   │   ├── ErrorMessage
│   │   ├── LogPathInfo
│   │   └── RetryButton
│   └── SpinnerIndicator
└── ThreeColumnLayout (shown after initialization)
    ├── LeftSidebar
    ├── WorkspaceExplorer
    └── MainChatPanel
```

### Key Interface Definitions

```typescript
// Enhanced startup status type with new 'waiting_for_ready' state
type StartupStatus = 
  | 'starting' 
  | 'connecting' 
  | 'fetching_status' 
  | 'waiting_for_ready'  // NEW: Waiting for agent and workspace
  | 'connected' 
  | 'error';

// Initialization step status
type InitStepStatus = 'pending' | 'in_progress' | 'success' | 'error';

// Individual initialization step
interface InitStep {
  id: string;
  labelKey: string;  // i18n translation key
  status: InitStepStatus;
  error?: string;
  interpolation?: Record<string, string | number>;
  children?: InitStep[];
}

// Readiness check result
interface ReadinessCheckResult {
  agentReady: boolean;
  workspaceReady: boolean;
  allReady: boolean;
  error?: string;
}

// Enhanced BackendStartupOverlay props
interface BackendStartupOverlayProps {
  onReady?: () => void;
  timeoutMs?: number;  // Default: 60000 (60 seconds)
  pollIntervalMs?: number;  // Default: 1000 (1 second)
}

// Initialization gate hook return type
interface UseInitializationGateReturn {
  status: StartupStatus;
  initSteps: InitStep[];
  errorMessage: string;
  isReady: boolean;
  retry: () => void;
  elapsedTime: number;
}
```

### UI Wireframes

#### Loading State (Waiting for Ready)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│                                                                              │
│                              ┌────────────┐                                  │
│                              │            │                                  │
│                              │   [LOGO]   │                                  │
│                              │            │                                  │
│                              └────────────┘                                  │
│                                                                              │
│                                SwarmAI                                       │
│                                                                              │
│                     ✓ Database initialized                                   │
│                     ◐ SwarmAgent ready                                       │
│                       └─ 5 system skills bound                               │
│                       └─ 2 system MCP servers bound                          │
│                     ○ Channel gateway started                                │
│                     ○ Swarm Workspace initialized                            │
│                       └─ ~/.swarm-ai/swarm-workspaces/...                                     │
│                                                                              │
│                     ┌────────────────────────────────────────┐               │
│                     │████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░│               │
│                     └────────────────────────────────────────┘               │
│                                                                              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

Legend:
  ✓ = Success (green)
  ◐ = In Progress (spinner, blue)
  ○ = Pending (gray)
  ✗ = Error (red)
```

#### Error State

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│                              ┌────────────┐                                  │
│                              │            │                                  │
│                              │   [LOGO]   │                                  │
│                              │            │                                  │
│                              └────────────┘                                  │
│                                                                              │
│                                SwarmAI                                       │
│                                                                              │
│                     ✓ Database initialized                                   │
│                     ✗ SwarmAgent ready                                       │
│                       └─ Error: Failed to bind system skills                 │
│                     ○ Channel gateway started                                │
│                     ○ Swarm Workspace initialized                            │
│                                                                              │
│                         ┌─────────────────────┐                              │
│                         │    ⚠ Error Icon     │                              │
│                         └─────────────────────┘                              │
│                                                                              │
│                           Failed to start                                    │
│                   SwarmAgent initialization failed                           │
│                                                                              │
│                   ┌─────────────────────────────────────┐                    │
│                   │ Please check the logs at:           │                    │
│                   │ ~/.swarm-ai/logs/                   │                    │
│                   └─────────────────────────────────────┘                    │
│                                                                              │
│                           ┌──────────────┐                                   │
│                           │    Retry     │                                   │
│                           └──────────────┘                                   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### TypeScript Types

```typescript
// Existing SystemStatus interface (from desktop/src/services/system.ts)
// No changes needed - already includes all required fields

export interface SystemStatus {
  database: DatabaseStatus;
  agent: AgentStatus;
  channelGateway: ChannelGatewayStatus;
  swarmWorkspace: SwarmWorkspaceStatus;
  initialized: boolean;
  timestamp: string;
}

// New: Initialization state for the gate
interface InitializationState {
  status: StartupStatus;
  initSteps: InitStep[];
  errorMessage: string;
  startTime: number;
  lastPollTime: number;
  pollCount: number;
}

// New: Configuration for initialization behavior
interface InitializationConfig {
  timeoutMs: number;        // Default: 60000
  pollIntervalMs: number;   // Default: 1000
  maxPollAttempts: number;  // Default: 60
  fadeOutDelayMs: number;   // Default: 500
  fadeOutDurationMs: number; // Default: 500
  stepAnimationDelayMs: number; // Default: 150
}

// Default configuration values
const DEFAULT_INIT_CONFIG: InitializationConfig = {
  timeoutMs: 60000,
  pollIntervalMs: 1000,
  maxPollAttempts: 60,
  fadeOutDelayMs: 500,
  fadeOutDurationMs: 500,
  stepAnimationDelayMs: 150,
};
```

### i18n Translation Keys

```json
{
  "startup": {
    "connectingToBackend": "Connecting to backend...",
    "databaseInitialized": "Database initialized",
    "swarmAgentReady": "SwarmAgent ready",
    "systemSkillsBound": "{{count}} system skills bound",
    "systemMcpServersBound": "{{count}} system MCP servers bound",
    "channelGatewayStarted": "Channel gateway started",
    "swarmWorkspaceInitialized": "Swarm Workspace initialized",
    "swarmWorkspacePath": "{{path}}",
    "waitingForAgent": "Waiting for SwarmAgent to be ready...",
    "waitingForWorkspace": "Waiting for SwarmWorkspace to be ready...",
    "initializationTimeout": "Initialization timed out after {{seconds}} seconds",
    "retryInitialization": "Retry",
    "failedToStart": "Failed to start",
    "checkLogsAt": "Please check the logs at:",
    "agentInitFailed": "SwarmAgent initialization failed",
    "workspaceInitFailed": "SwarmWorkspace initialization failed"
  }
}
```

### State Transitions

| Current State | Event | Next State | Actions |
|---------------|-------|------------|---------|
| starting | initializeBackend() success | connecting | Start health polling |
| starting | initializeBackend() error | error | Set error message |
| connecting | health check success | fetching_status | Fetch system status |
| connecting | health check fail (< max) | connecting | Increment poll count |
| connecting | health check fail (>= max) | error | Set timeout error |
| fetching_status | status fetched, not ready | waiting_for_ready | Start readiness polling |
| fetching_status | status fetched, all ready | connected | Build init steps, animate |
| fetching_status | status fetch error | connected | Graceful degradation |
| waiting_for_ready | poll success, all ready | connected | Update steps, animate |
| waiting_for_ready | poll success, not ready | waiting_for_ready | Update steps |
| waiting_for_ready | timeout | error | Set timeout error |
| connected | animation complete | (hidden) | Call onReady, hide overlay |
| error | retry clicked | starting | Reset state |



## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Initialization Gate Blocks Until Both Ready

*For any* system status response, the Main_Chat_Window SHALL NOT be displayed unless both `agent.ready === true` AND `swarmWorkspace.ready === true`. The initialization gate must block on both conditions.

**Validates: Requirements 1.1, 2.1, 6.2**

### Property 2: Overlay Visibility While Not Ready

*For any* initialization state where either SwarmAgent or SwarmWorkspace is not ready, the Backend_Startup_Overlay SHALL remain visible and the Main_Chat_Window SHALL be hidden.

**Validates: Requirements 1.2, 2.2**

### Property 3: Polling Continues Until Ready

*For any* initialization sequence where the system is in 'waiting_for_ready' state, the System SHALL continue polling the System_Status_API at the configured interval until either all components are ready OR the timeout is reached.

**Validates: Requirements 1.3**

### Property 4: Readiness Check Sequence

*For any* initialization sequence, the System SHALL verify SwarmWorkspace readiness only after SwarmAgent readiness has been confirmed. The sequence is: health check → fetch status → verify agent → verify workspace → display main UI.

**Validates: Requirements 1.4, 2.3, 2.4**

### Property 5: Timeout Triggers Error State

*For any* initialization sequence that exceeds the configured timeout (60 seconds), the System SHALL transition to 'error' state and display a timeout error message with a retry option.

**Validates: Requirements 1.5, 2.5, 7.2**

### Property 6: Step Status Indicators

*For any* initialization step, the visual indicator SHALL match the step status: success steps display checkmark (✓), error steps display red X (✗) with error message, and in-progress steps display spinner.

**Validates: Requirements 3.3, 4.1, 4.2**

### Property 7: Retry Resets State

*For any* retry action triggered from the error state, the System SHALL reset all initialization state (status, steps, error message, poll count, elapsed time) and restart from the 'starting' state.

**Validates: Requirements 7.3, 7.4**

### Property 8: State Machine Valid Transitions

*For any* state transition in the initialization process, the transition SHALL follow the defined state machine: starting → connecting → fetching_status → waiting_for_ready → connected, with error being reachable from any state and retry returning to starting.

**Validates: Requirements 6.1**

### Property 9: i18n Keys Used for All Messages

*For any* text displayed in the Backend_Startup_Overlay, the text SHALL be retrieved using i18n translation keys rather than hardcoded strings.

**Validates: Requirements 8.1**

## Error Handling

### Error Categories and Handling

| Error Category | Trigger | User Message | Recovery Action |
|----------------|---------|--------------|-----------------|
| Backend Connection Failure | Health check fails after max attempts | "Failed to connect to backend" | Retry button |
| Backend Timeout | Health check times out (60s) | "Backend service failed to start within 60 seconds" | Retry button |
| Status Fetch Failure | /api/system/status returns error | Graceful degradation - proceed without status | Auto-proceed |
| Agent Init Failure | agent.ready remains false after timeout | "SwarmAgent initialization failed" | Retry button |
| Workspace Init Failure | swarmWorkspace.ready remains false after timeout | "SwarmWorkspace initialization failed" | Retry button |
| Network Error | Network request fails | "Network error - please check your connection" | Retry button |

### Error State UI Requirements

1. Display error icon (red warning symbol)
2. Show descriptive error message
3. Display platform-specific log path for troubleshooting
4. Provide "Retry" button to restart initialization
5. Maintain visibility of completed steps (with their status)

### Graceful Degradation

The system implements graceful degradation for non-critical failures:

- If system status fetch fails but health check passes, proceed to main UI
- If individual step status is unknown, show as pending rather than error
- If timeout occurs during status fetch (5s), proceed with available data

### Error Logging

All errors are logged with:
- Timestamp
- Error type/code
- Error message
- Stack trace (if available)
- Current initialization state

Log location: `~/.swarm-ai/logs/` (all platforms)

## Testing Strategy

### Unit Testing

Unit tests will focus on:
- State machine transitions
- Step building logic from system status
- Error message generation
- Timeout calculation
- Retry state reset

### Property-Based Testing

Property-based tests will use **fast-check** library for TypeScript to validate the correctness properties defined above. Each property test will:
- Run minimum 100 iterations with randomized inputs
- Be tagged with the corresponding property number
- Reference the requirements being validated

**Tag Format**: `Feature: app-initialization-loading, Property {N}: {property_title}`

Example test structure:
```typescript
import fc from 'fast-check';
import { describe, it, expect } from 'vitest';

describe('Initialization Gate', () => {
  // Feature: app-initialization-loading, Property 1: Initialization Gate Blocks Until Both Ready
  it('should block main window until both agent and workspace are ready', () => {
    fc.assert(
      fc.property(
        fc.record({
          agentReady: fc.boolean(),
          workspaceReady: fc.boolean(),
        }),
        ({ agentReady, workspaceReady }) => {
          const shouldShowMainWindow = agentReady && workspaceReady;
          const result = checkInitializationGate({ agentReady, workspaceReady });
          return result.showMainWindow === shouldShowMainWindow;
        }
      ),
      { numRuns: 100 }
    );
  });

  // Feature: app-initialization-loading, Property 2: Overlay Visibility While Not Ready
  it('should keep overlay visible while not ready', () => {
    fc.assert(
      fc.property(
        fc.record({
          agentReady: fc.boolean(),
          workspaceReady: fc.boolean(),
        }),
        ({ agentReady, workspaceReady }) => {
          const allReady = agentReady && workspaceReady;
          const result = checkOverlayVisibility({ agentReady, workspaceReady });
          // Overlay visible when NOT all ready
          return result.overlayVisible === !allReady;
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('State Machine', () => {
  // Feature: app-initialization-loading, Property 8: State Machine Valid Transitions
  it('should only allow valid state transitions', () => {
    const validTransitions: Record<string, string[]> = {
      starting: ['connecting', 'error'],
      connecting: ['fetching_status', 'error'],
      fetching_status: ['waiting_for_ready', 'connected', 'error'],
      waiting_for_ready: ['connected', 'error'],
      connected: [],
      error: ['starting'],
    };

    fc.assert(
      fc.property(
        fc.constantFrom(...Object.keys(validTransitions)),
        fc.constantFrom(...Object.keys(validTransitions)),
        (fromState, toState) => {
          const isValid = validTransitions[fromState]?.includes(toState) ?? false;
          const result = validateTransition(fromState, toState);
          return result.valid === isValid;
        }
      ),
      { numRuns: 100 }
    );
  });
});
```

### Integration Testing

Integration tests will verify:
- Full initialization flow from app start to main UI
- Retry flow after error
- Timeout behavior
- Graceful degradation when status fetch fails

### Test File Organization

```
desktop/src/
├── components/
│   └── common/
│       ├── BackendStartupOverlay.tsx
│       ├── BackendStartupOverlay.test.tsx        # Unit tests
│       └── BackendStartupOverlay.property.test.tsx  # Property tests
├── hooks/
│   ├── useInitializationGate.ts                  # NEW: Custom hook
│   ├── useInitializationGate.test.tsx            # Unit tests
│   └── useInitializationGate.property.test.tsx   # Property tests
└── services/
    └── system.ts                                 # Existing service
```

### Test Coverage Requirements

- All state transitions must be tested
- All error scenarios must have corresponding tests
- Property tests must cover all 9 correctness properties
- Integration tests must cover happy path and error recovery
