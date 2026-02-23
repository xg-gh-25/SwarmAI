/**
 * Property-Based Tests for useRightSidebarGroup Hook
 *
 * **Feature: right-sidebar-mutual-exclusion**
 * **Property 1: Mutual Exclusion Invariant**
 * **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1**
 *
 * These property tests verify that the mutual exclusion invariant holds
 * across all possible sequences of sidebar open operations.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import fc from 'fast-check';
import { useRightSidebarGroup } from './useRightSidebarGroup';
import { RIGHT_SIDEBAR_IDS, RIGHT_SIDEBAR_WIDTH_CONFIGS, type RightSidebarId } from '../pages/chat/constants';

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

let originalLocalStorage: Storage;
let mockStorage: MockLocalStorage;

// ============== Arbitraries ==============

// Arbitrary for sidebar IDs
const sidebarIdArb = fc.constantFrom<RightSidebarId>('todoRadar', 'chatHistory', 'fileBrowser');

// Arbitrary for sequences of operations
const operationSequenceArb = fc.array(sidebarIdArb, { minLength: 1, maxLength: 20 });

// ============== Property Tests ==============

describe('Property 1: Mutual Exclusion Invariant', () => {
  beforeEach(() => {
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
    });
    mockStorage.clear();
  });

  /**
   * Property Test: Mutual Exclusion Invariant
   *
   * For any sequence of sidebar open operations, at most one sidebar
   * from the Right_Sidebar_Group shall be visible at any time.
   * When `openSidebar(id)` is called, the resulting state shall have
   * exactly that sidebar active and all others inactive.
   *
   * **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1**
   */
  it('should maintain exactly one active sidebar after any open operation', () => {
    fc.assert(
      fc.property(sidebarIdArb, operationSequenceArb, (initial, operations) => {
        // Initialize hook with the generated initial sidebar
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: initial,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        // Verify initial state has exactly one active sidebar
        let activeCount = RIGHT_SIDEBAR_IDS.filter(id => result.current.isActive(id)).length;
        expect(activeCount).toBe(1);
        expect(result.current.activeSidebar).toBe(initial);

        // Apply each operation in the sequence
        for (const op of operations) {
          act(() => {
            result.current.openSidebar(op);
          });

          // Invariant: exactly one sidebar is active
          activeCount = RIGHT_SIDEBAR_IDS.filter(id => result.current.isActive(id)).length;
          expect(activeCount).toBe(1);

          // The active sidebar is the one we just opened
          expect(result.current.activeSidebar).toBe(op);
        }
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Active sidebar matches last opened
   *
   * After any sequence of open operations, the active sidebar
   * should always be the last one that was opened.
   *
   * **Validates: Requirements 1.1, 1.2, 1.3, 2.1**
   */
  it('should have active sidebar match the last opened sidebar', () => {
    fc.assert(
      fc.property(sidebarIdArb, operationSequenceArb, (initial, operations) => {
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: initial,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        // Apply all operations
        for (const op of operations) {
          act(() => {
            result.current.openSidebar(op);
          });
        }

        // The final active sidebar should be the last operation
        const lastOperation = operations[operations.length - 1];
        expect(result.current.activeSidebar).toBe(lastOperation);
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: isActive consistency with activeSidebar
   *
   * The isActive function should return true only for the
   * currently active sidebar and false for all others.
   *
   * **Validates: Requirements 1.4**
   */
  it('should have isActive consistent with activeSidebar state', () => {
    fc.assert(
      fc.property(sidebarIdArb, operationSequenceArb, (initial, operations) => {
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: initial,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        // Apply all operations
        for (const op of operations) {
          act(() => {
            result.current.openSidebar(op);
          });

          // Verify isActive is consistent with activeSidebar
          for (const id of RIGHT_SIDEBAR_IDS) {
            const expectedActive = id === result.current.activeSidebar;
            expect(result.current.isActive(id)).toBe(expectedActive);
          }
        }
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Opening a sidebar closes all others
   *
   * When opening any sidebar, all other sidebars should become inactive.
   *
   * **Validates: Requirements 1.1, 1.2, 1.3**
   */
  it('should close all other sidebars when opening one', () => {
    fc.assert(
      fc.property(sidebarIdArb, sidebarIdArb, (initial, targetSidebar) => {
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: initial,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        act(() => {
          result.current.openSidebar(targetSidebar);
        });

        // Only the target sidebar should be active
        for (const id of RIGHT_SIDEBAR_IDS) {
          if (id === targetSidebar) {
            expect(result.current.isActive(id)).toBe(true);
          } else {
            expect(result.current.isActive(id)).toBe(false);
          }
        }
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Property-Based Tests for useRightSidebarGroup Hook
 *
 * **Feature: right-sidebar-mutual-exclusion**
 * **Property 2: No-op on Active Sidebar Click**
 * **Validates: Requirements 2.2**
 *
 * These property tests verify that clicking an already active sidebar
 * button results in no state change.
 */
describe('Property 2: No-op on Active Sidebar Click', () => {
  beforeEach(() => {
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
    });
    mockStorage.clear();
  });

  /**
   * Property Test: No-op on Active Sidebar Click
   *
   * For any active sidebar state, calling `openSidebar(activeSidebar)`
   * shall result in no state change—the same sidebar remains active.
   *
   * **Validates: Requirements 2.2**
   */
  it('should not change state when clicking active sidebar button', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        // Initialize hook with the generated sidebar as active
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: activeSidebar,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        // Capture state before clicking
        const stateBefore = result.current.activeSidebar;

        // Click the already active sidebar button
        act(() => {
          result.current.openSidebar(activeSidebar);
        });

        // State should remain unchanged
        expect(result.current.activeSidebar).toBe(stateBefore);
        expect(result.current.activeSidebar).toBe(activeSidebar);
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Repeated clicks on active sidebar maintain state
   *
   * For any active sidebar, multiple consecutive clicks on the same
   * sidebar button should all be no-ops.
   *
   * **Validates: Requirements 2.2**
   */
  it('should maintain state across multiple clicks on active sidebar', () => {
    fc.assert(
      fc.property(
        sidebarIdArb,
        fc.integer({ min: 1, max: 10 }),
        (activeSidebar, clickCount) => {
          const { result } = renderHook(() =>
            useRightSidebarGroup({
              defaultActive: activeSidebar,
              widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
            })
          );

          // Click the active sidebar multiple times
          for (let i = 0; i < clickCount; i++) {
            act(() => {
              result.current.openSidebar(activeSidebar);
            });

            // State should remain the same after each click
            expect(result.current.activeSidebar).toBe(activeSidebar);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: isActive remains consistent after no-op click
   *
   * After clicking an already active sidebar, the isActive function
   * should still return the same values for all sidebars.
   *
   * **Validates: Requirements 2.2**
   */
  it('should maintain isActive consistency after clicking active sidebar', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const { result } = renderHook(() =>
          useRightSidebarGroup({
            defaultActive: activeSidebar,
            widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
          })
        );

        // Capture isActive state before clicking
        const isActiveBefore = RIGHT_SIDEBAR_IDS.map(id => ({
          id,
          active: result.current.isActive(id),
        }));

        // Click the already active sidebar
        act(() => {
          result.current.openSidebar(activeSidebar);
        });

        // isActive should return the same values
        for (const { id, active } of isActiveBefore) {
          expect(result.current.isActive(id)).toBe(active);
        }
      }),
      { numRuns: 100 }
    );
  });
});
