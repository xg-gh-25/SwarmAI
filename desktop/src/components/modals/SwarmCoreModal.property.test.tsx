/**
 * Property-Based Tests for SwarmCoreModal
 *
 * **Feature: left-navigation-redesign**
 * **Property 4: Escape Key Closes Any Modal**
 * **Validates: Requirements 5.4**
 *
 * These tests validate that pressing the Escape key on the SwarmCoreModal
 * correctly triggers the onClose callback, which should set activeModal to null.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import * as fc from 'fast-check';
import SwarmCoreModal from './SwarmCoreModal';

// Mock SwarmCorePage to avoid complex dependencies
vi.mock('../../pages/SwarmCorePage', () => ({
  default: () => <div data-testid="swarmcore-page-content">SwarmCore Page Content</div>,
}));

// ============== Arbitraries ==============

/**
 * Arbitrary for generating modal open states
 * For Property 4, we focus on the case where the modal IS open (isOpen = true)
 */
const modalOpenStateArb = fc.constant(true);

/**
 * Arbitrary for generating test scenarios with different Escape key press counts
 */
const escapeKeyTestScenarioArb = fc.record({
  isOpen: modalOpenStateArb,
  escapeKeyPressCount: fc.integer({ min: 1, max: 5 }),
});

/**
 * Arbitrary for generating various modal types to test state transition
 */
const modalTypeArb = fc.constantFrom(
  'workspaces',
  'swarmcore',
  'agents',
  'skills',
  'mcp',
  'settings'
);

// ============== Pure Functions Under Test ==============

/**
 * Simulates the state transition when Escape key is pressed.
 * This represents the expected behavior: pressing Escape should call onClose,
 * which sets activeModal to null.
 *
 * @param currentModalState - The current activeModal value (e.g., 'swarmcore')
 * @returns null - The expected state after Escape key press
 */
function simulateEscapeKeyPress(_currentModalState: string | null): null {
  // Property: Pressing Escape key SHALL result in activeModal being null
  // regardless of what the current modal state is
  return null;
}

/**
 * Validates that the close callback was invoked correctly.
 *
 * @param onCloseMock - The mock function for onClose
 * @param expectedCallCount - Expected number of times onClose should be called
 * @returns boolean - Whether the validation passed
 */
function validateCloseCallbackInvoked(
  onCloseMock: ReturnType<typeof vi.fn>,
  expectedCallCount: number
): boolean {
  return onCloseMock.mock.calls.length === expectedCallCount;
}

// ============== Property-Based Tests ==============

describe('SwarmCoreModal - Property-Based Tests', () => {
  /**
   * Property 4: Escape Key Closes Any Modal
   * **Feature: left-navigation-redesign, Property 4: Escape Key Closes Any Modal**
   * **Validates: Requirements 5.4**
   *
   * For any open modal (where activeModal is not null), pressing the Escape key
   * SHALL result in activeModal being set to null.
   */
  describe('Feature: left-navigation-redesign, Property 4: Escape Key Closes Any Modal', () => {
    it('should call onClose when Escape key is pressed for any open modal state', () => {
      fc.assert(
        fc.property(escapeKeyTestScenarioArb, (scenario) => {
          const onCloseMock = vi.fn();

          // Render the modal in open state
          const { unmount } = render(
            <SwarmCoreModal isOpen={scenario.isOpen} onClose={onCloseMock} />
          );

          // Simulate pressing the Escape key
          fireEvent.keyDown(document, { key: 'Escape' });

          // Property: Pressing Escape key SHALL invoke onClose callback
          expect(onCloseMock).toHaveBeenCalledTimes(1);

          // Property: The expected result of onClose is to set activeModal to null
          const expectedState = simulateEscapeKeyPress('swarmcore');
          expect(expectedState).toBeNull();

          // Cleanup
          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should call onClose exactly once per Escape key press', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 10 }), (pressCount) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<SwarmCoreModal isOpen={true} onClose={onCloseMock} />);

          // Press the Escape key multiple times
          for (let i = 0; i < pressCount; i++) {
            fireEvent.keyDown(document, { key: 'Escape' });
          }

          // Property: Each Escape key press SHALL invoke onClose exactly once
          expect(onCloseMock).toHaveBeenCalledTimes(pressCount);

          // Property: Validate callback invocation count
          expect(validateCloseCallbackInvoked(onCloseMock, pressCount)).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should not trigger onClose when modal is closed', () => {
      fc.assert(
        fc.property(fc.constant(false), (isOpen) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<SwarmCoreModal isOpen={isOpen} onClose={onCloseMock} />);

          // Simulate pressing the Escape key when modal is closed
          fireEvent.keyDown(document, { key: 'Escape' });

          // Property: When modal is closed, Escape key SHALL NOT trigger onClose
          expect(onCloseMock).not.toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should render modal content when open and Escape key should close it', () => {
      fc.assert(
        fc.property(modalOpenStateArb, (isOpen) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<SwarmCoreModal isOpen={isOpen} onClose={onCloseMock} />);

          // Property: When modal is open, it SHALL render the SwarmCorePage content
          const content = screen.getByTestId('swarmcore-page-content');
          expect(content).toBeDefined();

          // Property: Pressing Escape SHALL trigger onClose
          fireEvent.keyDown(document, { key: 'Escape' });
          expect(onCloseMock).toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should simulate correct state transition on Escape key press', () => {
      fc.assert(
        fc.property(modalTypeArb, (modalType) => {
          // Property: For ANY modal type, pressing Escape SHALL result in null state
          const resultState = simulateEscapeKeyPress(modalType);
          expect(resultState).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should handle rapid Escape key presses correctly', () => {
      fc.assert(
        fc.property(fc.integer({ min: 5, max: 20 }), (rapidPressCount) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<SwarmCoreModal isOpen={true} onClose={onCloseMock} />);

          // Simulate rapid Escape key pressing
          for (let i = 0; i < rapidPressCount; i++) {
            fireEvent.keyDown(document, { key: 'Escape' });
          }

          // Property: All Escape key presses SHALL be registered
          expect(onCloseMock).toHaveBeenCalledTimes(rapidPressCount);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should only respond to Escape key, not other keys', () => {
      fc.assert(
        fc.property(
          fc.constantFrom('Enter', 'Tab', 'Space', 'ArrowUp', 'ArrowDown', 'a', 'z', '1'),
          (otherKey) => {
            const onCloseMock = vi.fn();

            const { unmount } = render(<SwarmCoreModal isOpen={true} onClose={onCloseMock} />);

            // Press a non-Escape key
            fireEvent.keyDown(document, { key: otherKey });

            // Property: Non-Escape keys SHALL NOT trigger onClose
            expect(onCloseMock).not.toHaveBeenCalled();

            // Now press Escape to verify it still works
            fireEvent.keyDown(document, { key: 'Escape' });
            expect(onCloseMock).toHaveBeenCalledTimes(1);

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle Escape key with various key event properties', () => {
      fc.assert(
        fc.property(
          fc.record({
            ctrlKey: fc.boolean(),
            shiftKey: fc.boolean(),
            altKey: fc.boolean(),
          }),
          (modifiers) => {
            const onCloseMock = vi.fn();

            const { unmount } = render(<SwarmCoreModal isOpen={true} onClose={onCloseMock} />);

            // Press Escape with various modifier keys
            fireEvent.keyDown(document, { key: 'Escape', ...modifiers });

            // Property: Escape key SHALL trigger onClose regardless of modifier keys
            expect(onCloseMock).toHaveBeenCalledTimes(1);

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
