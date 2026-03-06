<!-- PE-REVIEWED -->
# App Restart Chat Layout Collapse — Bugfix Design

## Overview

During Tauri webview reload/restart cycles, `desktop/src/main.tsx` re-executes and calls `createRoot()` without any idempotency guard. This mounts a new React tree into `#root` on every reload without clearing the previous one, causing 10+ duplicate `h-screen` app trees to stack vertically in the DOM. The visible viewport shows only a fraction of the stacked content, making it appear as if Chat Tabs and the Chat Message area have disappeared.

The fix adds an idempotency guard to `main.tsx` that clears `#root`'s children before calling `createRoot`, ensuring exactly one React tree exists at all times.

## Glossary

- **Bug_Condition (C)**: The `#root` element already contains child nodes when the entry script executes (i.e., a previous React tree was mounted and not cleaned up)
- **Property (P)**: After the entry script executes, `#root` contains exactly one React-managed subtree, and the layout renders correctly in a single `h-screen` container
- **Preservation**: Cold-start mounting, normal runtime behavior, tab restore flow, BackendStartupOverlay sequencing, and React StrictMode compatibility must remain unchanged
- **`main.tsx`**: The React entry point in `desktop/src/main.tsx` that calls `createRoot` and renders `<App />` inside `<StrictMode>`
- **`ThreeColumnLayout`**: The layout component wrapping all routes; its root div uses `h-screen` which causes the visual stacking when duplicated
- **Webview reload**: Tauri re-executes the frontend entry script without a full page teardown, leaving prior DOM content intact

## Bug Details

### Fault Condition

The bug manifests when the Tauri webview reloads or the app restarts. The entry script in `desktop/src/main.tsx` calls `createRoot(document.getElementById('root')!)` unconditionally. If the `#root` element already contains a previously mounted React tree (from a prior script execution), a new React root is created and appended alongside the old one. Each root renders the full `<App />` component tree including `ThreeColumnLayout` with its `h-screen` container, causing vertical stacking that pushes content off-screen.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { rootElement: HTMLElement, scriptExecution: "initial" | "reload" }
  OUTPUT: boolean

  RETURN input.scriptExecution == "reload"
         AND input.rootElement.childNodes.length > 0
         AND NOT previousRootUnmounted(input.rootElement)
END FUNCTION
```

### Examples

- **Reload once**: User triggers app restart → `#root` now contains 2 full app trees → layout appears compressed, tabs partially visible
- **Reload 5 times**: `#root` contains 6 stacked app trees, each 100vh tall → visible viewport shows only the bottom portion of the last tree (input box area), tabs and messages are scrolled far above
- **Reload 10+ times**: `#root` contains 10+ trees → total DOM height is 10×100vh → Chat Tabs and Chat Message area are completely off-screen, only the input box and right sidebar fragments remain visible
- **Cold start (no bug)**: Fresh app launch → `#root` is empty → single tree mounts correctly → layout renders as expected

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Cold start (first mount into empty `#root`) must continue to work identically
- Normal runtime with a single React root must be unaffected
- Tab state restoration via `doRestore` → `restoreFromFile` → `loadSessionMessages` must continue working
- `BackendStartupOverlay` must still gate route mounting in production until backend is ready
- React StrictMode double-invocation of effects/renders in development must not be affected
- All existing routes, modals, and the three-column layout must render correctly

**Scope:**
All inputs that do NOT involve a webview reload with pre-existing DOM children in `#root` should be completely unaffected by this fix. This includes:
- First-time app launch (cold start)
- Normal in-app navigation between routes
- React re-renders triggered by state changes
- React StrictMode double-mounting in development

## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is confirmed:

1. **No idempotency guard in `main.tsx`**: The entry script calls `createRoot(document.getElementById('root')!).render(...)` unconditionally. There is no check for whether `#root` already has children, no cleanup of previous content, and no reuse of an existing React root. This is the direct cause.

2. **Tauri webview reload behavior**: Unlike a full browser page reload (which clears the DOM), Tauri's webview reload/restart cycle re-executes the entry script while potentially preserving the existing DOM state. This means `#root` retains its children from the previous mount.

3. **`h-screen` stacking amplifies the visual impact**: Each duplicate `<ThreeColumnLayoutInner>` renders with `className="flex flex-col h-screen bg-[var(--color-bg)]"`. Multiple `h-screen` containers stack vertically, making the total document height N×100vh. This pushes the visible content far below the viewport fold.

4. **No unmount/cleanup on webview teardown**: There is no `beforeunload` or Tauri event listener that unmounts the React root before the webview reloads, so stale trees persist.

## Correctness Properties

Property 1: Fault Condition - Single React Root After Reload

_For any_ webview reload where the `#root` element already contains child nodes from a previous mount, the fixed entry script SHALL clear those children and mount exactly one new React tree, so that `document.getElementById('root').children.length` (Element children, not raw childNodes) equals 1 after mounting completes, and the document contains exactly one element with class `h-screen`.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Cold Start Mounting

_For any_ cold start where the `#root` element is empty (no prior children), the fixed entry script SHALL mount the React app identically to the original code, producing a single React tree with the full `<StrictMode><App /></StrictMode>` component hierarchy and correct three-column layout.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Stale Async Isolation

_For any_ webview reload, the newly mounted React tree SHALL operate independently of any prior mount's async state. Since Tauri webview reload creates a fresh JS execution context, no stale timers, fetch calls, or subscriptions from the previous execution context SHALL interfere with the new mount. The fix SHALL only need to address stale DOM nodes (via `replaceChildren()`), not JS-level cleanup.

**Validates: Requirements 2.1, 3.2**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/main.tsx`

**Function**: Top-level module script (entry point)

**Specific Changes**:

1. **Clear `#root` children before mounting**: Before calling `createRoot`, check if the `#root` element has existing children. If it does, clear them with `rootElement.replaceChildren()` to remove any stale React trees from a previous webview execution. Log a `console.warn` when this guard fires for observability.

2. **Guard the root element lookup**: Extract `document.getElementById('root')` into a variable with a null check, rather than using the non-null assertion `!` directly in the `createRoot` call. This improves robustness.

3. **Preserve existing behavior**: The `StrictMode` wrapper, `App` component, and all imports must remain unchanged. The only addition is the cleanup guard before `createRoot`.

**Proposed implementation sketch:**
```typescript
const rootElement = document.getElementById('root');
if (rootElement) {
  // Idempotency guard: clear any stale React trees from prior webview executions.
  // Using replaceChildren() instead of innerHTML='' because:
  // 1. It avoids innerHTML's HTML parser overhead
  // 2. It's the modern DOM API (supported in all Chromium-based Tauri webviews)
  // 3. It cleanly removes all child nodes in one atomic operation
  if (rootElement.hasChildNodes()) {
    console.warn('[main] Stale React tree detected in #root — clearing before remount (Tauri webview reload)');
    rootElement.replaceChildren();
  }
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
} else {
  console.error('[main] Fatal: #root element not found in document — app cannot mount');
}
```

**Important considerations for stale async cleanup:**
- When `replaceChildren()` removes the old DOM tree, React's internal fiber tree for the previous root becomes orphaned. Any pending `useEffect` cleanup functions, `QueryClient` refetches, or `BrowserRouter` listeners from the old tree will NOT be properly unmounted.
- However, since Tauri's webview reload re-executes the entire script context (new JS execution context), all old closures, timers, and references from the previous execution are garbage-collected by the JS engine. The stale DOM nodes are the only artifact that persists across reloads — the JS runtime state does not.
- Therefore, `replaceChildren()` is sufficient: it removes the stale DOM, and the JS engine handles cleanup of the old execution context's async operations.

This is a minimal, surgical change that addresses the root cause without touching any other files or components.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that the mount function does not clear pre-existing children in `#root`.

**Test Plan**: Write tests using vitest + jsdom that test the DOM cleanup guard logic directly — pre-populate `#root` with child nodes, then execute the guard + mount logic. Focus on testing the idempotency guard behavior (DOM cleanup), not full React tree rendering (which differs between jsdom and Chromium).

**Test Cases**:
1. **Reload with existing children**: Set `#root.innerHTML` to mock content, then call the mount function — verify the guard clears children before mounting (will fail on unfixed code because no guard exists)
2. **Multiple reloads**: Execute the mount function multiple times — verify `#root.children.length` stays at 1 (will fail on unfixed code)
3. **Console warning emission**: Verify that `console.warn` is called when stale children are detected and cleared

**Expected Counterexamples**:
- `#root.childNodes.length > 1` after a simulated reload
- Multiple `h-screen` containers visible in the DOM tree
- Possible cause: `createRoot` always appends a new container rather than replacing existing content

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds (reload with existing children), the fixed function produces exactly one React tree.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := mountApp_fixed(input.rootElement)
  ASSERT input.rootElement.childNodes.length == 1
  ASSERT result contains exactly one App component tree
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (cold start, empty `#root`), the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT mountApp_original(input.rootElement) == mountApp_fixed(input.rootElement)
  ASSERT input.rootElement.childNodes.length == 1
END FOR
```

**Testing Approach**: Property-based testing with fast-check is recommended for preservation checking because:
- It can generate varied initial DOM states (empty root, root with whitespace, root with comments) to verify the guard handles all clean-start scenarios
- It catches edge cases like text nodes or comment nodes that might be present in `#root`
- It provides strong guarantees that cold-start behavior is unchanged

**Test Plan**: Observe behavior on UNFIXED code first for cold-start scenarios, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Cold start preservation**: Verify that mounting into an empty `#root` produces identical results with both original and fixed code
2. **StrictMode preservation**: Verify that React StrictMode double-invocation still works correctly after the fix
3. **Root element content preservation**: Verify that the final mounted tree structure is identical regardless of whether the guard cleared children or not

### Unit Tests

- Test that `#root` is cleared before mounting when it has existing children
- Test that `#root` with no children mounts normally (cold start path)
- Test that exactly one child exists in `#root` after mounting in all scenarios
- Test that the guard handles edge cases: `#root` with text nodes, comment nodes, or whitespace

### Property-Based Tests

- Generate random pre-existing DOM states for `#root` (varying numbers of child elements, text nodes, comments) and verify the fixed mount guard always results in an empty `#root` before `createRoot` is called
- Generate cold-start scenarios with empty `#root` and verify the guard is a no-op (no `replaceChildren()` call, no console.warn)
- Test that repeated mount calls (simulating multiple reloads) always result in exactly 1 Element child in `#root` (using `children.length`, not `childNodes.length`)

### Integration Tests

- Test full app render after simulated reload: verify `ThreeColumnLayout` renders once with correct `h-screen` container
- Test that tab restoration works correctly after the idempotency guard clears and remounts
- Test that `BackendStartupOverlay` sequencing is preserved when mounting after a reload
