# PE Review: Error Handling UX Design

**Reviewer:** Principal Engineer Review (Automated)
**Date:** 2026-03-15
**Spec:** `.kiro/specs/error-handling-ux/design.md`
**Against:** `.kiro/specs/error-handling-ux/requirements.md`

---

## 1. Summary Verdict: APPROVE WITH CHANGES

The design is architecturally sound and covers all 9 requirements. The layered approach (infrastructure → notification → enforcement → display) is well-structured, and the decision to extend `useChatStreamingLifecycle` rather than creating parallel hooks is correct. The tabMapRef-authoritative pattern is respected throughout.

However, there are **3 critical issues** that must be resolved before implementation, **5 important suggestions** that should be addressed, and several minor notes. The most significant concern is the collision between the proposed `ToastProvider` system and the existing `Toast` component already used in 4+ places, which will cause a confusing dual-toast situation if not addressed in the design.

---

## 2. Critical Issues (Must Fix)

### C1. Existing Toast Component Collision — Migration Strategy Missing

**Problem:** The design proposes a new `ToastProvider` + `useToast()` context system (Section 2), but the codebase already has:
- `desktop/src/components/common/Toast.tsx` — A standalone `Toast` component with `ToastType = 'info' | 'success' | 'warning' | 'error'`
- `desktop/src/components/common/ErrorBoundary.tsx` — Contains `ErrorToast` (positioned `bottom-4 right-4`)
- `desktop/src/components/common/PolicyViolationToast.tsx` — A specialized toast for 409 policy violations
- Active usage in `ChatPage.tsx`: `tabLimitToast`, context window warning toast, and in `AssistantMessageView.tsx` for memory save feedback

The existing `Toast` renders at `fixed bottom-4 right-4`. The proposed `ToastStack` renders at `fixed top-right`. This creates two independent toast systems rendering in different corners with no coordination.

**Required Change:** The design MUST include a migration section that:
1. Specifies that the new `ToastProvider` replaces all existing ad-hoc toast usage
2. Lists every current toast callsite that must be migrated (at minimum: `tabLimitToast` in ChatPage, context warning toast in ChatPage, `ErrorToast` in ErrorBoundary.tsx, `PolicyViolationToast`, memory save toast in AssistantMessageView)
3. Defines whether `PolicyViolationToast` (which has a "Resolve" action button) maps to the new system or remains separate
4. Specifies the deprecation path for the existing `Toast.tsx` component

**Suggested addition to Section 2 (Toast Notification System):**

```markdown
#### Migration from Existing Toast Components

The codebase has several ad-hoc toast implementations that will be consolidated:

| Current Component | Location | Migration |
|---|---|---|
| `Toast` (standalone) | `components/common/Toast.tsx` | Replace with `useToast()` calls. Deprecate component. |
| `ErrorToast` | `components/common/ErrorBoundary.tsx` | Remove. Error boundary logs to console; API errors use `useToast()`. |
| `PolicyViolationToast` | `components/common/PolicyViolationToast.tsx` | Extend `ToastOptions` with optional `action` button. Migrate to `useToast()`. |
| `tabLimitToast` state | `ChatPage.tsx` | Replace `useState` + `<Toast>` with `useToast().addToast()`. |
| Context warning toast | `ChatPage.tsx` | Replace with `useToast().addToast()`. |
| Memory save toast | `AssistantMessageView.tsx` | Replace with `useToast().addToast()`. |

The existing `Toast.tsx` will be kept temporarily as a deprecated wrapper that
internally calls `useToast()`, then removed in a follow-up cleanup pass.
```

### C2. Existing ErrorBoundary Already Exists — Design Doesn't Acknowledge It

**Problem:** The design (Section 3) proposes creating `desktop/src/components/common/ErrorBoundary.tsx` with a new `ErrorBoundary` class component. But this file **already exists** with a fully functional `ErrorBoundary` class component that has:
- `getDerivedStateFromError` + `componentDidCatch`
- `ErrorFallback` component with retry button
- `ApiError` component for structured error display
- `ErrorToast` for toast-style errors

The design's proposed interface (`fallback: 'tab' | 'app'`) differs from the existing interface (`fallback?: ReactNode`, `onError?`, `onRetry?`). Implementing as written would break all existing consumers.

**Required Change:** The design must:
1. Acknowledge the existing `ErrorBoundary` component
2. Specify whether to extend it (add `fallback: 'tab' | 'app'` mode) or create a new wrapper
3. Preserve backward compatibility with existing `ErrorBoundary` usage

**Suggested revision to Section 3:**

```markdown
### 3. React Error Boundaries (extends existing `ErrorBoundary`)

**File:** `desktop/src/components/common/ErrorBoundary.tsx` (modified, not new)

The existing `ErrorBoundary` class component already provides `getDerivedStateFromError`,
`componentDidCatch`, `ErrorFallback`, and `ApiError`. This design extends it with:

1. A new `variant` prop (`'tab' | 'app' | 'default'`) that selects the fallback UI:
   - `'default'` (existing behavior): Uses `props.fallback` or `ErrorFallback`
   - `'tab'`: Shows compact error + "Reload Tab" button (resets boundary state)
   - `'app'`: Shows full-page error + "Reload App" button (`window.location.reload()`)
2. The existing `fallback`, `onError`, `onRetry` props remain supported for backward compat.
```

### C3. Existing `handleStop` Already Implements R3 — Design Doesn't Acknowledge It

**Problem:** The design (Section 5) proposes adding SSE stream cancellation to `useChatStreamingLifecycle`, including a `stopGeneration()` function. But `ChatPage.tsx` already has a fully working `handleStop` function (lines 1112-1131) that:
- Reads the active tab's abort controller from `tabMapRef` (correct pattern)
- Calls `tabState.abortController.abort()`
- Calls `chatService.stopSession(tabSessionId)`
- Appends a "⏹️ Generation stopped by user." message
- Clears streaming state via `setIsStreaming(false, ...)`

The stop button is already wired to `ChatInput` via `onStop={handleStop}`.

**Required Change:** The design must:
1. Acknowledge the existing `handleStop` implementation
2. Clarify what R3 adds beyond what already exists (the answer: R3.4's 120s timeout warning is new, and R3.3's partial content preservation may need refinement)
3. Specify whether `handleStop` moves into the hook or stays in ChatPage

**Suggested revision to Section 5:**

```markdown
### 5. SSE Stream Cancellation (refinements to existing `handleStop`)

The app already has a working stop mechanism:
- `ChatPage.handleStop()` aborts the fetch, calls `stopSession()`, and appends a stop message
- The stop button is visible during streaming via `ChatInput.onStop`

This design adds two refinements:

1. **Partial content preservation (R3.3):** The current `handleStop` appends a new
   "Generation stopped" message. The refinement preserves the in-progress assistant
   message's partial content blocks and appends the stop indicator to that message
   instead of creating a separate one.

2. **Long-stream timeout warning (R3.4):** A timer in `useChatStreamingLifecycle`
   fires a warning toast after 120s of active streaming, suggesting the user may
   cancel. This is new functionality added to the hook.

No changes to the stop button visibility logic (R3.1) or the abort+stop-request
flow (R3.2) are needed — they already work correctly.
```

---

## 3. Important Suggestions (Should Fix)

### S1. Health Monitor Polling Interval Is Aggressive for a Local Sidecar

**Concern:** The design specifies 10-second polling for `GET /health`. This backend is a local Tauri sidecar process, not a remote server. It either works or it doesn't — there's no network latency, load balancer, or intermittent connectivity. A 10s poll with 2-failure threshold means the UI shows "disconnected" after just 20 seconds, which is appropriate for a remote service but aggressive for a local process.

More importantly, the sidecar crash is typically detected much faster via the Tauri process management (the sidecar exit event). Polling adds unnecessary overhead.

**Suggestion:** 
1. Increase default interval to 30 seconds (the sidecar is local — if it's down, it's down)
2. Add a note that Tauri's sidecar process exit event should be the primary crash detection mechanism, with health polling as a secondary check for "alive but unhealthy" states
3. Consider whether the health monitor should also check the `/health` response body for `status: "initializing"` (which the backend returns before startup completes)

**Suggested addition:**

```markdown
**Note on local sidecar context:** Unlike a remote service, the backend runs as a
local Tauri sidecar. The primary crash detection mechanism is Tauri's process exit
event (which fires immediately). Health polling serves as a secondary check for
"alive but unhealthy" states (e.g., `status: "initializing"` during startup).
The default 30-second interval reflects this — increase frequency only if needed.
```

### S2. SSE Reconnection Cannot Resume Mid-Stream

**Concern:** R2.2 says "the Frontend SHALL resume processing events from the stream." But the backend's SSE chat endpoint (`/api/chat/stream`) is a stateful streaming response tied to a Claude SDK conversation turn. If the connection drops mid-stream, reconnecting to the same endpoint won't resume from where it left off — it would start a new turn or fail because the previous turn is still in-flight on the backend.

The design's reconnection logic (Section 4) doesn't address this fundamental limitation. Exponential backoff reconnection makes sense for the initial connection attempt (before any data flows), but mid-stream reconnection is architecturally impossible with the current backend.

**Suggestion:** Clarify the reconnection scope:
1. **Pre-data reconnection:** If the fetch fails before any SSE events are received (e.g., connection refused), retry with backoff. This is valid.
2. **Mid-stream failure:** If the connection drops after data has started flowing, do NOT attempt to reconnect to the same stream. Instead, show the partial content + error message + Retry button (which re-sends the last user message as a new turn).

**Suggested revision to Section 4:**

```markdown
**Reconnection scope:** Reconnection applies only to connection-phase failures
(before any SSE events are received). Once data has started flowing, a mid-stream
failure cannot be resumed — the backend's Claude SDK turn is stateful and
non-resumable. In this case, preserve partial content and show an error with a
"Retry" button that re-sends the last user message as a new conversation turn.
```

### S3. Rate Limiter `useRef<Map>` + 1-Second Interval Timer — Re-render Risk

**Concern:** The design stores rate limits in a `useRef<Map>` (good — avoids re-renders on registration) but then runs a 1-second interval timer to "update a display state for countdown UIs." This interval timer will trigger a `setState` every second for every active rate limit, causing re-renders across all components consuming the hook.

In a multi-tab app, if the rate limiter hook is used at the app level (which it must be, since it integrates with the axios interceptor), this means the entire app re-renders every second during a rate limit cooldown.

**Suggestion:**
1. The 1-second countdown timer should only run when a component is actively displaying the countdown (not globally)
2. Consider splitting: the `useRateLimiter` hook manages the ref-based map and exposes `isLimited()`/`getRemainingSeconds()`, while a separate `useRateLimitCountdown(endpoint)` hook runs the interval timer only when mounted in a countdown UI component
3. Alternatively, use `requestAnimationFrame` or a single shared timer that only bumps state when there are active listeners

### S4. Permission Timeout — Frontend and Backend Race Condition

**Concern:** R7.1-7.2 adds a 5-minute frontend timeout that auto-denies. The backend already has a 300s timeout in `wait_for_permission_decision`. The design acknowledges this ("The frontend adds a visible countdown and auto-deny at 5 minutes to match") but doesn't address the race condition:

1. Frontend timer fires at 299.9s → sends deny to backend
2. Backend timer fires at 300.0s → auto-denies independently
3. The deny request from step 1 arrives at a backend that already moved past the permission wait

This is likely harmless (the backend will ignore the late deny), but the design should explicitly state the expected behavior and confirm the backend handles duplicate/late permission decisions gracefully.

**Suggested addition to Section 7:**

```markdown
**Race condition note:** The frontend timeout (5 min) and backend timeout (300s in
`wait_for_permission_decision`) may fire near-simultaneously. The backend already
handles late/duplicate permission decisions gracefully (the asyncio Event is
already set). The frontend should treat a 404/409 response to its auto-deny POST
as a no-op (the backend already moved on).
```

### S5. Missing Property for R9.2, R9.3, R9.4 — Error-Code-Specific Behaviors

**Concern:** Property 18 covers the general styling and `suggestedAction` rendering for chat errors, but R9.2 (AGENT_TIMEOUT → Retry button), R9.3 (RATE_LIMIT_EXCEEDED → countdown timer), and R9.4 (SERVICE_UNAVAILABLE → trigger health check) each have specific behavioral requirements that are not covered by any property.

These are distinct, testable behaviors:
- R9.2: Given an error event with code `AGENT_TIMEOUT`, the Retry button must re-send the last user message
- R9.3: Given an error event with code `RATE_LIMIT_EXCEEDED` and a `retryAfter` value, a countdown timer must be displayed and input must auto-re-enable on expiry
- R9.4: Given an error event with code `SERVICE_UNAVAILABLE`, `useHealthMonitor` must perform an immediate health check

**Suggestion:** Add 3 new properties (P19, P20, P21) or expand P18 into sub-properties:

```markdown
### Property 19: AGENT_TIMEOUT error triggers retry with last user message

*For any* SSE error event with code "AGENT_TIMEOUT" rendered in the chat area,
a "Retry" button should be displayed. When clicked, the system should re-send
the last user message from the current tab's message history as a new chat request.

**Validates: Requirement 9.2**

### Property 20: RATE_LIMIT_EXCEEDED error shows countdown and auto-re-enables

*For any* SSE error event with code "RATE_LIMIT_EXCEEDED" containing a retryAfter
value, a countdown timer should be displayed showing the remaining seconds. When
the countdown reaches zero, the chat input for the affected tab should be
automatically re-enabled.

**Validates: Requirement 9.3**

### Property 21: SERVICE_UNAVAILABLE error triggers immediate health check

*For any* SSE error event with code "SERVICE_UNAVAILABLE" rendered in the chat area,
the health monitor should perform an immediate out-of-cycle health check (not
waiting for the next polling interval).

**Validates: Requirement 9.4**
```

---

## 4. Minor Notes

### M1. Toast Position: Top-Right vs Bottom-Right

The existing `Toast.tsx` renders at `bottom-4 right-4`. The proposed `ToastStack` renders at top-right. This is a UX decision, not a bug, but the migration plan (C1) should specify the final position. Top-right is the more standard choice for notification systems.

### M2. `ToastOptions.id` Deduplication — Good Design

The deduplication-by-id feature is well thought out. This prevents the health monitor from stacking "Backend disconnected" toasts on every failed poll. Ensure the health monitor uses a stable id like `'health-disconnected'`.

### M3. Stall Detection Timer Cleanup

The 45-second stall detection timer (Section 4) must be cleaned up on component unmount and on stream abort. The design doesn't explicitly mention cleanup, but the implementation should use `useEffect` cleanup or store the timer ID in the tab state for proper teardown.

### M4. `ChatErrorMessage` Props Use `StreamEvent` Type

The design specifies `error: StreamEvent` as the prop type for `ChatErrorMessage`. This is correct since SSE error events are already typed as `StreamEvent` with `type === 'error'`. However, consider a narrower type alias like `ErrorStreamEvent` for clarity.

### M5. Health Monitor `initializing` State

The backend `/health` endpoint returns `{ status: "initializing" }` before startup completes. The design's `HealthState` includes `'initializing'` as a status value, which is good. But the design doesn't specify what the UI does during `initializing` — it should probably show a "Starting up..." indicator rather than "disconnected."

### M6. Testing Strategy — Backend PBT Section Is Empty

The testing strategy mentions `hypothesis` for backend PBT but then says "No new backend endpoints or models are needed." Since all changes are frontend-only, the backend PBT section can be removed to avoid confusion. The `python` test tag format example is unnecessary.

### M7. `useValidationErrors` — Form Identification

R8.5 says validation errors must work on "agent configuration forms, MCP server configuration forms, and skill creation forms." The design's `useValidationErrors` hook is generic (good), but the design doesn't specify how field names from the backend map to input field identifiers in different forms. Consider adding a `formId` or `fieldPrefix` parameter to scope errors per form.

---

## 5. Specific Revisions

### Revision 1: Add migration table to Section 2 (per C1)
**Location:** After the `ToastStack` description in Section 2
**Action:** Add the migration table from C1 above

### Revision 2: Rewrite Section 3 header and first paragraph (per C2)
**Location:** Section 3 title and opening
**Old text:**
```
### 3. React Error Boundaries

**File:** `desktop/src/components/common/ErrorBoundary.tsx`
```
**New text:**
```
### 3. React Error Boundaries (extends existing `ErrorBoundary`)

**File:** `desktop/src/components/common/ErrorBoundary.tsx` (modified, not new)

The existing `ErrorBoundary` class component already provides `getDerivedStateFromError`,
`componentDidCatch`, `ErrorFallback`, and `ApiError`. This design extends it with a
`variant` prop while preserving full backward compatibility.
```

### Revision 3: Rewrite Section 5 to acknowledge existing handleStop (per C3)
**Location:** Section 5 title and content
**Old text:**
```
### 5. SSE Stream Cancellation (additions to `useChatStreamingLifecycle`)
```
**New text:**
```
### 5. SSE Stream Cancellation (refinements to existing `handleStop` in ChatPage)
```
Then add the acknowledgment text from C3.

### Revision 4: Change health monitor default interval (per S1)
**Location:** Section 1, `useHealthMonitor` options
**Old text:**
```
  intervalMs?: number;       // default 10_000
```
**New text:**
```
  intervalMs?: number;       // default 30_000 (local sidecar — see note below)
```

### Revision 5: Add reconnection scope clarification (per S2)
**Location:** End of Section 4
**Action:** Add the "Reconnection scope" paragraph from S2

### Revision 6: Add race condition note to Section 7 (per S4)
**Location:** End of Section 7
**Action:** Add the "Race condition note" paragraph from S4

### Revision 7: Add Properties 19, 20, 21 (per S5)
**Location:** After Property 18 in the Correctness Properties section
**Action:** Add the three new properties from S5

### Revision 8: Update testing strategy table for new properties
**Location:** Testing Strategy table
**Action:** Add rows for P19, P20, P21:

```
| P19: AGENT_TIMEOUT retry | `ChatErrorMessage.property.test.tsx` | Generate error events with AGENT_TIMEOUT code, verify Retry button re-sends last user message |
| P20: RATE_LIMIT countdown | `ChatErrorMessage.property.test.tsx` | Generate RATE_LIMIT_EXCEEDED events with retryAfter values, verify countdown and auto-re-enable |
| P21: SERVICE_UNAVAILABLE health check | `ChatErrorMessage.property.test.tsx` | Generate SERVICE_UNAVAILABLE events, verify immediate health check trigger |
```

---

## 6. Requirement Coverage Matrix

| Req | AC | Covered in Design? | Property? | Notes |
|-----|-----|---------------------|-----------|-------|
| R1 | 1.1 | ✅ Section 1 | — | Unit test covers startup |
| R1 | 1.2 | ✅ Section 1 | P1 | |
| R1 | 1.3 | ✅ Section 1 | P1 | |
| R1 | 1.4 | ✅ Section 1 | — | Unit test covers transition toast |
| R1 | 1.5 | ✅ Section 1 | — | Unit test covers recovery toast |
| R1 | 1.6 | ✅ Section 1 | P2 | |
| R1 | 1.7 | ✅ Section 1 | P2 | |
| R2 | 2.1 | ✅ Section 4 | P3 | ⚠️ Needs reconnection scope clarification (S2) |
| R2 | 2.2 | ⚠️ Section 4 | — | Mid-stream resume is impossible (S2) |
| R2 | 2.3 | ✅ Section 4 | — | Unit test |
| R2 | 2.4 | ✅ Section 4 | — | Unit test |
| R2 | 2.5 | ✅ Section 4 | P4 | |
| R2 | 2.6 | ✅ Section 4 | P5 | |
| R3 | 3.1 | ✅ Already exists | P6 | Already implemented |
| R3 | 3.2 | ✅ Already exists | — | `handleStop` already works |
| R3 | 3.3 | ⚠️ Section 5 | P7 | Current impl creates separate msg; needs refinement |
| R3 | 3.4 | ✅ Section 5 | — | New: 120s warning toast |
| R4 | 4.1 | ✅ Already exists | P8 | Existing ErrorBoundary (C2) |
| R4 | 4.2 | ✅ Already exists | P8 | Existing ErrorFallback has retry |
| R4 | 4.3 | ✅ Already exists | P8 | Existing componentDidCatch logs |
| R4 | 4.4 | ✅ Section 3 | — | New: per-tab wrapping |
| R4 | 4.5 | ✅ Section 3 | — | New: app-level wrapping |
| R5 | 5.1 | ✅ Section 2 | P11 | |
| R5 | 5.2 | ✅ Section 2 | — | Top-right position |
| R5 | 5.3 | ✅ Section 2 | P9 | |
| R5 | 5.4 | ✅ Section 2 | P9 | |
| R5 | 5.5 | ✅ Section 2 | P10 | |
| R5 | 5.6 | ✅ Section 2 | — | `useToast()` hook |
| R6 | 6.1 | ✅ Section 6 | P12 | |
| R6 | 6.2 | ✅ Section 6 | — | Unit test |
| R6 | 6.3 | ✅ Section 6 | P13 | ⚠️ Re-render risk (S3) |
| R6 | 6.4 | ✅ Section 6 | — | Unit test |
| R7 | 7.1 | ✅ Section 7 | — | Unit test |
| R7 | 7.2 | ✅ Section 7 | P14 | ⚠️ Race condition (S4) |
| R7 | 7.3 | ✅ Section 7 | P15 | |
| R7 | 7.4 | ✅ Section 7 | — | Unit test |
| R7 | 7.5 | ✅ Section 7 | — | Unit test |
| R8 | 8.1 | ✅ Section 8 | P16 | |
| R8 | 8.2 | ✅ Section 8 | P16 | |
| R8 | 8.3 | ✅ Section 8 | P16 | |
| R8 | 8.4 | ✅ Section 8 | P17 | |
| R8 | 8.5 | ✅ Section 8 | — | ⚠️ Field mapping unclear (M7) |
| R9 | 9.1 | ✅ Section 9 | P18 | |
| R9 | 9.2 | ✅ Section 9 | ❌ Missing | Needs P19 (S5) |
| R9 | 9.3 | ✅ Section 9 | ❌ Missing | Needs P20 (S5) |
| R9 | 9.4 | ✅ Section 9 | ❌ Missing | Needs P21 (S5) |
| R9 | 9.5 | ✅ Section 9 | P18 | |

**Coverage: 37/37 acceptance criteria addressed in design. 3 missing properties identified.**

---

## 7. Risk Assessment Summary

| Risk | Severity | Mitigation |
|------|----------|------------|
| Dual toast systems (old + new) confusing users and devs | HIGH | Migration plan (C1) |
| ErrorBoundary rewrite breaks existing consumers | HIGH | Extend, don't replace (C2) |
| Mid-stream SSE reconnection impossible | MEDIUM | Clarify scope (S2) |
| 1-second countdown timer causes re-render storms | MEDIUM | Split countdown into local hook (S3) |
| Permission timeout race with backend | LOW | Document expected behavior (S4) |
| Health polling overhead for local sidecar | LOW | Increase interval, note Tauri events (S1) |
| Stall detection timer leak on unmount | LOW | Ensure cleanup in implementation (M3) |
