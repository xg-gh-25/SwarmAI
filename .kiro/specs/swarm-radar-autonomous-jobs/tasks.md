# Implementation Plan: Swarm Radar Autonomous Jobs (Sub-Spec 5 of 5)

## Overview

Build the Autonomous Jobs layer of the Swarm Radar — backend Pydantic models and placeholder API endpoint, frontend service layer addition (`fetchAutonomousJobs` + `jobToCamelCase`), the `useJobZone` state management hook with React Query polling (60s, gated by visibility), the AutonomousJobList/AutonomousJobItem UI components with "Coming soon" tooltip, cross-zone error referencing in the Needs Attention zone, and CSS styles. Backend first, then service layer, then hook, then components, then wiring.

## Tasks

- [ ] 1. Backend Pydantic models and placeholder API
  - [ ] 1.1 Create `backend/schemas/autonomous_job.py`
    - Define `AutonomousJobCategory` enum: `system`, `user_defined`
    - Define `AutonomousJobStatus` enum: `running`, `paused`, `error`, `completed`
    - Define `AutonomousJobResponse` model with fields: `id` (str), `name` (str), `category` (AutonomousJobCategory), `status` (AutonomousJobStatus), `schedule` (Optional[str]), `last_run_at` (Optional[str]), `next_run_at` (Optional[str]), `description` (Optional[str])
    - Use snake_case field names per backend convention
    - Include module-level docstring per dev rules
    - _Requirements: 5.2, 5.5, 5.6, 5.9_

  - [ ] 1.2 Create `backend/routers/autonomous_jobs.py`
    - Define `MOCK_JOBS` list with hardcoded mock data: 3 system jobs ("Workspace Sync", "Knowledge Indexing", "Overdue Check" — all running) and 2 user-defined jobs ("Daily Digest" running, "Weekly Report" paused)
    - Implement `GET /api/autonomous-jobs` endpoint returning `MOCK_JOBS` with `response_model=list[AutonomousJobResponse]`
    - Always returns HTTP 200 — no error cases in initial release
    - Include module-level docstring per dev rules
    - _Requirements: 5.1, 5.3, 5.4, 5.7_

  - [ ] 1.3 Register autonomous jobs router in `backend/main.py`
    - Import `router as autonomous_jobs_router` from `backend.routers.autonomous_jobs`
    - Add `app.include_router(autonomous_jobs_router)` alongside existing router registrations
    - _Requirements: 5.8_

  - [ ]* 1.4 Write property test for backend endpoint (Property 4)
    - **Property 4: Backend autonomous jobs endpoint returns valid mock data**
    - Create `backend/tests/test_autonomous_jobs.py`
    - Verify HTTP 200 with JSON array
    - Verify each job has required fields: `id` (non-empty string), `name` (non-empty string), `category` (one of `system`, `user_defined`), `status` (one of `running`, `paused`, `error`, `completed`)
    - Verify at least 3 system jobs and at least 2 user-defined jobs
    - Verify all `id` values are unique
    - Minimum 100 iterations
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**

- [ ] 2. Checkpoint — Backend models and API
  - Ensure all backend tests pass (`cd backend && pytest`), ask the user if questions arise.

- [ ] 3. Frontend service layer — add autonomous jobs to `radar.ts`
  - [ ] 3.1 Add `jobToCamelCase` and `fetchAutonomousJobs` to `desktop/src/services/radar.ts`
    - Implement `jobToCamelCase(job)` mapping snake_case fields to camelCase: `last_run_at` → `lastRunAt`, `next_run_at` → `nextRunAt`, `user_defined` category preserved as-is
    - Add `fetchAutonomousJobs()` to `radarService` object — calls `GET /api/autonomous-jobs`, maps response through `jobToCamelCase`
    - Export `jobToCamelCase` for direct use in tests (Property 5)
    - Use existing HTTP client pattern consistent with `desktop/src/services/tasks.ts`
    - Include module-level docstring update per dev rules
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 3.2 Write property test for toCamelCase conversion (Property 5)
    - **Property 5: toCamelCase conversion for autonomous job responses is correct**
    - Create `desktop/src/pages/chat/components/radar/__tests__/caseConversion.property.test.ts`
    - Use `fast-check` to generate random backend response objects with snake_case fields
    - Verify `jobToCamelCase` maps `last_run_at` → `lastRunAt`, `next_run_at` → `nextRunAt`
    - Verify field values are preserved exactly
    - Verify output has exactly 8 fields matching `RadarAutonomousJob` type
    - Verify no fields are dropped or added
    - Minimum 100 iterations
    - **Validates: Requirements 6.3**

- [ ] 4. Implement `useJobZone` hook
  - [ ] 4.1 Create `desktop/src/pages/chat/components/radar/hooks/useJobZone.ts`
    - Implement React Query data fetching with key `['radar', 'autonomousJobs']`, 60-second polling interval, gated by `enabled: isVisible`
    - `queryFn` calls `radarService.fetchAutonomousJobs()`
    - Apply `sortAutonomousJobs` from Spec 1 to fetched results in `useMemo`
    - Partition sorted results: `systemJobs` (category === 'system'), `userJobs` (category === 'user_defined') in `useMemo`
    - Extract `errorJobs` (status === 'error') for cross-zone Needs Attention reference in `useMemo`
    - Expose `handleJobClick` callback (no-op in initial release — tooltip managed locally in AutonomousJobItem)
    - Return `{ systemJobs, userJobs, errorJobs, isLoading, handleJobClick }`
    - Include module-level docstring per dev rules
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [ ]* 4.2 Write property test for job categorization (Property 1)
    - **Property 1: Autonomous job categorization separates system and user-defined jobs**
    - Create `desktop/src/pages/chat/components/radar/__tests__/jobCategorization.property.test.ts`
    - Use `fast-check` to generate random arrays of `RadarAutonomousJob` with mixed categories
    - Verify system group contains only `category === 'system'` jobs
    - Verify user group contains only `category === 'user_defined'` jobs
    - Verify no job appears in both groups or is missing from both
    - Verify system count + user count = total input count
    - Minimum 100 iterations
    - **Validates: Requirements 1.1, 7.4**

  - [ ]* 4.3 Write property test for badge tint error state (Property 2)
    - **Property 2: Badge tint reflects error state for autonomous jobs**
    - Add to `jobCategorization.property.test.ts`
    - Use `fast-check` to generate random arrays of jobs with all four status values
    - Verify empty array → neutral tint
    - Verify no errors → neutral tint
    - Verify at least one error → red tint
    - Verify all errors → red tint
    - Minimum 100 iterations
    - **Validates: Requirements 4.1, 4.2**

  - [ ]* 4.4 Write property test for sort total order (Property 3)
    - **Property 3: Autonomous job sort produces a total order with deterministic tiebreaking**
    - Add to `jobCategorization.property.test.ts`
    - Use `fast-check` to generate random arrays of jobs with varied categories, names (including duplicates), and unique ids
    - Verify category ordering: all `system` before all `user_defined`
    - Verify alphabetical name ordering within same category (case-insensitive)
    - Verify `id` ascending tiebreaker when category and name match
    - Verify idempotence: `sort(sort(x))` deep-equals `sort(x)`
    - Verify purity: input array is not mutated
    - Minimum 100 iterations
    - **Validates: Requirements 1.4**

  - [ ]* 4.5 Write property test for polling visibility gating (Property 6)
    - **Property 6: Polling is gated by visibility — zero queries when hidden**
    - Add to `jobCategorization.property.test.ts`
    - Generate random `isVisible` state sequences
    - Verify `isVisible=false` → React Query `enabled` is false, zero queries
    - Verify transitions resume/stop polling immediately
    - Minimum 100 iterations
    - **Validates: Requirements 7.3, 7.7**

  - [ ]* 4.6 Write property test for error cross-zone reference (Property 7)
    - **Property 7: Error-state jobs surface in Needs Attention zone cross-reference**
    - Add to `jobCategorization.property.test.ts`
    - Use `fast-check` to generate random arrays of jobs with all four status values
    - Verify only `status === 'error'` jobs pass the error filter
    - Verify count of error items equals count of error-status jobs in input
    - Verify non-error jobs are excluded
    - Verify empty input → empty output
    - Minimum 100 iterations
    - **Validates: Requirements 3.1, 3.3, 3.4**

- [ ] 5. Checkpoint — Service layer, hook, and property tests
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 6. Autonomous Jobs UI components
  - [ ] 6.1 Create `desktop/src/pages/chat/components/radar/AutonomousJobItem.tsx`
    - Accept `AutonomousJobItemProps`: `job: RadarAutonomousJob`, `onClick: () => void`
    - Render as `<li role="listitem" className="radar-job-item">` with conditional `radar-job-item--error` class for error status
    - Display job name, status indicator (✅ Running, ⏸️ Paused, ❌ Error, ✔️ Completed), last run timestamp (relative: "5m ago", "1h ago", "2d ago"; "Never" when `lastRunAt` is null)
    - For user-defined jobs: also display schedule description when `job.schedule` is non-null
    - Implement "Coming soon" tooltip: local `useState` + `useRef` for timer, auto-dismiss after 2 seconds, dismiss on subsequent click via document-level listener, cleanup timer on unmount
    - Tooltip uses `--color-card` background, `--color-text-muted` text, `--color-border` border, `role="status"`, `aria-live="polite"`
    - Click does NOT navigate away or open any panels
    - Focusable via Tab key, click action accessible via Enter or Space
    - Include `aria-label` describing job name and status (e.g., "Workspace Sync, Running")
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 1.2, 1.3, 1.8, 2.1, 2.2, 2.3, 2.4, 8.1, 8.2, 8.4_

  - [ ] 6.2 Create `desktop/src/pages/chat/components/radar/AutonomousJobList.tsx`
    - Accept `AutonomousJobListProps`: `systemJobs: RadarAutonomousJob[]`, `userJobs: RadarAutonomousJob[]`, `onJobClick: (jobId: string) => void`
    - Render two sub-sections: "System" (`<h4>` with `aria-label="System jobs"`) and "Recurring" (`<h4>` with `aria-label="Recurring jobs"`)
    - Each sub-section renders `<ul role="list">` containing `AutonomousJobItem` components
    - Hide a sub-section entirely (including header) when its job array is empty
    - Render nothing when both arrays are empty — parent `RadarZone` handles zone-level empty state
    - Use `clsx` for conditional class composition
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 1.1, 1.6, 1.7, 1.9, 8.3, 8.5_

  - [ ]* 6.3 Write unit tests for AutonomousJobList and AutonomousJobItem
    - Create `desktop/src/pages/chat/components/radar/__tests__/AutonomousJobList.test.tsx`
    - Test: renders "System" and "Recurring" sub-sections with correct items
    - Test: hides sub-section when its array is empty
    - Test: renders nothing when both arrays are empty
    - Test: status indicators (✅, ⏸️, ❌, ✔️) display correctly per status
    - Test: last run timestamp shows relative time; "Never" when null
    - Test: schedule description shown for user-defined jobs, hidden for system jobs
    - Test: click shows "Coming soon" tooltip
    - Test: tooltip auto-dismisses after 2 seconds (fake timers)
    - Test: tooltip dismisses on subsequent click
    - Test: click does not navigate away
    - Test: Tab key focuses items, Enter/Space triggers click action
    - Test: `role="list"` on containers, `role="listitem"` on items
    - Test: `aria-label` on items includes job name and status
    - Test: sub-section headers have `aria-label` for screen readers
    - _Requirements: 1.1, 1.2, 1.3, 1.8, 2.1, 2.2, 2.3, 2.4, 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 7. Add CSS styles for autonomous job components
  - [ ] 7.1 Add autonomous job styles to `desktop/src/pages/chat/components/radar/SwarmRadar.css`
    - Define `.radar-job-item` layout: job name, status indicator, last run timestamp, schedule description
    - Define `.radar-job-item--error` for visual emphasis on error-state jobs (uses `--color-danger`)
    - Define `.radar-job-tooltip` for "Coming soon" tooltip: positioned absolutely, `--color-card` background, `--color-text-muted` text, `--color-border` border, small rounded rectangle with subtle shadow
    - Define `.radar-job-subsection` for sub-section headers ("System", "Recurring")
    - Define `.radar-error-jobs` and `.radar-error-job-item` for cross-zone error items in Needs Attention zone
    - Define `.radar-error-job-link` for "View in Jobs" button
    - Define hover states using `--color-hover`
    - Use only `--color-*` CSS variables — no hardcoded colors
    - Match font sizes, weights, spacing of existing radar components
    - _Requirements: 1.2, 1.3, 2.3, 3.2, 4.4_

- [ ] 8. Checkpoint — UI components and styles
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 9. Wire Autonomous Jobs components into SwarmRadar
  - [ ] 9.1 Integrate `useJobZone` into SwarmRadar composition
    - Import and call `useJobZone` hook with `isVisible` from sidebar state
    - Compose into `useSwarmRadar` hook alongside `useTodoZone` (Spec 2), `useWaitingInputZone` (Spec 3), and `useTaskZone` (Spec 4)
    - Replace mock Autonomous Jobs `<li>` elements in the Autonomous Jobs zone with `<AutonomousJobList>` component
    - Pass `systemJobs`, `userJobs`, and `handleJobClick` from the hook to `AutonomousJobList`
    - Update Autonomous Jobs badge count to use `systemJobs.length + userJobs.length` from the hook
    - Compute badge tint via `getBadgeTint('autonomousJobs', { jobs: [...systemJobs, ...userJobs] })` from Spec 1
    - _Requirements: 1.1, 1.4, 1.5, 4.1, 4.2, 4.3, 7.8_

  - [ ] 9.2 Add cross-zone error items to Needs Attention zone
    - Pass `errorJobs` from `useJobZone` to the Needs Attention zone in SwarmRadar
    - Render error-state jobs below existing Needs Attention content (below TodoList and WaitingInputList)
    - Each error item displays: job name, "❌ Error" status indicator, and "View in Jobs" button
    - "View in Jobs" button scrolls to the Autonomous Jobs zone via `scrollToJobsZone()` (scroll the Autonomous Jobs RadarZone into view)
    - Update Needs Attention badge count to include `errorJobs.length` (todos.length + waitingItems.length + errorJobs.length)
    - Error items use `role="listitem"`, container uses `role="list"` with `aria-label="Jobs with errors"`
    - Error items removed from Needs Attention on next data refresh when job status changes to non-error
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 9.3 Write unit tests for integration wiring
    - Test: Autonomous Jobs zone renders AutonomousJobList with system and user jobs
    - Test: Badge count reflects total job count (system + user)
    - Test: Badge tint is neutral when no errors, red when any error
    - Test: Error-state jobs appear in Needs Attention zone with job name, ❌ Error, and "View in Jobs" link
    - Test: Needs Attention badge count includes error job count
    - Test: Error items removed when job status changes to non-error (mock polling refresh)
    - Test: "View in Jobs" scrolls to Autonomous Jobs zone
    - _Requirements: 1.1, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2_

- [ ] 10. Final checkpoint — Full integration
  - Ensure all tests pass (`cd desktop && npm test -- --run` and `cd backend && pytest`).
  - Ensure no TypeScript compilation errors (`cd desktop && npx tsc --noEmit`).
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend tasks (1.x) run first so the API is ready before frontend integration
- Property tests validate universal correctness properties from the design document
- Checkpoints ensure incremental validation at natural break points
- This spec builds on Spec 1 (types, `sortAutonomousJobs`, `getBadgeTint`, RadarZone, CSS, mock data) and Spec 2 (`radar.ts` service layer)
- The backend endpoint returns hardcoded mock data — no database queries, no user input validation, no external service calls
- `handleJobClick` is a no-op at the hook level — the "Coming soon" tooltip is managed as local state within `AutonomousJobItem`
- All sort functions use `id` as ultimate tiebreaker for deterministic ordering (PE Finding #6, addressed in Spec 1)
- Cross-zone error items are derived from the same `useJobZone` data — no separate API call needed
- Polling at 60s (vs 30s for tasks) reflects the lower-frequency nature of autonomous job status changes
