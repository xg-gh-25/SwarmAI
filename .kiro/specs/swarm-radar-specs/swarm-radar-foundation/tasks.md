# Implementation Plan: Swarm Radar Foundation (Sub-Spec 1 of 5)

## Overview

Build the foundational shell, shared types, reusable components, utility functions, mock data, and styles for the Swarm Radar redesign. This is a pure frontend spec — TypeScript/React only, no backend changes. All tasks target `desktop/src/`.

## Tasks

- [ ] 1. Define shared TypeScript types in `desktop/src/types/radar.ts`
  - [ ] 1.1 Create `desktop/src/types/radar.ts` with all Radar type definitions
    - Define `RadarZoneId` union type
    - Define `RadarTodo` interface with all fields (id, workspaceId, title, description, source, sourceType, status, priority, dueDate, linkedContext, taskId, createdAt, updatedAt)
    - Define `RadarWipTask` type using `Pick<Task, ...> & { hasWaitingInput: boolean }`
    - Define `RadarCompletedTask` interface with all fields including `reviewRequired` (always false) and `reviewRiskLevel` (always null)
    - Define `RadarWaitingItem` interface with JSDoc documenting PE Finding #1 (`createdAt` uses SSE event arrival timestamp, NOT `Date.now()`)
    - Define `RadarAutonomousJob` interface with all fields
    - Define `RadarReviewItem` placeholder interface with JSDoc noting it is not populated in initial release
    - Include module-level docstring per dev rules
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [ ] 1.2 Re-export all Radar types from `desktop/src/types/index.ts`
    - Add `export * from './radar';` to the existing index.ts
    - _Requirements: 4.9_

- [ ] 2. Implement sorting utility functions in `radarSortUtils.ts`
  - [ ] 2.1 Create `desktop/src/pages/chat/components/radar/radarSortUtils.ts`
    - Implement `sortTodos`: overdue first → priority (high→medium→low→none) → dueDate (earliest first, null last) → createdAt (newest first) → id ascending tiebreaker
    - Implement `sortWipTasks`: status order (blocked→wip→draft) → startedAt (most recent first) → id ascending tiebreaker
    - Implement `sortCompletedTasks`: completedAt descending → id ascending tiebreaker
    - Implement `sortWaitingItems`: createdAt ascending → id ascending tiebreaker
    - Implement `sortAutonomousJobs`: category (system before user_defined) → name alphabetical → id ascending tiebreaker
    - All functions must be pure (return new array, no mutation)
    - Include module-level docstring per dev rules
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [ ]* 2.2 Write property tests for sort total order (Property 4)
    - **Property 4: Sort functions produce a total order with deterministic tiebreaking**
    - Create `desktop/src/pages/chat/components/radar/__tests__/radarSortUtils.property.test.ts`
    - Use `fast-check` to generate random arrays of items for each sort function
    - Verify correct sort order per the multi-key comparator rules
    - Verify idempotence: `sort(sort(x))` deep-equals `sort(x)`
    - Verify purity: input array is not mutated
    - Verify total order: for any two distinct items, comparator produces strict ordering via `id` tiebreaker
    - Minimum 100 iterations per property
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8**

- [ ] 3. Implement indicator and badge tint utilities in `radarIndicators.ts`
  - [ ] 3.1 Create `desktop/src/pages/chat/components/radar/radarIndicators.ts`
    - Implement `getPriorityIndicator`: high→🔴, medium→🟡, low→🔵, none→''
    - Implement `getTimelineIndicator`: overdue→⚠️, due today→⏰, otherwise→''
    - Implement `getSourceTypeLabel`: manual→✏️, email→📧, slack→💬, meeting→📅, integration→🔗, chat→💭, ai_detected→🤖
    - Implement `getBadgeTint(zoneId, items)`: Needs Attention→red when overdue/high-priority, In Progress→always yellow, Completed→always green, Autonomous Jobs→red when error status
    - Include module-level docstring per dev rules
    - _Requirements: 6.1, 6.2, 6.8, 6.9, 6.10_

  - [ ]* 3.2 Write property tests for indicator mapping (Property 5)
    - **Property 5: Priority and timeline indicator mapping is consistent and total**
    - Create `desktop/src/pages/chat/components/radar/__tests__/radarIndicators.property.test.ts`
    - Use `fast-check` to generate random priority values and verify correct emoji mapping
    - Verify `getTimelineIndicator` returns ⚠️ for overdue, ⏰ for due today
    - Verify `getSourceTypeLabel` is total (all 7 source types mapped) and injective (no duplicate outputs)
    - Minimum 100 iterations per property
    - **Validates: Requirements 6.1, 6.2, 6.9**

  - [ ]* 3.3 Write property tests for badge tint (Property 6)
    - **Property 6: Badge tint reflects urgency and error state**
    - Add to `radarIndicators.property.test.ts`
    - Use `fast-check` to generate random arrays of todos with varied statuses/priorities
    - Verify Needs Attention badge is `red` when any todo is overdue or high-priority, `neutral` otherwise
    - Verify Autonomous Jobs badge is `red` when any job has error status, `neutral` otherwise
    - Verify In Progress is always `yellow`, Completed is always `green`
    - Minimum 100 iterations per property
    - **Validates: Requirements 6.3, 6.4, 6.5, 6.6, 6.10**

- [ ] 4. Checkpoint — Verify utility modules
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 5. Create mock data module
  - [ ] 5.1 Create `desktop/src/pages/chat/components/radar/mockData.ts`
    - Implement `getMockTodos()`: ≥3 items (one high-priority overdue, one medium due-today, one low-priority), varied source types
    - Implement `getMockWaitingItems()`: ≥2 items (one mid-execution question, one conditional review placeholder), stable `createdAt` timestamps
    - Implement `getMockWipTasks()`: ≥2 items (one `wip`, one `draft`), different agents
    - Implement `getMockCompletedTasks()`: ≥3 items with varying `completedAt` within 7-day archive window
    - Implement `getMockSystemJobs()`: ≥2 items ("Workspace Sync", "Knowledge Indexing"), category `system`
    - Implement `getMockUserJobs()`: ≥2 items ("Daily Digest", "Weekly Report"), category `user_defined`
    - All IDs follow `mock-{zone}-{index}` pattern (stable, deterministic)
    - Each factory returns a new array on every call (no shared mutable state)
    - Include module-level docstring per dev rules
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [ ]* 5.2 Write unit tests for mock data invariants (Property 7)
    - **Property 7: Mock data satisfies minimum item count invariants**
    - Create `desktop/src/pages/chat/components/radar/__tests__/mockData.test.ts`
    - Verify minimum counts: todos≥3, waitingItems≥2, wipTasks≥2, completedTasks≥3, systemJobs≥2, userJobs≥2
    - Verify all IDs are non-empty strings
    - Verify ID uniqueness within each factory function's output
    - Verify determinism: calling same factory twice returns items with identical IDs
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.9**

- [ ] 6. Build the RadarZone reusable component
  - [ ] 6.1 Create `desktop/src/pages/chat/components/radar/RadarZone.tsx`
    - Accept props: `zoneId`, `emoji`, `label`, `count`, `badgeTint`, `isExpanded`, `onToggle`, `children`, `emptyMessage?`
    - Render clickable header `<button>` with emoji, label, and tinted badge count
    - Badge uses `clsx('radar-zone-badge', 'badge-${badgeTint}')` and `aria-label` for screen reader (e.g., "Needs Attention, 5 items")
    - Header button uses `aria-expanded={isExpanded}` and `aria-controls={`zone-content-${zoneId}`}`
    - When collapsed: render only header, hide children
    - When expanded and count=0: render `emptyMessage` in `--color-text-muted`, centered
    - When expanded and count>0: render `children`
    - Zone content panel uses `id={`zone-content-${zoneId}`}` and `role="list"`
    - Include module-level docstring per dev rules
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 7.1, 7.3, 7.4, 7.6, 8.6_

  - [ ]* 6.2 Write property tests for RadarZone (Properties 1, 2, 3)
    - **Property 1: Zone badge counts equal the number of items in each zone**
    - **Property 2: Zone expand/collapse toggling is independent and preserves other zones**
    - **Property 3: Empty zone states display correct messages**
    - Create `desktop/src/pages/chat/components/radar/__tests__/radarZone.property.test.ts`
    - Property 1: Generate arbitrary item arrays, verify badge count = sum of array lengths per zone
    - Property 2: Generate random initial expand/collapse states (4 booleans) and random zone IDs, verify toggle flips only target zone, verify involution (toggle twice = original)
    - Property 3: Generate random zone IDs and counts (0 or positive), verify correct empty message for count=0, no empty message for count>0
    - Minimum 100 iterations per property
    - **Validates: Requirements 1.4, 1.5, 1.6, 2.5, 6.3–6.6, 8.1–8.4, 8.6, 10.6**

- [ ] 7. Build the SwarmRadar shell component and CSS
  - [ ] 7.1 Create `desktop/src/pages/chat/components/radar/SwarmRadar.css`
    - Define styles for `.swarm-radar` root, `.swarm-radar-header`, `.swarm-radar-content` (scrollable area)
    - Define `.radar-zone`, `.radar-zone-header`, `.radar-zone-content` with expand/collapse animation (150–200ms CSS transition on max-height)
    - Define `.radar-zone-badge` base and tint variants: `.badge-red`, `.badge-yellow`, `.badge-green`, `.badge-neutral`
    - Define `.radar-empty-state` for empty zone messages (`--color-text-muted`, centered)
    - Use only `--color-*` CSS variables (never hardcoded colors)
    - Use same font sizes, weights, spacing as existing sidebars (`ChatHistorySidebar`, `FileBrowserSidebar`)
    - Define hover states using `--color-hover`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 6.7, 1.7_

  - [ ] 7.2 Create `desktop/src/pages/chat/components/radar/SwarmRadar.tsx`
    - Define `SwarmRadarProps` interface: `width`, `isResizing`, `onClose?`, `onMouseDown`, `pendingQuestion`, `pendingPermission` (passed through, unused until Spec 3)
    - Render fixed header bar with "Swarm Radar" title, `radar` material icon, and close button (same pattern as `TodoRadarSidebar`)
    - Render left-edge resize handle (identical pattern to `TodoRadarSidebar`)
    - Render single scrollable `<div>` content area containing four `RadarZone` components in fixed order: Needs Attention (🔴), In Progress (🟡), Completed (🟢), Autonomous Jobs (🤖)
    - Manage zone expand/collapse state via `useState<Record<RadarZoneId, boolean>>` (all expanded by default, session-only)
    - Populate zones with mock data from `mockData.ts`, apply sorting via `radarSortUtils.ts`, compute badge tints via `radarIndicators.ts`
    - Compute badge counts: Needs Attention = todos.length + waitingItems.length; In Progress = wipTasks.length; Completed = completedTasks.length; Autonomous Jobs = systemJobs.length + userJobs.length
    - Pass zone-specific `emptyMessage` strings per Requirement 8
    - Render mock items as simple `<li>` elements within each zone (zone-specific list components come in later specs)
    - Use `role="region"` and `aria-label="Swarm Radar"` on root element
    - Add `aria-live="polite"` region for Needs Attention zone
    - Use `clsx` for conditional class composition
    - Import `SwarmRadar.css`
    - Include module-level docstring per dev rules
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 2.1, 2.2, 2.3, 2.4, 2.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 8. Wire SwarmRadar into ChatPage as drop-in replacement
  - [ ] 8.1 Update `desktop/src/pages/chat/components/index.ts` to export `SwarmRadar`
    - Add export for `SwarmRadar` from the radar directory
    - _Requirements: 1.1, 1.8_

  - [ ] 8.2 Update `ChatPage.tsx` to render `SwarmRadar` instead of `TodoRadarSidebar`
    - Replace `<TodoRadarSidebar>` with `<SwarmRadar>` passing same base props plus `pendingQuestion` and `pendingPermission`
    - Keep `useRightSidebarGroup` hook and `todoRadar` sidebar ID unchanged
    - Keep `RIGHT_SIDEBAR_WIDTH_CONFIGS` unchanged
    - _Requirements: 1.1, 1.8, 1.9, 1.10_

- [ ] 9. Final checkpoint — Ensure all tests pass
  - Run `cd desktop && npm test -- --run` and verify all property and unit tests pass.
  - Ensure no TypeScript compilation errors (`cd desktop && npx tsc --noEmit`).
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document
- This spec produces the foundation layer — no backend changes, no API calls
- The `TodoRadarSidebar.tsx` file is NOT deleted in this spec (handled by a later sub-spec when full replacement is wired up)
- `pendingQuestion` and `pendingPermission` props are passed through but unused until Spec 3
