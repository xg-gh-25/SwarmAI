<!-- STALE REFERENCES: This spec references code that has since been refactored or removed:
- ContextPreviewPanel → REMOVED (was planned for future project detail view, never rendered in production)
- useTabState / tabStateRef / saveTabState → SUPERSEDED by useUnifiedTabState hook
- saveCurrentTab → REMOVED (was a no-op in useUnifiedTabState)
This spec is preserved as a historical record of the design decisions made at the time. -->

# Requirements Document — Swarm Radar Redesign (Overall Spec)

## Introduction

This document defines the comprehensive requirements for the **Swarm Radar** redesign — transforming the current mock `TodoRadarSidebar` into a full unified attention & action control panel for SwarmAI. The Swarm Radar is the right-sidebar panel that provides real-time, glanceable awareness of all work items across their lifecycle:

**Source → ToDo → Task (WIP) → Waiting Input / Review → Completed → Archived**

The Swarm Radar answers five questions at a glance:
1. What needs my attention? (ToDos, Waiting Input)
2. What is the AI working on? (WIP Tasks)
3. What is waiting for my input or review? (Waiting Input / ToReview)
4. What has been completed? (Recently Completed)
5. What is running automatically? (Autonomous Jobs)

This is the **overall spec** covering all sections. After alignment, it will be broken into 5 executable sub-specs:
1. **Foundation** — Layout, zones, sidebar shell, mock data infrastructure
2. **ToDos & Quick-Add** — ToDo unified inbox, quick-add, lifecycle actions
3. **Waiting Input & Review** — Mid-execution input, conditional review
4. **WIP Tasks & Completed** — In-progress tasks, recently completed archive
5. **Autonomous Jobs & External Integrations** — System/user jobs, integration placeholders

### Source of Truth

The product design document at `.kiro/specs/swarm-radar-specs/SWARM-RADAR-PRODUCT-DESIGN.md` is the authoritative source for Swarm Radar behavior and structure.

### Cross-References (No Hard Dependencies)

| Spec | Reference Purpose |
|------|-------------------|
| `thread-scoped-cognitive-context` | TSCC_State, Thread_Lifecycle_State, SSE Telemetry_Events — reference data models to avoid duplication |
| `swarmws-explorer-ux` | Semantic_Zone concept for visual grouping inspiration |
| `swarmai-product-design-specs/swarmai-product-design-principles.md` | Core competitive design principles alignment |

### Design Principles Alignment

- **Signals First: Separate Intent From Execution** — ToDos are structured intent signals, separate from Tasks
- **Chat is the Command Surface** — Click-based actions in Radar feed into chat for execution
- **Visible Planning Builds Trust** — WIP Tasks show execution state transparently
- **Progressive Disclosure** — Collapsed zones, expandable sections, minimal default view
- **Human Review Gates** — Waiting Input / ToReview surfaces only necessary decisions
- **Glanceable Awareness** — Users understand priorities in seconds

### What This Replaces

- The current mock `TodoRadarSidebar` component (`desktop/src/pages/chat/components/TodoRadarSidebar.tsx`) with hardcoded `MOCK_OVERDUE_ITEMS` and `MOCK_PENDING_ITEMS`
- The existing `pendingQuestion` and `pendingPermission` state management in `ChatPage.tsx` will be cleaned up and consolidated into the Waiting Input zone

## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Replaces the former `TodoRadarSidebar`.
- **Radar_Zone**: A visually distinct, collapsible section within the Swarm_Radar. Four zones exist: Needs Attention, In Progress, Completed, and Autonomous Jobs.
- **Needs_Attention_Zone**: The top Radar_Zone containing ToDos (Unified Inbox) and Waiting Input / ToReview items. Indicated by 🔴.
- **In_Progress_Zone**: The Radar_Zone containing WIP Tasks currently being executed. Indicated by 🟡.
- **Completed_Zone**: The Radar_Zone containing recently completed tasks within the archive window. Indicated by 🟢.
- **Autonomous_Jobs_Zone**: The Radar_Zone containing system built-in and user-defined recurring agent jobs. Indicated by 🤖.
- **ToDo**: A structured intent signal representing incoming work. DB-canonical entity with fields: id, workspace_id, title, description, source, source_type, status, priority, due_date, linked_context, created_at, updated_at. Existing schema at `backend/schemas/todo.py`.
- **ToDo_Source_Type**: The origin of a ToDo: manual, email, slack, meeting, integration, chat, ai_detected.
- **ToDo_Priority**: Priority level of a ToDo: high, medium, low, none.
- **ToDo_Status**: Lifecycle state of a ToDo: pending, overdue, in_discussion, handled, cancelled, deleted.
- **Quick_Add**: A simple inline input within the Needs_Attention_Zone for creating ToDos manually without leaving the Radar.
- **WIP_Task**: A Task entity in an active execution state (`wip`, `draft`, `blocked`). Displayed in the In_Progress_Zone. These are the actual frontend `TaskStatus` values — there is no `running`, `pending`, `waiting_for_input`, or `paused` status.
- **Completed_Task**: A Task entity that has finished execution. Displayed temporarily in the Completed_Zone before archival.
- **Archive_Window**: The time period (default 7 days) after which completed tasks are removed from the Completed_Zone and moved to history.
- **Autonomous_Job**: A background or recurring agent job. Two categories: System_Built_In (sync, indexing) and User_Defined (daily digest, reports).
- **System_Built_In_Job**: An autonomous job managed by the system (e.g., workspace sync, knowledge indexing).
- **User_Defined_Job**: An autonomous job created by the user (e.g., daily digest, weekly report generation).
- **Waiting_Input_Item**: An item derived from SSE `ask_user_question` events in the active chat session, passed as props from ChatPage to SwarmRadar. Ephemeral — exists only during the active SSE session. Disappears on page reload. The agent will re-ask if the question is still relevant.
- **ToReview_Item**: A completed task that requires user review based on risk-level policy before final closure. **Placeholder for initial release** — `review_required` and `review_risk_level` are always `false`/`null`. Risk-assessment logic is deferred to a future spec.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **Mock_Data**: Realistic sample data pre-populated in all Radar zones to demonstrate the full feature. Replaces existing hardcoded mock data.
- **Priority_Indicator**: Visual emoji/icon indicators for ToDo priority: 🔴 High, 🟡 Medium, 🔵 Low, ⏰ Due Today, ⚠️ Overdue.
- **Zone_Badge**: A count badge displayed next to a Radar_Zone header showing the number of items in that zone.
- **ChatPage**: The main orchestrator component at `desktop/src/pages/ChatPage.tsx`.
- **useRightSidebarGroup**: The existing hook managing mutual exclusion for right sidebars (TodoRadar, ChatHistory, FileBrowser).
- **Task**: The existing execution entity. Schema at `backend/schemas/` and types at `desktop/src/types/index.ts`. Has fields: id, workspaceId, agentId, sessionId, status, title, description, priority, sourceTodoId, etc.
- **TSCC_State**: Thread-Scoped Cognitive Context state from the TSCC spec. Referenced for thread lifecycle awareness — no hard dependency.
- **Telemetry_Event**: SSE events from the TSCC spec (agent_activity, tool_invocation, etc.). Referenced for real-time WIP Task status updates — no hard dependency.

## Requirements

### Requirement 1: Swarm Radar Shell — Layout and Zone Structure

**User Story:** As a knowledge worker, I want the right sidebar to be a structured, zone-based control panel, so that I can instantly see what needs attention, what is in progress, what is done, and what runs autonomously.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL replace the existing `TodoRadarSidebar` component entirely, removing all hardcoded `MOCK_OVERDUE_ITEMS` and `MOCK_PENDING_ITEMS`.
2. THE Swarm_Radar SHALL display four collapsible Radar_Zones in fixed vertical order: Needs_Attention_Zone (🔴), In_Progress_Zone (🟡), Completed_Zone (🟢), Autonomous_Jobs_Zone (🤖).
3. THE Swarm_Radar SHALL display a header bar with the title "Swarm Radar", a radar icon, and a close button (using the existing `onClose` pattern).
4. EACH Radar_Zone SHALL display a zone header with: zone emoji indicator, zone label, and a Zone_Badge showing the count of items in that zone.
5. EACH Radar_Zone SHALL be independently collapsible via click on the zone header.
6. THE Swarm_Radar SHALL persist zone expand/collapse state per session (in memory, not localStorage).
7. THE Swarm_Radar SHALL use CSS variables in `--color-*` format for all colors, with soft visual separators between zones (inspired by the Semantic_Zone concept from `swarmws-explorer-ux`).
8. THE Swarm_Radar SHALL integrate with the existing `useRightSidebarGroup` hook, maintaining the `todoRadar` sidebar ID and mutual exclusion with ChatHistory and FileBrowser sidebars.
9. THE Swarm_Radar SHALL support the existing resize handle pattern (left-edge drag) with the width constraints defined in `RIGHT_SIDEBAR_WIDTH_CONFIGS`.
10. THE Swarm_Radar SHALL use virtualized or lazy rendering for zones with many items to maintain scroll performance.

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
2. THE Mock_Data for Needs_Attention_Zone SHALL include at least: 3 ToDo items (one high priority overdue, one medium due today, one low priority), and 2 Waiting_Input_Items (one mid-execution question, one conditional review).
3. THE Mock_Data for In_Progress_Zone SHALL include at least: 2 WIP_Tasks (one executing, one paused/waiting for input).
4. THE Mock_Data for Completed_Zone SHALL include at least: 3 Completed_Tasks with varying completion timestamps within the 7-day Archive_Window.
5. THE Mock_Data for Autonomous_Jobs_Zone SHALL include at least: 2 System_Built_In_Jobs (e.g., "Workspace Sync", "Knowledge Indexing") and 2 User_Defined_Jobs (e.g., "Daily Digest", "Weekly Report").
6. THE Mock_Data SHALL use realistic titles, descriptions, priorities, timestamps, and source types that demonstrate the intended usage patterns.
7. THE Mock_Data SHALL be defined in a dedicated mock data module (not inline in components) to facilitate future replacement with real API data.
8. THE existing hardcoded mock data in `TodoRadarSidebar.tsx` SHALL be deleted when the component is replaced.

### Requirement 4: ToDo Unified Inbox — Display and Sorting

**User Story:** As a knowledge worker, I want to see all my ToDos in a unified inbox within the Needs Attention zone, so that I can triage and prioritize incoming work signals.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a "ToDos" sub-section listing all active ToDo items (status: pending or overdue).
2. EACH ToDo item SHALL display: title, source type icon/label, Priority_Indicator, and due date (if set).
3. THE ToDo list SHALL sort items by: overdue items first, then by priority (high → medium → low → none), then by due date (earliest first), then by creation date (newest first).
4. WHEN a ToDo has status `overdue`, THE ToDo item SHALL display the ⚠️ Overdue indicator prominently.
5. WHEN a ToDo has a due date matching today, THE ToDo item SHALL display the ⏰ Due Today indicator.
6. THE ToDo list SHALL display the source type for each item using a compact label: 📧 Email, 💬 Slack, 📅 Meeting, 🔗 Integration, 💭 Chat, 🤖 AI-detected, ✏️ Manual.
7. THE ToDo list SHALL fetch data from the existing `GET /api/todos` backend endpoint (DB-canonical, query via API).

### Requirement 5: ToDo Lifecycle Actions — Click-Based

**User Story:** As a knowledge worker, I want to start, edit, complete, cancel, or delete ToDos directly from the Radar via click actions, so that I can manage my work without navigating away from chat.

#### Acceptance Criteria

1. EACH ToDo item SHALL display a compact action menu (accessible via a `⋯` overflow button shown on hover) with the following Click_Actions: Start, Edit, Complete, Cancel, Delete.
2. WHEN the user clicks "Start" on a ToDo, THE System SHALL convert the ToDo to a Task using the existing `convert_to_task` API endpoint, update the ToDo status to `handled`, and move the resulting WIP_Task to the In_Progress_Zone.
3. WHEN the user clicks "Edit" on a ToDo, THE System SHALL display an inline edit form allowing modification of title, description, priority, and due date.
4. WHEN the user clicks "Complete" on a ToDo, THE System SHALL update the ToDo status to `handled` without creating a Task (resolved without execution).
5. WHEN the user clicks "Cancel" on a ToDo, THE System SHALL update the ToDo status to `cancelled` and remove the ToDo from the active list.
6. WHEN the user clicks "Delete" on a ToDo, THE System SHALL update the ToDo status to `deleted` and remove the ToDo from the active list. THE deletion SHALL be traceable in history.
7. THE action menu SHALL use minimal icons shown only on hover, consistent with the visual design principles from `swarmws-explorer-ux` (Requirement 14, AC 5).
8. WHEN a destructive action (Cancel, Delete) is selected, THE System SHALL display a brief inline confirmation before executing.

### Requirement 6: Quick-Add ToDo

**User Story:** As a knowledge worker, I want to quickly create a new ToDo directly from the Radar without opening a modal, so that I can capture work signals with minimal friction.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a Quick_Add input at the top of the ToDo sub-section.
2. THE Quick_Add input SHALL be a single-line text field with a placeholder "Add a ToDo..." and a submit button (or Enter key).
3. WHEN the user submits a Quick_Add input, THE System SHALL create a new ToDo via the existing `POST /api/todos` endpoint with: the entered text as title, source_type as `manual`, priority as `none`, and status as `pending`.
4. WHEN a Quick_Add ToDo is created successfully, THE new ToDo SHALL appear at the appropriate position in the sorted ToDo list.
5. THE Quick_Add input SHALL clear after successful submission.
6. IF the Quick_Add submission fails, THEN THE System SHALL display a brief inline error message below the input field.

### Requirement 7: Waiting Input / ToReview Sub-Section

**User Story:** As a knowledge worker, I want to see items that need my input or review in a dedicated sub-section, so that I can quickly unblock AI execution and review completed work.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a "Waiting Input / Review" sub-section below the ToDo sub-section.
2. THE Waiting Input sub-section SHALL display Waiting_Input_Items derived from SSE `ask_user_question` and `permission_request` events, passed as `pendingQuestion` and `pendingPermission` props from ChatPage. These are NOT queried from the database. **To Review is a placeholder in the initial release** — `review_required` is always `false` and `review_risk_level` is always `null`. Risk-assessment logic is deferred to a future spec.
3. EACH Waiting_Input_Item SHALL display: task title, the question text (derived from the first element of the `AskUserQuestion[]` array's `.question` field, truncated to 200 characters), the agent name, and a "Respond" Click_Action button.
4. WHEN the user clicks "Respond" on a Waiting_Input_Item, THE System SHALL navigate to or open the associated chat thread where the pending question is displayed.
5. **(DEFERRED)** EACH ToReview_Item SHALL display: task title, risk level indicator (Medium, High, Critical), completion summary (truncated), and "Review" / "Approve" Click_Action buttons. **This is a placeholder for the initial release — no ToReview items will be populated until the risk-assessment mechanism is implemented in a future spec.**
6. **(DEFERRED)** WHEN the user clicks "Review" on a ToReview_Item, THE System SHALL navigate to the associated chat thread for detailed review.
7. **(DEFERRED)** WHEN the user clicks "Approve" on a ToReview_Item, THE System SHALL mark the task as approved and move the item to the Completed_Zone.
8. THE Waiting Input sub-section SHALL display items sorted by creation time (oldest first).

### Requirement 8: Pending Question / Permission Cleanup

**User Story:** As a developer, I want the existing `pendingQuestion` and `pendingPermission` state in ChatPage to be consolidated with the Waiting Input zone, so that there is a single source of truth for items needing user attention.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL surface `pendingQuestion` events (SSE `ask_user_question` type) as Waiting_Input_Items in the Needs_Attention_Zone. These are passed as the `pendingQuestion` prop from ChatPage to SwarmRadar. They are ephemeral — they exist only during the active SSE session and are NOT persisted in the database.
2. THE Swarm_Radar SHALL surface `pendingPermission` events (SSE `permission_request` type) as Waiting_Input_Items in the Needs_Attention_Zone. These are passed as the `pendingPermission` prop from ChatPage to SwarmRadar. They are ephemeral — same SSE-session-only behavior as pending questions.
3. THE existing `pendingQuestion` and `pendingPermission` state variables in `ChatPage.tsx` SHALL remain functional for the inline chat experience (the chat thread still shows the question/permission inline).
4. THE Swarm_Radar SHALL provide an additional entry point to these pending items — clicking a Waiting_Input_Item in the Radar SHALL scroll to or focus the relevant pending question/permission in the active chat thread.
5. WHEN a pending question or permission is resolved (answered or approved/denied), THE corresponding Waiting_Input_Item SHALL be removed from the Needs_Attention_Zone.
6. WHEN the page is reloaded, pending questions and permissions SHALL be cleared from the Needs_Attention_Zone. The agent will re-ask if the question is still relevant when the session resumes. There is no API endpoint to retrieve historical pending questions.

### Requirement 9: WIP Tasks — In Progress Zone

**User Story:** As a knowledge worker, I want to see all currently executing tasks in the In Progress zone, so that I know what the AI is working on at any moment.

#### Acceptance Criteria

1. THE In_Progress_Zone SHALL display all WIP_Tasks with active execution states: `wip`, `draft`, or `blocked` (the actual frontend `TaskStatus` values).
2. EACH WIP_Task item SHALL display: task title, agent name, execution status indicator (🔄 WIP (active), 📋 Draft (queued), 🚫 Blocked), elapsed time since start, and a progress hint (if available).
3. WHEN the user clicks on a WIP_Task item, THE System SHALL navigate to or open the associated chat thread for that task.
4. EACH WIP_Task item SHALL display a compact action menu (on hover) with: "View Thread" and "Cancel".
5. THE WIP_Task list SHALL fetch data from the existing `GET /api/tasks` backend endpoint, filtered by active statuses (`status=wip,draft,blocked`).
6. THE WIP_Task list SHALL sort items by: `blocked` first (needs attention), then `wip` (active), then `draft` (queued), then by start time (most recent first).
7. WHEN a WIP_Task transitions to `completed` status, THE item SHALL move from the In_Progress_Zone to the Completed_Zone.
8. WHEN a WIP_Task has a pending SSE `ask_user_question` event (i.e., the `pendingQuestion` prop from ChatPage references this task's session), THE item SHALL also appear as a Waiting_Input_Item in the Needs_Attention_Zone (dual presence via props). The `hasWaitingInput` flag on `RadarWipTask` SHALL be set to `true`.

### Requirement 10: Completed Tasks — Recently Completed Zone

**User Story:** As a knowledge worker, I want to see recently completed tasks in a lightweight closure zone, so that I can review outcomes and track what has been accomplished.

#### Acceptance Criteria

1. THE Completed_Zone SHALL display all Completed_Tasks that finished within the Archive_Window (default 7 days).
2. EACH Completed_Task item SHALL display: task title, completion timestamp (relative, e.g., "2h ago", "Yesterday"), agent name, and a brief outcome summary (truncated to 1 line).
3. WHEN the user clicks on a Completed_Task item, THE System SHALL navigate to the associated chat thread to review the full execution history.
4. EACH Completed_Task item SHALL display a compact action menu (on hover) with: "View Thread" and "Resume" (to create a new thread seeded with completion context).
5. THE Completed_Zone SHALL fetch data from the existing `GET /api/tasks` endpoint, filtered by `completed` status and `completed_at` within the Archive_Window.
6. THE Completed_Zone SHALL sort items by completion time (most recent first).
7. WHEN a Completed_Task exceeds the 7-day Archive_Window, THE item SHALL be automatically removed from the Completed_Zone display. THE task remains in the database for full traceability.
8. THE Completed_Zone header SHALL display the count of recently completed tasks as a Zone_Badge.

### Requirement 11: Autonomous Jobs Zone

**User Story:** As a knowledge worker, I want to see system background jobs and my recurring agent jobs in a dedicated zone, so that I know what is running automatically on my behalf.

#### Acceptance Criteria

1. THE Autonomous_Jobs_Zone SHALL display two sub-sections: "System" for System_Built_In_Jobs and "Recurring" for User_Defined_Jobs.
2. EACH System_Built_In_Job SHALL display: job name, status indicator (✅ Running, ⏸️ Paused, ❌ Error), and last run timestamp.
3. EACH User_Defined_Job SHALL display: job name, schedule description (e.g., "Daily at 9am", "Every Monday"), status indicator, and last run timestamp.
4. WHEN an Autonomous_Job enters an error or attention-needed state, THE job SHALL also surface as an item in the Needs_Attention_Zone with a link back to the Autonomous_Jobs_Zone.
5. WHEN the user clicks on an Autonomous_Job, THE System SHALL open a configuration or discussion context for that job (placeholder for future implementation — display a "Coming soon" tooltip in the initial release).
6. THE Autonomous_Jobs_Zone SHALL use Mock_Data for the initial release, with placeholder APIs that return realistic sample data.
7. THE Mock_Data SHALL include both system categories (Workspace Sync, Knowledge Indexing, Overdue Check) and user-defined categories (Daily Digest, Weekly Report, Code Review Reminder).

### Requirement 12: Priority Indicators and Visual Hierarchy

**User Story:** As a knowledge worker, I want clear visual priority and timeline indicators across all Radar zones, so that I can instantly identify what is urgent.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use consistent Priority_Indicators across all zones: 🔴 for High priority, 🟡 for Medium priority, 🔵 for Low priority.
2. THE Swarm_Radar SHALL use timeline indicators: ⏰ for items due today, ⚠️ for overdue items.
3. THE Needs_Attention_Zone header SHALL use a red-tinted Zone_Badge when overdue or high-priority items exist.
4. THE In_Progress_Zone header SHALL use a yellow-tinted Zone_Badge.
5. THE Completed_Zone header SHALL use a green-tinted Zone_Badge.
6. THE Autonomous_Jobs_Zone header SHALL use a neutral-tinted Zone_Badge, switching to red-tinted when any job is in error state.
7. ALL color tints SHALL use CSS variables in `--color-*` format, never hardcoded color values.

### Requirement 13: Click-to-Chat Action Model

**User Story:** As a knowledge worker, I want to act on any Radar item via click actions that feed into the chat, so that all deep work happens in the conversational command surface.

#### Acceptance Criteria

1. WHEN the user clicks "Start" on a ToDo, THE System SHALL create a new chat thread (or reuse an existing one) with the ToDo context pre-loaded, and navigate the user to that thread.
2. WHEN the user clicks "View Thread" on a WIP_Task, THE System SHALL switch to the associated chat thread tab.
3. WHEN the user clicks "Respond" on a Waiting_Input_Item, THE System SHALL switch to the associated chat thread and scroll to the pending question.
4. WHEN the user clicks "Resume" on a Completed_Task, THE System SHALL create a new chat thread seeded with the completion context from the original thread.
5. ALL click actions SHALL use the existing tab management system (`useTabState` hook) to open or switch to the appropriate chat thread tab.
6. THE click-to-chat model SHALL be the primary interaction pattern. Drag-and-drop is deferred to a future spec.

### Requirement 14: Swarm Radar — Accessibility

**User Story:** As a knowledge worker using assistive technology, I want the Swarm Radar to be keyboard navigable and screen reader compatible, so that I can manage my work regardless of how I interact with the application.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL be keyboard navigable: zone headers SHALL be focusable and expandable via Enter or Space key.
2. EACH Radar item SHALL be focusable via Tab key navigation, with the action menu accessible via Enter or Space.
3. THE Swarm_Radar SHALL use appropriate ARIA attributes: `role="region"` with `aria-label="Swarm Radar"`, zone headers with `aria-expanded` reflecting collapse state, and item lists with `role="list"`.
4. THE Quick_Add input SHALL have an accessible label: `aria-label="Add a new ToDo"`.
5. THE Zone_Badge counts SHALL be announced to screen readers using `aria-label` (e.g., "Needs Attention, 5 items").
6. WHEN a new item appears in the Needs_Attention_Zone, THE Swarm_Radar SHALL use an `aria-live="polite"` region to announce the new item to screen readers.

### Requirement 15: Backend — ToDo Schema Extensions

**User Story:** As a developer, I want the ToDo schema to support additional source types and linked context, so that the Radar can display rich ToDo metadata from all sources.

#### Acceptance Criteria

1. THE Backend SHALL extend the `ToDoSourceType` enum in `backend/schemas/todo.py` to include two additional values: `chat` and `ai_detected`.
2. THE Backend SHALL add an optional `linked_context` field to the `ToDoCreate`, `ToDoUpdate`, and `ToDoResponse` models. THE `linked_context` field SHALL be a JSON string containing reference metadata (e.g., `{"type": "thread", "thread_id": "abc123"}` or `{"type": "message", "message_id": "xyz789"}`).
3. THE Backend SHALL use snake_case field names for all new and existing fields.
4. THE Frontend SHALL define corresponding camelCase TypeScript interfaces and update `toCamelCase()` / `toSnakeCase()` conversion functions in the ToDo service layer.
5. THE existing `ToDoStatus` enum values (pending, overdue, in_discussion, handled, cancelled, deleted) SHALL remain unchanged.
6. THE Backend SHALL implement a SQLite migration strategy for the schema changes:
   - The `linked_context` column SHALL be added via `ALTER TABLE todos ADD COLUMN linked_context TEXT` (safe, non-destructive).
   - The `source_type` CHECK constraint cannot be altered in SQLite via `ALTER TABLE`. The migration SHALL use the standard SQLite table-rebuild pattern (create new table with updated CHECK → copy data → drop old → rename new) to add `chat` and `ai_detected` to the allowed values.
   - Pydantic enum validation (`ToDoSourceType`) SHALL serve as the primary enforcement layer for allowed `source_type` values, with the SQLite CHECK as a secondary safeguard.

### Requirement 16: Backend — Autonomous Jobs Placeholder API

**User Story:** As a developer, I want placeholder API endpoints for autonomous jobs that return realistic mock data, so that the frontend can render the Autonomous Jobs zone with real API calls.

#### Acceptance Criteria

1. THE Backend SHALL provide a `GET /api/autonomous-jobs` endpoint that returns a list of Autonomous_Job objects.
2. EACH Autonomous_Job response SHALL contain: `id` (string), `name` (string), `category` (enum: "system" or "user_defined"), `status` (enum: "running", "paused", "error", "completed"), `schedule` (optional string), `last_run_at` (optional ISO 8601 timestamp), `next_run_at` (optional ISO 8601 timestamp), `description` (optional string).
3. THE `GET /api/autonomous-jobs` endpoint SHALL return hardcoded mock data in the initial release, including both System_Built_In_Jobs and User_Defined_Jobs.
4. THE Backend SHALL define Pydantic models for Autonomous_Job using snake_case field names.
5. THE Frontend SHALL define TypeScript interfaces for Autonomous_Job using camelCase field names with appropriate `toCamelCase()` conversion.

### Requirement 17: Backend — Task Query Support and Schema Extensions

**User Story:** As a developer, I want backend query support for filtering tasks by actual status values and completion date, so that the Radar can populate the WIP and Completed zones.

#### Acceptance Criteria

1. THE Backend SHALL support filtering tasks by actual `TaskStatus` values via the existing `GET /api/tasks` endpoint. The actual frontend statuses are: `draft`, `wip`, `blocked`, `completed`, `cancelled`. There is NO `waiting_for_input` status — waiting state is detected via SSE events passed as props from ChatPage.
2. THE Backend SHALL add a `review_required` boolean field to the Task response model. **This field is always `false` in the initial release** — the population mechanism for risk-assessment is deferred to a future spec.
3. THE Backend SHALL add a `review_risk_level` optional field (enum: "low", "medium", "high", "critical") to the Task response model. **This field is always `null` in the initial release** — deferred to a future spec.
4. THE Backend SHALL support filtering tasks by `completed_after=<ISO8601>` via the existing `GET /api/tasks` endpoint, for archive window filtering.
5. THE Backend SHALL use snake_case field names for all new fields.
6. THE Frontend SHALL update the Task TypeScript interface and `toCamelCase()` conversion in `desktop/src/services/tasks.ts` to include the new fields.
7. THE `GET /api/tasks` endpoint SHALL use comma-separated query parameter format with OR semantics within the same parameter and AND semantics across different parameter types. Example: `GET /api/tasks?status=wip,draft,blocked&workspace_id=abc` means `(status=wip OR status=draft OR status=blocked) AND workspace_id=abc`.

### Requirement 18: Frontend — Swarm Radar Service Layer

**User Story:** As a developer, I want a dedicated frontend service module for Swarm Radar data fetching, so that API calls are centralized and follow the established service pattern.

#### Acceptance Criteria

1. THE Frontend SHALL create a `desktop/src/services/radar.ts` service module that centralizes all Swarm Radar API calls.
2. THE radar service SHALL include functions for: fetching active ToDos, fetching WIP tasks, fetching completed tasks (within archive window), fetching waiting-input items, fetching to-review items, and fetching autonomous jobs.
3. THE radar service SHALL implement `toCamelCase()` conversion for all backend responses following the established pattern in `desktop/src/services/tasks.ts`.
4. THE radar service SHALL implement `toSnakeCase()` conversion for all request payloads sent to the backend.
5. THE radar service SHALL use React Query (`useQuery`) integration patterns consistent with existing services.

### Requirement 19: Frontend — Swarm Radar State Management

**User Story:** As a developer, I want a dedicated React hook for Swarm Radar state management, so that zone data, collapse state, and real-time updates are cleanly managed.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useSwarmRadar` hook that manages state for all four Radar_Zones.
2. THE `useSwarmRadar` hook SHALL use React Query for data fetching with appropriate polling intervals (e.g., 30 seconds for ToDos and tasks, 60 seconds for autonomous jobs).
3. THE `useSwarmRadar` hook SHALL manage per-zone expand/collapse state in memory.
4. THE `useSwarmRadar` hook SHALL provide computed counts for each zone (used by Zone_Badges).
5. THE `useSwarmRadar` hook SHALL expose action handlers for ToDo lifecycle actions (start, edit, complete, cancel, delete) and task actions (view thread, cancel).
6. THE `useSwarmRadar` hook SHALL handle optimistic updates for lifecycle actions (update UI immediately, revert on API failure).
7. ALL React Query polling hooks within `useSwarmRadar` SHALL be gated by an `enabled: isVisible` flag, where `isVisible` is derived from `rightSidebars.isActive('todoRadar')`. WHEN the sidebar is hidden or collapsed, zero polling queries SHALL execute.
8. THE `useSwarmRadar` hook SHALL be decomposed into per-zone hooks for maintainability: `useTodoZone()` (todo data + actions), `useTaskZone()` (WIP + completed task data + actions), and `useJobZone()` (autonomous job data). The main `useSwarmRadar` hook SHALL compose these sub-hooks and manage zone expand/collapse state.
9. THE `useSwarmRadar` hook SHALL accept `pendingQuestion` and `pendingPermission` props and derive `RadarWaitingItem[]` from them for the Needs Attention zone.

### Requirement 20: Frontend — Component Architecture

**User Story:** As a developer, I want the Swarm Radar built with a clean component hierarchy, so that each zone and item type is independently testable and maintainable.

#### Acceptance Criteria

1. THE Frontend SHALL implement the following component hierarchy:
   - `SwarmRadar` — Root shell component (replaces `TodoRadarSidebar`)
   - `RadarZone` — Reusable collapsible zone wrapper
   - `TodoList` — ToDo items list with sorting
   - `TodoItem` — Individual ToDo with actions
   - `QuickAddTodo` — Inline quick-add input
   - `WaitingInputList` — Waiting input / review items
   - `WipTaskList` — WIP task items
   - `CompletedTaskList` — Completed task items
   - `AutonomousJobList` — Autonomous job items
2. EACH component SHALL be placed in `desktop/src/pages/chat/components/radar/` directory.
3. THE `SwarmRadar` root component SHALL accept the same props interface as the current `TodoRadarSidebar` (width, isResizing, onClose, onMouseDown) for drop-in replacement.
4. THE `ChatPage.tsx` SHALL import `SwarmRadar` instead of `TodoRadarSidebar`, with no other changes to the ChatPage integration point.

### Requirement 21: Completed Tasks — Archive Window Enforcement

**User Story:** As a knowledge worker, I want completed tasks to automatically disappear from the Radar after 7 days, so that the completed zone stays clean and focused on recent work.

#### Acceptance Criteria

1. THE Completed_Zone SHALL display only tasks where `completed_at` is within the last 7 days (Archive_Window).
2. THE Frontend SHALL filter completed tasks client-side based on the Archive_Window, using the `completedAt` timestamp from the Task response.
3. THE Backend SHALL support an optional `completed_after` query parameter on the `GET /api/tasks` endpoint to allow server-side filtering by completion date.
4. THE Archive_Window value (7 days) SHALL be defined as a constant in the radar configuration, allowing future configurability.

### Requirement 22: Real-Time Updates via Polling

**User Story:** As a knowledge worker, I want the Radar to reflect changes in near real-time, so that new ToDos, task status changes, and job updates appear without manual refresh.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use React Query polling to refresh data at regular intervals: 30 seconds for ToDos and tasks, 60 seconds for autonomous jobs.
2. WHEN a user performs a lifecycle action (e.g., start ToDo, cancel task), THE Swarm_Radar SHALL optimistically update the UI and invalidate the relevant React Query cache.
3. WHEN the ChatPage receives SSE events that affect Radar state (e.g., task completion, new pending question), THE Swarm_Radar SHALL invalidate the relevant React Query cache to trigger a refresh.
4. THE polling intervals SHALL be defined as constants in the radar configuration, allowing future tuning.
5. WHEN the ChatPage receives an SSE `ask_user_question` event, THE `pendingQuestion` state SHALL be updated and passed as a prop to SwarmRadar, which SHALL immediately derive and display a corresponding Waiting_Input_Item in the Needs_Attention_Zone. This is a reactive prop flow, not a polling-based update.

### Requirement 23: Delete Existing Mock Component

**User Story:** As a developer, I want the old mock TodoRadarSidebar completely removed, so that there is no dead code or confusion about which component is active.

#### Acceptance Criteria

1. THE file `desktop/src/pages/chat/components/TodoRadarSidebar.tsx` SHALL be deleted.
2. ALL imports of `TodoRadarSidebar` in `ChatPage.tsx` and any other files SHALL be replaced with imports of the new `SwarmRadar` component.
3. THE `RIGHT_SIDEBAR_WIDTH_CONFIGS` entry for `todoRadar` SHALL be updated if width defaults change, or remain unchanged if the new component uses the same width constraints.
4. ALL existing tests referencing `TodoRadarSidebar` SHALL be updated to reference `SwarmRadar`.

### Requirement 24: Swarm Radar — Empty States

**User Story:** As a knowledge worker, I want clear, friendly empty states when a zone has no items, so that I understand the zone's purpose even when there is nothing to show.

#### Acceptance Criteria

1. WHEN the Needs_Attention_Zone has no ToDos and no Waiting Input items, THE zone SHALL display: "All clear — nothing needs your attention right now."
2. WHEN the In_Progress_Zone has no WIP_Tasks, THE zone SHALL display: "No tasks running. Start a ToDo or chat to kick things off."
3. WHEN the Completed_Zone has no recent completions, THE zone SHALL display: "No completed tasks in the last 7 days."
4. WHEN the Autonomous_Jobs_Zone has no jobs, THE zone SHALL display: "No autonomous jobs configured yet."
5. ALL empty state messages SHALL use `--color-text-muted` for text color and be centered within the zone content area.

### Requirement 25: Swarm Radar — Visual Design Consistency

**User Story:** As a knowledge worker, I want the Swarm Radar to feel visually consistent with the rest of SwarmAI, so that the experience is cohesive and calm.

#### Acceptance Criteria

1. THE Swarm_Radar SHALL use the same font sizes, weights, and spacing patterns as the existing sidebar components (ChatHistorySidebar, FileBrowserSidebar).
2. THE Swarm_Radar SHALL use `--color-card` for the background, `--color-border` for separators, `--color-text` for primary text, and `--color-text-muted` for secondary text.
3. THE Swarm_Radar SHALL use subtle hover states (`--color-hover`) for interactive items, consistent with the existing sidebar hover patterns.
4. THE Swarm_Radar SHALL use the `material-symbols-outlined` icon font for all icons, consistent with the existing icon usage across the application.
5. THE Swarm_Radar SHALL use smooth expand/collapse animations (150–200ms duration) for zone toggling, consistent with the progressive disclosure animations specified in `swarmws-explorer-ux` (Requirement 11, AC 3).
6. THE Swarm_Radar SHALL use `clsx` for conditional class composition, consistent with the existing component patterns.
