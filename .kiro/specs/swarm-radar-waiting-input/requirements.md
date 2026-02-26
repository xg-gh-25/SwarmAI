# Requirements Document — Swarm Radar Waiting Input (Sub-Spec 3 of 5)

## Introduction

This document defines the requirements for the **Swarm Radar Waiting Input** — the third sub-spec of the Swarm Radar Redesign. It covers the Waiting Input / ToReview sub-section within the Needs Attention zone, the derivation of `RadarWaitingItem` objects from SSE props (`pendingQuestion` and `pendingPermission`), the `hasWaitingInput` flag on WIP tasks, the "Respond" click-to-chat action, and the cleanup/consolidation of pending question/permission state between ChatPage and SwarmRadar.

This spec builds on the foundation established in Spec 1 (`swarm-radar-foundation`) and the ToDo infrastructure from Spec 2 (`swarm-radar-todos`).

### Scope

- WaitingInputList and WaitingInputItem components for the Needs Attention zone
- Derivation logic: mapping `pendingQuestion` and `pendingPermission` SSE props into `RadarWaitingItem[]`
- `hasWaitingInput` derivation on `RadarWipTask` by correlating `activeSessionId` with WIP task `sessionId`
- "Respond" click-to-chat action (navigate to chat thread with pending question)
- Pending question / permission cleanup and consolidation between ChatPage and SwarmRadar
- Sorting of waiting items by creation time (oldest first, with `id` tiebreaker per PE Finding #6)
- `useWaitingInputZone` hook for deriving waiting items from SSE props
- `activeSessionId` prop addition to SwarmRadar for session-to-task correlation (PE Finding #3 fix)

### Out of Scope (Handled by Other Sub-Specs)

- SwarmRadar shell, RadarZone, shared types, sorting utilities, mock data, CSS, empty states (Spec 1 — done)
- ToDo inbox, quick-add, lifecycle actions, radar.ts service layer, old component deletion (Spec 2 — done)
- WIP tasks display, completed tasks, archive window logic (Spec 4)
- Autonomous jobs API and zone (Spec 5)
- Full review mechanism (deferred to future spec — `review_required` is always `false`, `review_risk_level` is always `null`)

### Parent Spec

The overall Swarm Radar Redesign spec is at `.kiro/specs/swarm-radar-redesign/`. This sub-spec extracts and adapts Requirements 7, 8, and 13 (Respond action only) and Correctness Properties 10 and 15 from that parent.

### Dependencies

- **Spec 1 (`swarm-radar-foundation`)**: SwarmRadar shell, RadarZone component, shared types (`RadarWaitingItem`, `RadarWipTask`, `RadarZoneId`), `sortWaitingItems` utility, mock data (`getMockWaitingItems()`), CSS styles, empty state support.
- **Spec 2 (`swarm-radar-todos`)**: SwarmRadar integration in ChatPage (the `pendingQuestion` and `pendingPermission` props are already wired to SwarmRadar), `radar.ts` service layer, `useTodoZone` hook, old component deletion.

### Design Principles Alignment

- **Human Review Gates Are Essential** — Waiting Input surfaces only necessary decisions that block AI execution
- **Chat is the Command Surface** — "Respond" navigates to the chat thread where the pending question is displayed
- **Visible Planning Builds Trust** — WIP tasks with `hasWaitingInput=true` show that the agent is blocked and needs human input
- **Progressive Disclosure** — Waiting items appear only when SSE events produce them; disappear when resolved
- **Glanceable Awareness** — Waiting items in the Needs Attention zone provide instant visibility into blocked agents

### PE Review Findings Addressed

1. **Finding #1 (API Design, Medium)**: `RadarWaitingItem.createdAt` uses the SSE event arrival timestamp (captured when `pendingQuestion` state is set in ChatPage) or the task's `startedAt` as a stable proxy for creation time, NOT `Date.now()` at derivation time. The type definition in Spec 1 already documents this. This spec implements the logic: when deriving `RadarWaitingItem` from `pendingQuestion`, the `createdAt` is set to the matched WIP task's `startedAt` (if found) or the current timestamp captured once at SSE event arrival time (stored alongside the `pendingQuestion` state).

2. **Finding #2 (Data Model Correctness, Medium)**: `pendingQuestion` is typed as `PendingQuestion | null` (a single nullable value, not an array). `pendingPermission` is typed as `PermissionRequest | null` (also a single nullable value). The maximum number of `RadarWaitingItem` objects in the initial release is 2 (one from `pendingQuestion`, one from `pendingPermission`). The `RadarWaitingItem[]` array type is correct for extensibility (future SSE events may produce multiple items), but this is explicitly documented.

3. **Finding #3 (Correctness Properties, Medium)**: `PendingQuestion` has no `sessionId` field — it contains `{ toolUseId: string, questions: AskUserQuestion[] }`. The correlation mechanism is: ChatPage passes the `activeSessionId` (the `sessionId` of the currently active chat thread that produced the SSE event) as an additional prop to SwarmRadar. The `useWaitingInputZone` hook matches `activeSessionId` against WIP tasks' `sessionId` to set `hasWaitingInput = true` and to look up the task title for the `RadarWaitingItem`.


## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Defined in Spec 1.
- **Needs_Attention_Zone**: The top Radar_Zone containing ToDos and Waiting Input / ToReview items. Indicated by 🔴. Defined in Spec 1.
- **Waiting_Input_Item**: An item derived from SSE `ask_user_question` or `permission_request` events in the active chat session, passed as props from ChatPage to SwarmRadar. Ephemeral — exists only during the active SSE session. Disappears on page reload. The agent will re-ask if the question is still relevant.
- **RadarWaitingItem**: Frontend TypeScript type representing a pending question or permission request. Defined in Spec 1 at `desktop/src/types/radar.ts`. Fields: `id`, `title`, `agentId`, `sessionId`, `question`, `createdAt`.
- **RadarWipTask**: Frontend TypeScript type representing a WIP task. Defined in Spec 1. Includes `hasWaitingInput: boolean` derived by matching `activeSessionId` against the task's `sessionId`.
- **PendingQuestion**: The SSE-derived state type in ChatPage: `{ toolUseId: string, questions: AskUserQuestion[] }` where `AskUserQuestion = { question: string, header: string, options: AskUserQuestionOption[], multiSelect: boolean }`. A single nullable value (`PendingQuestion | null`), not an array.
- **PermissionRequest**: The SSE-derived state type for permission requests: `{ requestId: string, toolName: string, toolInput: Record<string, unknown>, reason: string, options: string[] }`. A single nullable value (`PermissionRequest | null`), not an array.
- **Active_Session_Id**: The `sessionId` of the currently active chat thread in ChatPage that produced the SSE event. Passed as a prop to SwarmRadar to enable correlation between `pendingQuestion` and WIP tasks (PE Finding #3 fix).
- **SSE_Event_Arrival_Timestamp**: The timestamp captured when `pendingQuestion` or `pendingPermission` state is set in ChatPage, used as the `createdAt` for `RadarWaitingItem` to ensure stable sort ordering (PE Finding #1 fix).
- **ToReview_Item**: A completed task that requires user review. **Placeholder for initial release** — `review_required` is always `false` and `review_risk_level` is always `null`. Deferred to a future spec.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **ChatPage**: The main orchestrator component at `desktop/src/pages/ChatPage.tsx`.
- **Total_Order_Tiebreaker**: All sort functions use `id` (string comparison) as the ultimate tiebreaker after all other sort keys to guarantee deterministic ordering (PE Finding #6). The `sortWaitingItems` function is defined in Spec 1.
- **Dual_Presence**: A WIP task with `hasWaitingInput=true` appears in both the In Progress zone (as a WIP task) and the Needs Attention zone (as a Waiting Input item). This is by design — the user sees the blocked task in context and the actionable question in the attention zone.

## Requirements

### Requirement 1: Waiting Input / ToReview Sub-Section

**User Story:** As a knowledge worker, I want to see items that need my input in a dedicated sub-section of the Needs Attention zone, so that I can quickly unblock AI execution.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a "Waiting Input" sub-section below the ToDo sub-section.
2. THE Waiting Input sub-section SHALL display Waiting_Input_Items derived from SSE `ask_user_question` and `permission_request` events, passed as `pendingQuestion` and `pendingPermission` props from ChatPage. These items are NOT queried from the database — they are ephemeral and exist only during the active SSE session.
3. EACH Waiting_Input_Item derived from `pendingQuestion` SHALL display: the associated task title (looked up from WIP tasks by matching `activeSessionId` against task `sessionId`), the question text (derived from `pendingQuestion.questions[0].question` truncated to 200 characters), and a "Respond" Click_Action button.
4. EACH Waiting_Input_Item derived from `pendingPermission` SHALL display: the associated task title (looked up from WIP tasks by matching `activeSessionId` against task `sessionId`), the permission reason text (`pendingPermission.reason` truncated to 200 characters), and a "Respond" Click_Action button.
5. THE Waiting Input sub-section SHALL display items sorted by creation time (oldest first), with `id` as the ultimate tiebreaker (using `sortWaitingItems` from Spec 1, PE Finding #6).
6. WHEN there are no Waiting_Input_Items and no ToDo items, THE Needs_Attention_Zone SHALL display the empty state message from Spec 1: "All clear — nothing needs your attention right now."
7. WHEN there are no Waiting_Input_Items but there are ToDo items, THE Waiting Input sub-section SHALL not render (no empty sub-section message — the zone-level empty state handles the fully-empty case).
8. THE maximum number of Waiting_Input_Items in the initial release SHALL be 2 (one from `pendingQuestion`, one from `pendingPermission`). THE `RadarWaitingItem[]` array type is correct for extensibility — future SSE events may produce multiple items (PE Finding #2).
9. **(DEFERRED)** THE "To Review" portion of this sub-section is a placeholder for the initial release. No ToReview items will be populated until the risk-assessment mechanism is implemented in a future spec. THE `review_required` field on tasks is always `false` and `review_risk_level` is always `null`.

### Requirement 2: Waiting Input Item Derivation from SSE Props

**User Story:** As a developer, I want a clear derivation mechanism that maps SSE props into RadarWaitingItem objects, so that the Waiting Input sub-section displays correct, stable data.

#### Acceptance Criteria

1. THE `useWaitingInputZone` hook SHALL derive `RadarWaitingItem[]` from the `pendingQuestion` and `pendingPermission` props passed to SwarmRadar.
2. WHEN `pendingQuestion` is not null, THE hook SHALL create a `RadarWaitingItem` with the following mapping:
   - `id` = `pendingQuestion.toolUseId`
   - `title` = the matched WIP task's `title` (looked up by matching `activeSessionId` against WIP tasks' `sessionId`), or "Agent Question" as fallback if no matching task is found
   - `agentId` = the matched WIP task's `agentId`, or empty string as fallback
   - `sessionId` = `activeSessionId`
   - `question` = `pendingQuestion.questions[0].question` truncated to 200 characters. IF `pendingQuestion.questions` is empty, THE question SHALL be "Pending question" as fallback.
   - `createdAt` = the SSE event arrival timestamp (PE Finding #1). THE implementation SHALL use the matched WIP task's `startedAt` as a stable proxy. IF no matching task is found, THE implementation SHALL use the timestamp captured when `pendingQuestion` was set in ChatPage state.
3. WHEN `pendingPermission` is not null, THE hook SHALL create a `RadarWaitingItem` with the following mapping:
   - `id` = `pendingPermission.requestId`
   - `title` = the matched WIP task's `title` (same lookup as pendingQuestion), or "Permission Required" as fallback
   - `agentId` = the matched WIP task's `agentId`, or empty string as fallback
   - `sessionId` = `activeSessionId`
   - `question` = `pendingPermission.reason` truncated to 200 characters
   - `createdAt` = the SSE event arrival timestamp (same strategy as pendingQuestion)
4. WHEN both `pendingQuestion` and `pendingPermission` are null, THE hook SHALL return an empty array.
5. THE hook SHALL apply `sortWaitingItems` from Spec 1 to the derived array before returning.
6. THE derivation SHALL be a pure transformation of props — no side effects, no API calls, no state mutations.

### Requirement 3: hasWaitingInput Derivation on WIP Tasks

**User Story:** As a developer, I want the `hasWaitingInput` flag on WIP tasks to be correctly derived from SSE props, so that WIP tasks visually indicate when they are blocked waiting for user input.

#### Acceptance Criteria

1. THE `useWaitingInputZone` hook (or the composing `useSwarmRadar` hook) SHALL compute `hasWaitingInput` for each WIP task by checking if `activeSessionId` matches the task's `sessionId` AND either `pendingQuestion` or `pendingPermission` is not null.
2. WHEN a WIP task's `sessionId` matches `activeSessionId` AND `pendingQuestion` is not null, THE task's `hasWaitingInput` SHALL be `true`.
3. WHEN a WIP task's `sessionId` matches `activeSessionId` AND `pendingPermission` is not null, THE task's `hasWaitingInput` SHALL be `true`.
4. WHEN a WIP task's `sessionId` does NOT match `activeSessionId`, THE task's `hasWaitingInput` SHALL be `false`, regardless of `pendingQuestion` or `pendingPermission` state.
5. WHEN both `pendingQuestion` and `pendingPermission` are null, ALL WIP tasks SHALL have `hasWaitingInput` equal to `false`.
6. THE `hasWaitingInput` derivation SHALL produce Dual_Presence: a WIP task with `hasWaitingInput=true` appears in the In_Progress_Zone as a WIP task AND a corresponding `RadarWaitingItem` appears in the Needs_Attention_Zone.

### Requirement 4: activeSessionId Prop Addition

**User Story:** As a developer, I want ChatPage to pass the active session ID to SwarmRadar, so that the Waiting Input derivation can correlate pending questions with specific WIP tasks (PE Finding #3 fix).

#### Acceptance Criteria

1. THE `SwarmRadarProps` interface SHALL be extended with an `activeSessionId: string | undefined` prop.
2. THE `ChatPage.tsx` SHALL pass the current `sessionId` state variable as the `activeSessionId` prop to SwarmRadar.
3. THE `useSwarmRadar` hook's `UseSwarmRadarParams` interface SHALL be extended with `activeSessionId: string | undefined`.
4. THE `activeSessionId` SHALL be used by the `useWaitingInputZone` hook to correlate `pendingQuestion` and `pendingPermission` with WIP tasks' `sessionId` fields.
5. WHEN `activeSessionId` is undefined (no active session), THE Waiting Input derivation SHALL still produce `RadarWaitingItem` objects from non-null `pendingQuestion`/`pendingPermission` props, but with fallback values for `title` and `agentId` (since no task can be matched).

### Requirement 5: Pending Question / Permission Cleanup and Consolidation

**User Story:** As a developer, I want the existing `pendingQuestion` and `pendingPermission` state in ChatPage to be consolidated with the Waiting Input zone, so that there is a single source of truth for items needing user attention.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL surface `pendingQuestion` events (SSE `ask_user_question` type) as Waiting_Input_Items in the Needs_Attention_Zone. These are passed as the `pendingQuestion` prop from ChatPage to SwarmRadar. They are ephemeral — they exist only during the active SSE session and are NOT persisted in the database.
2. THE Swarm_Radar SHALL surface `pendingPermission` events (SSE `permission_request` type) as Waiting_Input_Items in the Needs_Attention_Zone. These are passed as the `pendingPermission` prop from ChatPage to SwarmRadar. They are ephemeral — same SSE-session-only behavior as pending questions.
3. THE existing `pendingQuestion` and `pendingPermission` state variables in `ChatPage.tsx` SHALL remain functional for the inline chat experience. THE chat thread still shows the question/permission inline — the Radar provides an additional entry point, not a replacement.
4. THE Swarm_Radar SHALL provide an additional entry point to these pending items — clicking a Waiting_Input_Item in the Radar SHALL navigate to or focus the relevant pending question/permission in the active chat thread.
5. WHEN a pending question or permission is resolved (answered or approved/denied in the chat thread), THE corresponding Waiting_Input_Item SHALL be removed from the Needs_Attention_Zone. This happens automatically because ChatPage sets `pendingQuestion`/`pendingPermission` to `null` when resolved, and the prop change triggers re-derivation.
6. WHEN the page is reloaded, pending questions and permissions SHALL be cleared from the Needs_Attention_Zone. THE agent will re-ask if the question is still relevant when the session resumes. There is no API endpoint to retrieve historical pending questions.

### Requirement 6: Click-to-Chat — Respond Action

**User Story:** As a knowledge worker, I want to click "Respond" on a Waiting Input item to navigate to the chat thread where the pending question is displayed, so that I can unblock the AI agent.

#### Acceptance Criteria

1. WHEN the user clicks "Respond" on a Waiting_Input_Item, THE System SHALL switch to the associated chat thread tab using the existing tab management system (`useTabState` hook).
2. THE "Respond" action SHALL use the `sessionId` from the `RadarWaitingItem` to identify the correct chat thread tab.
3. IF the associated chat thread tab is already open, THE System SHALL switch to that tab. IF the tab is not open, THE System SHALL open it.
4. THE "Respond" action SHALL scroll to or focus the pending question/permission in the chat thread (using the existing inline question display mechanism in ChatPage).
5. THE "Respond" button SHALL be visually prominent on the Waiting_Input_Item — not hidden behind a hover menu, since responding to blocked agents is a high-priority action.

### Requirement 7: WaitingInputList Component

**User Story:** As a developer, I want a dedicated WaitingInputList component that renders waiting input items with their respond actions, so that the Waiting Input sub-section is independently testable and maintainable.

#### Acceptance Criteria

1. THE WaitingInputList component SHALL be implemented at `desktop/src/pages/chat/components/radar/WaitingInputList.tsx`.
2. THE WaitingInputList component SHALL accept props: `waitingItems: RadarWaitingItem[]` and `onRespond: (itemId: string) => void`.
3. EACH WaitingInputItem SHALL render: the task title, the question text (already truncated to 200 chars by the derivation hook), and a "Respond" button.
4. THE WaitingInputItem SHALL use `--color-text` for the task title, `--color-text-muted` for the question text, and a visually distinct style for the "Respond" button.
5. THE WaitingInputList SHALL render items in the order provided (pre-sorted by the `useWaitingInputZone` hook).
6. EACH WaitingInputItem SHALL be focusable via Tab key navigation, with the "Respond" button accessible via Enter or Space.
7. THE WaitingInputList SHALL use `role="list"` and each item SHALL use `role="listitem"` for screen reader compatibility.

### Requirement 8: useWaitingInputZone Hook

**User Story:** As a developer, I want a dedicated React hook for Waiting Input zone state management, so that the SSE-to-Radar derivation logic is cleanly encapsulated and testable.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useWaitingInputZone` hook at `desktop/src/pages/chat/components/radar/hooks/useWaitingInputZone.ts`.
2. THE `useWaitingInputZone` hook SHALL accept parameters: `pendingQuestion: PendingQuestion | null`, `pendingPermission: PermissionRequest | null`, `activeSessionId: string | undefined`, and `wipTasks: RadarWipTask[]`.
3. THE hook SHALL return: `waitingItems: RadarWaitingItem[]` (derived and sorted), and `respondToItem: (itemId: string) => void` (action handler).
4. THE hook SHALL use `useMemo` to derive `RadarWaitingItem[]` from the input props, recomputing only when `pendingQuestion`, `pendingPermission`, `activeSessionId`, or `wipTasks` change.
5. THE hook SHALL NOT make any API calls — all data comes from props. There is no polling for waiting items.
6. THE `respondToItem` action handler SHALL use the existing tab management system (`useTabState` hook) to navigate to the chat thread associated with the waiting item's `sessionId`.
7. THE hook SHALL also export a pure derivation function `deriveWaitingItems(pendingQuestion, pendingPermission, activeSessionId, wipTasks): RadarWaitingItem[]` for unit and property-based testing.


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Waiting Input sort ordering by creation time (with id tiebreaker)

*For any* list of `RadarWaitingItem` objects with arbitrary `createdAt` timestamps and `id` values, the `sortWaitingItems` function SHALL produce a list ordered by `createdAt` ascending (oldest first). When two items have identical `createdAt` values, the item with the lexicographically smaller `id` SHALL come first. Sorting the same input twice SHALL produce identical output (idempotence). No two distinct items SHALL have ambiguous relative ordering — the `id` tiebreaker guarantees a total order (PE Finding #6).

**Validates:** Requirement 1.5
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 2: Task status changes and SSE events produce correct hasWaitingInput derivation

*For any* set of WIP tasks with arbitrary `sessionId` values, and *for any* `activeSessionId` value, and *for any* combination of `pendingQuestion` (null or non-null) and `pendingPermission` (null or non-null):
- A WIP task SHALL have `hasWaitingInput = true` if and only if its `sessionId` equals `activeSessionId` AND at least one of `pendingQuestion` or `pendingPermission` is not null.
- The count of WIP tasks with `hasWaitingInput = true` SHALL be at most 1 (since only one task can match the single `activeSessionId`), or 0 if no task matches or both pending props are null.
- When `activeSessionId` is undefined, ALL WIP tasks SHALL have `hasWaitingInput = false`.

**Validates:** Requirement 3.1, 3.2, 3.3, 3.4, 3.5
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 3: Waiting item derivation produces correct count and mapping

*For any* combination of `pendingQuestion` (null or non-null) and `pendingPermission` (null or non-null):
- When both are null, the derived `RadarWaitingItem[]` SHALL be empty (length 0).
- When only `pendingQuestion` is non-null, the derived array SHALL have exactly 1 item with `id` equal to `pendingQuestion.toolUseId`.
- When only `pendingPermission` is non-null, the derived array SHALL have exactly 1 item with `id` equal to `pendingPermission.requestId`.
- When both are non-null, the derived array SHALL have exactly 2 items — one with `id` equal to `pendingQuestion.toolUseId` and one with `id` equal to `pendingPermission.requestId`.
- The maximum length of the derived array SHALL be 2 in the initial release (PE Finding #2).

**Validates:** Requirement 1.8, 2.1, 2.2, 2.3, 2.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 4: Waiting item question text truncation

*For any* `pendingQuestion` with a `questions[0].question` string of arbitrary length, the derived `RadarWaitingItem.question` SHALL have length at most 200 characters. If the original question is 200 characters or fewer, the derived question SHALL equal the original. If the original question exceeds 200 characters, the derived question SHALL be the first 200 characters of the original (or first 197 characters plus "..." — implementation may choose either truncation strategy, but the result SHALL never exceed 200 characters). The same truncation rule SHALL apply to `pendingPermission.reason`.

**Validates:** Requirement 2.2, 2.3
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 5: Waiting items disappear when pending props become null

*For any* non-null `pendingQuestion` that produces a `RadarWaitingItem`, when `pendingQuestion` transitions to null (question answered), the re-derived `RadarWaitingItem[]` SHALL NOT contain an item with the previous `pendingQuestion.toolUseId` as its `id`. The same property SHALL hold for `pendingPermission` transitioning to null. This validates the automatic cleanup behavior described in Requirement 5.5.

**Validates:** Requirement 5.5, 5.6
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 6: Dual presence — hasWaitingInput implies corresponding RadarWaitingItem exists

*For any* WIP task with `hasWaitingInput = true`, there SHALL exist exactly one `RadarWaitingItem` in the derived waiting items array whose `sessionId` matches the task's `sessionId`. Conversely, *for any* `RadarWaitingItem` in the derived array, there SHALL be at most one WIP task whose `sessionId` matches the item's `sessionId` (the task that produced the SSE event). If no WIP task matches, the waiting item still exists but with fallback title/agentId values.

**Validates:** Requirement 3.6, 2.2, 2.3
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`

### Property 7: Derivation is a pure function of inputs

*For any* identical set of inputs (`pendingQuestion`, `pendingPermission`, `activeSessionId`, `wipTasks`), the `deriveWaitingItems` function SHALL produce identical output. Calling the function multiple times with the same inputs SHALL be idempotent — no side effects, no state mutations, no accumulated changes.

**Validates:** Requirement 2.6, 8.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/waitingInput.property.test.ts`
