# Requirements Document — Swarm Radar Foundation (Sub-Spec 1 of 5)

## Introduction

This document defines the requirements for the **Swarm Radar Foundation** — the first sub-spec of the Swarm Radar Redesign. It covers the shell layout, zone structure, responsive scrolling, mock data infrastructure, TypeScript type definitions, priority indicators, visual design consistency, accessibility, and empty states.

This foundation layer establishes the component skeleton, reusable `RadarZone` wrapper, all shared TypeScript types (used by subsequent sub-specs), and a dedicated mock data module that replaces the existing hardcoded mock data in `TodoRadarSidebar.tsx`.

### Scope

- Swarm Radar shell component with header, close button, resize handle
- Four collapsible Radar Zones in fixed vertical order
- Responsive scrolling and overflow behavior
- All shared TypeScript types (`RadarTodo`, `RadarWipTask`, `RadarCompletedTask`, `RadarWaitingItem`, `RadarAutonomousJob`, `RadarZoneId`)
- Mock data module with realistic sample data for all zones
- Priority indicators and visual hierarchy
- Empty state messages per zone
- Visual design consistency (CSS variables, icon font, animations)
- Accessibility (keyboard navigation, ARIA attributes, screen reader support)

### Out of Scope (Handled by Later Sub-Specs)

- ToDo data fetching, lifecycle actions, quick-add (Spec 2)
- Waiting input / pending question handling from SSE props (Spec 3)
- WIP tasks, completed tasks, archive window logic (Spec 4)
- Autonomous jobs API (Spec 5)
- The `useSwarmRadar` hook composition (built incrementally across Specs 2–5)

### Parent Spec

The overall Swarm Radar Redesign spec is at `.kiro/specs/swarm-radar-redesign/`. This sub-spec extracts and adapts Requirements 1, 2, 3, 12, 14, 24, 25 and Correctness Properties 6, 7, 14 from that parent.

### Design Principles Alignment

- **Progressive Disclosure** — Collapsed zones, expandable sections, minimal default view
- **Glanceable Awareness** — Zone badges, priority indicators, fixed zone ordering
- **Visible Planning Builds Trust** — Transparent zone structure shows what the Radar tracks
- **Signals First** — ToDos and Waiting Input are visually separated from Tasks

### PE Review Findings Addressed

1. **Finding #6 (Determinism)**: All sorting rules include `id` (string comparison) as the ultimate tiebreaker after `createdAt` to guarantee a total order. This is critical for property-based test correctness.
2. **Finding #1 (API Design)**: `RadarWaitingItem.createdAt` is defined to use the SSE event arrival timestamp (captured when `pendingQuestion` state is set in ChatPage) or the task's `startedAt` as a stable proxy for creation time, NOT `Date.now()` at derivation time. This is documented in the type definition even though the waiting input logic is implemented in Spec 3.

## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Replaces the former `TodoRadarSidebar`.
- **Radar_Zone**: A visually distinct, collapsible section within the Swarm_Radar. Four zones exist: Needs Attention, In Progress, Completed, and Autonomous Jobs.
- **Needs_Attention_Zone**: The top Radar_Zone containing ToDos and Waiting Input / ToReview items. Indicated by 🔴.
- **In_Progress_Zone**: The Radar_Zone containing WIP Tasks currently being executed. Indicated by 🟡.
- **Completed_Zone**: The Radar_Zone containing recently completed tasks within the archive window. Indicated by 🟢.
- **Autonomous_Jobs_Zone**: The Radar_Zone containing system built-in and user-defined recurring agent jobs. Indicated by 🤖.
- **Zone_Badge**: A count badge displayed next to a Radar_Zone header showing the number of items in that zone.
- **Priority_Indicator**: Visual emoji/icon indicators for ToDo priority: 🔴 High, 🟡 Medium, 🔵 Low, ⏰ Due Today, ⚠️ Overdue.
- **Mock_Data**: Realistic sample data pre-populated in all Radar zones to demonstrate the full feature. Replaces existing hardcoded mock data.
- **RadarZoneId**: TypeScript union type identifying the four zones: `'needsAttention' | 'inProgress' | 'completed' | 'autonomousJobs'`.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **ChatPage**: The main orchestrator component at `desktop/src/pages/ChatPage.tsx`.
- **useRightSidebarGroup**: The existing hook managing mutual exclusion for right sidebars (TodoRadar, ChatHistory, FileBrowser).
- **RadarTodo**: Frontend TypeScript type representing a ToDo item in the Radar. Maps from the backend `ToDoResponse` model.
- **RadarWipTask**: Frontend TypeScript type representing a WIP task. Uses `Pick<Task, ...>` plus a `hasWaitingInput` boolean.
- **RadarCompletedTask**: Frontend TypeScript type representing a completed task in the archive window.
- **RadarWaitingItem**: Frontend TypeScript type representing a pending question or permission request. Ephemeral — derived from SSE props, not persisted. `createdAt` uses the SSE event arrival timestamp or the task's `startedAt` as a stable proxy (PE Finding #1).
- **RadarAutonomousJob**: Frontend TypeScript type representing a system or user-defined autonomous job.
- **Archive_Window**: The time period (default 7 days) after which completed tasks are removed from the Completed_Zone.
- **Total_Order_Tiebreaker**: All sort functions use `id` (string comparison) as the ultimate tiebreaker after all other sort keys to guarantee deterministic ordering (PE Finding #6).

## Requirements

### Requirement 1: Swarm Radar Shell — Layout and Zone Structure

**User Story:** As a knowledge worker, I want the right sidebar to be a structured, zone-based control panel, so that I can instantly see what needs attention, what is in progress, what is done, and what runs autonomously.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL render as a root shell component in `desktop/src/pages/chat/components/radar/SwarmRadar.tsx` that replaces the existing `TodoRadarSidebar` component.
2. THE Swarm_Radar SHALL display four collapsible Radar_Zones in fixed vertical order: Needs_Attention_Zone (🔴), In_Progress_Zone (🟡), Completed_Zone (🟢), Autonomous_Jobs_Zone (🤖).
3. THE Swarm_Radar SHALL display a header bar with the title "Swarm Radar", a radar icon (using `material-symbols-outlined`), and a close button (using the existing `onClose` pattern from `TodoRadarSidebar`).
4. EACH Radar_Zone SHALL display a zone header with: zone emoji indicator, zone label, and a Zone_Badge showing the count of items in that zone.
5. EACH Radar_Zone SHALL be independently collapsible via click on the zone header.
6. THE Swarm_Radar SHALL persist zone expand/collapse state per session in memory (not localStorage). THE default state SHALL be all zones expanded.
7. THE Swarm_Radar SHALL use CSS variables in `--color-*` format for all colors, with soft visual separators (`--color-border`) between zones.
8. THE Swarm_Radar SHALL integrate with the existing `useRightSidebarGroup` hook, maintaining the `todoRadar` sidebar ID and mutual exclusion with ChatHistory and FileBrowser sidebars.
9. THE Swarm_Radar SHALL support the existing resize handle pattern (left-edge drag) with the width constraints defined in `RIGHT_SIDEBAR_WIDTH_CONFIGS`.
10. THE Swarm_Radar SHALL accept the same base props interface as the current `TodoRadarSidebar` (`width`, `isResizing`, `onClose`, `onMouseDown`) for drop-in replacement compatibility.
11. THE Swarm_Radar SHALL use `clsx` for conditional class composition, consistent with existing component patterns.

### Requirement 2: Swarm Radar — Responsive Scrolling and Overflow

**User Story:** As a knowledge worker, I want the Radar to scroll smoothly when content exceeds the viewport, so that I can access all zones without layout issues.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use a single scrollable content area below the header for all four Radar_Zones.
2. WHEN the total content height exceeds the available viewport, THE Swarm_Radar SHALL enable vertical scrolling within the content area.
3. THE Swarm_Radar header SHALL remain fixed (non-scrolling) at the top of the sidebar.
4. THE Swarm_Radar SHALL preserve scroll position when zone expand/collapse state changes.
5. WHEN a zone is collapsed, THE Swarm_Radar SHALL show only the zone header with the Zone_Badge, freeing vertical space for other zones.

### Requirement 3: Mock Data Infrastructure

**User Story:** As a user seeing the Swarm Radar for the first time, I want all zones populated with realistic sample data, so that I understand the full feature without needing real integrations.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL display built-in Mock_Data for all four Radar_Zones on first render.
2. THE Mock_Data for Needs_Attention_Zone SHALL include at least: 3 ToDo items (one high priority overdue, one medium due today, one low priority), and 2 Waiting_Input_Items (one mid-execution question, one conditional review placeholder).
3. THE Mock_Data for In_Progress_Zone SHALL include at least: 2 WIP_Tasks (one with status `wip`, one with status `draft`).
4. THE Mock_Data for Completed_Zone SHALL include at least: 3 Completed_Tasks with varying completion timestamps within the 7-day Archive_Window.
5. THE Mock_Data for Autonomous_Jobs_Zone SHALL include at least: 2 System_Built_In_Jobs (e.g., "Workspace Sync", "Knowledge Indexing") and 2 User_Defined_Jobs (e.g., "Daily Digest", "Weekly Report").
6. THE Mock_Data SHALL use realistic titles, descriptions, priorities, timestamps, and source types that demonstrate the intended usage patterns.
7. THE Mock_Data SHALL be defined in a dedicated module at `desktop/src/pages/chat/components/radar/mockData.ts` (not inline in components) to facilitate future replacement with real API data.
8. THE Mock_Data module SHALL export factory functions: `getMockTodos()`, `getMockWaitingItems()`, `getMockWipTasks()`, `getMockCompletedTasks()`, `getMockSystemJobs()`, `getMockUserJobs()`.
9. EACH mock data item SHALL have a stable, deterministic `id` field (not randomly generated) to support testing and snapshot stability.
10. THE existing hardcoded mock data (`MOCK_OVERDUE_ITEMS`, `MOCK_PENDING_ITEMS`) in `TodoRadarSidebar.tsx` SHALL be superseded by the new mock data module. THE old `TodoRadarSidebar.tsx` file deletion is handled by a later sub-spec when the full replacement is wired up.

### Requirement 4: Shared TypeScript Type Definitions

**User Story:** As a developer, I want all Swarm Radar TypeScript types defined in a shared location, so that all sub-specs use consistent type definitions.

#### Acceptance Criteria

1. THE Frontend SHALL define all Radar-specific types in `desktop/src/types/radar.ts`.
2. THE `RadarZoneId` type SHALL be defined as: `'needsAttention' | 'inProgress' | 'completed' | 'autonomousJobs'`.
3. THE `RadarTodo` interface SHALL include fields: `id` (string), `workspaceId` (string), `title` (string), `description` (string | null), `source` (string | null), `sourceType` (union of `'manual' | 'email' | 'slack' | 'meeting' | 'integration' | 'chat' | 'ai_detected'`), `status` (union of `'pending' | 'overdue' | 'in_discussion' | 'handled' | 'cancelled' | 'deleted'`), `priority` (union of `'high' | 'medium' | 'low' | 'none'`), `dueDate` (string | null, ISO 8601), `linkedContext` (string | null, JSON string), `taskId` (string | null), `createdAt` (string), `updatedAt` (string).
4. THE `RadarWipTask` type SHALL use `Pick<Task, 'id' | 'workspaceId' | 'agentId' | 'sessionId' | 'status' | 'title' | 'description' | 'priority' | 'sourceTodoId' | 'model' | 'createdAt' | 'startedAt' | 'error'> & { hasWaitingInput: boolean }` to avoid parallel type duplication with the existing `Task` type.
5. THE `RadarCompletedTask` interface SHALL include fields: `id` (string), `workspaceId` (string | null), `agentId` (string), `sessionId` (string | null), `title` (string), `description` (string | null), `priority` (string | null), `completedAt` (string, ISO 8601), `reviewRequired` (boolean, always false in initial release), `reviewRiskLevel` (string | null, always null in initial release).
6. THE `RadarWaitingItem` interface SHALL include fields: `id` (string), `title` (string), `agentId` (string), `sessionId` (string | null), `question` (string, truncated to 200 chars), `createdAt` (string, ISO 8601). THE `createdAt` field SHALL be documented as using the SSE event arrival timestamp (captured when `pendingQuestion` state is set in ChatPage) or the task's `startedAt` as a stable proxy for creation time, NOT `Date.now()` at derivation time (PE Finding #1).
7. THE `RadarAutonomousJob` interface SHALL include fields: `id` (string), `name` (string), `category` (union of `'system' | 'user_defined'`), `status` (union of `'running' | 'paused' | 'error' | 'completed'`), `schedule` (string | null), `lastRunAt` (string | null, ISO 8601), `nextRunAt` (string | null, ISO 8601), `description` (string | null).
8. THE `RadarReviewItem` interface SHALL be defined as a placeholder type (deferred to future spec): `id` (string), `title` (string), `agentId` (string), `sessionId` (string | null), `riskLevel` (union of `'low' | 'medium' | 'high' | 'critical'`), `completionSummary` (string), `completedAt` (string). THE type SHALL include a JSDoc comment noting it is not populated in the initial release.
9. THE types file SHALL export all types and be re-exported from `desktop/src/types/index.ts`.

### Requirement 5: Sorting Utility Functions

**User Story:** As a developer, I want shared sorting utility functions for all Radar zones, so that sort logic is testable, reusable, and deterministic.

#### Acceptance Criteria

1. THE Frontend SHALL define sorting utility functions in `desktop/src/pages/chat/components/radar/radarSortUtils.ts`.
2. THE `sortTodos` function SHALL sort `RadarTodo[]` by: overdue items first, then by priority (high → medium → low → none), then by due date (earliest first, null due dates last), then by creation date (newest first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
3. THE `sortWipTasks` function SHALL sort `RadarWipTask[]` by: `blocked` first, then `wip`, then `draft`, then by start time (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
4. THE `sortCompletedTasks` function SHALL sort `RadarCompletedTask[]` by: `completedAt` descending (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
5. THE `sortWaitingItems` function SHALL sort `RadarWaitingItem[]` by: `createdAt` ascending (oldest first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
6. THE `sortAutonomousJobs` function SHALL sort `RadarAutonomousJob[]` by: `system` category before `user_defined`, then alphabetical by name, then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
7. EACH sort function SHALL be a pure function that returns a new sorted array without mutating the input.
8. EACH sort function SHALL produce a total order — no two distinct items may have ambiguous relative ordering.

### Requirement 6: Priority Indicators and Visual Hierarchy

**User Story:** As a knowledge worker, I want clear visual priority and timeline indicators across all Radar zones, so that I can instantly identify what is urgent.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use consistent Priority_Indicators across all zones: 🔴 for High priority, 🟡 for Medium priority, 🔵 for Low priority.
2. THE Swarm_Radar SHALL use timeline indicators: ⏰ for items due today, ⚠️ for overdue items.
3. THE Needs_Attention_Zone header SHALL use a red-tinted Zone_Badge when overdue or high-priority items exist.
4. THE In_Progress_Zone header SHALL use a yellow-tinted Zone_Badge.
5. THE Completed_Zone header SHALL use a green-tinted Zone_Badge.
6. THE Autonomous_Jobs_Zone header SHALL use a neutral-tinted Zone_Badge, switching to red-tinted when any job has `status` equal to `error`.
7. ALL color tints SHALL use CSS variables in `--color-*` format, never hardcoded color values.
8. THE Frontend SHALL define indicator mapping functions (`getPriorityIndicator`, `getTimelineIndicator`, `getSourceTypeLabel`) in a shared utility module at `desktop/src/pages/chat/components/radar/radarIndicators.ts`.
9. THE `getSourceTypeLabel` function SHALL map each source type to its correct emoji label: manual → ✏️, email → 📧, slack → 💬, meeting → 📅, integration → 🔗, chat → 💭, ai_detected → 🤖.
10. THE `getBadgeTint` function SHALL compute the badge tint for each zone based on its items: Needs Attention → `red` when overdue or high-priority items exist, otherwise default; Autonomous Jobs → `red` when any job has error status, otherwise `neutral`.

### Requirement 7: Swarm Radar — Accessibility

**User Story:** As a knowledge worker using assistive technology, I want the Swarm Radar to be keyboard navigable and screen reader compatible, so that I can manage my work regardless of how I interact with the application.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL be keyboard navigable: zone headers SHALL be focusable and expandable via Enter or Space key.
2. EACH Radar item SHALL be focusable via Tab key navigation, with the action menu accessible via Enter or Space.
3. THE Swarm_Radar SHALL use appropriate ARIA attributes: `role="region"` with `aria-label="Swarm Radar"` on the root element, zone headers with `aria-expanded` reflecting collapse state, and item lists with `role="list"`.
4. THE Zone_Badge counts SHALL be announced to screen readers using `aria-label` (e.g., "Needs Attention, 5 items").
5. WHEN a new item appears in the Needs_Attention_Zone, THE Swarm_Radar SHALL use an `aria-live="polite"` region to announce the new item to screen readers.
6. THE RadarZone header button SHALL use `aria-controls` referencing the zone content panel id.

### Requirement 8: Swarm Radar — Empty States

**User Story:** As a knowledge worker, I want clear, friendly empty states when a zone has no items, so that I understand the zone's purpose even when there is nothing to show.

#### Acceptance Criteria

1. WHEN the Needs_Attention_Zone has no ToDos and no Waiting Input items, THE zone SHALL display: "All clear — nothing needs your attention right now."
2. WHEN the In_Progress_Zone has no WIP_Tasks, THE zone SHALL display: "No tasks running. Start a ToDo or chat to kick things off."
3. WHEN the Completed_Zone has no recent completions, THE zone SHALL display: "No completed tasks in the last 7 days."
4. WHEN the Autonomous_Jobs_Zone has no jobs, THE zone SHALL display: "No autonomous jobs configured yet."
5. ALL empty state messages SHALL use `--color-text-muted` for text color and be centered within the zone content area.
6. THE RadarZone component SHALL accept an `emptyMessage` prop and render it when `count` is 0 and no children are provided.

### Requirement 9: Swarm Radar — Visual Design Consistency

**User Story:** As a knowledge worker, I want the Swarm Radar to feel visually consistent with the rest of SwarmAI, so that the experience is cohesive and calm.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use the same font sizes, weights, and spacing patterns as the existing sidebar components (`ChatHistorySidebar`, `FileBrowserSidebar`).
2. THE Swarm_Radar SHALL use `--color-card` for the background, `--color-border` for separators, `--color-text` for primary text, and `--color-text-muted` for secondary text.
3. THE Swarm_Radar SHALL use subtle hover states (`--color-hover`) for interactive items, consistent with the existing sidebar hover patterns.
4. THE Swarm_Radar SHALL use the `material-symbols-outlined` icon font for all icons, consistent with the existing icon usage across the application.
5. THE Swarm_Radar SHALL use smooth expand/collapse animations (150–200ms duration) for zone toggling, using CSS transitions on `max-height` or equivalent.
6. THE Swarm_Radar SHALL use `clsx` for conditional class composition, consistent with the existing component patterns.
7. ALL Swarm_Radar styles SHALL be defined in a dedicated CSS module at `desktop/src/pages/chat/components/radar/SwarmRadar.css` (or co-located CSS modules per component).

### Requirement 10: RadarZone Reusable Component

**User Story:** As a developer, I want a reusable RadarZone wrapper component, so that all four zones share consistent expand/collapse behavior, badge rendering, and accessibility patterns.

#### Acceptance Criteria

1. THE RadarZone component SHALL be defined at `desktop/src/pages/chat/components/radar/RadarZone.tsx`.
2. THE RadarZone component SHALL accept props: `emoji` (string), `label` (string), `count` (number), `badgeTint` (union of `'red' | 'yellow' | 'green' | 'neutral'`), `isExpanded` (boolean), `onToggle` (function), `children` (ReactNode), `emptyMessage` (optional string).
3. THE RadarZone SHALL render a clickable header with the emoji, label, and tinted Zone_Badge.
4. THE RadarZone SHALL animate expand/collapse with a smooth transition (150–200ms).
5. WHEN `isExpanded` is false, THE RadarZone SHALL render only the header, hiding children.
6. WHEN `isExpanded` is true and `count` is 0, THE RadarZone SHALL render the `emptyMessage` in `--color-text-muted` centered text.
7. THE RadarZone header button SHALL use `aria-expanded`, `aria-controls`, and be keyboard-accessible (Enter/Space to toggle).

## Correctness Properties

### Property 1: Zone badge counts equal the number of items in each zone

*For any* combination of zone data (todos, waiting items, WIP tasks, completed tasks, autonomous jobs), the computed badge count for each zone SHALL equal: Needs Attention = count(active todos) + count(waiting items); In Progress = count(WIP tasks); Completed = count(completed tasks within archive window); Autonomous Jobs = count(all jobs). The count SHALL never be negative.

**Validates: Requirements 1.4, 6.3, 6.4, 6.5, 6.6**

### Property 2: Zone expand/collapse toggling is independent and preserves other zones

*For any* initial expand/collapse state of all four zones, toggling one zone SHALL flip only that zone's expanded state while leaving all other zones unchanged. The resulting state SHALL persist across re-renders within the same session. Toggling the same zone twice SHALL return it to its original state (involution).

**Validates: Requirements 1.5, 1.6, 2.5**

### Property 3: Empty zone states display correct messages

*For any* zone with zero items, the rendered output SHALL contain the zone-specific empty state message: Needs Attention → "All clear — nothing needs your attention right now."; In Progress → "No tasks running. Start a ToDo or chat to kick things off."; Completed → "No completed tasks in the last 7 days."; Autonomous Jobs → "No autonomous jobs configured yet." Zones with one or more items SHALL NOT display the empty state message.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

### Property 4: Sort functions produce a total order with deterministic tiebreaking

*For any* list of Radar items, each sort function (`sortTodos`, `sortWipTasks`, `sortCompletedTasks`, `sortWaitingItems`, `sortAutonomousJobs`) SHALL produce a total order where no two distinct items have ambiguous relative ordering. Specifically, for any two items `a` and `b` where `a !== b`, exactly one of `a < b` or `a > b` holds. The `id` field (string comparison) serves as the ultimate tiebreaker after all other sort keys (PE Finding #6). Sorting the same input twice SHALL produce identical output (idempotence).

**Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8**

### Property 5: Priority and timeline indicator mapping is consistent and total

*For any* `RadarTodo` item, the priority indicator function SHALL map: `high` → 🔴, `medium` → 🟡, `low` → 🔵, `none` → no indicator (empty string or null). The timeline indicator function SHALL map: status `overdue` → ⚠️, due date equal to today → ⏰. The source type label function SHALL map each of the 7 source types to exactly one emoji label. No source type SHALL be unmapped. No two source types SHALL map to the same emoji.

**Validates: Requirements 6.1, 6.2, 6.8, 6.9**

### Property 6: Badge tint reflects urgency and error state

*For any* set of items in the Needs Attention zone, the badge tint SHALL be `red` when at least one item is overdue or has high priority, and the default tint otherwise. *For any* set of autonomous jobs, the badge tint SHALL be `red` when at least one job has `status` equal to `error`, and `neutral` otherwise. The In Progress zone badge tint SHALL always be `yellow`. The Completed zone badge tint SHALL always be `green`.

**Validates: Requirements 6.3, 6.4, 6.5, 6.6, 6.10**

### Property 7: Mock data satisfies minimum item count invariants

*For any* call to the mock data factory functions, the returned arrays SHALL satisfy: `getMockTodos().length >= 3`, `getMockWaitingItems().length >= 2`, `getMockWipTasks().length >= 2`, `getMockCompletedTasks().length >= 3`, `getMockSystemJobs().length >= 2`, `getMockUserJobs().length >= 2`. All returned items SHALL have non-empty `id` fields. All `id` values within a single factory function's output SHALL be unique.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.9**
