# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Context Ring SVG Clamping and Null State Bugs
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate all three bugs exist
  - **Scoped PBT Approach**: Scope the property to concrete failing cases for each bug:
    - Bug 1 (close-last-tab): Render ChatPage, set contextWarning to non-null, simulate closing the last tab, assert contextWarning is reset to null. On unfixed code, contextWarning remains stale — test FAILS.
    - Bug 2 (SVG overflow): Render ContextUsageRing with pct values > 100 (e.g. 115, 150, 200) and < 0 (e.g. -10), assert fillPct is clamped to [0, 100] and strokeDashoffset is in [0, circumference]. On unfixed code, fillPct passes through unclamped — test FAILS.
    - Bug 3 (null indistinguishable): Render ContextUsageRing with pct=null and pct=0, assert the SVG attributes differ (null state has dashed stroke or reduced opacity). On unfixed code, both render identically — test FAILS.
  - Create test file: `desktop/src/pages/chat/components/__tests__/ContextUsageRing.bugfix.test.tsx`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bugs exist)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Ring Rendering and Color Thresholds for Valid Percentages
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code: ContextUsageRing with pct in [0, 100] renders correct fill proportion and color thresholds
  - Observe: pct=0 → fillPct=0, strokeColor=#10b981 (green), offset=circumference
  - Observe: pct=50 → fillPct=50, strokeColor=#10b981 (green), offset=circumference/2
  - Observe: pct=70 → fillPct=70, strokeColor=#f59e0b (amber), offset=0.3*circumference
  - Observe: pct=85 → fillPct=85, strokeColor=#ef4444 (red), offset=0.15*circumference
  - Observe: pct=100 → fillPct=100, strokeColor=#ef4444 (red), offset=0
  - Observe: tooltip shows "{pct}% context used" for non-null, "No context data yet" for null
  - Write property-based tests using fast-check:
    - For all pct in [0, 100]: strokeDashoffset is in [0, circumference] and equals circumference - (pct/100)*circumference
    - For all pct in [0, 70): strokeColor is #10b981 (green)
    - For all pct in [70, 85): strokeColor is #f59e0b (amber)
    - For all pct in [85, 100]: strokeColor is #ef4444 (red)
    - For all non-null pct: tooltip text is "{pct}% context used"
    - For null pct: tooltip text is "No context data yet"
  - Create test file: `desktop/src/pages/chat/components/__tests__/ContextUsageRing.preservation.test.tsx`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Fix Context Monitor Ring UX bugs

  - [x] 3.1 Add full state reset in close-last-tab cleanup
    - In `desktop/src/pages/ChatPage.tsx`, function `handleTabClose` (~line 465)
    - In the close-last-tab cleanup block (~line 493-499), add all missing resets alongside existing `setMessages([])`, `setSessionId(undefined)`, `setPendingQuestion(null)`:
      - `setContextWarning(null)` — prevents stale ring on fresh tab
      - `setPendingPermission(null)` — prevents stale permission modal on fresh tab
      - `setIsExpanded(false)` — ensures fresh tab starts in compact mode
    - Add `setContextWarning`, `setPendingPermission` to the `useCallback` dependency array
    - _PE Review Finding: 🔴 High — handleNewSession resets all 6 fields but handleTabClose only resets 3_
    - _Requirements: 2.1, 3.1, 3.2_

  - [x] 3.2 Remove redundant Toast for ok/warn context warnings
    - In `desktop/src/pages/ChatPage.tsx`, Toast rendering block (~line 1356)
    - Change `{contextWarning && (` to `{contextWarning && contextWarning.level === 'critical' && (` so only critical-level warnings produce a Toast
    - The ring already communicates ok/warn states visually (color + fill); only critical (≥85%) needs an actionable Toast
    - _Requirements: 2.4_

  - [x] 3.3 Clamp fillPct to [0, 100] in ContextUsageRing (with NaN/Infinity safety)
    - In `desktop/src/pages/chat/components/ContextUsageRing.tsx`
    - Replace `const fillPct = pct ?? 0` with `const fillPct = Math.min(Math.max(Number.isFinite(pct) ? pct! : 0, 0), 100)`
    - This ensures strokeDashoffset is always a finite number in [0, circumference] regardless of backend-reported pct, including NaN and Infinity edge cases
    - _PE Review Finding: 🔴 High — Math.min(Math.max(NaN, 0), 100) returns NaN, producing NaN strokeDashoffset_
    - _Requirements: 2.2, 3.3_

  - [x] 3.4 Add visual distinction for null pct state
    - In `desktop/src/pages/chat/components/ContextUsageRing.tsx`
    - When `pct === null`, apply dashed stroke pattern (`strokeDasharray="2 2"`) and reduced opacity (`opacity={0.5}`) to the background circle
    - Use conditional: `pct === null ? { strokeDasharray: "2 2", opacity: 0.5 } : {}` on the background `<circle>`
    - Leave foreground circle unchanged — it already has zero-length stroke when fillPct=0
    - _Bug_Condition: isBugCondition(input) where input.pct == null_
    - _Expected_Behavior: result.svgAttrs.backgroundStrokeDasharray != null AND result.svgAttrs.backgroundOpacity < 1.0_
    - _Preservation: Non-null pct rendering is unchanged — background circle has no dash/opacity attrs_
    - _Requirements: 2.3, 3.3_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Context Ring SVG Clamping and Null State Bugs
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior for all three bugs
    - When this test passes, it confirms:
      - contextWarning is null after close-last-tab
      - fillPct is clamped to [0, 100] for all inputs (including NaN/Infinity)
      - null state has visually distinct SVG attributes (dashed stroke, reduced opacity)
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms all three bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** - Ring Rendering and Color Thresholds for Valid Percentages
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all property-based tests still pass: fill proportions, color thresholds, tooltip text
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd desktop && npm test -- --run`
  - Ensure all bug condition tests pass (task 1 tests now green)
  - Ensure all preservation tests pass (task 2 tests still green)
  - Ensure no other existing tests are broken
  - Ask the user if questions arise
