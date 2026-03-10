# Bugfix Requirements Document

## Introduction

The Context Monitor ring (displayed below the chat input) has three related display bugs:
1. When the user closes all tabs, the auto-created fresh tab still shows stale context data from the previous tab because `setContextWarning(null)` is missing from the close-last-tab cleanup path.
2. The SVG ring fill doesn't visually match the percentage when `pct` exceeds 100% (possible with cumulative SDK token counts), causing the stroke offset to go negative and the ring to wrap incorrectly.
3. There is no visual distinction between "no data" (null) and "very low usage" at the ring's small default size, making it impossible for users to tell if the ring has been reset.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the user closes the last open tab (triggering auto-creation of a fresh tab) THEN the system retains the stale `contextWarning` React state from the previous tab, causing the ring to display the old percentage and color instead of resetting to the "no data" gray state.

1.2 WHEN the backend reports a context usage percentage greater than 100% (e.g. due to cumulative SDK token counts) THEN the system computes a negative `strokeDashoffset`, causing the SVG ring fill to wrap around incorrectly and display a visually broken ring.

1.3 WHEN the context usage percentage is null (no data) THEN the system renders a fully gray ring that is visually indistinguishable from a very low percentage green ring at the default 18px size, providing no clear "empty/reset" indicator to the user.

### Expected Behavior (Correct)

2.1 WHEN the user closes the last open tab (triggering auto-creation of a fresh tab) THEN the system SHALL call `setContextWarning(null)`, `setPendingPermission(null)`, and `setIsExpanded(false)` in the close-last-tab cleanup block, resetting all transient state to match the welcome screen (consistent with `handleNewSession` behavior).

2.2 WHEN the backend reports a context usage percentage greater than 100% THEN the system SHALL clamp `fillPct` to the range [0, 100] so the SVG ring fill never exceeds a full circle and always visually matches the displayed percentage.

2.3 WHEN the context usage percentage is null (no data) THEN the system SHALL render a visually distinct "empty" ring state (e.g. dashed stroke, reduced opacity, or other visual differentiation) so users can clearly distinguish "no data" from "very low usage".

### Expected Behavior (Correct) — continued

2.4 WHEN the backend emits a `context_warning` SSE event with level `ok` or `warn` THEN the system SHALL NOT display a Toast notification, because the ring already communicates the state visually (color + fill percentage). Only `critical` level (≥85%) SHALL trigger a Toast, since it requires user action (save context / start new session).

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the user switches between existing tabs that have active sessions THEN the system SHALL CONTINUE TO restore the correct per-tab `contextWarning` from the tab map and display the corresponding ring color and percentage.

3.2 WHEN the user creates a new tab via the "New Session" button THEN the system SHALL CONTINUE TO reset `contextWarning` to null and show the gray ring (existing behavior in `handleNewSession`).

3.3 WHEN the context usage percentage is between 0% and 100% inclusive THEN the system SHALL CONTINUE TO render the ring with the correct fill proportion and color thresholds (green < 70%, amber 70–84%, red >= 85%).

3.4 WHEN the user hovers over the ring THEN the system SHALL CONTINUE TO display the tooltip showing the exact percentage or "No context data yet" for null values.
