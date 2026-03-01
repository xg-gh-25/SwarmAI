# Implementation Plan: App Initialization Loading

## Overview

This implementation plan enhances the existing `BackendStartupOverlay` component to implement an initialization gate that waits for both SwarmAgent and SwarmWorkspace to be ready before displaying the main chat window. The implementation builds on existing patterns and minimizes changes to the codebase.

## Tasks

- [x] 1. Add new 'waiting_for_ready' state to BackendStartupOverlay
  - [x] 1.1 Update StartupStatus type to include 'waiting_for_ready' state
    - Add 'waiting_for_ready' to the StartupStatus union type in BackendStartupOverlay.tsx
    - _Requirements: 6.1_
  
  - [x] 1.2 Write property test for state machine valid transitions
    - **Property 8: State Machine Valid Transitions**
    - **Validates: Requirements 6.1**

- [x] 2. Implement initialization gate logic
  - [x] 2.1 Add readiness check function
    - Create function to check if both agent.ready and swarmWorkspace.ready are true
    - Return ReadinessCheckResult with agentReady, workspaceReady, allReady flags
    - _Requirements: 1.1, 2.1_
  
  - [x] 2.2 Implement polling loop for waiting_for_ready state
    - Add polling logic that continues until both components are ready or timeout
    - Poll interval: 1 second
    - Timeout: 60 seconds
    - _Requirements: 1.3, 7.1_
  
  - [x] 2.3 Update state transitions in useEffect
    - Transition from fetching_status to waiting_for_ready when not all ready
    - Transition from waiting_for_ready to connected when all ready
    - Transition to error on timeout
    - _Requirements: 1.4, 2.3, 2.4_
  
  - [x] 2.4 Write property test for initialization gate blocks until both ready
    - **Property 1: Initialization Gate Blocks Until Both Ready**
    - **Validates: Requirements 1.1, 2.1, 6.2**
  
  - [x] 2.5 Write property test for overlay visibility while not ready
    - **Property 2: Overlay Visibility While Not Ready**
    - **Validates: Requirements 1.2, 2.2**

- [x] 3. Checkpoint - Verify gate logic works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement timeout and retry mechanism
  - [x] 4.1 Add timeout tracking state
    - Add startTime state to track when initialization started
    - Add elapsedTime calculation
    - _Requirements: 7.1_
  
  - [x] 4.2 Implement timeout detection
    - Check if elapsed time exceeds 60 seconds
    - Transition to error state on timeout
    - Display timeout error message with elapsed seconds
    - _Requirements: 1.5, 2.5, 7.2_
  
  - [x] 4.3 Implement retry functionality
    - Reset all state (status, steps, error, poll count, start time)
    - Restart from 'starting' state
    - _Requirements: 7.3, 7.4_
  
  - [x] 4.4 Write property test for timeout triggers error state
    - **Property 5: Timeout Triggers Error State**
    - **Validates: Requirements 1.5, 2.5, 7.2**
  
  - [x] 4.5 Write property test for retry resets state
    - **Property 7: Retry Resets State**
    - **Validates: Requirements 7.3, 7.4**

- [x] 5. Update UI for waiting_for_ready state
  - [x] 5.1 Update step status indicators
    - Show spinner for in-progress steps
    - Show checkmark for completed steps
    - Show red X for failed steps
    - _Requirements: 3.3, 3.4, 3.5, 4.1_
  
  - [x] 5.2 Update buildInitSteps to handle in-progress state
    - Set step status to 'in_progress' when component is not ready but no error
    - Set step status to 'success' when component is ready
    - Set step status to 'error' when component has error
    - _Requirements: 3.2_
  
  - [x] 5.3 Write property test for step status indicators
    - **Property 6: Step Status Indicators**
    - **Validates: Requirements 3.3, 4.1, 4.2**

- [x] 6. Checkpoint - Verify UI updates correctly
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Add i18n translation keys
  - [x] 7.1 Add new translation keys to en.json
    - Add keys for waitingForAgent, waitingForWorkspace, initializationTimeout
    - Add keys for agentInitFailed, workspaceInitFailed
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  
  - [x] 7.2 Write property test for i18n keys used for all messages
    - **Property 9: i18n Keys Used for All Messages**
    - **Validates: Requirements 8.1**

- [x] 8. Implement smooth transition to main UI
  - [x] 8.1 Ensure fade-out only starts after all ready
    - Verify connected state is only reached when both agent and workspace ready
    - Maintain existing 500ms delay before fade-out
    - Maintain existing 500ms fade-out duration
    - _Requirements: 5.1, 5.2, 5.3_
  
  - [x] 8.2 Ensure onReady callback fires after fade-out
    - Call onReady only after fade-out animation completes
    - Main UI should be ready for interaction when visible
    - _Requirements: 5.5_
  
  - [x] 8.3 Write property test for readiness check sequence
    - **Property 4: Readiness Check Sequence**
    - **Validates: Requirements 1.4, 2.3, 2.4**
  
  - [x] 8.4 Write property test for polling continues until ready
    - **Property 3: Polling Continues Until Ready**
    - **Validates: Requirements 1.3**

- [x] 9. Final checkpoint - Full integration test
  - Ensure all tests pass, ask the user if questions arise.
  - Verify complete initialization flow works end-to-end
  - Test error scenarios and retry functionality
  - Test timeout behavior

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The implementation builds on existing BackendStartupOverlay component
- No backend changes required - uses existing /api/system/status endpoint
