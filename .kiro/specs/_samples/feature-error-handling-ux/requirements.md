# Requirements Document

## Introduction

This feature improves error handling UX across the SwarmAI desktop application (Tauri 2.0 + React + Python FastAPI sidecar). The app currently has structured error responses on the backend and basic error parsing on the frontend, but lacks critical user-facing feedback mechanisms: no backend health monitoring, no SSE reconnection, no toast notifications, no React error boundaries, and no enforcement of rate limit or timeout behaviors. This spec addresses the HIGH and MEDIUM priority gaps to make the app resilient and communicative when things go wrong.

## Glossary

- **App**: The SwarmAI Tauri 2.0 desktop application comprising a React frontend and a Python FastAPI backend sidecar process.
- **Backend**: The Python FastAPI sidecar process that runs locally and serves REST and SSE endpoints.
- **Frontend**: The React-based UI rendered in the Tauri webview.
- **Health_Monitor**: A frontend service that periodically polls the Backend to detect availability and report status changes.
- **SSE_Stream**: A Server-Sent Events connection from the Frontend to the Backend used for chat streaming, using manual fetch + TextDecoder (not EventSource).
- **Toast_System**: A UI notification layer that displays transient, dismissible messages to the user (success, warning, error, info).
- **Error_Boundary**: A React Error Boundary component that catches unhandled JavaScript errors in the component tree and renders a fallback UI instead of crashing the entire app.
- **Rate_Limiter**: Frontend logic that respects HTTP 429 responses and Retry-After headers by queuing or blocking requests until the cooldown expires.
- **Tab**: A chat session tab in the multi-tab UI. Error state for tabs follows the tabMapRef pattern (authoritative source) with React state as display mirror.
- **Permission_Dialog**: The UI component that presents command permission requests to the user for approval or denial.

## Requirements

### Requirement 1: Backend Health Monitoring

**User Story:** As a user, I want to know when the backend sidecar is unavailable, so that I understand why my actions are failing and can wait for recovery.

#### Acceptance Criteria

1. WHEN the App starts, THE Health_Monitor SHALL begin polling the Backend health endpoint at a configurable interval (default 10 seconds).
2. WHEN the Health_Monitor receives a successful response from the Backend, THE Health_Monitor SHALL record the Backend status as "connected".
3. WHEN the Health_Monitor fails to receive a response from the Backend for 2 consecutive polls, THE Health_Monitor SHALL record the Backend status as "disconnected".
4. WHEN the Backend status transitions from "connected" to "disconnected", THE Toast_System SHALL display a persistent warning notification stating that the backend is unavailable.
5. WHEN the Backend status transitions from "disconnected" to "connected", THE Toast_System SHALL display a success notification stating that the backend has reconnected.
6. WHILE the Backend status is "disconnected", THE Frontend SHALL display a visual indicator in the UI header showing the disconnected state.
7. WHILE the Backend status is "disconnected", THE Frontend SHALL disable chat input and action buttons that require Backend communication.

### Requirement 2: SSE Stream Resilience

**User Story:** As a user, I want chat streaming to recover automatically from transient failures, so that I do not lose my conversation or have to manually retry.

#### Acceptance Criteria

1. WHEN an SSE_Stream connection fails due to a network error (non-abort), THE Frontend SHALL attempt to reconnect using exponential backoff starting at 1 second, doubling up to a maximum of 30 seconds, for up to 3 retry attempts.
2. WHEN the SSE_Stream reconnection succeeds, THE Frontend SHALL resume processing events from the stream and display a Toast_System info notification indicating the stream has reconnected.
3. WHEN all SSE_Stream reconnection attempts are exhausted, THE Frontend SHALL display an error message in the chat area with a manual "Retry" button.
4. WHEN the user clicks the "Retry" button after SSE_Stream reconnection failure, THE Frontend SHALL initiate a new SSE_Stream connection for the active session.
5. WHILE an SSE_Stream reconnection is in progress, THE Frontend SHALL display a "Reconnecting..." indicator in the chat area for the affected Tab.
6. WHEN no data is received on an active SSE_Stream for 45 seconds (including heartbeats), THE Frontend SHALL treat the stream as stalled and trigger the reconnection logic described in acceptance criterion 1.

### Requirement 3: SSE Stream Cancellation

**User Story:** As a user, I want to cancel long-running chat operations, so that I am not stuck waiting indefinitely for a response.

#### Acceptance Criteria

1. WHILE an SSE_Stream is actively receiving data for a Tab, THE Frontend SHALL display a visible "Stop" button in the chat area for that Tab.
2. WHEN the user clicks the "Stop" button, THE Frontend SHALL abort the active SSE_Stream connection and send a stop request to the Backend for the session.
3. WHEN the SSE_Stream is aborted by the user, THE Frontend SHALL display the partial response received so far and append a "Generation stopped" indicator.
4. WHEN an SSE_Stream has been active for more than 120 seconds without a result event, THE Frontend SHALL display a Toast_System warning notification suggesting the user may cancel the operation.

### Requirement 4: React Error Boundary

**User Story:** As a user, I want the app to gracefully handle unexpected UI errors, so that a crash in one component does not take down the entire application.

#### Acceptance Criteria

1. THE Error_Boundary SHALL catch unhandled JavaScript errors in the React component tree and render a fallback UI instead of a blank screen.
2. WHEN the Error_Boundary catches an error, THE Error_Boundary SHALL display a user-friendly error message with a "Reload" button that resets the component tree.
3. WHEN the Error_Boundary catches an error, THE Error_Boundary SHALL log the error details (component stack, error message) to the browser console for debugging.
4. THE App SHALL wrap each Tab's chat content in a separate Error_Boundary so that an error in one Tab does not affect other Tabs.
5. THE App SHALL wrap the top-level application layout in an Error_Boundary as a last-resort fallback.

### Requirement 5: Toast Notification System

**User Story:** As a user, I want to see brief, non-blocking notifications for transient errors and status changes, so that I stay informed without losing my workflow context.

#### Acceptance Criteria

1. THE Toast_System SHALL support four notification severity levels: "success", "info", "warning", and "error".
2. THE Toast_System SHALL display notifications in a fixed position (top-right corner) that does not obscure the main chat area.
3. WHEN a toast notification is displayed, THE Toast_System SHALL auto-dismiss "success" and "info" notifications after 5 seconds.
4. WHEN a toast notification is displayed with severity "warning" or "error", THE Toast_System SHALL keep the notification visible until the user dismisses it manually, unless the notification is marked as auto-dismissible.
5. THE Toast_System SHALL stack multiple concurrent notifications vertically with a maximum of 5 visible notifications, queuing additional notifications.
6. THE Toast_System SHALL provide a programmatic API (React context or hook) that any component can use to trigger notifications without prop drilling.

### Requirement 6: Rate Limit Enforcement and Display

**User Story:** As a user, I want to be informed when I hit rate limits and have the app automatically wait before retrying, so that I do not waste time on requests that will fail.

#### Acceptance Criteria

1. WHEN the Frontend receives an HTTP 429 response with a Retry-After header, THE Rate_Limiter SHALL block further requests to the same endpoint until the Retry-After period expires.
2. WHEN the Rate_Limiter is active, THE Toast_System SHALL display a warning notification showing the remaining cooldown time in seconds.
3. WHILE the Rate_Limiter is active for chat endpoints, THE Frontend SHALL disable the chat input for the affected Tab and display a countdown indicator.
4. WHEN the Rate_Limiter cooldown expires, THE Frontend SHALL re-enable the chat input and display a Toast_System info notification indicating the user may resume.

### Requirement 7: Permission Request Lifecycle Management

**User Story:** As a user, I want permission request dialogs to have timeouts and clear status, so that stale permission requests do not block my workflow indefinitely.

#### Acceptance Criteria

1. WHEN a Permission_Dialog is displayed, THE Frontend SHALL start a 5-minute timeout timer for the permission request.
2. WHEN the Permission_Dialog timeout expires without user action, THE Frontend SHALL automatically deny the permission request and send the denial to the Backend.
3. WHEN the Permission_Dialog timeout is within 60 seconds of expiring, THE Frontend SHALL display a countdown indicator on the Permission_Dialog.
4. IF the Backend session associated with a Permission_Dialog is no longer active, THEN THE Frontend SHALL dismiss the Permission_Dialog and display a Toast_System info notification explaining the request is no longer valid.
5. WHEN a permission decision is submitted and the Backend returns an error, THE Toast_System SHALL display an error notification with the failure reason.

### Requirement 8: Validation Error Field-Level Display

**User Story:** As a user, I want to see validation errors next to the specific fields that caused them, so that I can quickly identify and fix my input mistakes.

#### Acceptance Criteria

1. WHEN the Frontend receives a ValidationErrorResponse from the Backend containing a "fields" array, THE Frontend SHALL display each field error adjacent to the corresponding input field in the UI.
2. WHEN a field-level validation error is displayed, THE Frontend SHALL highlight the affected input field with a red border and display the error message below the field.
3. WHEN the user modifies an input field that has a validation error, THE Frontend SHALL clear the validation error for that specific field.
4. IF the Frontend receives a ValidationErrorResponse but cannot map a field error to a visible input field, THEN THE Frontend SHALL display the unmapped error in a Toast_System error notification with the field name and error message.
5. THE Frontend SHALL support displaying validation errors on agent configuration forms, MCP server configuration forms, and skill creation forms.

### Requirement 9: Structured Error Display in Chat

**User Story:** As a user, I want chat errors to show actionable information instead of raw error messages, so that I understand what went wrong and what I can do about it.

#### Acceptance Criteria

1. WHEN an SSE_Stream delivers an error event with a "suggested_action" field, THE Frontend SHALL display the suggested action as a distinct, actionable element below the error message in the chat area.
2. WHEN an SSE_Stream delivers an error event with code "AGENT_TIMEOUT", THE Frontend SHALL display the error with a "Retry" button that re-sends the last user message.
3. WHEN an SSE_Stream delivers an error event with code "RATE_LIMIT_EXCEEDED", THE Frontend SHALL display the error with a countdown timer based on the Retry-After value and automatically re-enable input when the timer expires.
4. WHEN an SSE_Stream delivers an error event with code "SERVICE_UNAVAILABLE", THE Frontend SHALL display the error and trigger the Health_Monitor to perform an immediate health check.
5. THE Frontend SHALL render error events in the chat area with a visually distinct style (red accent border) that differentiates errors from normal assistant messages.

## Future Enhancements (Out of Scope)

The following items are noted for future consideration but are not part of this spec:

- **Offline detection and offline queue**: Detecting when the system is fully offline and queuing operations for later replay.
- **Circuit breaker pattern**: Automatically disabling endpoints after repeated failures and re-enabling after a cooldown period.
- **Exponential backoff on REST retries**: Applying backoff strategies to all REST API calls (currently only planned for SSE reconnection).
- **Error telemetry and analytics**: Collecting and aggregating error frequency data for proactive issue detection.
