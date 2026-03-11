# Implementation Plan: Error Handling UX

## Overview

Implements nine interconnected error-handling features for the SwarmAI desktop app in a layered approach: notification infrastructure first (Toast, Error Boundaries), then detection (Health Monitor, SSE Resilience), then enforcement (Rate Limiter, Permission Lifecycle), and finally display (Validation Errors, Chat Errors, Stream Cancellation refinements). Each task builds on previous tasks, with toast migration consolidated early to avoid dual-system conflicts.

## Tasks

- [x] 1. Create Toast Notification System and migrate existing callsites
  - [x] 1.1 Add frontend types for Toast, Health, RateLimit, and ValidationError to `desktop/src/types/index.ts`
    - Add `ToastSeverity`, `ToastOptions`, `ToastItem`, `BackendStatus`, `HealthState`, `RateLimitEntry`, `FieldErrorMap` types
    - _Requirements: 5.1, 6.1, 8.1_

  - [x] 1.2 Create `ToastContext.tsx` in `desktop/src/contexts/` with `ToastProvider` and `useToast` hook
    - Implement toast queue with max 5 visible, overflow queued
    - Auto-dismiss success/info after 5s; warning/error persist unless `autoDismiss: true`
    - Deduplication by optional `id` field
    - Support optional `action?: { label: string; onClick: () => void }` for actionable toasts
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_

  - [x] 1.3 Create `ToastStack.tsx` in `desktop/src/components/common/`
    - Render toasts in fixed top-right position
    - Each toast shows severity icon, message, dismiss button
    - Animate entry/exit
    - _Requirements: 5.2, 5.5_

  - [ ]* 1.4 Write property tests for Toast system in `desktop/src/contexts/ToastContext.property.test.tsx`
    - **Property 9: Toast severity determines auto-dismiss behavior**
    - **Property 10: Toast stack maximum visibility cap**
    - **Property 11: Toast system accepts all four severity levels**
    - **Validates: Requirements 5.1, 5.3, 5.4, 5.5**

  - [x] 1.5 Wire `ToastProvider` into `App.tsx` at root level (inside `ThemeProvider`, outside `BrowserRouter`)
    - _Requirements: 5.6_

  - [x] 1.6 Migrate existing toast callsites to `useToast()`
    - Replace `tabLimitToast` useState + `<Toast>` in `ChatPage.tsx` with `useToast().addToast()`
    - Replace context warning toast in `ChatPage.tsx` with `useToast().addToast()`
    - Replace memory save toast in `AssistantMessageView.tsx` with `useToast().addToast()`
    - Replace compact button toast in `AssistantMessageView.tsx` with `useToast().addToast()`
    - Remove `ErrorToast` usage from `ErrorBoundary.tsx` (errors use console + useToast)
    - Remove `PolicyViolationToast.tsx` (dead code — never rendered)
    - Deprecate standalone `Toast.tsx` component (keep as wrapper calling useToast internally)
    - _Requirements: 5.6_

- [x] 2. Checkpoint — Toast system complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Extend ErrorBoundary with variant prop and add per-tab + app-level wrapping
  - [x] 3.1 Extend existing `ErrorBoundary` in `desktop/src/components/common/ErrorBoundary.tsx` with `variant` prop
    - Add `variant?: 'tab' | 'app' | 'default'` prop (default preserves existing behavior)
    - `variant='tab'`: compact error + "Reload Tab" button that resets boundary state
    - `variant='app'`: full-page error + "Reload App" button that calls `window.location.reload()`
    - Preserve existing `fallback`, `onError`, `onRetry` props for backward compatibility
    - Log error + component stack to `console.error` in `componentDidCatch`
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ]* 3.2 Write property test for ErrorBoundary in `desktop/src/components/common/ErrorBoundary.property.test.tsx`
    - **Property 8: Error boundary catches errors and renders fallback**
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [x] 3.3 Wrap each tab's chat content in `ErrorBoundary variant="tab"` in `ChatPage.tsx`
    - Ensure error in one tab does not affect other tabs
    - _Requirements: 4.4_

  - [x] 3.4 Wrap top-level application layout in `ErrorBoundary variant="app"` in `App.tsx`
    - Wraps `<BrowserRouter>` as last-resort fallback
    - _Requirements: 4.5_

- [x] 4. Implement Health Monitor
  - [x] 4.1 Create `useHealthMonitor` hook in `desktop/src/hooks/useHealthMonitor.ts`
    - Poll `GET /health` at configurable interval (default 30s)
    - Track `consecutiveFailures`; transition to `'disconnected'` after threshold (default 2)
    - Single success resets failure count and restores `'connected'`
    - Handle `'initializing'` status from backend response body
    - Fire toast on connected→disconnected transition (persistent warning, id: `'health-disconnected'`)
    - Fire toast on disconnected→connected transition (success)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 4.2 Create `HealthContext.tsx` in `desktop/src/contexts/` with `HealthProvider`
    - Expose `HealthContextValue` with `health: HealthState`
    - Add `triggerHealthCheck()` method for on-demand checks (used by R9.4)
    - Wire `HealthProvider` into `App.tsx` (inside `ToastProvider`)
    - _Requirements: 1.6, 1.7_

  - [x] 4.3 Add disconnected indicator in UI header and disable chat input when disconnected
    - Show visual indicator in header when `status === 'disconnected'`
    - Show "Starting up..." indicator when `status === 'initializing'`
    - Disable chat input and action buttons when `status === 'disconnected'`
    - _Requirements: 1.6, 1.7_

  - [ ]* 4.4 Write property tests for Health Monitor
    - **Property 1: Health monitor state machine** in `desktop/src/hooks/useHealthMonitor.property.test.ts`
    - **Property 2: Disconnected state disables UI** in `desktop/src/contexts/HealthContext.property.test.tsx`
    - **Validates: Requirements 1.2, 1.3, 1.6, 1.7**

- [x] 5. Checkpoint — Error boundaries and health monitor complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement SSE Stream Resilience
  - [x] 6.1 Add reconnection logic to `useChatStreamingLifecycle` in `desktop/src/hooks/useChatStreamingLifecycle.ts`
    - Add `ReconnectionState` tracking (attempt, maxAttempts=3, baseDelayMs=1000, maxDelayMs=30000)
    - Store reconnection state in `tabMapRef` entry (not shared React state) per multi-tab isolation Principle 3
    - Use `capturedTabId` pattern for all state mutations during reconnection
    - On non-abort fetch error (connection-phase only, before any SSE events received): retry with exponential backoff `min(baseDelay * 2^attempt, maxDelay)`
    - Mid-stream failure: preserve partial content, show error + "Retry" button (no reconnection — backend turn is stateful)
    - Abort reconnection loop if tab is closed (`tabMapRef.get(capturedTabId) === undefined`)
    - On successful reconnect: fire info toast via `useToast()`
    - After 3 failed attempts: set error message in chat with manual "Retry" button
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 6.2 Add stall detection to `useChatStreamingLifecycle`
    - Reset a 45-second timer on every received chunk (including heartbeats)
    - On stall timeout: trigger reconnection logic from 6.1
    - Clean up stall timer on unmount, stream abort, and tab close
    - _Requirements: 2.6_

  - [x] 6.3 Add "Reconnecting..." indicator rendering in chat area for the affected tab
    - Read `isReconnecting` from tab state to conditionally render indicator
    - _Requirements: 2.5_

  - [ ]* 6.4 Write property tests for SSE reconnection in `desktop/src/__tests__/sseReconnection.property.test.ts`
    - **Property 3: Exponential backoff delay calculation**
    - **Validates: Requirements 2.1**

  - [ ]* 6.5 Write property tests for SSE stall and reconnecting indicator
    - **Property 4: Reconnecting indicator visibility** in `desktop/src/__tests__/sseReconnection.property.test.ts`
    - **Property 5: Stall detection triggers reconnection** in `desktop/src/__tests__/sseReconnection.property.test.ts`
    - **Validates: Requirements 2.5, 2.6**

- [x] 7. Implement SSE Stream Cancellation refinements
  - [x] 7.1 Refine `handleStop` in `ChatPage.tsx` for partial content preservation
    - Instead of appending a new "Generation stopped" message, preserve the in-progress assistant message's partial content blocks and append the stop indicator to that message
    - _Requirements: 3.3_

  - [x] 7.2 Add 120-second long-stream timeout warning in `useChatStreamingLifecycle`
    - Start timer when SSE stream begins receiving data
    - Fire warning toast after 120s suggesting user may cancel
    - Clear timer on stream completion, abort, or tab close
    - _Requirements: 3.4_

  - [ ]* 7.3 Write property tests for SSE cancellation in `desktop/src/__tests__/sseCancellation.property.test.ts`
    - **Property 6: Stop button visibility tracks streaming state**
    - **Property 7: Partial content preservation on abort**
    - **Validates: Requirements 3.1, 3.3**

- [x] 8. Checkpoint — SSE resilience and cancellation complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement Rate Limiter
  - [x] 9.1 Create `useRateLimiter` hook in `desktop/src/hooks/useRateLimiter.ts`
    - Store rate limits in `useRef<Map<string, RateLimitEntry>>` (avoids re-renders)
    - Expose `registerRateLimit(endpoint, retryAfterSec)`, `isLimited(endpoint)`, `getRemainingSeconds(endpoint)`, `activeLimits`
    - On registration: fire warning toast with cooldown info
    - On expiry: fire info toast indicating user may resume
    - _Requirements: 6.1, 6.2, 6.4_

  - [x] 9.2 Create `useRateLimitCountdown` hook in `desktop/src/hooks/useRateLimitCountdown.ts`
    - Separate hook that runs a 1-second interval timer only when mounted in a countdown UI component
    - Accepts endpoint string, returns `remainingSeconds` state
    - Avoids app-wide re-renders by isolating countdown state to consuming components
    - _Requirements: 6.3_

  - [x] 9.3 Integrate rate limiter with axios interceptor in `desktop/src/services/api.ts`
    - Add response interceptor: on HTTP 429 with Retry-After header, call `registerRateLimit`
    - Block further requests to the same endpoint while limited
    - _Requirements: 6.1_

  - [x] 9.4 Disable chat input and show countdown indicator when rate-limited
    - Read rate limit state for chat endpoints
    - Disable chat input for affected tab
    - Display countdown using `useRateLimitCountdown`
    - _Requirements: 6.3_

  - [ ]* 9.5 Write property tests for Rate Limiter in `desktop/src/hooks/useRateLimiter.property.test.ts`
    - **Property 12: Rate limiter blocks requests during cooldown**
    - **Property 13: Rate limit disables chat input**
    - **Validates: Requirements 6.1, 6.3**

- [x] 10. Implement Permission Request Lifecycle
  - [x] 10.1 Add timeout and countdown to `PermissionRequestModal` in `desktop/src/components/chat/PermissionRequestModal.tsx`
    - Start 5-minute (300,000ms) countdown timer on mount
    - Show visible countdown indicator when remaining time ≤ 60 seconds
    - Auto-deny permission and send denial to backend when timer expires
    - Treat 404/409 response to auto-deny POST as no-op (backend already moved on)
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 10.2 Handle stale permission requests and submission errors
    - If backend session is no longer active (detected via health check or SSE error), dismiss dialog with info toast
    - On permission submission error, display error toast with failure reason
    - _Requirements: 7.4, 7.5_

  - [ ]* 10.3 Write property tests for Permission lifecycle in `desktop/src/components/chat/PermissionRequestModal.property.test.tsx`
    - **Property 14: Permission timeout auto-denies**
    - **Property 15: Permission countdown visibility**
    - **Validates: Requirements 7.2, 7.3**

- [x] 11. Checkpoint — Rate limiter and permission lifecycle complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Implement Validation Error Display (DEFERRED — low ROI with current minimal forms)
  - [ ]* 12.1 Create `useValidationErrors` hook in `desktop/src/hooks/useValidationErrors.ts`
    - Implement `fieldErrors: Map<string, string>`, `setFieldErrors`, `clearFieldError`, `clearAllErrors`, `getFieldError`, `hasError`
    - Parse `ValidationErrorResponse.fields` array and map field names to error messages
    - Surface unmapped field errors (no matching input in UI) via `useToast()` with field name and message
    - _Requirements: 8.1, 8.4_

  - [ ]* 12.2 Add field-level error rendering to form components
    - Highlight affected input fields with red border when `hasError(fieldName)` is true
    - Display error message below the field
    - Call `clearFieldError(fieldName)` on input `onChange`
    - Apply to agent configuration forms, MCP server configuration forms, and skill creation forms
    - _Requirements: 8.1, 8.2, 8.3, 8.5_

  - [ ]* 12.3 Write property tests for Validation Errors in `desktop/src/hooks/useValidationErrors.property.test.ts`
    - **Property 16: Validation errors map to fields and clear on edit**
    - **Property 17: Unmapped validation errors surface as toasts**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

- [x] 13. Implement Structured Chat Error Display
  - [x] 13.1 Create `ChatErrorMessage` component in `desktop/src/components/chat/ChatErrorMessage.tsx`
    - Render SSE error events with red accent left border, visually distinct from assistant messages
    - Display `suggestedAction` as a highlighted actionable element below error message when present
    - _Requirements: 9.1, 9.5_

  - [x] 13.2 Implement error-code-specific behaviors in `ChatErrorMessage`
    - `AGENT_TIMEOUT`: show "Retry" button that re-sends the last user message from the tab's message history
    - `RATE_LIMIT_EXCEEDED`: show countdown timer based on Retry-After value, auto-re-enable input on expiry (integrate with `useRateLimiter`)
    - `SERVICE_UNAVAILABLE`: trigger immediate health check via `HealthContext.triggerHealthCheck()`
    - _Requirements: 9.2, 9.3, 9.4_

  - [x] 13.3 Integrate `ChatErrorMessage` into chat message rendering pipeline
    - Render `ChatErrorMessage` for SSE events with `type === 'error'` in the chat message list
    - Wire `onRetry` to re-send last user message for the active tab
    - _Requirements: 9.1, 9.2_

  - [ ]* 13.4 Write property tests for ChatErrorMessage in `desktop/src/components/chat/ChatErrorMessage.property.test.tsx`
    - **Property 18: Chat error events render with distinct styling and suggested actions**
    - **Property 19: AGENT_TIMEOUT error triggers retry with last user message**
    - **Property 20: RATE_LIMIT_EXCEEDED error shows countdown and auto-re-enables**
    - **Property 21: SERVICE_UNAVAILABLE error triggers immediate health check**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**

- [x] 14. Final checkpoint — All features integrated
  - Ensure all tests pass, ask the user if questions arise.
  - Verify toast migration is complete: no remaining direct `<Toast>` usage except deprecated wrapper
  - Verify `PolicyViolationToast.tsx` is removed
  - Verify error boundaries wrap both individual tabs and app root
  - Verify health monitor, rate limiter, and permission lifecycle all fire toasts correctly

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (21 properties total)
- All SSE reconnection state must use `capturedTabId` + `tabMapRef` pattern per multi-tab isolation principles
- The `useRateLimitCountdown` hook is intentionally separate from `useRateLimiter` to avoid app-wide re-renders
- No backend changes are needed — all existing endpoints and error schemas are sufficient
