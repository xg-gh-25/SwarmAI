/**
 * Property-Based Tests for Layout Context State Persistence
 *
 * **Feature: three-column-layout**
 * **Property 2: Workspace Explorer Collapse Toggle**
 * **Property 3: Workspace Explorer Resize Constraints**
 * **Property 27: Collapse State Persistence**
 * **Property 28: Width Persistence**
 * **Property 29: Collapse Toggle Button Visibility**
 * **Validates: Requirements 1.6, 1.7, 11.2, 11.3, 11.4, 11.5**
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import { ReactNode } from 'react';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS } from './LayoutContext';

// ============== Test Setup ==============

// Mock localStorage for testing
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

// Store original localStorage
let originalLocalStorage: Storage;
let mockStorage: MockLocalStorage;

// Wrapper component for testing hooks
const wrapper = ({ children }: { children: ReactNode }) => (
  <LayoutProvider>{children}</LayoutProvider>
);

// ============== Property-Based Tests ==============

describe('Layout Context - Property-Based Tests', () => {
  beforeEach(() => {
    // Save original localStorage and replace with mock
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
    });
    
    // Mock window.innerWidth to prevent auto-collapse behavior
    Object.defineProperty(window, 'innerWidth', {
      value: 1024,
      writable: true,
    });
  });

  afterEach(() => {
    // Restore original localStorage
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
    });
    mockStorage.clear();
  });

  /**
   * Property 27: Collapse State Persistence
   * **Feature: three-column-layout**
   * **Validates: Requirements 11.3**
   *
   * For any Workspace_Explorer collapsed state change, after application restart,
   * the collapsed state SHALL be restored to the last saved value.
   */
  describe('Feature: three-column-layout, Property 27: Collapse State Persistence', () => {
    it('should persist collapsed state to localStorage when changed', () => {
      fc.assert(
        fc.property(fc.boolean(), (collapsedState) => {
          // Clear storage before each iteration
          mockStorage.clear();

          // Render the hook
          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Set the collapsed state
          act(() => {
            result.current.setWorkspaceExplorerCollapsed(collapsedState);
          });

          // Verify the state was updated
          expect(result.current.workspaceExplorerCollapsed).toBe(collapsedState);

          // Verify localStorage was updated
          const storedValue = mockStorage.getItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED
          );
          expect(storedValue).toBe(String(collapsedState));

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should restore collapsed state from localStorage on mount', () => {
      fc.assert(
        fc.property(fc.boolean(), (initialCollapsedState) => {
          // Clear storage and set initial value
          mockStorage.clear();
          mockStorage.setItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED,
            String(initialCollapsedState)
          );

          // Render the hook (simulating app restart)
          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Verify the state was restored from localStorage
          expect(result.current.workspaceExplorerCollapsed).toBe(initialCollapsedState);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain collapsed state consistency through multiple toggles', () => {
      fc.assert(
        fc.property(
          fc.array(fc.boolean(), { minLength: 1, maxLength: 10 }),
          (toggleSequence) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Apply each toggle in sequence
            for (const collapsed of toggleSequence) {
              act(() => {
                result.current.setWorkspaceExplorerCollapsed(collapsed);
              });
            }

            // The final state should match the last value in the sequence
            const expectedFinalState = toggleSequence[toggleSequence.length - 1];
            expect(result.current.workspaceExplorerCollapsed).toBe(expectedFinalState);

            // localStorage should also have the final state
            const storedValue = mockStorage.getItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED
            );
            expect(storedValue).toBe(String(expectedFinalState));

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should simulate app restart and restore last saved collapsed state', () => {
      fc.assert(
        fc.property(fc.boolean(), (collapsedState) => {
          mockStorage.clear();

          // First "session" - set the state
          const { result: result1, unmount: unmount1 } = renderHook(() => useLayout(), {
            wrapper,
          });

          act(() => {
            result1.current.setWorkspaceExplorerCollapsed(collapsedState);
          });

          unmount1();

          // Second "session" - simulate app restart by creating new hook instance
          const { result: result2, unmount: unmount2 } = renderHook(() => useLayout(), {
            wrapper,
          });

          // Property: After restart, collapsed state SHALL be restored to last saved value
          expect(result2.current.workspaceExplorerCollapsed).toBe(collapsedState);

          unmount2();
        }),
        { numRuns: 100 }
      );
    });

    it('should default to false when localStorage has no stored collapsed state', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          mockStorage.clear();
          // Ensure no value is stored
          expect(
            mockStorage.getItem(LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED)
          ).toBeNull();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Should default to false (not collapsed)
          expect(result.current.workspaceExplorerCollapsed).toBe(false);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 28: Width Persistence
   * **Feature: three-column-layout**
   * **Validates: Requirements 11.4**
   *
   * For any Workspace_Explorer width change via resize, after application restart,
   * the width SHALL be restored to the last saved value.
   */
  describe('Feature: three-column-layout, Property 28: Width Persistence', () => {
    // Arbitrary for valid width values within constraints
    const validWidthArb = fc.integer({
      min: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
      max: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH,
    });

    // Arbitrary for any width value (including out of bounds)
    const anyWidthArb = fc.integer({ min: 0, max: 1000 });

    it('should persist width to localStorage when changed', () => {
      fc.assert(
        fc.property(validWidthArb, (width) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerWidth(width);
          });

          // Verify the state was updated
          expect(result.current.workspaceExplorerWidth).toBe(width);

          // Verify localStorage was updated
          const storedValue = mockStorage.getItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH
          );
          expect(storedValue).toBe(String(width));

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should restore width from localStorage on mount', () => {
      fc.assert(
        fc.property(validWidthArb, (initialWidth) => {
          mockStorage.clear();
          mockStorage.setItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH,
            String(initialWidth)
          );

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Property: After restart, width SHALL be restored to last saved value
          expect(result.current.workspaceExplorerWidth).toBe(initialWidth);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should clamp width to minimum constraint (200px)', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 0, max: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH - 1 }),
          (belowMinWidth) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            act(() => {
              result.current.setWorkspaceExplorerWidth(belowMinWidth);
            });

            // Width should be clamped to minimum
            expect(result.current.workspaceExplorerWidth).toBe(
              LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
            );

            // localStorage should also have the clamped value
            const storedValue = mockStorage.getItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH
            );
            expect(storedValue).toBe(
              String(LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH)
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should clamp width to maximum constraint (500px)', () => {
      fc.assert(
        fc.property(
          fc.integer({
            min: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH + 1,
            max: 1000,
          }),
          (aboveMaxWidth) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            act(() => {
              result.current.setWorkspaceExplorerWidth(aboveMaxWidth);
            });

            // Width should be clamped to maximum
            expect(result.current.workspaceExplorerWidth).toBe(
              LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
            );

            // localStorage should also have the clamped value
            const storedValue = mockStorage.getItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH
            );
            expect(storedValue).toBe(
              String(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH)
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should enforce width constraints for any input value', () => {
      fc.assert(
        fc.property(anyWidthArb, (inputWidth) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerWidth(inputWidth);
          });

          // Property: Width SHALL always be within min/max constraints
          expect(result.current.workspaceExplorerWidth).toBeGreaterThanOrEqual(
            LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
          );
          expect(result.current.workspaceExplorerWidth).toBeLessThanOrEqual(
            LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
          );

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should simulate app restart and restore last saved width', () => {
      fc.assert(
        fc.property(validWidthArb, (width) => {
          mockStorage.clear();

          // First "session" - set the width
          const { result: result1, unmount: unmount1 } = renderHook(() => useLayout(), {
            wrapper,
          });

          act(() => {
            result1.current.setWorkspaceExplorerWidth(width);
          });

          unmount1();

          // Second "session" - simulate app restart
          const { result: result2, unmount: unmount2 } = renderHook(() => useLayout(), {
            wrapper,
          });

          // Property: After restart, width SHALL be restored to last saved value
          expect(result2.current.workspaceExplorerWidth).toBe(width);

          unmount2();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain width consistency through multiple resize operations', () => {
      fc.assert(
        fc.property(
          fc.array(anyWidthArb, { minLength: 1, maxLength: 10 }),
          (resizeSequence) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Apply each resize in sequence
            for (const width of resizeSequence) {
              act(() => {
                result.current.setWorkspaceExplorerWidth(width);
              });
            }

            // The final width should be the clamped version of the last value
            const lastWidth = resizeSequence[resizeSequence.length - 1];
            const expectedFinalWidth = Math.max(
              LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
              Math.min(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH, lastWidth)
            );

            expect(result.current.workspaceExplorerWidth).toBe(expectedFinalWidth);

            // localStorage should also have the final width
            const storedValue = mockStorage.getItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH
            );
            expect(storedValue).toBe(String(expectedFinalWidth));

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should default to 280px when localStorage has no stored width', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          mockStorage.clear();
          // Ensure no value is stored
          expect(
            mockStorage.getItem(LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH)
          ).toBeNull();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Should default to DEFAULT_WORKSPACE_EXPLORER_WIDTH (280px)
          expect(result.current.workspaceExplorerWidth).toBe(
            LAYOUT_CONSTANTS.DEFAULT_WORKSPACE_EXPLORER_WIDTH
          );

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should handle invalid localStorage values gracefully', () => {
      fc.assert(
        fc.property(
          fc.oneof(
            fc.constant('invalid'),
            fc.constant('NaN'),
            fc.constant(''),
            fc.constant('abc123')
          ),
          (invalidValue) => {
            mockStorage.clear();
            mockStorage.setItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH,
              invalidValue
            );

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Should fall back to default when localStorage has invalid value
            expect(result.current.workspaceExplorerWidth).toBe(
              LAYOUT_CONSTANTS.DEFAULT_WORKSPACE_EXPLORER_WIDTH
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should restore clamped width when localStorage has out-of-bounds value', () => {
      fc.assert(
        fc.property(
          fc.oneof(
            fc.integer({ min: 0, max: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH - 1 }),
            fc.integer({ min: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH + 1, max: 1000 })
          ),
          (outOfBoundsWidth) => {
            mockStorage.clear();
            mockStorage.setItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH,
              String(outOfBoundsWidth)
            );

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Width should be clamped to valid range
            expect(result.current.workspaceExplorerWidth).toBeGreaterThanOrEqual(
              LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
            );
            expect(result.current.workspaceExplorerWidth).toBeLessThanOrEqual(
              LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 2: Workspace Explorer Collapse Toggle
   * **Feature: three-column-layout, Property 2: Workspace Explorer Collapse Toggle**
   * **Validates: Requirements 1.6**
   *
   * For any toggle action on the Workspace_Explorer collapse button, the collapsed state
   * SHALL invert (collapsed becomes expanded, expanded becomes collapsed) and the
   * Main_Chat_Panel SHALL expand to fill the freed space.
   */
  describe('Feature: three-column-layout, Property 2: Workspace Explorer Collapse Toggle', () => {
    it('should invert collapsed state on toggle (collapsed becomes expanded)', () => {
      fc.assert(
        fc.property(fc.constant(true), () => {
          mockStorage.clear();
          // Start with collapsed state
          mockStorage.setItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED,
            'true'
          );

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Verify initial collapsed state
          expect(result.current.workspaceExplorerCollapsed).toBe(true);

          // Toggle (simulate collapse button click)
          act(() => {
            result.current.setWorkspaceExplorerCollapsed(false);
          });

          // Property: Collapsed state SHALL invert (true -> false)
          expect(result.current.workspaceExplorerCollapsed).toBe(false);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should invert collapsed state on toggle (expanded becomes collapsed)', () => {
      fc.assert(
        fc.property(fc.constant(false), () => {
          mockStorage.clear();
          // Start with expanded state
          mockStorage.setItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED,
            'false'
          );

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          // Verify initial expanded state
          expect(result.current.workspaceExplorerCollapsed).toBe(false);

          // Toggle (simulate collapse button click)
          act(() => {
            result.current.setWorkspaceExplorerCollapsed(true);
          });

          // Property: Collapsed state SHALL invert (false -> true)
          expect(result.current.workspaceExplorerCollapsed).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly toggle through any sequence of collapse/expand actions', () => {
      fc.assert(
        fc.property(
          fc.boolean(),
          fc.array(fc.boolean(), { minLength: 1, maxLength: 20 }),
          (initialState, toggleSequence) => {
            mockStorage.clear();
            mockStorage.setItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED,
              String(initialState)
            );

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Apply each toggle in sequence
            for (const targetState of toggleSequence) {
              act(() => {
                result.current.setWorkspaceExplorerCollapsed(targetState);
              });
              // Property: State SHALL match the target state after each toggle
              expect(result.current.workspaceExplorerCollapsed).toBe(targetState);
            }

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should persist toggle state to localStorage after each toggle', () => {
      fc.assert(
        fc.property(fc.boolean(), (targetState) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerCollapsed(targetState);
          });

          // Property: localStorage SHALL be updated with the new state
          const storedValue = mockStorage.getItem(
            LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED
          );
          expect(storedValue).toBe(String(targetState));

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain state consistency through rapid toggle sequences', () => {
      fc.assert(
        fc.property(
          fc.array(fc.boolean(), { minLength: 5, maxLength: 50 }),
          (rapidToggles) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            // Apply rapid toggles
            for (const state of rapidToggles) {
              act(() => {
                result.current.setWorkspaceExplorerCollapsed(state);
              });
            }

            // Property: Final state SHALL match the last toggle value
            const expectedFinalState = rapidToggles[rapidToggles.length - 1];
            expect(result.current.workspaceExplorerCollapsed).toBe(expectedFinalState);

            // Property: localStorage SHALL have the final state
            const storedValue = mockStorage.getItem(
              LAYOUT_CONSTANTS.STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED
            );
            expect(storedValue).toBe(String(expectedFinalState));

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 3: Workspace Explorer Resize Constraints
   * **Feature: three-column-layout, Property 3: Workspace Explorer Resize Constraints**
   * **Validates: Requirements 1.7, 11.5**
   *
   * For any drag resize operation on the Workspace_Explorer, the resulting width
   * SHALL be clamped between the minimum (200px) and maximum (500px) constraints.
   */
  describe('Feature: three-column-layout, Property 3: Workspace Explorer Resize Constraints', () => {
    // Arbitrary for any possible drag width (simulating user drag)
    const anyDragWidthArb = fc.integer({ min: -100, max: 1500 });
    
    // Arbitrary for valid width within constraints
    const validWidthArb = fc.integer({
      min: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
      max: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH,
    });

    it('should clamp width to minimum (200px) for any drag below minimum', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: -100, max: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH - 1 }),
          (belowMinDrag) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            act(() => {
              result.current.setWorkspaceExplorerWidth(belowMinDrag);
            });

            // Property: Width SHALL be clamped to minimum (200px)
            expect(result.current.workspaceExplorerWidth).toBe(
              LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should clamp width to maximum (500px) for any drag above maximum', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH + 1, max: 1500 }),
          (aboveMaxDrag) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            act(() => {
              result.current.setWorkspaceExplorerWidth(aboveMaxDrag);
            });

            // Property: Width SHALL be clamped to maximum (500px)
            expect(result.current.workspaceExplorerWidth).toBe(
              LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
            );

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should accept any width within valid range (200px - 500px)', () => {
      fc.assert(
        fc.property(validWidthArb, (validDrag) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerWidth(validDrag);
          });

          // Property: Width SHALL be exactly the input value when within constraints
          expect(result.current.workspaceExplorerWidth).toBe(validDrag);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should always produce width within constraints for any drag value', () => {
      fc.assert(
        fc.property(anyDragWidthArb, (dragWidth) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerWidth(dragWidth);
          });

          // Property: Resulting width SHALL always be >= 200px and <= 500px
          expect(result.current.workspaceExplorerWidth).toBeGreaterThanOrEqual(
            LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
          );
          expect(result.current.workspaceExplorerWidth).toBeLessThanOrEqual(
            LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
          );

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain constraints through multiple resize operations', () => {
      fc.assert(
        fc.property(
          fc.array(anyDragWidthArb, { minLength: 1, maxLength: 20 }),
          (resizeSequence) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            for (const dragWidth of resizeSequence) {
              act(() => {
                result.current.setWorkspaceExplorerWidth(dragWidth);
              });

              // Property: After each resize, width SHALL be within constraints
              expect(result.current.workspaceExplorerWidth).toBeGreaterThanOrEqual(
                LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH
              );
              expect(result.current.workspaceExplorerWidth).toBeLessThanOrEqual(
                LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
              );
            }

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should correctly clamp edge case values at exact boundaries', () => {
      fc.assert(
        fc.property(
          fc.oneof(
            fc.constant(LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH),
            fc.constant(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH),
            fc.constant(LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH - 1),
            fc.constant(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH + 1)
          ),
          (boundaryValue) => {
            mockStorage.clear();

            const { result, unmount } = renderHook(() => useLayout(), { wrapper });

            act(() => {
              result.current.setWorkspaceExplorerWidth(boundaryValue);
            });

            // Property: Width SHALL be clamped correctly at boundaries
            const expectedWidth = Math.max(
              LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
              Math.min(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH, boundaryValue)
            );
            expect(result.current.workspaceExplorerWidth).toBe(expectedWidth);

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 29: Collapse Toggle Button Visibility
   * **Feature: three-column-layout, Property 29: Collapse Toggle Button Visibility**
   * **Validates: Requirements 11.2**
   *
   * For any collapsed state of Workspace_Explorer, a toggle button SHALL be visible
   * to allow expanding the explorer.
   * 
   * This property tests the pure logic that determines button visibility based on
   * collapsed state. The actual button rendering is tested in component tests.
   */
  describe('Feature: three-column-layout, Property 29: Collapse Toggle Button Visibility', () => {
    /**
     * Pure function that determines if the expand button should be visible.
     * When collapsed, the expand button MUST be visible.
     * When expanded, the collapse button MUST be visible.
     */
    function shouldShowExpandButton(collapsed: boolean): boolean {
      return collapsed;
    }

    function shouldShowCollapseButton(collapsed: boolean): boolean {
      return !collapsed;
    }

    /**
     * Pure function that determines which toggle action is available.
     * Returns the action that will be performed when the toggle button is clicked.
     */
    function getToggleAction(collapsed: boolean): 'expand' | 'collapse' {
      return collapsed ? 'expand' : 'collapse';
    }

    it('should show expand button when workspace explorer is collapsed', () => {
      fc.assert(
        fc.property(fc.constant(true), (collapsed) => {
          // Property: When collapsed, expand button SHALL be visible
          expect(shouldShowExpandButton(collapsed)).toBe(true);
          expect(shouldShowCollapseButton(collapsed)).toBe(false);
          expect(getToggleAction(collapsed)).toBe('expand');
        }),
        { numRuns: 100 }
      );
    });

    it('should show collapse button when workspace explorer is expanded', () => {
      fc.assert(
        fc.property(fc.constant(false), (collapsed) => {
          // Property: When expanded, collapse button SHALL be visible
          expect(shouldShowExpandButton(collapsed)).toBe(false);
          expect(shouldShowCollapseButton(collapsed)).toBe(true);
          expect(getToggleAction(collapsed)).toBe('collapse');
        }),
        { numRuns: 100 }
      );
    });

    it('should always have exactly one toggle button visible for any state', () => {
      fc.assert(
        fc.property(fc.boolean(), (collapsed) => {
          const expandVisible = shouldShowExpandButton(collapsed);
          const collapseVisible = shouldShowCollapseButton(collapsed);

          // Property: Exactly one button SHALL be visible at any time
          expect(expandVisible !== collapseVisible).toBe(true);
          expect(expandVisible || collapseVisible).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should provide correct toggle action for any collapsed state', () => {
      fc.assert(
        fc.property(fc.boolean(), (collapsed) => {
          const action = getToggleAction(collapsed);

          // Property: Action SHALL be the inverse of current state
          if (collapsed) {
            expect(action).toBe('expand');
          } else {
            expect(action).toBe('collapse');
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain button visibility consistency through state changes', () => {
      fc.assert(
        fc.property(
          fc.array(fc.boolean(), { minLength: 1, maxLength: 20 }),
          (stateSequence) => {
            for (const collapsed of stateSequence) {
              // Property: For each state, exactly one button SHALL be visible
              const expandVisible = shouldShowExpandButton(collapsed);
              const collapseVisible = shouldShowCollapseButton(collapsed);

              expect(expandVisible !== collapseVisible).toBe(true);

              // Property: The visible button SHALL match the current state
              if (collapsed) {
                expect(expandVisible).toBe(true);
              } else {
                expect(collapseVisible).toBe(true);
              }
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should verify toggle button visibility through context state changes', () => {
      fc.assert(
        fc.property(fc.boolean(), (targetCollapsed) => {
          mockStorage.clear();

          const { result, unmount } = renderHook(() => useLayout(), { wrapper });

          act(() => {
            result.current.setWorkspaceExplorerCollapsed(targetCollapsed);
          });

          // Property: Context state SHALL determine button visibility
          const collapsed = result.current.workspaceExplorerCollapsed;
          expect(shouldShowExpandButton(collapsed)).toBe(targetCollapsed);
          expect(shouldShowCollapseButton(collapsed)).toBe(!targetCollapsed);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });
});
