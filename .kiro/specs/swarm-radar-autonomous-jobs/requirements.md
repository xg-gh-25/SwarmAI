# Requirements Document — Swarm Radar Autonomous Jobs (Sub-Spec 5 of 5)

## Introduction

This document defines the requirements for the **Swarm Radar Autonomous Jobs** — the fifth and final sub-spec of the Swarm Radar Redesign. It covers the Autonomous Jobs zone display, the placeholder backend API that returns hardcoded mock data, the frontend service layer additions, and the `useJobZone` hook.

This spec builds on the foundation established in Spec 1 (`swarm-radar-foundation`), which provides the SwarmRadar shell, RadarZone component, shared TypeScript types (`RadarAutonomousJob`, `RadarZoneId`), sorting utilities (`sortAutonomousJobs`), mock data module (`getMockSystemJobs()`, `getMockUserJobs()`), CSS styles, and empty state support. It also builds on Spec 2 (`swarm-radar-todos`), which created the `radar.ts` service layer where this spec adds the `fetchAutonomousJobs` function.

### Scope

- AutonomousJobList and AutonomousJobItem components for the Autonomous Jobs zone
- Two sub-sections: "System" for system built-in jobs and "Recurring" for user-defined jobs
- "Coming soon" tooltip on job click (placeholder for future configuration UI)
- Backend `GET /api/autonomous-jobs` placeholder endpoint returning hardcoded mock data
- Backend Pydantic models for Autonomous Job (`AutonomousJobResponse`, `AutonomousJobCategory`, `AutonomousJobStatus`)
- Frontend service layer addition to `radar.ts`: `fetchAutonomousJobs`
- Frontend `useJobZone` hook with React Query polling (60s, gated by `enabled: isVisible`)
- Cross-zone reference: jobs in error state surface in the Needs Attention zone
- Badge tint: neutral by default, red when any job has `status` equal to `error`

### Out of Scope (Handled by Other Sub-Specs — All Complete)

- SwarmRadar shell, RadarZone, shared types, sorting utilities, mock data, CSS, empty states (Spec 1)
- ToDo inbox, quick-add, lifecycle actions, radar.ts service layer (ToDo functions), old component deletion (Spec 2)
- Waiting input derivation, hasWaitingInput, activeSessionId, WaitingInputList component (Spec 3)
- WIP tasks, completed tasks, archive window logic, useTaskZone hook (Spec 4)

### Parent Spec

The overall Swarm Radar Redesign spec is at `.kiro/specs/swarm-radar-redesign/`. This sub-spec extracts and adapts Requirements 11 and 16, and Correctness Properties 11 and 12 from that parent.

### Dependencies

- **Spec 1 (`swarm-radar-foundation`)**: SwarmRadar shell, RadarZone component, shared types (`RadarAutonomousJob`, `RadarZoneId`), sorting utility (`sortAutonomousJobs`), mock data (`getMockSystemJobs()`, `getMockUserJobs()`), CSS styles, empty state support, badge tint utility (`getBadgeTint`).
- **Spec 2 (`swarm-radar-todos`)**: `radar.ts` service layer (this spec adds the `fetchAutonomousJobs` function to it).

### Design Principles Alignment

- **Glanceable Awareness** — Status indicators, last run timestamps, and schedule descriptions provide instant context about background automation
- **Progressive Disclosure** — "Coming soon" tooltip defers job configuration complexity to a future release
- **Visible Planning Builds Trust** — Showing system and user-defined jobs transparently reveals what runs on the user's behalf
- **Signals First** — Jobs in error state surface in the Needs Attention zone as attention signals

### PE Review Findings Addressed

No PE review findings directly apply to this spec. All 7 PE findings have been addressed in Specs 1–4.


## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Defined in Spec 1.
- **Autonomous_Jobs_Zone**: The Radar_Zone containing system built-in and user-defined recurring agent jobs. Indicated by 🤖. Defined in Spec 1.
- **Autonomous_Job**: A background or recurring agent job. Two categories: System_Built_In (sync, indexing) and User_Defined (daily digest, reports).
- **System_Built_In_Job**: An autonomous job managed by the system (e.g., workspace sync, knowledge indexing, overdue check). Category value: `system`.
- **User_Defined_Job**: An autonomous job created by the user (e.g., daily digest, weekly report generation). Category value: `user_defined`.
- **RadarAutonomousJob**: Frontend TypeScript type representing a system or user-defined autonomous job. Defined in Spec 1 at `desktop/src/types/radar.ts`. Fields: `id`, `name`, `category`, `status`, `schedule`, `lastRunAt`, `nextRunAt`, `description`.
- **AutonomousJobResponse**: Backend Pydantic model for the autonomous job API response. Uses snake_case field names.
- **AutonomousJobCategory**: Backend Pydantic enum: `system`, `user_defined`.
- **AutonomousJobStatus**: Backend Pydantic enum: `running`, `paused`, `error`, `completed`.
- **Needs_Attention_Zone**: The top Radar_Zone containing ToDos and Waiting Input / ToReview items. Indicated by 🔴. Defined in Spec 1. Jobs in error state cross-reference here.
- **Zone_Badge**: A count badge displayed next to a Radar_Zone header showing the number of items in that zone. Defined in Spec 1.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **Mock_Data**: Realistic sample data returned by the placeholder backend API. The frontend mock data module from Spec 1 (`getMockSystemJobs()`, `getMockUserJobs()`) provides client-side fallback data.
- **Polling_Interval**: The configurable interval at which React Query refetches data. Autonomous jobs use 60 seconds. Gated by `enabled: isVisible`.
- **useJobZone**: The per-zone React hook managing autonomous job data and polling. Composed into the main `useSwarmRadar` hook.
- **Total_Order_Tiebreaker**: All sort functions use `id` (string comparison) as the ultimate tiebreaker after all other sort keys to guarantee deterministic ordering (PE Finding #6, addressed in Spec 1).

## Requirements

### Requirement 1: Autonomous Jobs Zone — Display and Sub-Sections

**User Story:** As a knowledge worker, I want to see system background jobs and my recurring agent jobs in a dedicated zone, so that I know what is running automatically on my behalf.

#### Acceptance Criteria

1. THE Autonomous_Jobs_Zone SHALL display two sub-sections: "System" for System_Built_In_Jobs and "Recurring" for User_Defined_Jobs.
2. EACH System_Built_In_Job SHALL display: job name, status indicator (✅ Running, ⏸️ Paused, ❌ Error), and last run timestamp (relative, e.g., "5m ago", "1h ago").
3. EACH User_Defined_Job SHALL display: job name, schedule description (e.g., "Daily at 9am", "Every Monday"), status indicator (✅ Running, ⏸️ Paused, ❌ Error), and last run timestamp.
4. THE Autonomous_Jobs_Zone SHALL sort items using the `sortAutonomousJobs` function from Spec 1: `system` category before `user_defined`, then alphabetical by name, then by `id` ascending as the ultimate tiebreaker.
5. THE Autonomous_Jobs_Zone SHALL fetch data from the `GET /api/autonomous-jobs` backend endpoint.
6. THE AutonomousJobList component SHALL be implemented at `desktop/src/pages/chat/components/radar/AutonomousJobList.tsx`.
7. THE AutonomousJobItem component SHALL be implemented at `desktop/src/pages/chat/components/radar/AutonomousJobItem.tsx`.
8. EACH AutonomousJobItem SHALL be focusable via Tab key navigation, with the click action accessible via Enter or Space.
9. THE AutonomousJobList component SHALL accept props: `systemJobs: RadarAutonomousJob[]`, `userJobs: RadarAutonomousJob[]`, and `onJobClick: (jobId: string) => void`.

### Requirement 2: Autonomous Job Click Action — Coming Soon Placeholder

**User Story:** As a knowledge worker, I want feedback when I click on an autonomous job, so that I know the feature is recognized even though configuration is not yet available.

#### Acceptance Criteria

1. WHEN the user clicks on an Autonomous_Job item, THE System SHALL display a "Coming soon" tooltip near the clicked item.
2. THE "Coming soon" tooltip SHALL auto-dismiss after 2 seconds or on any subsequent click.
3. THE tooltip SHALL use `--color-card` background, `--color-text-muted` text, and `--color-border` border, consistent with the existing tooltip patterns.
4. THE click action SHALL NOT navigate away from the current view or open any new panels.

### Requirement 3: Autonomous Jobs — Error State Cross-Zone Reference

**User Story:** As a knowledge worker, I want jobs in error state to also appear in the Needs Attention zone, so that I notice failures without scrolling to the Autonomous Jobs zone.

#### Acceptance Criteria

1. WHEN an Autonomous_Job has `status` equal to `error`, THE job SHALL also surface as an attention item in the Needs_Attention_Zone with a link back to the Autonomous_Jobs_Zone.
2. THE cross-zone error item SHALL display: job name, "❌ Error" status indicator, and a "View in Jobs" link that scrolls to the Autonomous_Jobs_Zone.
3. THE Needs_Attention_Zone badge count SHALL include error-state autonomous jobs in its total count.
4. WHEN the error-state job's status changes to a non-error state (via polling refresh), THE cross-zone item SHALL be removed from the Needs_Attention_Zone on the next data refresh.

### Requirement 4: Autonomous Jobs Zone — Badge Tint

**User Story:** As a knowledge worker, I want the Autonomous Jobs zone badge to visually signal when a job has failed, so that I can spot errors at a glance.

#### Acceptance Criteria

1. THE Autonomous_Jobs_Zone header SHALL use a neutral-tinted Zone_Badge by default.
2. WHEN any Autonomous_Job has `status` equal to `error`, THE Autonomous_Jobs_Zone header SHALL switch to a red-tinted Zone_Badge.
3. THE badge tint computation SHALL use the `getBadgeTint` function from Spec 1, which already handles the autonomous jobs error-state logic.
4. ALL badge tint colors SHALL use CSS variables in `--color-*` format, never hardcoded color values.

### Requirement 5: Backend — Autonomous Jobs Placeholder API

**User Story:** As a developer, I want placeholder API endpoints for autonomous jobs that return realistic mock data, so that the frontend can render the Autonomous Jobs zone with real API calls.

#### Acceptance Criteria

1. THE Backend SHALL provide a `GET /api/autonomous-jobs` endpoint that returns a list of Autonomous_Job objects.
2. EACH Autonomous_Job response SHALL contain: `id` (string), `name` (string), `category` (enum: `system` or `user_defined`), `status` (enum: `running`, `paused`, `error`, `completed`), `schedule` (optional string), `last_run_at` (optional ISO 8601 timestamp), `next_run_at` (optional ISO 8601 timestamp), `description` (optional string).
3. THE `GET /api/autonomous-jobs` endpoint SHALL return hardcoded mock data in the initial release, including both System_Built_In_Jobs and User_Defined_Jobs.
4. THE hardcoded mock data SHALL include at least: 3 System_Built_In_Jobs (e.g., "Workspace Sync" running, "Knowledge Indexing" running, "Overdue Check" running) and 2 User_Defined_Jobs (e.g., "Daily Digest" running, "Weekly Report" paused).
5. THE Backend SHALL define Pydantic models in `backend/schemas/autonomous_job.py`: `AutonomousJobResponse`, `AutonomousJobCategory` (enum: `system`, `user_defined`), and `AutonomousJobStatus` (enum: `running`, `paused`, `error`, `completed`).
6. THE Backend SHALL use snake_case field names for all fields in the Pydantic models.
7. THE `GET /api/autonomous-jobs` endpoint SHALL always return HTTP 200 with the mock data. There are no error cases in the initial release.
8. THE Backend SHALL register the autonomous jobs router in the FastAPI application.
9. THE `backend/schemas/autonomous_job.py` module SHALL include a detailed module-level docstring describing the module's purpose, key models, and the placeholder nature of the initial release.

### Requirement 6: Frontend — Swarm Radar Service Layer (Autonomous Jobs Function)

**User Story:** As a developer, I want an autonomous jobs fetch function in the radar service module, so that the API call is centralized alongside the existing ToDo and task functions.

#### Acceptance Criteria

1. THE Frontend SHALL add a `fetchAutonomousJobs` function to the existing `desktop/src/services/radar.ts` service module (created in Spec 2).
2. THE `fetchAutonomousJobs` function SHALL call `GET /api/autonomous-jobs` and return `Promise<RadarAutonomousJob[]>`.
3. THE `fetchAutonomousJobs` function SHALL implement `toCamelCase()` conversion for the backend response, mapping snake_case fields (`last_run_at`, `next_run_at`, `user_defined`) to camelCase (`lastRunAt`, `nextRunAt`, `userDefined`).
4. THE radar service SHALL use the existing HTTP client pattern consistent with `desktop/src/services/tasks.ts`.

### Requirement 7: Frontend — useJobZone State Management Hook

**User Story:** As a developer, I want a dedicated React hook for autonomous job zone state management, so that job data fetching and polling are cleanly encapsulated.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useJobZone` hook at `desktop/src/pages/chat/components/radar/hooks/useJobZone.ts`.
2. THE `useJobZone` hook SHALL accept a parameter: `isVisible: boolean`.
3. THE `useJobZone` hook SHALL use React Query for data fetching with a 60-second polling interval, gated by `enabled: isVisible` where `isVisible` is derived from `rightSidebars.isActive('todoRadar')`.
4. THE `useJobZone` hook SHALL return: `systemJobs: RadarAutonomousJob[]` (filtered by `category === 'system'`, sorted), `userJobs: RadarAutonomousJob[]` (filtered by `category === 'user_defined'`, sorted), and `isLoading: boolean`.
5. THE `useJobZone` hook SHALL apply the `sortAutonomousJobs` function from Spec 1 to the fetched results before partitioning by category.
6. THE `useJobZone` hook SHALL use the React Query cache key `['radar', 'autonomousJobs']`.
7. WHEN `isVisible` is false, THE `useJobZone` hook SHALL execute zero polling queries.
8. THE `useJobZone` hook SHALL be composed into the main `useSwarmRadar` hook alongside `useTodoZone` (Spec 2) and `useTaskZone` (Spec 4).
9. THE `useJobZone.ts` file SHALL include a detailed module-level docstring describing the hook's purpose, parameters, return values, and polling behavior.

### Requirement 8: Autonomous Jobs Zone — Accessibility

**User Story:** As a knowledge worker using assistive technology, I want the Autonomous Jobs zone to be keyboard navigable and screen reader compatible, so that I can understand job status regardless of how I interact with the application.

#### Acceptance Criteria

1. EACH AutonomousJobItem SHALL be focusable via Tab key navigation.
2. WHEN an AutonomousJobItem is focused, pressing Enter or Space SHALL trigger the click action (showing the "Coming soon" tooltip).
3. THE AutonomousJobList SHALL use `role="list"` on the container and `role="listitem"` on each job item.
4. EACH AutonomousJobItem SHALL include an `aria-label` describing the job name and status (e.g., "Workspace Sync, Running").
5. THE "System" and "Recurring" sub-section headers SHALL use appropriate heading semantics or `aria-label` to distinguish the two groups for screen readers.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Autonomous job categorization separates system and user-defined jobs

*For any* set of autonomous jobs with mixed categories (`system` and `user_defined`), the categorization function SHALL partition them into exactly two groups: jobs with `category === 'system'` in the System sub-section, and jobs with `category === 'user_defined'` in the Recurring sub-section. No job SHALL appear in both groups. No job SHALL be missing from both groups. The count of system jobs plus the count of user-defined jobs SHALL equal the total count of input jobs.

**Validates:** Requirement 1.1, 7.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`

### Property 2: Badge tint reflects error state for autonomous jobs

*For any* set of autonomous jobs, the badge tint SHALL be `red` when at least one job has `status` equal to `error`, and `neutral` otherwise. The badge tint SHALL be `neutral` when the job list is empty. The badge tint SHALL be `red` when exactly one job has `error` status among many non-error jobs. The badge tint SHALL be `red` when all jobs have `error` status.

**Validates:** Requirement 4.1, 4.2
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`

### Property 3: Autonomous job sort produces a total order with deterministic tiebreaking

*For any* list of autonomous jobs with arbitrary categories, names, and ids, the `sortAutonomousJobs` function SHALL produce a list where: all `system` jobs appear before all `user_defined` jobs; within the same category, jobs are ordered alphabetically by name (case-insensitive); when category and name are equal, jobs are ordered by `id` ascending (string comparison) as the ultimate tiebreaker. Sorting the same input twice SHALL produce identical output (idempotence). No two distinct jobs SHALL have ambiguous relative ordering.

**Validates:** Requirement 1.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`

### Property 4: Backend autonomous jobs endpoint returns valid mock data

*For any* call to `GET /api/autonomous-jobs`, the response SHALL be HTTP 200 with a JSON array. Each element SHALL have all required fields: `id` (non-empty string), `name` (non-empty string), `category` (one of `system` or `user_defined`), `status` (one of `running`, `paused`, `error`, `completed`). The response SHALL contain at least 3 system jobs and at least 2 user-defined jobs. All `id` values SHALL be unique across the response.

**Validates:** Requirement 5.1, 5.2, 5.3, 5.4
**Test type:** Property-based (pytest + hypothesis), min 100 iterations
**Test file:** `backend/tests/test_autonomous_jobs.py`

### Property 5: toCamelCase conversion for autonomous job responses is correct

*For any* valid backend autonomous job response object with snake_case field names (`last_run_at`, `next_run_at`, `user_defined`), applying `toCamelCase` SHALL produce an object with the correct camelCase field names (`lastRunAt`, `nextRunAt`, `userDefined`) and identical field values. The conversion SHALL not drop or add any fields.

**Validates:** Requirement 6.3
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/caseConversion.property.test.ts`

### Property 6: Polling is gated by visibility — zero queries when hidden

*For any* state where `isVisible` is `false`, the `useJobZone` hook SHALL execute zero polling queries for `['radar', 'autonomousJobs']`. When `isVisible` transitions from `false` to `true`, polling SHALL resume at the configured 60-second interval. When `isVisible` transitions from `true` to `false`, polling SHALL stop immediately.

**Validates:** Requirement 7.3, 7.7
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`

### Property 7: Error-state jobs surface in Needs Attention zone cross-reference

*For any* set of autonomous jobs, the cross-zone error reference function SHALL return only jobs with `status === 'error'`. The count of cross-zone error items SHALL equal the count of error-state jobs in the input. Jobs with non-error statuses (`running`, `paused`, `completed`) SHALL NOT appear in the cross-zone reference. When no jobs have error status, the cross-zone reference SHALL return an empty list.

**Validates:** Requirement 3.1, 3.3, 3.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`
