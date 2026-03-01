/**
 * Integration Tests for useTabState Hook
 *
 * **Feature: chat-header-tabs-redesign**
 * **Validates: Requirements 1.7, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4**
 *
 * These tests validate the useTabState custom hook for managing
 * browser-like session tabs with localStorage persistence.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import { useTabState } from './useTabState';
import { OPEN_TABS_STORAGE_KEY, ACTIVE_TAB_STORAGE_KEY } from '../pages/chat/constants';
import type { OpenTab } from '../pages/chat/types';

// ============== Test Setup ==============

// Mock localStorage
class MockLocalStorage {
  private store: Map<string, string> = new Map();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }

  get length(): number {
    return this.store.size;
  }

  key(index: number): string | null {
    const keys = Array.from(this.store.keys());
    return keys[index] ?? null;
  }
}

// Mock crypto.randomUUID
let uuidCounter = 0;
const mockRandomUUID = () => `test-uuid-${++uuidCounter}`;

let originalLocalStorage: Storage;
let originalRandomUUID: typeof crypto.randomUUID;
let mockStorage: MockLocalStorage;

const DEFAULT_AGENT_ID = 'test-agent-123';

// Helper to create a valid OpenTab for testing
const createTestTab = (overrides: Partial<OpenTab> = {}): OpenTab => ({
  id: mockRandomUUID(),
  title: 'Test Session',
  agentId: DEFAULT_AGENT_ID,
  isNew: false,
  ...overrides,
});

// ============== Test Suite ==============

describe('useTabState Hook - Integration Tests', () => {
  beforeEach(() => {
    // Reset UUID counter
    uuidCounter = 0;

    // Mock localStorage
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
    });

    // Mock crypto.randomUUID
    originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, 'randomUUID', {
      value: mockRandomUUID,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
    });
    Object.defineProperty(crypto, 'randomUUID', {
      value: originalRandomUUID,
      writable: true,
    });
    mockStorage.clear();
  });

  /**
   * Task 9.1: Test tab creation flow (+ button creates new tab)
   * **Validates: Requirements 2.1, 2.2, 2.3**
   */
  describe('Task 9.1: Tab Creation Flow', () => {
    it('should create new tab with "New Session" title', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const initialTabCount = result.current.openTabs.length;

      act(() => {
        result.current.addTab(DEFAULT_AGENT_ID);
      });

      // Requirement 2.2: New sessions open with default title "New Session"
      const newTab = result.current.openTabs[result.current.openTabs.length - 1];
      expect(newTab.title).toBe('New Session');
      expect(newTab.isNew).toBe(true);
      expect(newTab.agentId).toBe(DEFAULT_AGENT_ID);
      expect(result.current.openTabs.length).toBe(initialTabCount + 1);

      unmount();
    });

    it('should make new tab the active tab immediately', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      let newTab: OpenTab;
      act(() => {
        newTab = result.current.addTab(DEFAULT_AGENT_ID);
      });

      // Requirement 2.3: The new session tab becomes the active tab immediately
      expect(result.current.activeTabId).toBe(newTab!.id);

      unmount();
    });

    it('should return the created tab for immediate use', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      let returnedTab: OpenTab;
      act(() => {
        returnedTab = result.current.addTab(DEFAULT_AGENT_ID);
      });

      expect(returnedTab!).toBeDefined();
      expect(returnedTab!.id).toBeDefined();
      expect(returnedTab!.title).toBe('New Session');
      expect(result.current.openTabs).toContainEqual(returnedTab!);

      unmount();
    });

    it('should create multiple tabs with unique IDs', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const tabs: OpenTab[] = [];
      act(() => {
        tabs.push(result.current.addTab(DEFAULT_AGENT_ID));
        tabs.push(result.current.addTab(DEFAULT_AGENT_ID));
        tabs.push(result.current.addTab(DEFAULT_AGENT_ID));
      });

      const ids = tabs.map(t => t.id);
      const uniqueIds = new Set(ids);
      expect(uniqueIds.size).toBe(ids.length);

      unmount();
    });

    it('should persist new tab to localStorage', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.addTab(DEFAULT_AGENT_ID);
      });

      const storedTabs = JSON.parse(mockStorage.getItem(OPEN_TABS_STORAGE_KEY) || '[]');
      expect(storedTabs.length).toBeGreaterThan(1);
      expect(storedTabs[storedTabs.length - 1].title).toBe('New Session');

      unmount();
    });
  });

  /**
   * Task 9.2: Test tab switching (clicking inactive tab loads session)
   * **Validates: Requirements 1.6**
   */
  describe('Task 9.2: Tab Switching', () => {
    it('should update activeTabId when selecting a tab', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      expect(result.current.activeTabId).toBe('tab-1');

      act(() => {
        result.current.selectTab('tab-2');
      });

      // Requirement 1.6: Clicking an inactive tab switches to that session
      expect(result.current.activeTabId).toBe('tab-2');

      unmount();
    });

    it('should persist active tab selection to localStorage', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.selectTab('tab-2');
      });

      expect(mockStorage.getItem(ACTIVE_TAB_STORAGE_KEY)).toBe('tab-2');

      unmount();
    });

    it('should allow selecting the same tab (no-op)', () => {
      mockStorage.clear();
      const tabs = [createTestTab({ id: 'tab-1', title: 'Tab 1' })];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.selectTab('tab-1');
      });

      expect(result.current.activeTabId).toBe('tab-1');

      unmount();
    });
  });

  /**
   * Task 9.3: Test tab close (X button, last tab behavior)
   * **Validates: Requirements 1.5, 3.3, 3.4**
   */
  describe('Task 9.3: Tab Close', () => {
    it('should remove tab when closed', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      expect(result.current.openTabs.length).toBe(2);

      act(() => {
        result.current.closeTab('tab-2');
      });

      // Requirement 1.5: Each tab has an X button to close the session
      expect(result.current.openTabs.length).toBe(1);
      expect(result.current.openTabs.find(t => t.id === 'tab-2')).toBeUndefined();

      unmount();
    });

    it('should switch to adjacent tab when closing active tab', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
        createTestTab({ id: 'tab-3', title: 'Tab 3' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-2');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.closeTab('tab-2');
      });

      // Should switch to adjacent tab (tab-3 at same index, or tab-1 if at end)
      expect(['tab-1', 'tab-3']).toContain(result.current.activeTabId);

      unmount();
    });

    it('should auto-create new tab when closing last tab', () => {
      mockStorage.clear();
      const tabs = [createTestTab({ id: 'tab-1', title: 'Only Tab' })];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.closeTab('tab-1');
      });

      // Requirement 3.3: Closing the last remaining tab auto-creates a new "New Session" tab
      expect(result.current.openTabs.length).toBe(1);
      expect(result.current.openTabs[0].title).toBe('New Session');
      expect(result.current.openTabs[0].isNew).toBe(true);
      expect(result.current.activeTabId).toBe(result.current.openTabs[0].id);

      unmount();
    });

    it('should not affect active tab when closing inactive tab', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.closeTab('tab-2');
      });

      expect(result.current.activeTabId).toBe('tab-1');

      unmount();
    });

    it('should persist tab removal to localStorage', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.closeTab('tab-2');
      });

      const storedTabs = JSON.parse(mockStorage.getItem(OPEN_TABS_STORAGE_KEY) || '[]');
      expect(storedTabs.length).toBe(1);
      expect(storedTabs.find((t: OpenTab) => t.id === 'tab-2')).toBeUndefined();

      unmount();
    });
  });

  /**
   * Task 9.4: Test tab title update (first message updates title)
   * **Validates: Requirements 2.4**
   */
  describe('Task 9.4: Tab Title Update', () => {
    it('should update tab title', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const tabId = result.current.openTabs[0].id;

      act(() => {
        result.current.updateTabTitle(tabId, 'Updated Title');
      });

      // Requirement 2.4: When user sends first message, tab title updates
      expect(result.current.openTabs[0].title).toBe('Updated Title');

      unmount();
    });

    it('should persist title update to localStorage', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const tabId = result.current.openTabs[0].id;

      act(() => {
        result.current.updateTabTitle(tabId, 'Persisted Title');
      });

      const storedTabs = JSON.parse(mockStorage.getItem(OPEN_TABS_STORAGE_KEY) || '[]');
      expect(storedTabs[0].title).toBe('Persisted Title');

      unmount();
    });

    it('should update isNew flag via setTabIsNew', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const tabId = result.current.openTabs[0].id;
      expect(result.current.openTabs[0].isNew).toBe(true);

      act(() => {
        result.current.setTabIsNew(tabId, false);
      });

      expect(result.current.openTabs[0].isNew).toBe(false);

      unmount();
    });

    it('should update sessionId via updateTabSessionId', () => {
      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const tabId = result.current.openTabs[0].id;
      expect(result.current.openTabs[0].sessionId).toBeUndefined();

      act(() => {
        result.current.updateTabSessionId(tabId, 'session-123');
      });

      expect(result.current.openTabs[0].sessionId).toBe('session-123');

      unmount();
    });

    it('should only update the specified tab', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      act(() => {
        result.current.updateTabTitle('tab-1', 'Updated Tab 1');
      });

      expect(result.current.openTabs.find(t => t.id === 'tab-1')?.title).toBe('Updated Tab 1');
      expect(result.current.openTabs.find(t => t.id === 'tab-2')?.title).toBe('Tab 2');

      unmount();
    });
  });

  /**
   * Task 9.5: Test persistence (refresh restores tabs)
   * **Validates: Requirements 1.7, 3.1, 3.2**
   */
  describe('Task 9.5: Persistence', () => {
    it('should restore tabs from localStorage on mount', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'saved-tab-1', title: 'Saved Tab 1' }),
        createTestTab({ id: 'saved-tab-2', title: 'Saved Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'saved-tab-2');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      // Requirement 3.1: On app load, previously open sessions are restored as tabs
      expect(result.current.openTabs.length).toBe(2);
      expect(result.current.openTabs[0].title).toBe('Saved Tab 1');
      expect(result.current.openTabs[1].title).toBe('Saved Tab 2');
      expect(result.current.activeTabId).toBe('saved-tab-2');

      unmount();
    });

    it('should create default tab when no saved tabs exist', () => {
      mockStorage.clear();

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      // Requirement 3.2: If no previous sessions exist, a single "New Session" tab is shown
      expect(result.current.openTabs.length).toBe(1);
      expect(result.current.openTabs[0].title).toBe('New Session');
      expect(result.current.openTabs[0].isNew).toBe(true);

      unmount();
    });

    it('should create default tab when saved tabs array is empty', () => {
      mockStorage.clear();
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, '[]');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      expect(result.current.openTabs.length).toBe(1);
      expect(result.current.openTabs[0].title).toBe('New Session');

      unmount();
    });

    it('should handle invalid JSON in localStorage gracefully', () => {
      mockStorage.clear();
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, 'invalid-json');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      // Should fall back to default tab
      expect(result.current.openTabs.length).toBe(1);
      expect(result.current.openTabs[0].title).toBe('New Session');

      unmount();
    });

    it('should fall back to first tab if saved activeTabId is invalid', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Tab 1' }),
        createTestTab({ id: 'tab-2', title: 'Tab 2' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'non-existent-tab');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      expect(result.current.activeTabId).toBe('tab-1');

      unmount();
    });

    it('should simulate app restart and restore state', () => {
      mockStorage.clear();

      // First session
      const { result: result1, unmount: unmount1 } = renderHook(() =>
        useTabState(DEFAULT_AGENT_ID)
      );

      act(() => {
        result1.current.addTab(DEFAULT_AGENT_ID);
        result1.current.updateTabTitle(result1.current.openTabs[0].id, 'First Tab');
      });

      const tabsBeforeRestart = [...result1.current.openTabs];
      unmount1();

      // Second session (simulating restart)
      const { result: result2, unmount: unmount2 } = renderHook(() =>
        useTabState(DEFAULT_AGENT_ID)
      );

      // Requirement 1.7: Tab state persists across app restarts
      expect(result2.current.openTabs.length).toBe(tabsBeforeRestart.length);
      expect(result2.current.openTabs[0].title).toBe('First Tab');

      unmount2();
    });
  });

  /**
   * Task 9.6: Test invalid session filtering (removeInvalidTabs)
   * **Validates: Requirements 3.4**
   */
  describe('Task 9.6: Invalid Session Filtering', () => {
    it('should clear sessionId for tabs with invalid sessions', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Valid Session', sessionId: 'session-1' }),
        createTestTab({ id: 'tab-2', title: 'Invalid Session', sessionId: 'session-deleted' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const validSessionIds = new Set(['session-1']);

      act(() => {
        result.current.removeInvalidTabs(validSessionIds);
      });

      // Tab with valid session should be unchanged
      const validTab = result.current.openTabs.find(t => t.id === 'tab-1');
      expect(validTab?.sessionId).toBe('session-1');
      expect(validTab?.title).toBe('Valid Session');

      // Tab with invalid session should be reset
      const invalidTab = result.current.openTabs.find(t => t.id === 'tab-2');
      expect(invalidTab?.sessionId).toBeUndefined();
      expect(invalidTab?.title).toBe('New Session');
      expect(invalidTab?.isNew).toBe(true);

      unmount();
    });

    it('should not modify tabs without sessionId', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'New Tab', sessionId: undefined, isNew: true }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const validSessionIds = new Set(['session-1']);

      act(() => {
        result.current.removeInvalidTabs(validSessionIds);
      });

      expect(result.current.openTabs[0].title).toBe('New Tab');
      expect(result.current.openTabs[0].isNew).toBe(true);

      unmount();
    });

    it('should handle empty validSessionIds set', () => {
      mockStorage.clear();
      const tabs = [
        createTestTab({ id: 'tab-1', title: 'Session Tab', sessionId: 'session-1' }),
      ];
      mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
      mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'tab-1');

      const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

      const validSessionIds = new Set<string>();

      act(() => {
        result.current.removeInvalidTabs(validSessionIds);
      });

      // All tabs with sessionIds should be reset
      expect(result.current.openTabs[0].sessionId).toBeUndefined();
      expect(result.current.openTabs[0].title).toBe('New Session');

      unmount();
    });
  });

  /**
   * Property-Based Tests: Tab Invariants
   * **Validates: Design doc constraint - always ≥1 tab exists**
   */
  describe('Property-Based Tests: Tab Invariants', () => {
    it('should always maintain at least one tab', () => {
      fc.assert(
        fc.property(
          fc.array(fc.oneof(fc.constant('add'), fc.constant('close')), { minLength: 1, maxLength: 20 }),
          (operations) => {
            mockStorage.clear();
            uuidCounter = 0;

            const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

            for (const op of operations) {
              act(() => {
                if (op === 'add') {
                  result.current.addTab(DEFAULT_AGENT_ID);
                } else if (result.current.openTabs.length > 0) {
                  // Close a random tab
                  const randomIndex = Math.floor(Math.random() * result.current.openTabs.length);
                  result.current.closeTab(result.current.openTabs[randomIndex].id);
                }
              });
            }

            // Invariant: Always at least one tab
            expect(result.current.openTabs.length).toBeGreaterThanOrEqual(1);

            unmount();
          }
        ),
        { numRuns: 50 }
      );
    });

    it('should always have a valid activeTabId', () => {
      fc.assert(
        fc.property(
          fc.array(fc.oneof(fc.constant('add'), fc.constant('close'), fc.constant('select')), { minLength: 1, maxLength: 15 }),
          (operations) => {
            mockStorage.clear();
            uuidCounter = 0;

            const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

            for (const op of operations) {
              act(() => {
                if (op === 'add') {
                  result.current.addTab(DEFAULT_AGENT_ID);
                } else if (op === 'close' && result.current.openTabs.length > 0) {
                  const randomIndex = Math.floor(Math.random() * result.current.openTabs.length);
                  result.current.closeTab(result.current.openTabs[randomIndex].id);
                } else if (op === 'select' && result.current.openTabs.length > 0) {
                  const randomIndex = Math.floor(Math.random() * result.current.openTabs.length);
                  result.current.selectTab(result.current.openTabs[randomIndex].id);
                }
              });
            }

            // Invariant: activeTabId should always reference an existing tab
            const activeTab = result.current.openTabs.find(t => t.id === result.current.activeTabId);
            expect(activeTab).toBeDefined();

            unmount();
          }
        ),
        { numRuns: 50 }
      );
    });

    it('should persist state correctly through operations', () => {
      fc.assert(
        fc.property(
          fc.array(fc.string({ minLength: 1, maxLength: 20 }), { minLength: 1, maxLength: 5 }),
          (titles) => {
            mockStorage.clear();
            uuidCounter = 0;

            const { result, unmount } = renderHook(() => useTabState(DEFAULT_AGENT_ID));

            // Add tabs and update titles
            for (const title of titles) {
              act(() => {
                const newTab = result.current.addTab(DEFAULT_AGENT_ID);
                result.current.updateTabTitle(newTab.id, title);
              });
            }

            // Verify localStorage matches state
            const storedTabs = JSON.parse(mockStorage.getItem(OPEN_TABS_STORAGE_KEY) || '[]');
            expect(storedTabs.length).toBe(result.current.openTabs.length);

            unmount();
          }
        ),
        { numRuns: 30 }
      );
    });
  });
});
