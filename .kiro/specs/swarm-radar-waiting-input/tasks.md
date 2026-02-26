# Implementation Plan: Swarm Radar Waiting Input (Sub-Spec 3 of 5)

## Overview

Build the Waiting Input layer of the Swarm Radar — the `useWaitingInputZone` hook with its exported pure derivation functions (`deriveWaitingItems`, `computeHasWaitingInput`), the `WaitingInputList` and `WaitingInputItem` UI components, the `activeSessionId` prop wiring from ChatPage, and the "Respond" click-to-chat navigation. This is a pure frontend spec — TypeScript/React only, no backend changes. Builds on Spec 1 (types, sort utils, RadarZone, CSS) and Spec 2 (radar.ts service, useTodoZone, SwarmRadar integration in ChatPage).

## Tasks

- [ ] 1. Implement derivation logic and hook
  - [ ] 1.1 Create `desktop/src/pages/chat/components/radar/hooks/useWaitingInputZone.ts`
    - Implement `truncate(text: string, maxLength: number): string` — returns original if ≤ maxLength, otherwise first (maxLength - 3) chars + "..."
    - Implement `computeHasWaitingInput(task, activeSessionId, pendingQuestion, pendingPermission): boolean` — returns `true` iff `activeSessionId` is defined AND `task.sessionId === activeSessionId` AND at least one of `pendingQuestion`/`pendingPermission` is non-null
    - Implement `deriveWaitingItems(pendingQuestion, pendingPermission, activeSessionId, wipTasks): RadarWaitingItem[]` — pure function, no side effects:
      - Find matching WIP task: `wipTasks.find(t => t.sessionId === activeSessionId)`
      - If `pendingQuestion` non-null: create item with `id=toolUseId`, `title=matchedTask.title ?? "Agent Question"`, `agentId=matchedTask.agentId ?? ""`, `sessionId=activeSessionId ?? null`, `question=truncate(questions[0]?.question ?? "Pending question", 200)`, `createdAt=matchedTask.startedAt ?? new Date().toISOString()`
      - If `pendingPermission` non-null: create item with `id=requestId`, `title=matchedTask.title ?? "Permission Required"`, `agentId=matchedTask.agentId ?? ""`, `sessionId=activeSessionId ?? null`, `question=truncate(reason, 200)`, `createdAt=matchedTask.startedAt ?? new Date().toISOString()`
      - Apply `sortWaitingItems` from Spec 1 before returning
    - Implement `useWaitingInputZone` hook accepting `UseWaitingInputZoneParams` (`pendingQuestion`, `pendingPermission`, `activeSessionId`, `wipTasks`)
      - Use `useMemo` to call `deriveWaitingItems`, recomputing only when inputs change
      - Implement `respondToItem(itemId)` via `useCallback` — find item by id, use `useTabState` to switch to existing tab or create new tab for the item's `sessionId`
      - Return `{ waitingItems, respondToItem }`
    - Export `deriveWaitingItems`, `computeHasWaitingInput`, and `truncate` for direct testing
    - Include module-level docstring per dev rules
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 1.2 Write property test: sort ordering by creation time with id tiebreaker (Property 1)
    - **Property 1: Waiting Input sort ordering by creation time (with id tiebreaker)**
    - Create `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`
    - Generate random arrays of `RadarWaitingItem` with arbitrary `createdAt` and `id` values
    - Verify `sortWaitingItems` produces `createdAt` ascending order; when equal, `id` lexicographically ascending
    - Verify idempotence: `sort(sort(x))` deep-equals `sort(x)`
    - Minimum 100 iterations
    - **Validates: Requirements 1.5, 2.5**

  - [ ]* 1.3 Write property test: hasWaitingInput derivation correctness (Property 2)
    - **Property 2: Task status changes and SSE events produce correct hasWaitingInput derivation**
    - Add to `waitingInput.property.test.ts`
    - Generate random WIP task arrays with varied `sessionId` values, random `activeSessionId` (including `undefined`), random `pendingQuestion`/`pendingPermission` (null or non-null)
    - Verify `computeHasWaitingInput` returns `true` iff `task.sessionId === activeSessionId` AND at least one pending prop is non-null
    - Verify at most 1 WIP task has `hasWaitingInput = true`
    - Verify all tasks have `hasWaitingInput = false` when `activeSessionId` is `undefined`
    - Minimum 100 iterations
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

  - [ ]* 1.4 Write property test: waiting item count and id mapping (Property 3)
    - **Property 3: Waiting item derivation produces correct count and mapping**
    - Add to `waitingInput.property.test.ts`
    - Generate random `PendingQuestion` (null or non-null with arbitrary `toolUseId`) and `PermissionRequest` (null or non-null with arbitrary `requestId`), random WIP tasks and `activeSessionId`
    - Verify: both null → length 0; only question → length 1 with `id === toolUseId`; only permission → length 1 with `id === requestId`; both non-null → length 2 with both ids present
    - Verify max length is 2
    - Minimum 100 iterations
    - **Validates: Requirements 1.8, 2.1, 2.2, 2.3, 2.4**

  - [ ]* 1.5 Write property test: question text truncation (Property 4)
    - **Property 4: Waiting item question text truncation**
    - Add to `waitingInput.property.test.ts`
    - Generate random strings of length 0–1000+ as question text and permission reasons
    - Verify derived `question` field length ≤ 200
    - Verify strings ≤ 200 chars are preserved exactly
    - Verify strings > 200 chars are truncated to exactly 200 chars (first 197 + "...")
    - Minimum 100 iterations
    - **Validates: Requirements 1.3, 1.4, 2.2, 2.3**

  - [ ]* 1.6 Write property test: items disappear when pending props become null (Property 5)
    - **Property 5: Waiting items disappear when pending props become null**
    - Add to `waitingInput.property.test.ts`
    - Generate non-null `pendingQuestion`, call `deriveWaitingItems` to get "before" array, then call with `pendingQuestion = null` to get "after" array
    - Verify item with original `toolUseId` is absent from "after" array
    - Same for `pendingPermission` → null transition
    - Minimum 100 iterations
    - **Validates: Requirements 5.5, 5.6**

  - [ ]* 1.7 Write property test: dual presence consistency (Property 6)
    - **Property 6: Dual presence — hasWaitingInput implies corresponding RadarWaitingItem exists**
    - Add to `waitingInput.property.test.ts`
    - Generate random WIP tasks, pending props, and `activeSessionId`
    - For every task where `computeHasWaitingInput` returns `true`, verify exactly one `RadarWaitingItem` in derived array has matching `sessionId`
    - For every `RadarWaitingItem`, verify at most one WIP task has matching `sessionId`
    - Minimum 100 iterations
    - **Validates: Requirements 3.6, 2.2, 2.3**

  - [ ]* 1.8 Write property test: derivation purity (Property 7)
    - **Property 7: Derivation is a pure function of inputs**
    - Add to `waitingInput.property.test.ts`
    - Generate random inputs, call `deriveWaitingItems` twice with identical inputs
    - Verify outputs are deeply equal
    - Minimum 100 iterations
    - **Validates: Requirements 2.6, 8.4**

- [ ] 2. Checkpoint — Derivation logic and property tests
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 3. Build WaitingInputList and WaitingInputItem components
  - [ ] 3.1 Create `desktop/src/pages/chat/components/radar/WaitingInputList.tsx`
    - Define `WaitingInputItemProps`: `item: RadarWaitingItem`, `onRespond: () => void`
    - Implement `WaitingInputItem` component:
      - Render as `<li role="listitem" className="radar-waiting-item">`
      - Display task title in `--color-text`
      - Display question text in `--color-text-muted` (already truncated to 200 chars by hook)
      - Render visually prominent "Respond" button — always visible, not behind hover menu (Req 6.5)
      - Focusable via Tab key; "Respond" button accessible via Enter or Space (Req 7.6)
    - Define `WaitingInputListProps`: `waitingItems: RadarWaitingItem[]`, `onRespond: (itemId: string) => void`
    - Implement `WaitingInputList` component:
      - Render `<ul role="list">` containing one `WaitingInputItem` per entry
      - Render nothing when `waitingItems.length === 0` (no empty sub-section message — zone-level empty state handles it per Req 1.7)
      - Items rendered in order provided (pre-sorted by `useWaitingInputZone`)
    - Use `clsx` for conditional class composition
    - Use only `--color-*` CSS variables — no hardcoded colors
    - Include module-level docstring per dev rules
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 3.2 Write unit tests for WaitingInputList and WaitingInputItem
    - Create `desktop/src/pages/chat/components/radar/__tests__/WaitingInputList.test.tsx`
    - Test: renders items with title and question text
    - Test: does not render when `waitingItems` is empty
    - Test: Respond button is always visible (not hover-only)
    - Test: ARIA — `role="list"` on list, `role="listitem"` on each item
    - Test: keyboard — Tab focuses items, Enter/Space activates Respond
    - Test: `onRespond` called with correct `itemId` when Respond clicked
    - _Requirements: 1.3, 1.4, 1.7, 6.5, 7.3, 7.6, 7.7_

- [ ] 4. Add CSS styles for waiting input components
  - [ ] 4.1 Add waiting input styles to `desktop/src/pages/chat/components/radar/SwarmRadar.css`
    - Define `.radar-waiting-item` — layout for title, question text, and Respond button
    - Define `.radar-waiting-item-title` — uses `--color-text`
    - Define `.radar-waiting-item-question` — uses `--color-text-muted`, single-line truncation
    - Define `.radar-waiting-item-respond` — visually prominent button style, uses `--color-primary` or similar accent
    - Define hover state for Respond button using `--color-hover`
    - Use only `--color-*` CSS variables — no hardcoded colors
    - Match font sizes, weights, spacing of existing radar components
    - _Requirements: 7.4, 7.5_

- [ ] 5. Checkpoint — UI components and styles
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 6. Wire activeSessionId prop and integrate waiting input into SwarmRadar
  - [ ] 6.1 Extend `SwarmRadarProps` with `activeSessionId` and pass from ChatPage
    - Add `activeSessionId: string | undefined` to `SwarmRadarProps` interface in `SwarmRadar.tsx`
    - Update `ChatPage.tsx` to pass `sessionId` state variable as `activeSessionId` prop to `SwarmRadar`
    - _Requirements: 4.1, 4.2_

  - [ ] 6.2 Integrate `useWaitingInputZone` into `SwarmRadar.tsx`
    - Import and call `useWaitingInputZone` hook with `pendingQuestion`, `pendingPermission`, `activeSessionId`, and WIP tasks (from mock data or `useSwarmRadar` if composed)
    - Replace mock waiting item `<li>` elements in the Needs Attention zone with `<WaitingInputList>` component
    - Pass `waitingItems` and `respondToItem` from the hook to `WaitingInputList`
    - Render `WaitingInputList` below `TodoList` in the Needs Attention zone
    - Update Needs Attention badge count to include `waitingItems.length` (todos.length + waitingItems.length)
    - _Requirements: 1.1, 1.2, 5.1, 5.2, 5.4, 8.1_

  - [ ] 6.3 Augment WIP tasks with `hasWaitingInput` flag
    - Use `computeHasWaitingInput` to set `hasWaitingInput` on each WIP task before passing to the In Progress zone
    - Ensure WIP tasks with `hasWaitingInput=true` appear in both In Progress zone (as WIP task) and Needs Attention zone (as waiting item) — dual presence by design
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 6.4 Write unit tests for integration wiring
    - Test: ChatPage passes `sessionId` as `activeSessionId` prop to SwarmRadar
    - Test: Waiting Input sub-section renders below ToDo sub-section in Needs Attention zone
    - Test: Zone empty state shown when no todos and no waiting items
    - Test: Fallback title "Agent Question" when no WIP task matches (pendingQuestion)
    - Test: Fallback title "Permission Required" when no WIP task matches (pendingPermission)
    - Test: Empty questions array produces "Pending question" fallback text
    - Test: respondToItem switches to existing tab when session tab is open
    - Test: respondToItem creates new tab for unknown session
    - _Requirements: 1.1, 1.6, 2.2, 2.3, 4.2, 5.4, 6.3_

- [ ] 7. Final checkpoint — Full integration
  - Ensure all tests pass (`cd desktop && npm test -- --run`).
  - Ensure no TypeScript compilation errors (`cd desktop && npx tsc --noEmit`).
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document — all 7 properties are in a single test file
- This spec is pure frontend — no backend changes
- Builds on Spec 1 (`swarm-radar-foundation`) for types, `sortWaitingItems`, RadarZone, CSS, and Spec 2 (`swarm-radar-todos`) for SwarmRadar integration in ChatPage
- `deriveWaitingItems` and `computeHasWaitingInput` are exported as pure functions for direct testing without React rendering
- Max 2 waiting items in initial release (one `pendingQuestion` + one `pendingPermission`) — array type for future extensibility
- `createdAt` uses matched WIP task's `startedAt` as stable proxy, NOT `Date.now()` at derivation time (PE Finding #1)
- `activeSessionId` correlation enables linking waiting items to WIP tasks without a `sessionId` on `PendingQuestion` (PE Finding #3)
