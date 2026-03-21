# Implementation Plan: Dynamic Tab Scaling

## Overview

Replace hardcoded tab/concurrency limits with a dynamic value computed from available RAM. Backend first (compute + endpoint), then frontend (hook + UI), then wiring and tests. ~80 lines across 5 files.

## Tasks

- [x] 1. Add `compute_max_tabs()` to ResourceMonitor
  - [x] 1.1 Implement `compute_max_tabs()` method on `ResourceMonitor` in `backend/core/resource_monitor.py`
    - Add method: `compute_max_tabs(self) -> int` using formula `max(1, min(int((available_mb - 1024) // 500), 4))`
    - Uses existing `self.system_memory()` for the available RAM reading
    - On `system_memory()` fallback (1600MB), formula yields 1
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 1.2 Write property test for `compute_max_tabs()` formula correctness
    - **Property 1: Formula correctness (model-based)**
    - Generate random `available_mb` floats in [0, 65536], mock `system_memory()`, verify output matches reference formula `max(1, min(floor((available_mb - 1024) / 500), 4))`
    - Use `hypothesis` with `st.floats(min_value=0, max_value=65536)`
    - Place test in `backend/tests/test_dynamic_tab_scaling.py`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.6**

- [x] 2. Replace hardcoded `MAX_CONCURRENT` in SessionRouter
  - [x] 2.1 Update `_acquire_slot()` in `backend/core/session_router.py` to use dynamic limit
    - Replace `self.alive_count < self.MAX_CONCURRENT` with `self.alive_count < resource_monitor.compute_max_tabs()`
    - Keep `MAX_CONCURRENT=2` as fallback constant (unused in hot path)
    - Preserve existing `spawn_budget()` second-layer gate unchanged
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 2.2 Write property test for router respecting dynamic limit
    - **Property 2: Router respects dynamic limit**
    - Generate random `max_tabs` in [1,4] and `alive_count` in [0,6], mock `compute_max_tabs()`, verify slot grant/deny
    - **Validates: Requirements 2.1, 2.3**

  - [x] 2.3 Write property test for no eviction on budget shrinkage
    - **Property 3: No eviction on budget shrinkage**
    - Generate list of sessions with random states (STREAMING/WAITING_INPUT), decrease `max_tabs`, verify no session is killed
    - **Validates: Requirements 2.3, 7.1, 7.3**

- [x] 3. Checkpoint — Ensure backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Add `GET /api/system/max-tabs` endpoint
  - [x] 4.1 Add `MaxTabsResponse` model and `/max-tabs` route to `backend/routers/system.py`
    - Add `MaxTabsResponse(BaseModel)` with fields `max_tabs: int` and `memory_pressure: str`
    - Add `@router.get("/max-tabs")` handler that calls `resource_monitor.invalidate_cache()`, reads `system_memory()`, returns `compute_max_tabs()` and `pressure_level`
    - ~15 lines total
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 4.2 Write property test for API-method consistency
    - **Property 4: API-method consistency**
    - Verify `max_tabs` field from endpoint equals `compute_max_tabs()` for the same memory snapshot
    - **Validates: Requirements 3.1**

- [x] 5. Update frontend `useUnifiedTabState` for dynamic tab limit
  - [x] 5.1 Replace `MAX_OPEN_TABS=6` with dynamic fetch in `desktop/src/hooks/useUnifiedTabState.ts`
    - Remove `MAX_OPEN_TABS = 6` constant
    - Add `MAX_TABS_HARD_CEILING = 4` constant (for restore)
    - Add `MAX_OPEN_TABS_FALLBACK = 2` constant (for API failure)
    - Update `restoreFromFile()` to use `data.tabs.slice(0, MAX_TABS_HARD_CEILING)` instead of `MAX_OPEN_TABS`
    - Make `addTab()` async: fetch `GET /api/system/max-tabs` before checking limit, fall back to `MAX_OPEN_TABS_FALLBACK` on failure
    - Add `MaxTabsInfo` interface: `{ maxTabs: number; memoryPressure: 'ok' | 'warning' | 'critical' }`
    - Update `UseUnifiedTabStateReturn` type for async `addTab`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4a.1, 4a.2, 4a.5_

  - [x] 5.2 Write property test for addTab rejection at limit
    - **Property 5: Frontend addTab rejection at limit**
    - Generate random `tab_count` in [0,6] and `max_tabs` in [1,4], verify addTab behavior (reject when count ≥ max, accept when count < max)
    - Use `fast-check` in vitest
    - **Validates: Requirements 4.3, 7.2**

  - [x] 5.3 Write property test for tabs never auto-closed
    - **Property 6: Tabs never auto-closed by pressure or shrinkage**
    - Generate random pressure transitions, verify tab count never decreases without explicit `closeTab()`
    - **Validates: Requirements 6.5, 7.2**

  - [x] 5.4 Write property test for restore loading all saved tabs
    - **Property 7: Restore loads all saved tabs regardless of dynamic limit**
    - Generate saved tab count S in [1,4] and max tabs M in [1,4], verify `restoreFromFile()` loads S tabs and `addTab()` rejects when open ≥ M
    - **Validates: Requirements 4a.1, 4a.2, 4a.5**

- [x] 6. Update `ChatPage.tsx` for disabled button and memory pressure
  - [x] 6.1 Add disabled "+" button state and memory pressure indicator to `desktop/src/pages/ChatPage.tsx`
    - Disable "+" button when `openTabs.length >= maxTabs` (fetched from hook or polled)
    - Add tooltip: "System resources are limited. Close a tab or free memory to open another."
    - Poll `GET /api/system/max-tabs` every 30 seconds for memory pressure
    - Show yellow indicator at `warning` level, red at `critical`, hidden at `ok`
    - Indicator is informational only — no auto-close behavior
    - Update `MAX_OPEN_TABS` import/re-export to use new constants
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 7. Wire everything together and update imports
  - [x] 7.1 Update all references to old `MAX_OPEN_TABS` constant
    - Update the re-export in `ChatPage.tsx` to export `MAX_TABS_HARD_CEILING` instead of `MAX_OPEN_TABS`
    - Fix any other imports across the codebase that reference the old constant
    - Verify `restoreFromFile()` uses `MAX_TABS_HARD_CEILING` and `addTab()` uses dynamic fetch
    - _Requirements: 4.4, 4a.1, 4a.2_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Backend tasks (1–4) are independent of frontend tasks (5–6) and should be completed first
- Each task references specific requirements for traceability
- Property tests use `hypothesis` (backend) and `fast-check` (frontend) — both already in the project
- The `spawn_budget()` second-layer gate and `channels/gateway.py` are intentionally untouched
- Checkpoints at tasks 3 and 8 ensure incremental validation
