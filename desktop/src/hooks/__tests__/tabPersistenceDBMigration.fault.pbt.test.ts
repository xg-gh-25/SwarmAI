/**
 * Bug condition exploration test for tab persistence DB migration.
 *
 * Testing methodology: property-based testing (fast-check) + vitest.
 *
 * What is being tested:
 *   The `useUnifiedTabState` hook's `restoreFromFile` method, which
 *   loads tab state from ~/.swarm-ai/open_tabs.json via the backend API.
 *
 * Key property verified:
 *   Property 1 (File Restore): When open_tabs.json contains saved tabs,
 *   `restoreFromFile()` restores them with correct IDs, titles, sessionIds,
 *   and sets the correct activeTabId.
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import {
  useUnifiedTabState,
  MAX_OPEN_TABS,
} from '../useUnifiedTabState';
import { tabPersistenceService } from '../../services/tabPersistence';
import type { OpenTabsFileData, PersistedTab } from '../../services/tabPersistence';

// Mock the tabPersistence service
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: {
    load: vi.fn().mockResolvedValue(null),
    save: vi.fn().mockResolvedValue(undefined),
  },
}));

const DEFAULT_AGENT = 'test-agent';

// ---------------------------------------------------------------------------
// fast-check arbitrary: generate random saved tabs
// ---------------------------------------------------------------------------

const persistedTabArb = (index: number): fc.Arbitrary<PersistedTab> =>
  fc.record({
    id: fc.uuid(),
    agentId: fc.constant(DEFAULT_AGENT),
    title: fc.string({ minLength: 1, maxLength: 30 }),
    isNew: fc.constant(false),
    sessionId: fc.uuid(),
  });

const savedTabsArb: fc.Arbitrary<PersistedTab[]> = fc
  .integer({ min: 1, max: MAX_OPEN_TABS })
  .chain((count) =>
    fc.tuple(
      ...Array.from({ length: count }, (_, i) => persistedTabArb(i)),
    ),
  );

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('File Restore — Property 1: Tab Restoration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Property 1: restoreFromFile restores exact tabs from open_tabs.json
   *
   * For any set of 1–6 saved tabs with random titles and sessionIds:
   * When open_tabs.json contains those tabs, restoreFromFile() should
   * restore them with the exact same IDs, titles, and sessionIds.
   */
  it('Property 1: restoreFromFile restores tabs from file with correct data', async () => {
    // Generate 20 random tab sets and test each
    const samples = fc.sample(savedTabsArb, 20);

    for (const tabs of samples) {
      const fileData: OpenTabsFileData = {
        tabs,
        activeTabId: tabs[0].id,
      };

      vi.mocked(tabPersistenceService.load).mockResolvedValue(fileData);

      const { result, unmount } = renderHook(() =>
        useUnifiedTabState(DEFAULT_AGENT),
      );

      let restored = false;
      await act(async () => {
        restored = await result.current.restoreFromFile();
      });

      expect(restored).toBe(true);
      expect(result.current.openTabs.length).toBe(tabs.length);

      for (let i = 0; i < tabs.length; i++) {
        expect(result.current.openTabs[i].id).toBe(tabs[i].id);
        expect(result.current.openTabs[i].title).toBe(tabs[i].title);
        expect(result.current.openTabs[i].sessionId).toBe(tabs[i].sessionId);
      }

      expect(result.current.activeTabId).toBe(tabs[0].id);
      unmount();
    }
  });

  /**
   * Property 1b: restoreFromFile returns false when no file exists
   */
  it('Property 1b: restoreFromFile returns false when no saved tabs', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue(null);

    const { result, unmount } = renderHook(() =>
      useUnifiedTabState(DEFAULT_AGENT),
    );

    let restored = true;
    await act(async () => {
      restored = await result.current.restoreFromFile();
    });

    expect(restored).toBe(false);
    expect(result.current.openTabs.length).toBe(1);
    expect(result.current.openTabs[0].title).toBe('New Session');
    unmount();
  });
});
