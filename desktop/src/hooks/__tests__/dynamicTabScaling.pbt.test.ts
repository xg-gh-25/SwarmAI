/**
 * Property-based tests for dynamic tab scaling.
 *
 * Testing methodology: property-based testing (fast-check) + vitest.
 *
 * What is being tested:
 *   The `useUnifiedTabState` hook's tab management behavior under dynamic
 *   resource limits fetched from the backend API.
 *
 * Key properties verified:
 *   - Property 5: Frontend addTab rejection at limit — for any (tab_count,
 *     max_tabs) pair, addTab returns undefined when count ≥ max and returns
 *     a valid OpenTab when count < max.
 *   - Property 6: Tabs never auto-closed by pressure or shrinkage — for any
 *     sequence of memory pressure transitions, the tab count never decreases
 *     without an explicit closeTab() call.
 *
 * **Validates: Requirements 4.3, 6.5, 7.2**
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import {
  useUnifiedTabState,
} from '../useUnifiedTabState';
import type { OpenTab } from '../../pages/chat/types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock the tabPersistence service (file-based persistence is async)
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: {
    load: vi.fn().mockResolvedValue(null),
    save: vi.fn().mockResolvedValue(undefined),
  },
}));

// Mock the api service so we can control /system/max-tabs responses
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

import api from '../../services/api';
import { tabPersistenceService } from '../../services/tabPersistence';

const DEFAULT_AGENT = 'test-agent';

// ---------------------------------------------------------------------------
// Property-Based Tests — Dynamic Tab Scaling
// ---------------------------------------------------------------------------

describe('Dynamic Tab Scaling — Property-Based Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Feature: dynamic-tab-scaling, Property 6: Tabs never auto-closed by pressure or shrinkage
   *
   * For any initial tab count in [1, 4] and any sequence of memory pressure
   * transitions (varying maxTabs and memoryPressure levels), the number of
   * open tabs should never decrease. Memory pressure indicators and budget
   * shrinkage are informational only — tabs are never auto-closed.
   *
   * The only way tabs decrease is via explicit `closeTab()` — which this
   * test never calls.
   *
   * **Validates: Requirements 6.5, 7.2**
   */
  it('Property 6: tabs are never auto-closed by pressure transitions or budget shrinkage', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 4 }),
        fc.array(
          fc.record({
            maxTabs: fc.integer({ min: 1, max: 4 }),
            memoryPressure: fc.constantFrom('ok', 'warning', 'critical') as fc.Arbitrary<'ok' | 'warning' | 'critical'>,
          }),
          { minLength: 1, maxLength: 10 },
        ),
        async (initialTabCount, pressureTransitions) => {
          vi.clearAllMocks();

          const { result, unmount } = renderHook(() =>
            useUnifiedTabState(DEFAULT_AGENT),
          );

          // Step 1: Set high limit so we can freely add tabs during setup
          vi.mocked(api.get).mockResolvedValue({
            data: { max_tabs: 7, memory_pressure: 'ok' },
          });
          await act(async () => {
            await result.current.fetchMaxTabs();
          });

          // Step 2: Add tabs to reach initialTabCount (hook starts with 1)
          for (let i = 1; i < initialTabCount; i++) {
            act(() => {
              result.current.addTab(DEFAULT_AGENT);
            });
          }

          const tabCountBeforeTransitions = result.current.openTabs.length;
          expect(tabCountBeforeTransitions).toBe(initialTabCount);

          // Step 3: Apply each pressure transition via fetchMaxTabs()
          // After each transition, verify tab count has NOT decreased
          for (const transition of pressureTransitions) {
            vi.mocked(api.get).mockResolvedValue({
              data: {
                max_tabs: transition.maxTabs,
                memory_pressure: transition.memoryPressure,
              },
            });
            await act(async () => {
              await result.current.fetchMaxTabs();
            });

            // Key invariant: tab count must remain exactly the same
            // No tabs are auto-closed by pressure or shrinkage
            expect(result.current.openTabs.length).toBe(initialTabCount);
          }

          unmount();
        },
      ),
      { numRuns: 200 },
    );
  });

  /**
   * Feature: dynamic-tab-scaling, Property 5: Frontend addTab rejection at limit
   *
   * For any tab_count in [0, 6] and max_tabs in [1, 4]:
   * - If tab_count >= max_tabs: addTab() returns undefined (rejected), tab count unchanged
   * - If tab_count < max_tabs: addTab() returns a valid OpenTab (accepted), tab count = tab_count + 1
   *
   * **Validates: Requirements 4.3, 7.2**
   */
  it('Property 5: addTab rejects when tab count >= dynamic max, accepts when below', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 0, max: 6 }),
        fc.integer({ min: 1, max: 4 }),
        async (tabCount, maxTabs) => {
          vi.clearAllMocks();

          const { result, unmount } = renderHook(() =>
            useUnifiedTabState(DEFAULT_AGENT),
          );

          // Hook starts with 1 default tab. We need to:
          // 1. Set a high limit so we can freely add tabs during setup
          // 2. Add tabs to reach the desired tab_count
          // 3. Set the real max_tabs limit
          // 4. Try addTab and verify behavior

          // Step 1: Set high limit to allow unrestricted tab creation during setup
          vi.mocked(api.get).mockResolvedValue({
            data: { max_tabs: 7, memory_pressure: 'ok' },
          });
          await act(async () => {
            await result.current.fetchMaxTabs();
          });

          // Step 2: Add tabs to reach tab_count (we start with 1)
          for (let i = 1; i < tabCount; i++) {
            act(() => {
              result.current.addTab(DEFAULT_AGENT);
            });
          }

          // The hook enforces ≥1 tab invariant, so for tabCount=0 we still have 1 tab.
          const actualTabCount = result.current.openTabs.length;

          // Step 3: Set the real max_tabs limit
          vi.mocked(api.get).mockResolvedValue({
            data: { max_tabs: maxTabs, memory_pressure: 'ok' },
          });
          await act(async () => {
            await result.current.fetchMaxTabs();
          });

          // Step 4: Try to add one more tab
          let addResult: OpenTab | undefined;
          act(() => {
            addResult = result.current.addTab(DEFAULT_AGENT);
          });

          const countAfterAdd = result.current.openTabs.length;

          if (actualTabCount >= maxTabs) {
            // Should be rejected: addTab returns undefined, count unchanged
            expect(addResult).toBeUndefined();
            expect(countAfterAdd).toBe(actualTabCount);
          } else {
            // Should be accepted: addTab returns a valid OpenTab, count incremented
            expect(addResult).toBeDefined();
            expect(addResult!.id).toBeTruthy();
            expect(addResult!.title).toBe('New Session');
            expect(addResult!.agentId).toBe(DEFAULT_AGENT);
            expect(addResult!.isNew).toBe(true);
            expect(countAfterAdd).toBe(actualTabCount + 1);
          }

          unmount();
        },
      ),
      { numRuns: 200 },
    );
  });

  /**
   * Feature: dynamic-tab-scaling, Property 7: Restore loads all saved tabs regardless of dynamic limit
   *
   * For any saved tab count S in [1, 4] and any dynamic max tabs value M in
   * [1, 4], `restoreFromFile()` should restore ALL S tabs to the UI. The tab
   * count after restore should equal S, not M. The `addTab()` function should
   * still reject new tabs when open count ≥ M.
   *
   * Key invariant: `restoreFromFile()` uses `MAX_TABS_HARD_CEILING = 4` (not
   * the dynamic limit). The dynamic limit only gates NEW tab creation via
   * `addTab()`.
   *
   * **Validates: Requirements 4a.1, 4a.2, 4a.5**
   */
  it('Property 7: restoreFromFile loads all saved tabs regardless of dynamic max, addTab respects dynamic limit', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 4 }),
        fc.integer({ min: 1, max: 4 }),
        async (savedTabCount, maxTabs) => {
          vi.clearAllMocks();

          // Prepare saved tabs mock data
          const savedTabs = Array.from({ length: savedTabCount }, (_, i) => ({
            id: `saved-tab-${i}`,
            title: `Tab ${i}`,
            agentId: 'test-agent',
            isNew: false,
            sessionId: `session-${i}`,
          }));

          vi.mocked(tabPersistenceService.load).mockResolvedValue({
            tabs: savedTabs,
            activeTabId: 'saved-tab-0',
          });

          // Mock API to return the dynamic max tabs
          vi.mocked(api.get).mockResolvedValue({
            data: { max_tabs: maxTabs, memory_pressure: 'ok' },
          });

          const { result, unmount } = renderHook(() =>
            useUnifiedTabState(DEFAULT_AGENT),
          );

          // Step 1: Restore tabs from file — should load ALL S tabs
          await act(async () => {
            await result.current.restoreFromFile();
          });

          // Verify all saved tabs were restored regardless of maxTabs
          expect(result.current.openTabs.length).toBe(savedTabCount);

          // Step 2: Fetch the dynamic max tabs limit
          await act(async () => {
            await result.current.fetchMaxTabs();
          });

          // Tab count should still be S after fetching the limit
          expect(result.current.openTabs.length).toBe(savedTabCount);

          // Step 3: Try addTab — should be rejected when S >= M, accepted when S < M
          let addResult: OpenTab | undefined;
          act(() => {
            addResult = result.current.addTab(DEFAULT_AGENT);
          });

          if (savedTabCount >= maxTabs) {
            // Rejected: too many tabs open relative to dynamic limit
            expect(addResult).toBeUndefined();
            expect(result.current.openTabs.length).toBe(savedTabCount);
          } else {
            // Accepted: room for one more tab under the dynamic limit
            expect(addResult).toBeDefined();
            expect(addResult!.id).toBeTruthy();
            expect(result.current.openTabs.length).toBe(savedTabCount + 1);
          }

          unmount();
        },
      ),
      { numRuns: 200 },
    );
  });
});
