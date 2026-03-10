<!-- PE-REVIEWED -->
# Context Monitor Ring UX Bugfix Design

## Overview

Three related bugs in the Context Monitor ring component cause incorrect visual state after closing the last tab, broken SVG rendering when percentage exceeds 100%, and indistinguishable null vs. low-usage states. The fix targets two files: `ChatPage.tsx` (state reset) and `ContextUsageRing.tsx` (clamping + null visual). All changes are minimal and isolated to prevent regressions in the multi-tab chat system.

## Glossary

- **Bug_Condition (C)**: Three conditions that trigger incorrect ring behavior: (1) closing the last tab without resetting contextWarning, (2) pct > 100 causing negative strokeDashoffset, (3) pct === null rendering identically to low pct
- **Property (P)**: (1) contextWarning is null after close-last-tab, (2) fillPct is always in [0, 100], (3) null state has visually distinct SVG attributes
- **Preservation**: Tab switching, new session, 0–100% ring rendering, and tooltip behavior must remain unchanged
- **handleTabClose**: The callback in `ChatPage.tsx` (~line 465) that closes a tab, cleans up state, and auto-creates a fresh tab when the last one is closed
- **handleNewSession**: The callback in `ChatPage.tsx` (~line 335) that creates a new tab — correctly resets contextWarning to null
- **ContextUsageRing**: The SVG ring component in `ContextUsageRing.tsx` that renders a circular progress indicator for context window usage
- **tabMapRef**: The authoritative per-tab state store (React ref); React useState is a display mirror only
- **contextWarning**: Per-tab state containing `{ pct, level, message }` or null when no data exists

## Bug Details

### Bug Condition

The bugs manifest in three scenarios: (1) when the user closes the last open tab and the auto-created fresh tab inherits stale contextWarning state, (2) when the backend reports pct > 100 and the SVG stroke offset goes negative, (3) when pct is null and the gray ring is visually identical to a low-percentage green ring at 18px.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { action: string, pct: number | null, tabState: TabState, warningLevel: string | null }
  OUTPUT: boolean
  
  // Bug 1: Close-last-tab without contextWarning reset
  IF input.action == 'close-last-tab'
     AND input.tabState.contextWarning != null
     RETURN TRUE
  
  // Bug 2: SVG ring with unclamped pct > 100, pct < 0, or non-finite pct
  IF input.action == 'render-ring'
     AND input.pct != null
     AND (input.pct > 100 OR input.pct < 0 OR NOT isFinite(input.pct))
     RETURN TRUE
  
  // Bug 3: Null pct renders identically to low pct
  IF input.action == 'render-ring'
     AND input.pct == null
     RETURN TRUE
  
  // Bug 4: Redundant Toast for ok/warn levels
  IF input.action == 'show-toast'
     AND input.warningLevel IN ('ok', 'warn')
     RETURN TRUE
  
  RETURN FALSE
END FUNCTION
```

### Examples

- **Bug 1**: User has a tab with 72% context usage (amber ring). User closes that tab (the only open tab). Auto-created fresh tab still shows amber ring at 72% instead of gray "no data" ring.
- **Bug 2**: Backend reports pct = 115. `fillPct = 115`, `offset = circumference - 1.15 * circumference = -0.15 * circumference`. Negative offset causes SVG stroke to wrap past the full circle, rendering a broken visual.
- **Bug 3**: User opens a fresh tab (pct = null). Ring renders as solid gray circle. User starts a session with 1% usage. Ring renders as nearly-full gray circle with a tiny green sliver. At 18px, these two states are visually indistinguishable.
- **Edge case**: Backend reports pct = 0. Ring should show a fully gray background with no fill — distinct from null state which should have a dashed/dimmed appearance.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Tab switching must continue to restore the correct per-tab contextWarning from tabMapRef and display the corresponding ring color and percentage
- New session button must continue to reset contextWarning to null and show the gray ring (existing `handleNewSession` behavior)
- Ring must continue to render correct fill proportion and color thresholds for pct in [0, 100]: green < 70%, amber 70–84%, red >= 85%
- Tooltip must continue to show `"{pct}% context used"` for non-null values and `"No context data yet"` for null

**Scope:**
All inputs that do NOT involve the three bug conditions should be completely unaffected by this fix. This includes:
- Tab switching between tabs with active sessions
- Creating new sessions via the New Session button
- Normal ring rendering with pct in [0, 100]
- Tooltip hover behavior
- Mouse clicks and other non-close-last-tab tab operations

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **Missing state reset in close-last-tab path**: In `ChatPage.tsx` `handleTabClose` (line 465), the close-last-tab cleanup block (lines 493–499) resets `messages`, `sessionId`, and `pendingQuestion` but omits `setContextWarning(null)`, `setPendingPermission(null)`, and `setIsExpanded(false)`. Compare with `handleNewSession` (line 335) which resets `contextWarning` and `isExpanded` but also omits `pendingPermission`. Both paths should reset all transient state for a fresh tab. The `handleTabSelect` callback correctly resets `pendingPermission` on tab switch, but the close-last-tab and new-session paths do not.

2. **Unclamped percentage in SVG computation**: In `ContextUsageRing.tsx`, `fillPct = pct ?? 0` passes through any value from the backend without clamping. When pct > 100, `offset = circumference - (fillPct / 100) * circumference` produces a negative value, and SVG `strokeDashoffset` with a negative value causes the stroke to extend beyond the full circle. The fix is a simple `Math.min(Math.max(...), 100)` clamp.

3. **Identical rendering for null and zero states**: When `pct === null`, the component sets `fillPct = 0` and `strokeColor = 'var(--color-border)'`. This renders a background circle with a zero-length foreground stroke in the same border color — visually identical to a very low percentage green ring at 18px. The null state needs a distinct visual treatment (dashed stroke or reduced opacity on the background circle).

## Correctness Properties

Property 1: Bug Condition - Close-Last-Tab Resets Context Warning

_For any_ tab close action where the closed tab is the last open tab, the `handleTabClose` callback SHALL reset `contextWarning` to null, ensuring the auto-created fresh tab displays the "no data" gray ring state.

**Validates: Requirements 2.1**

Property 2: Bug Condition - Fill Percentage Clamped to Valid Range

_For any_ `pct` value passed to `ContextUsageRing` (including null, negative, zero, positive, values exceeding 100, NaN, and Infinity), the computed `fillPct` SHALL be clamped to the range [0, 100], and the resulting `strokeDashoffset` SHALL be a finite number in the range [0, circumference].

**Validates: Requirements 2.2**

Property 3: Bug Condition - Null State Visually Distinct

_For any_ render of `ContextUsageRing` where `pct === null`, the SVG output SHALL include visual attributes (dashed stroke pattern or reduced opacity) that are NOT present when `pct` is a number, making the null state distinguishable from low-usage states.

**Validates: Requirements 2.3**

Property 4: Preservation - Tab Switching Restores Correct State

_For any_ tab switch action between existing tabs with active sessions, the system SHALL continue to restore the correct per-tab `contextWarning` from `tabMapRef` and display the corresponding ring color and percentage, producing the same behavior as the original code.

**Validates: Requirements 3.1, 3.2**

Property 5: Preservation - Ring Rendering for Valid Percentages

_For any_ `pct` value in the range [0, 100], the `ContextUsageRing` SHALL produce the same fill proportion, color thresholds, and tooltip text as the original implementation.

**Validates: Requirements 3.3, 3.4**

Property 6: Bug Condition - Toast Only for Critical Level

_For any_ `context_warning` SSE event with level `ok` or `warn`, the system SHALL NOT display a Toast notification. Only `critical` level (≥85%) SHALL trigger a Toast.

**Validates: Requirements 2.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/pages/ChatPage.tsx`

**Function**: `handleTabClose`

**Specific Changes**:
1. **Add contextWarning reset**: In the close-last-tab cleanup block (around line 493–499), add `setContextWarning(null)` alongside the existing `setMessages([])`, `setSessionId(undefined)`, and `setPendingQuestion(null)` calls.
2. **Add pendingPermission reset**: Add `setPendingPermission(null)` in the same block — prevents a stale permission modal from appearing on the fresh tab.
3. **Add isExpanded reset**: Add `setIsExpanded(false)` in the same block — ensures the fresh tab starts in compact mode (matches `handleNewSession` behavior).
4. **Add dependency**: Add `setContextWarning`, `setPendingPermission` to the `useCallback` dependency array.

**Toast rendering block** (~line 1356):
5. **Remove redundant Toast for ok/warn levels**: Change the `{contextWarning && (` guard to `{contextWarning && contextWarning.level === 'critical' && (` so only critical-level warnings produce a Toast. The ring already communicates ok/warn states visually.

---

**File**: `desktop/src/pages/chat/components/ContextUsageRing.tsx`

**Function**: `ContextUsageRing`

**Specific Changes**:
1. **Clamp fillPct**: Replace `const fillPct = pct ?? 0` with `const fillPct = Math.min(Math.max(Number.isFinite(pct) ? pct! : 0, 0), 100)` to ensure the value is always in [0, 100] even for NaN or Infinity inputs.
2. **Add null-state visual distinction**: When `pct === null`, apply a dashed stroke pattern (`strokeDasharray` with a dash pattern like `"2 2"`) and reduced opacity (e.g., `opacity: 0.5`) to the background circle to visually differentiate it from the zero/low-usage state.
3. **Conditional rendering**: Use `pct === null` to conditionally set the background circle's `strokeDasharray` and `opacity` attributes, leaving the foreground circle unchanged.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write unit tests that exercise each bug condition on the unfixed code to observe failures and confirm root causes.

**Test Cases**:
1. **Close-Last-Tab Stale State**: Render ChatPage, set contextWarning to a non-null value, simulate closing the last tab, assert contextWarning is still non-null (will fail to reset on unfixed code)
2. **SVG Overflow at pct=150**: Render ContextUsageRing with pct=150, inspect strokeDashoffset — expect negative value on unfixed code
3. **SVG Overflow at pct=-10**: Render ContextUsageRing with pct=-10, inspect strokeDashoffset — expect value > circumference on unfixed code
4. **Null vs Zero Indistinguishable**: Render ContextUsageRing with pct=null and pct=0, compare SVG attributes — expect identical on unfixed code

**Expected Counterexamples**:
- Close-last-tab leaves contextWarning with stale `{ pct, level, message }` object
- strokeDashoffset is negative when pct > 100
- Null and zero states produce identical SVG output

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FUNCTION expectedBehavior(input, result)
  INPUT: input of type { action, pct, tabState }, result of type { contextWarning, fillPct, svgAttrs }
  OUTPUT: boolean

  // Bug 1: contextWarning must be null after close-last-tab
  IF input.action == 'close-last-tab'
     ASSERT result.contextWarning == null

  // Bug 2: fillPct must be clamped
  IF input.pct != null AND (input.pct > 100 OR input.pct < 0)
     ASSERT result.fillPct >= 0 AND result.fillPct <= 100
     ASSERT result.strokeDashoffset >= 0

  // Bug 3: null state must have distinct visual attributes
  IF input.pct == null
     ASSERT result.svgAttrs.backgroundStrokeDasharray != null  // dashed pattern
     ASSERT result.svgAttrs.backgroundOpacity < 1.0

  RETURN TRUE
END FUNCTION
```

```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedFunction(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many pct values in [0, 100] automatically to verify ring rendering is unchanged
- It catches edge cases at boundaries (0, 69, 70, 84, 85, 100) that manual tests might miss
- It provides strong guarantees that color thresholds and fill proportions are preserved

**Test Plan**: Observe behavior on UNFIXED code first for normal pct values and tab operations, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Ring Rendering Preservation**: For any pct in [0, 100], verify fillPct, strokeDashoffset, strokeColor, and tooltip text match the original implementation
2. **Color Threshold Preservation**: For pct values at boundaries (0, 69, 70, 84, 85, 100), verify correct color assignment
3. **Tab Switch Preservation**: Verify switching between tabs with different contextWarning values correctly restores each tab's ring state
4. **New Session Preservation**: Verify creating a new session still resets contextWarning to null

### Unit Tests

- Test `ContextUsageRing` renders correct SVG attributes for pct = null, 0, 50, 70, 85, 100
- Test `ContextUsageRing` clamps pct values outside [0, 100] (e.g., -10, 150, 200)
- Test null state has dashed stroke and reduced opacity
- Test close-last-tab resets contextWarning to null
- Test tooltip text for null and non-null pct values

### Property-Based Tests

- Generate random pct values in [0, 100] and verify strokeDashoffset is in [0, circumference] and color matches thresholds
- Generate random pct values across full number range and verify fillPct is always clamped to [0, 100]
- Generate random pct values and verify null state SVG attributes are always distinct from non-null state attributes

### Integration Tests

- Test full tab lifecycle: create tab → start session → receive contextWarning → close tab → verify fresh tab has null contextWarning
- Test tab switching with multiple tabs having different contextWarning values
- Test ring updates correctly as backend streams context usage updates during a session
