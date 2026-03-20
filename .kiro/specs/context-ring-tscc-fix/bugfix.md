# Bugfix Requirements Document

## Introduction

Two related bugs prevent the SwarmAI desktop app from displaying live session telemetry to the user. The Context Usage Ring in the TopBar is permanently gray ("No context data yet") because the backend filters out `context_warning` SSE events at the "ok" level, meaning normal usage below 70% never reaches the frontend. Separately, the TSCC Popover Button under the chat input shows empty/disabled content because the `useTSCCState` hook only fetches system prompt metadata on `sessionId` change — which fires before the backend has populated the metadata during the streaming loop — and never re-fetches after the conversation turn completes.

Both bugs share a common theme: the frontend never receives data it needs because of timing or filtering gaps in the backend-to-frontend data pipeline.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a conversation turn completes with context usage below 70% of the model's context window THEN the system does not emit a `context_warning` SSE event because `session_unit.py` filters out events where `level == "ok"`, leaving the frontend `contextWarning` state as null and the ContextUsageRing permanently gray with "No context data yet" tooltip.

1.2 WHEN a conversation turn completes with context usage at any level (ok, warn, or critical) THEN the TopBar ContextUsageRing shows no fill arc and gray color because `contextPct` is derived from `contextWarning?.pct ?? null` and `contextWarning` is never set for "ok" level events.

1.3 WHEN a new session starts and the `session_start` SSE event arrives THEN the `useTSCCState` hook immediately fetches system prompt metadata via `GET /api/chat/{session_id}/system-prompt`, but the metadata has not yet been populated by `prompt_builder.py` during the streaming loop, so the fetch returns empty/default metadata.

1.4 WHEN the conversation turn completes (a `result` SSE event arrives) and the system prompt metadata has been populated on the backend THEN the TSCC Popover Button continues to show empty/disabled content because `useTSCCState` has no mechanism to re-fetch metadata after streaming completes.

### Expected Behavior (Correct)

2.1 WHEN a conversation turn completes with context usage below 70% of the model's context window THEN the system SHALL emit a `context_warning` SSE event with `level: "ok"` containing the current `pct` and `tokensEst` values, so the frontend receives usage data at all levels.

2.2 WHEN a conversation turn completes with context usage at any level (ok, warn, or critical) THEN the TopBar ContextUsageRing SHALL display the correct fill arc and color (green for <60%, yellow for 60-80%, red for >80%) based on the `pct` value from the emitted `context_warning` event.

2.3 WHEN a conversation turn's streaming completes (indicated by a `result` SSE event) THEN the backend SHALL emit a `system_prompt_metadata` SSE event containing the assembled metadata, and the frontend SSE handler SHALL store it on `tabMapRef` and mirror it to React state, so that the TSCC Popover Button displays the current metadata assembled during that turn — using the same SSE pipeline as the context ring.

2.4 WHEN the TSCC Popover Button is opened after a conversation turn has completed THEN the system SHALL display the populated system prompt metadata (file list, token counts, full text) instead of empty/disabled content.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN context usage is at "warn" level (≥70%) or "critical" level (≥90%) THEN the system SHALL CONTINUE TO emit `context_warning` SSE events with the appropriate level, message, and percentage values exactly as before.

3.2 WHEN the frontend receives a `context_warning` SSE event with level "warn" or "critical" THEN the system SHALL CONTINUE TO display the context warning banner/toast and update the ContextUsageRing with the correct color (yellow for warn, red for critical).

3.3 WHEN `input_tokens` is null, zero, or negative THEN `build_context_warning()` SHALL CONTINUE TO return `None` and no SSE event shall be emitted.

3.4 WHEN the user switches tabs THEN the system SHALL CONTINUE TO restore the correct `contextWarning` state from `tabMapRef` for the newly active tab (display mirror pattern preserved).

3.5 WHEN `sessionId` changes (tab switch or new session) THEN the system SHALL CONTINUE TO restore the correct `promptMetadata` from `tabMapRef` for the newly active tab (display mirror pattern preserved, same as `contextWarning`).

3.6 WHEN the TSCC popover is open and the user switches tabs THEN the popover SHALL CONTINUE TO auto-close via the existing `sessionId` change detection in `TSCCPopoverButton`.

3.7 WHEN the backend restarts and no metadata exists for a session THEN the `GET /api/chat/{session_id}/system-prompt` endpoint SHALL CONTINUE TO return a default empty `SystemPromptMetadata` object (not a 404 error).
