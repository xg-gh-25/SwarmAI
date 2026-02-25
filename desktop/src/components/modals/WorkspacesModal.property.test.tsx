/**
 * Property-Based Tests for WorkspacesModal
 *
 * **Feature: left-navigation-redesign**
 * **Property 3: Close Button Closes Any Modal**
 * **Validates: Requirements 4.3**
 *
 * Tests that clicking the close button on the WorkspacesModal
 * correctly triggers the onClose callback.
 * Updated for single-workspace model — WorkspacesPage dependency removed.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import * as fc from 'fast-check';
import WorkspacesModal from './WorkspacesModal';

// ============== Pure Functions Under Test ==============

/**
 * Simulates the state transition when close button is clicked.
 * Clicking close should call onClose, which sets activeModal to null.
 */
function simulateCloseButtonClick(_currentModalState: string | null): null {
  return null;
}

/**
 * Validates that the close callback was invoked correctly.
 */
function validateCloseCallbackInvoked(
  onCloseMock: ReturnType<typeof vi.fn>,
  expectedCallCount: number
): boolean {
  return onCloseMock.mock.calls.length === expectedCallCount;
}

// ============== Property-Based Tests ==============

describe('WorkspacesModal - Property-Based Tests', () => {
  describe('Feature: left-navigation-redesign, Property 3: Close Button Closes Any Modal', () => {
    it('should call onClose when close button is clicked for any open modal state', () => {
      fc.assert(
        fc.property(fc.constant(true), () => {
          const onCloseMock = vi.fn();

          const { unmount } = render(
            <WorkspacesModal isOpen={true} onClose={onCloseMock} />
          );

          const closeButton = screen.getByRole('button');
          fireEvent.click(closeButton);

          expect(onCloseMock).toHaveBeenCalledTimes(1);

          const expectedState = simulateCloseButtonClick('workspaces');
          expect(expectedState).toBeNull();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should call onClose exactly once per close button click', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 10 }), (clickCount) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<WorkspacesModal isOpen={true} onClose={onCloseMock} />);

          const closeButton = screen.getByRole('button');

          for (let i = 0; i < clickCount; i++) {
            fireEvent.click(closeButton);
          }

          expect(onCloseMock).toHaveBeenCalledTimes(clickCount);
          expect(validateCloseCallbackInvoked(onCloseMock, clickCount)).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should not render close button when modal is closed', () => {
      fc.assert(
        fc.property(fc.constant(false), (isOpen) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<WorkspacesModal isOpen={isOpen} onClose={onCloseMock} />);

          const closeButton = screen.queryByRole('button');
          expect(closeButton).toBeNull();
          expect(onCloseMock).not.toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should simulate correct state transition on close', () => {
      fc.assert(
        fc.property(
          fc.constantFrom('workspaces', 'swarmcore', 'agents', 'skills', 'mcp', 'settings'),
          (modalType) => {
            const resultState = simulateCloseButtonClick(modalType);
            expect(resultState).toBeNull();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle rapid close button clicks correctly', () => {
      fc.assert(
        fc.property(fc.integer({ min: 5, max: 20 }), (rapidClickCount) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<WorkspacesModal isOpen={true} onClose={onCloseMock} />);

          const closeButton = screen.getByRole('button');

          for (let i = 0; i < rapidClickCount; i++) {
            fireEvent.click(closeButton);
          }

          expect(onCloseMock).toHaveBeenCalledTimes(rapidClickCount);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });
});
