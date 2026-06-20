# Implementation Plan: Chat Tab View Isolation (value-line scope)

## Overview

Goal: fix tab-switch lag with keep-mounted per-tab `TabView`s that read from their
own `MessageStore`. Frontend-only (`desktop/`), every task a separate commit, app
stays working at each step.

**Scope trim (decided 2026-06-20):** the value of this feature is keep-mounted tab
switching. That needs only the rendering changes below. The full "eliminate the
shared `messages` mirror" rewrite (former Step 4 / tasks 5.x) is **descoped to an
optional future cleanup** — it is not required for either reported symptom
(block-append was already fixed by memoization; tab-switch lag is fixed by
keep-mounted alone), it is the riskiest part, and it collides with the in-flight
Root-1 (`session-state-source-of-truth`) reconcile rewrite. The transitional
`messages` prop fallback added in task 3.1 lets the mirror coexist safely, so
nothing forces its removal. The 20-property PBT suite is likewise trimmed to the
few guards that protect the actual risk (cross-tab isolation, no-remount).

Verification (targeted only, never full suite):
- Types: `cd desktop && npx tsc --noEmit`
- Tests: `cd desktop && npm test -- --run <path>`
- Final build: `cd desktop && npm run build:all`

Preserved invariants at every step: `tabMapRef` authoritative; per-tab `sessionId`
for backend calls; `setIsStreaming(value, tabId)` tab-scoped; stream handlers write
to `capturedTabId`'s store.

## Tasks

### Completed

- [x] 1. Step 0 — Exercise `useMessageStore` on a live streaming path (gate). Commit `1354eba8`.
- [x] 2. Step 1 — Extract `TabView` from ChatPage's inline list (no behavior change). Commit `c099b420`.
- [x] 3.1 — `TabView` subscribes to its own per-tab `MessageStore` (prop kept as transitional fallback). Commit `c099b420`.

## Remaining (value line) `ChatPage.tsx`. HOLD them until Root-1's commit stream goes quiet
> (it is actively rewriting ChatPage/reconcile). Task 3.2 is safe to do anytime
> (new file + `TabView` only). Before resuming 4.x, re-read HEAD (Root-1 will have
> changed ChatPage since commit `c099b420`).

- [ ] 4. Per-tab streaming activity (safe now — no ChatPage edit)
  - [ ] 4.1 Add `useStreamingActivity(tabId)` gated to streaming
    - Create `desktop/src/hooks/useStreamingActivity.ts`: compute `displayedActivity`
      (debounced, `MIN_ACTIVITY_DISPLAY_MS`) + `elapsedSeconds` for a tab, running the
      timer/debounce ONLY while that tab's `isStreaming` is true (no timers for idle/
      background tabs). Wire `TabView` to use it instead of the `displayedActivity`/
      `elapsedSeconds` props. Keep `messagesEndRef`/`userScrolledUpRef` per-`TabView`.
    - _Requirements: 7.1, 8.1, 8.2 (F4)_
    - _Verify: `cd desktop && npx tsc --noEmit`_

- [ ] 5. Keep-mounted per-tab views (the actual fix — edits ChatPage; HOLD for Root-1)
  - [ ] 5.1 Render one `TabView` per open tab, toggle visibility
    - In `ChatPage.tsx`, replace the single active `<TabView>` with
      `openTabs.map(tab => <TabView isActive={tab.id===activeTabId} .../>)`; inactive
      tabs `display:none` + `aria-hidden`. Per-tab props from `tabMapRef`. Keep
      `TabView` memoized; ensure all TabView callbacks are stable (`useCallback`).
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.x_
    - _Verify: `cd desktop && npx tsc --noEmit`_
  - [ ] 5.2 Mount-on-first-activation (`everActivated` set + placeholder)
    - Never-activated tab renders a lightweight placeholder (no list, no markdown
      parse); on first activation mount content and keep it mounted. Avoids the
      startup N× parse storm.
    - _Requirements: 1.3, 1.5, 3.1 (F2)_
    - _Verify: `cd desktop && npx tsc --noEmit`_
  - [ ] 5.3 Remove the `sync-active-tab` swap effect
    - Delete the `setMessages([...activeTabState.messages])` swap effect (the
      remount/re-parse root cause). Background TabViews update their own hidden DOM
      via their own store subscription. Keep the shared `messages` + bridge as-is
      (mirror elimination is descoped).
    - _Requirements: 1.4, 1.5, 3.4_
    - _Verify: `cd desktop && npx tsc --noEmit`_

- [ ] 6. Guard tests (trimmed — the isolation/no-remount essentials only)
  - [ ]* 6.1 Cross-tab isolation + no-remount property tests
    - fast-check + RTL, 100 runs each, tagged. Cover: Property 3 (switch is
      non-destructive — no unmount/remount, no markdown re-parse for activated tabs),
      Property 4 (cross-tab content isolation incl. background streaming append),
      Property 5 (per-tab render isolation — a store notify re-renders only its TabView).
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabViewList.isolation.property.test.tsx`_
  - [ ]* 6.2 Tab-switch perf smoke
    - Two tabs (10 vs 200 msgs): switch is <100ms and independent of message count
      (no remount). Optional concurrent-streaming sanity.
    - _Verify: `cd desktop && npm test -- --run src/pages/chat/__tests__/TabSwitch.perf.test.tsx`_

- [ ] 7. Finish line
  - [ ] 7.1 Build verification + isolation sanity
    - `cd desktop && npm run build:all` clean. Manually confirm: switch between two
      long-history tabs is instant; background streaming continues and is visible on
      switch-back; no cross-tab content/stream leak; scroll position preserved per tab.
    - _Requirements: 14.x_
    - _Verify: `cd desktop && npm run build:all`_

## Descoped (optional future cleanup — not required for the fix)

- **Eliminate the shared `messages` mirror** (former Step 4 / tasks 5.1–5.4):
  establish store-exists invariant, remove the hot-path `else` fallback, replace the
  bridge with a minimal voice subscription (F1), delete remaining active-tab mirror
  writes, grep-bound audit to zero authoritative-display writes. Revisit ONLY if the
  shared mirror is shown to cause bugs, AND only after Root-1 lands (build on its
  shipped client_id/drain/pending contract; re-validate Req 13). Design details for
  this remain in `design.md` (F1/F3 + PE Review Resolutions).
- **Full 20-property PBT suite + per-step example tests** (former tasks 3.3/3.4,
  4.4–4.6, 5.5–5.8, 6.3, 7.2/7.3, 8.2–8.5): retained in `design.md` Correctness
  Properties; pull individual ones forward only if a specific risk warrants.

## Notes

- `*` = optional test sub-task.
- 4.1 (task group 4) is safe to run now. 5.x edits ChatPage — HOLD until Root-1 quiet, then re-read HEAD first.
- Requirements/design unchanged; only this task list was trimmed to the value line.

## Handoff FROM Root-1 SSOT Phase 3 (run_04006034, landed 2026-06-21)

Root-1 Phase 3 (frontend mirror) shipped and explicitly LEFT two items for this track:

1. **AC4 `pending_count` → session-level "N queued" badge (DEFERRED to this track).**
   Root-1 wired the *functional* half (drain retirement clears the local optimistic
   queue mirror) but NOT the *display* half — a badge driven by the authoritative
   `pending_count`. The field was intentionally NOT stored on tabState (a
   written-but-unread field is dead code, GUI82). When you wire the badge: add the
   field + the reconcile write + the TabView reader **atomically** in one change.
   Source: `getStreamingState()` already returns `pendingCount` (chat.ts); consume it.

2. **Step 4 (remove shared `messages` mirror): delete Root-1's parallel-writes.**
   Phase 3 added `tabState.messages = store.messages` parallel-writes in the reconcile
   loop (surfacePendingQuestion bg-tab path + drain-retire path) to keep tabState in
   sync because the store→tabState bridge is active-tab-only. These become DEAD writes
   once you remove the shared `messages` mirror in Step 4. Grep `tabState.messages =`
   in `useChatStreamingLifecycle.ts` and remove the now-dead mirror writes as part of
   Step 4 (the bg-tab ones are comment-tagged). Verify the store-less else-branch
   (drain-retire) is unreachable or route it through `getOrCreate` before deleting the prop.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["4.1"] },
    { "id": 1, "tasks": ["5.1"] },
    { "id": 2, "tasks": ["5.2"] },
    { "id": 3, "tasks": ["5.3"] },
    { "id": 4, "tasks": ["6.1", "6.2"] },
    { "id": 5, "tasks": ["7.1"] }
  ],
  "notes": "Tasks 1, 2, 3.1 already complete. 4.1 is safe now (new file + TabView only). 5.1–5.3 edit ChatPage.tsx — HOLD until Root-1 (session-state-source-of-truth) commit stream is quiet, then re-read HEAD before editing. 6.x optional guard tests. 7.1 final build."
}
```
