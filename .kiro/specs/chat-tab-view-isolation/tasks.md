# Implementation Plan: Chat Tab View Isolation

## Overview

This plan implements keep-mounted per-tab message views (`TabView`) that subscribe
directly to their own `MessageStore`, eliminating the shared swapped `messages`
mirror as the authoritative display source. It follows the design's amended
Migration Strategy exactly: a **blocking Step 0** that exercises the dead
`useMessageStore` primitive on a live streaming path (F7) before any ChatPage
pivot, then Steps 1–7.

Implementation language is **TypeScript / React 19** (the existing `desktop/`
frontend stack). This is a frontend-only change inside `desktop/` — no backend
changes. Every step is a separate commit and keeps the app working.

Targeted verification commands (NEVER the full suite):

- Tests: `cd desktop && npm test -- --run <path>`
- Types: `cd desktop && npx tsc --noEmit`
- Final build: `cd desktop && npm run build:all`

Property tests use **fast-check + @testing-library/react + Vitest**, minimum
**100 iterations** per property (`fc.assert(..., { numRuns: 100 })`), each tagged
`// Feature: chat-tab-view-isolation, Property N`.

Preserved isolation invariants (must hold at every step): `tabMapRef` is
authoritative; per-tab `sessionId` for backend calls; `setIsStreaming(value, tabId)`
is tab-scoped; stream handlers write to `capturedTabId`'s store.

## Tasks

- [x] 1. Step 0 (F7, BLOCKING) — Exercise `useMessageStore` before any ChatPage change
  - `useMessageStore` is dead code (zero component callers; only the definition,
    barrel re-export, and docstrings). De-risk betting the rewrite on an unexecuted
    primitive. NO ChatPage display change in this task. All later tasks depend on
    this passing.

  - [x] 1.1 Add a non-display parity observer for `useMessageStore(activeTabId)` in ChatPage
    - In `desktop/src/pages/ChatPage.tsx`, mount `useMessageStore(activeTabId)`
      purely as an observer: subscribe, compare its snapshot against the current
      hand-rolled bridge `messages` during streaming, and `console.warn` on any
      divergence. Do NOT change what the rendered list reads from.
    - Guard so the observer is inert when there is no active store (destroyed/null).
    - _Requirements: 2.1, 2.4_ (validates the store subscription primitive the
      design pivots onto; closes F7)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [x] 1.2 Write live-streaming integration test for `useMessageStore`
    - Create `desktop/src/stores/__tests__/useMessageStore.integration.test.tsx`.
    - Mount `useMessageStore(tabId)` against a live `MessageStore` and drive a full
      sequence: `append` → `updateLast`×N → `endStreaming` → `reconcile` →
      `destroy`. Assert the hook's `messages` tracks the store across the rAF flush
      and the 100 ms `setTimeout` fallback path, and tolerates `destroy()` (returns
      `[]`, no throw).
    - This is the blocking gate artifact for Step 0; it MUST pass before Step 1.
    - _Requirements: 2.1, 2.4, 5.3, 11.3_
    - _Verify: `cd desktop && npm test -- --run src/stores/__tests__/useMessageStore.integration.test.tsx`_

- [ ] 2. Step 1 — Extract `TabView` from ChatPage's inline list (no behavior change)
  - [ ] 2.1 Create `TabView` component receiving `messages` as a prop
    - Create `desktop/src/pages/chat/components/TabView.tsx`. Move ChatPage's inline
      scroll `<div>`, "Load earlier" control, `WelcomeScreen` empty-state, the
      `MessageBubble[]` map, streaming indicators, and `messagesEndRef` anchor into
      it. For this step `TabView` still takes `messages` as a prop and renders
      identical markup. Add the `/** */` module docstring (file purpose + exports).
    - _Requirements: 1.1, 2.1_ (structural extraction; behavior parity)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 2.2 Render a single active `TabView` from ChatPage
    - In `ChatPage.tsx`, replace the inline message list with one `<TabView>` for
      the active tab, passing the existing `messages` and stable callbacks. No
      visual change for the single active tab.
    - _Requirements: 1.1, 1.2_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 2.3 Write parity test for extracted `TabView`
    - Create `desktop/src/pages/chat/components/__tests__/TabView.parity.test.tsx`.
      Assert `TabView` renders the same message bubbles / WelcomeScreen as the prior
      inline list for a given `messages` prop (active-tab visual parity).
    - _Requirements: 1.1, 1.2_
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/components/__tests__/TabView.parity.test.tsx`_

- [ ] 3. Step 2 — Bind `TabView` to its own store + per-tab streaming activity (F4)
  - [ ] 3.1 Switch `TabView` to `useMessageStore(tabId)` and internalize per-tab derived values
    - In `TabView.tsx`, replace the `messages` prop source with
      `useMessageStore(tabId)` (authoritative per-tab source). Move
      `lastAssistantIdx`, `lastResumeBoundaryIdx`, the scroll container/`endRef`
      refs, and the streaming indicators into `TabView`, computed over this tab's
      store messages. ChatPage still renders only the active `TabView`.
    - _Requirements: 2.1, 2.4, 4.1_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 3.2 Add `useStreamingActivity(tabId)` gated to streaming (F4)
    - Create `desktop/src/hooks/useStreamingActivity.ts` computing `displayedActivity`
      + `elapsedSeconds` ONLY while that tab's `isStreaming` is true (no timer/debounce
      for idle/background tabs — avoids N concurrent timers). Make `messagesEndRef`
      and `userScrolledUpRef` per-`TabView` local refs. Wire `TabView` to use it.
    - _Requirements: 7.1, 8.1, 8.2_ (closes F4)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 3.3 Write property test for streaming hot path scope
    - Create `desktop/src/pages/chat/components/__tests__/TabView.hotpath.property.test.tsx`.
    - **Property 6: Streaming hot path stays scoped to the streaming bubble**
    - **Validates: Requirements 8.1, 8.2** — assert only the streaming `MessageBubble`
      re-renders on `updateLast`; historical bubble render counts stay constant.
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/components/__tests__/TabView.hotpath.property.test.tsx`_

  - [ ]* 3.4 Write property test for eventual consistency (settled view == snapshot)
    - **Property 8: Eventual consistency — settled view equals store snapshot**
    - **Validates: Requirements 5.3, 6.4, 10.4, 13.2** — for arbitrary op sequences
      then a notification flush, rendered messages deep-equal `store.getSnapshot()`.
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/components/__tests__/TabView.consistency.property.test.tsx`_

- [ ] 4. Step 3 — Render N keep-mounted `TabView`s with mount-on-first-activation (F2)
  - [ ] 4.1 Map over `openTabs` to render one `TabView` per tab, toggling visibility
    - In `ChatPage.tsx`, render `openTabs.map(tab => <TabView .../>)`, set
      `isActive={tab.id === activeTabId}`, toggle `display:flex|none` + `aria-hidden`.
      Pass per-tab props from `tabMapRef` (`sessionId`, `isStreaming`,
      `pendingQuestion`, `pendingPermissionRequestId`, `contextWarning`). Wrap
      `TabView` in `React.memo`.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.1, 7.3_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 4.2 Add `everActivated` set + placeholder (mount-on-first-activation, F2)
    - Maintain `everActivated: Set<tabId>` (in ChatPage or `useUnifiedTabState`). A
      `TabView` whose tab is not yet in the set renders a lightweight placeholder
      (no message list, no markdown parse). On first activation add the tab and
      mount real content, keep-mounted thereafter. Implement `onFirstActivate` flow
      in `TabView` via a `didActivateRef`.
    - _Requirements: 1.3, 1.5, 3.1_ (closes F2; avoids startup N× parse storm)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 4.3 Remove the `sync-active-tab` swap effect
    - Delete the `setMessages([...activeTabState.messages])` swap effect in
      `ChatPage.tsx` (the root cause of remount/re-parse). Background `TabView`s now
      update their own hidden DOM via their own store subscription.
    - _Requirements: 1.4, 1.5, 2.2, 3.4_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 4.4 Write property tests for view count, visibility, and non-destructive switch
    - Create `desktop/src/pages/chat/__tests__/TabViewList.structure.property.test.tsx`.
    - **Property 1: One mounted view per open tab, within the tab budget** —
      **Validates: Requirements 1.1, 10.1, 12.1, 12.2**
    - **Property 2: Exactly one visible view, the rest mounted and hidden** —
      **Validates: Requirements 1.2, 1.3**
    - **Property 3: Tab switch is non-destructive (no remount, no re-parse)** —
      scoped to tabs activated at least once (F2). **Validates: Requirements 1.4, 1.5, 3.1, 3.4**
    - **Property 5: Per-tab render isolation** — **Validates: Requirements 2.4, 8.3**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabViewList.structure.property.test.tsx`_

  - [ ]* 4.5 Write property tests for cross-tab isolation and concurrent streaming
    - Create `desktop/src/pages/chat/__tests__/TabViewList.isolation.property.test.tsx`.
    - **Property 4: Cross-tab content isolation** (incl. background streaming append)
      — **Validates: Requirements 2.1, 2.5, 4.1, 4.2, 4.3, 4.4, 5.2, 5.4, 14.2**
    - **Property 16: Concurrent multi-tab streaming stays isolated** —
      **Validates: Requirements 14.4**
    - **Property 10: Streaming status is per-tab and authoritative from tabMapRef** —
      **Validates: Requirements 7.1, 7.3**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabViewList.isolation.property.test.tsx`_

  - [ ]* 4.6 Write tab-switch performance + concurrent-streaming commit-budget tests
    - Create `desktop/src/pages/chat/__tests__/TabSwitch.perf.test.tsx`. Render two
      tabs (10 and 200 messages); switch and assert (a) switch latency under the
      100 ms ceiling and (b) it is independent of destination message count (no
      `TabView` unmount/remount). Add a 3–4 tab concurrent-streaming smoke asserting
      the active tab's per-token commit stays within budget.
    - _Requirements: 3.2, 3.3, 8.3_
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabSwitch.perf.test.tsx`_

- [ ] 5. Step 4 — Demote shared `messages`; remove dead hot-path fallback; F1 voice bridge
  - [ ] 5.1 Establish the store-exists-at-stream-start invariant (F3)
    - In `useChatStreamingLifecycle.ts` / `ChatPage.tsx`, assert (and log if violated)
      that `insertOptimisticMessages → getOrCreate(capturedTabId)` has run on send so
      the captured tab's store is non-null at stream start. This must hold before the
      dead `else` branch is removed in 5.2.
    - _Requirements: 6.1, 6.4_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 5.2 Remove the provably-dead hot-path `else` fallback branch WHOLESALE (F3)
    - In `useChatStreamingLifecycle.ts` (`~1888-1959`, the `text_delta` /
      `thinking_delta` / `assistant` handlers), remove the entire
      `else { ... setMessages(...) }` fallback branch (NOT just the `setMessages`
      line — leaving a branchless `if` with no fallback would silently blank content
      if the store is ever null). Keep the `if (textStore) textStore.updateLast(...)`
      path; add an assertion/log if the store is unexpectedly null at stream time.
    - _Requirements: 4.4, 6.3, 8.1, 8.3, 14.2_ (closes F3)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 5.3 Replace the store→React bridge with the F1 minimal voice subscription
    - Remove the hand-rolled bridge effect (`~1034-1073`). Add a minimal,
      explicitly NON-authoritative active-tab subscription (re-subscribes on
      `activeTabId` change to `messageStoreRegistry.get(activeTabId)`) that derives
      ONLY `latestTextContent` for voice TTS (`ChatPage.tsx:289/305/316`). It is read
      by no `TabView` and is never an authoritative display source (Req 2.3/2.5).
    - _Requirements: 2.3, 2.5, 13.3_ (closes F1)
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 5.4 Delete remaining active-tab mirror writes per the F3 bucket table
    - Remove the auq/perm/error/evolution/recovery active-mirror `setMessages` calls
      (hook `2003`, `2075`, `2607`, `3140`, `2855`, `2131`, `2391`, `2478`, `1565`,
      `1620`; ChatPage `459`, `672`, `728`) — these already write the store/tabState,
      so the `TabView` follows the store. Repoint recovery/restore writes to the
      **target tab's** `store.replace`. Keep `tabState.messages` cache for first-paint.
    - _Requirements: 2.2, 6.3, 14.1, 14.2, 13.2_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 5.5 Write property test for hot-path no active-tab mirror write
    - Create `desktop/src/hooks/__tests__/streaming.hotpath.property.test.tsx`.
    - **Property 17: Streaming hot path performs no active-tab mirror write** —
      structural (no `setMessages` in the delta/assistant branches) + behavioral
      (active tab does not re-render from a background tab's tokens).
      **Validates: Requirements 4.4, 8.3, 14.2** (closes F3)
    - _Verify: `cd desktop && npm test -- --run src/hooks/__tests__/streaming.hotpath.property.test.tsx`_

  - [ ]* 5.6 Write property test for live voice text during active-tab streaming
    - **Property 18: Voice text stays live during active-tab streaming** —
      `latestTextContent` updates monotonically with streamed assistant text after
      the bridge is demoted. **Validates: Requirements 13.x compatibility** (closes F1)
    - _Verify: `cd desktop && npm test -- --run src/hooks/__tests__/voice.live.property.test.tsx`_

  - [ ]* 5.7 Write NEGATIVE property test for store-null at stream time
    - **Property 19: Store-null at stream time still renders (no blank), never leaks**
      — where the captured tab's store is unexpectedly null, content still renders
      via the preserved path into that tab, the view never blanks, and no sibling
      view is written. **Validates: Requirements 6.4, 14.2** (guards F3)
    - _Verify: `cd desktop && npm test -- --run src/hooks/__tests__/storeNull.negative.property.test.tsx`_

  - [ ]* 5.8 Write NEGATIVE property test for destroy() during an active stream
    - **Property 20: destroy() during an active stream — no throw, no sibling leak**
      — destroying a mid-stream store throws nothing, the write becomes a guarded
      no-op (`_destroyed`), and no content appears in any sibling tab's `TabView`.
      **Validates: Requirements 11.x, 14.2**
    - _Verify: `cd desktop && npm test -- --run src/hooks/__tests__/destroyDuringStream.negative.property.test.tsx`_

- [ ] 6. Step 5 — Per-tab pagination + scroll cleanup (native DOM preservation)
  - [ ] 6.1 Move pagination state per tab and add `handleLoadOlder(tabId)`
    - Add `hasMoreMessages?` / `isLoadingOlderMessages?` to `UnifiedTab` in
      `useUnifiedTabState.ts` (or a parallel `Map<tabId, …>`). Replace the single
      ChatPage `loadOlderMessages` with `handleLoadOlder(tabId)` that prepends via
      the tab's store (`store.replace(mergeOlderMessages(...))` or `store.prepend`).
      Make `onLoadOlder` a `useCallback` (F5 stable prop).
    - _Requirements: 9.x, 10.4_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ] 6.2 Remove switch-time scroll save/restore; preserve scroll natively per `TabView`
    - In `TabView.tsx`, keep per-view auto-scroll-to-bottom (gated by that view's
      own `userScrolledUpRef`) and per-view prepend scroll preservation. Remove the
      switch-time `scrollPosition` save/restore in `ChatPage.tsx` (kept-mounted DOM
      preserves `scrollTop` natively). Retain `scrollPosition` as persistence-only
      for restart restore.
    - _Requirements: 9.1, 9.5_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 6.3 Write property test for per-tab UI state round-trip
    - **Property 11: Per-tab UI state round-trips and never crosses tabs** —
      scroll position, draft input, pending question, pending permission restore to
      the correct tab and never apply to another. **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/perTabUiState.property.test.tsx`_

- [ ] 7. Step 6 — Lazy-load wiring (first-activation, phase-gated)
  - [ ] 7.1 Wire `onFirstActivate` → `loadSessionMessages` into the tab's own store
    - In `ChatPage.tsx`, implement `handleTabFirstActivate(tabId)` (a `useCallback`,
      F5 stable prop) that, for a restored tab with a `sessionId` and an empty store,
      calls the existing `loadSessionMessages` targeting **that tab's** store
      (`store.replace(formatted)`), phase-gated (skip/queue while `isStreaming`).
      Keep seeding the per-tab `tabState.messages` cache + `sessionId`/`hasMoreMessages`.
    - _Requirements: 10.1, 10.2, 10.3, 10.4_
    - _Verify: `cd desktop && npx tsc --noEmit`_

  - [ ]* 7.2 Write property test for first-activation lazy load
    - **Property 12: First activation lazily loads into the tab's own store** —
      load invoked exactly once for that tab's `sessionId`; results populate that
      tab's store. **Validates: Requirements 10.2**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/lazyLoad.firstActivate.property.test.tsx`_

  - [ ]* 7.3 Write property test for phase-gated lazy load
    - **Property 13: Lazy load is phase-gated and never overwrites in-flight content**
      — during `'streaming'` phase a lazy load does not overwrite store messages
      (queued for post-stream drain). **Validates: Requirements 10.3**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/lazyLoad.phaseGate.property.test.tsx`_

- [ ] 8. Step 7 — Grep-bound audit + remaining isolation/compat properties (F5)
  - [ ] 8.1 Run the grep-bound migration audit gate
    - Run `grep -rn "setMessages(" desktop/src` and confirm **zero
      authoritative-display writes** remain; the only permitted survivor is the F1
      non-authoritative active-tab voice subscription (5.3). Document the result in
      the commit message. Fix any stray authoritative writes found.
    - _Requirements: 2.2, 2.5, 6.3, 14.1_
    - _Verify: `grep -rn "setMessages(" desktop/src` (manual gate) + `cd desktop && npx tsc --noEmit`_

  - [ ]* 8.2 Write F5 stable-prop structural test for `TabView`
    - Assert every `TabView` prop (callbacks incl. `onFirstActivate`/`onLoadOlder`,
      and `tabMapRef`-derived object props) is referentially stable across
      consecutive ChatPage renders for non-changing tabs (Properties 3 & 5 and Req 3.2
      depend on it).
    - _Requirements: 3.2_ (closes F5)
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabView.stableProps.test.tsx`_

  - [ ]* 8.3 Write property tests for close/destroy and backend sessionId
    - **Property 14: Closing a tab destroys both its view and its store** —
      **Validates: Requirements 11.1, 11.2, 11.3, 12.3**
    - **Property 15: Backend calls use the per-tab sessionId** —
      **Validates: Requirements 14.3**
    - **Property 7: Switching away from a streaming tab does not abort its stream** —
      **Validates: Requirements 5.1**
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/closeDestroy.property.test.tsx`_

  - [ ]* 8.4 Write property test for optimistic-echo reconciliation
    - **Property 9: Optimistic echo reconciles without duplication** — reconciling a
      DB message whose `metadata.client_id` equals an optimistic `local-` id yields a
      single merged message (DB version wins). **Validates: Requirements 6.1, 13.1**
    - _Verify: `cd desktop && npm test -- --run src/stores/__tests__/optimisticReconcile.property.test.tsx`_

  - [ ]* 8.5 Write example/structural tests for compatibility and architectural negatives
    - camelCase Read_Model pass-through (`pendingQuestion`, `waitingInput`) unchanged
      shape (Req 13.3); assistant placeholder insertion through the store (Req 6.2);
      structural negatives that the message source is store-only (Req 2.2, 6.3, 14.1).
    - _Requirements: 6.2, 13.3, 13.4, 2.2, 14.1_
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/compat.example.test.tsx`_

- [ ] 9. Final build verification and isolation regression gate
  - [ ] 9.1 Run the production build and confirm the migration is complete
    - Run `cd desktop && npm run build:all` and confirm a clean build. Re-run the
      grep-bound audit gate from 8.1 as the final check. Confirm the preserved
      invariants survive (tabMapRef authoritative; per-tab `sessionId`; tab-scoped
      `setIsStreaming`; stream handlers write to `capturedTabId`'s store). Ensure all
      targeted tests pass; ask the user if questions arise.
    - _Requirements: 14.1, 14.2, 14.3, 14.4_
    - _Verify: `cd desktop && npm run build:all`_

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster
  MVP; core implementation sub-tasks are never optional.
- **Task 1 (Step 0) is a blocking prerequisite** (F7): it lands the previously-dead
  `useMessageStore` on a live streaming path before the ChatPage pivot. Every later
  task depends on it.
- Steps follow the design's amended Migration Strategy order (0 → 1 → 2 → 3 → 4 →
  5 → 6 → 7). Each step is a separate, independently committable change that keeps
  the app working (no broken intermediate state).
- Frontend-only (`desktop/`); no backend changes. All multi-tab isolation invariants
  are preserved at every step.
- Property tests use fast-check + @testing-library/react + Vitest, min 100 iterations,
  tagged `// Feature: chat-tab-view-isolation, Property N`. All 20 properties (P1–P20)
  are covered; P19/P20 are negative/failure-mode properties.
- Verification uses targeted commands only (`npm test -- --run <path>`,
  `npx tsc --noEmit`); never the full suite. Final build via `npm run build:all`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3"] },
    { "id": 4, "tasks": ["3.1"] },
    { "id": 5, "tasks": ["3.2"] },
    { "id": 6, "tasks": ["3.3", "3.4"] },
    { "id": 7, "tasks": ["4.1"] },
    { "id": 8, "tasks": ["4.2"] },
    { "id": 9, "tasks": ["4.3"] },
    { "id": 10, "tasks": ["4.4", "4.5", "4.6"] },
    { "id": 11, "tasks": ["5.1"] },
    { "id": 12, "tasks": ["5.2"] },
    { "id": 13, "tasks": ["5.3"] },
    { "id": 14, "tasks": ["5.4"] },
    { "id": 15, "tasks": ["5.5", "5.6", "5.7", "5.8"] },
    { "id": 16, "tasks": ["6.1"] },
    { "id": 17, "tasks": ["6.2"] },
    { "id": 18, "tasks": ["6.3"] },
    { "id": 19, "tasks": ["7.1"] },
    { "id": 20, "tasks": ["7.2", "7.3"] },
    { "id": 21, "tasks": ["8.1"] },
    { "id": 22, "tasks": ["8.2", "8.3", "8.4", "8.5"] },
    { "id": 23, "tasks": ["9.1"] }
  ]
}
```
