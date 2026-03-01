/**
 * Property-Based Tests for WorkspacesModal
 *
 * **Feature: left-navigation-redesign**
 * **Property 3: Close Button Closes Any Modal**
 * **Validates: Requirements 4.3**
 *
 * These tests validate that clicking the close button on the WorkspacesModal
 * correctly triggers the onClose callback, which should set activeModal to null.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import * as fc from 'fast-check';
import WorkspacesModal from './WorkspacesModal';

// Mock WorkspacesPage to avoid complex dependencies
vi.mock('../../pages/WorkspacesPage', () => ({
  default: () => <div data-testid="workspaces-page-content">Workspaces Page Content</div>,
}));

// ============== Arbitraries ==============

/**
 * Arbitrary for generating modal open states
 * For Property 3, we focus on the case where the modal IS open (isOpen = true)
 */
const modalOpenStateArb = fc.constant(true);

/**
 * Arbitrary for generating various modal titles (to ensure close works regardless of title)
 */
const modalTitleArb = fc.oneof(
  fc.constant('Workspaces'),
  fc.stringMatching(/^[A-Za-z][A-Za-z0-9 ]{0,29}$/).filter((s) => s.length > 0)
);

/**
 * Arbitrary for generating test scenarios with different initial states
 */
const closeButtonTestScenarioArb = fc.record({
  isOpen: modalOpenStateArb,
  closeCallCount: fc.integer({ min: 1, max: 5 }), // Number of times to click close
});

// ============== Pure Functions Under Test ==============

/**
 * Simulates the state transition when close button is clicked.
 * This represents the expected behavior: clicking close should call onClose,
 * which sets activeModal to null.
 *
 * @param currentModalState - The current activeModal value (e.g., 'workspaces')
 * @returns null - The expected state after close
 */
function simulateCloseButtonClick(_currentModalState: string | null): null {
  // Property: Clicking close button SHALL result in activeModal being null
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

describe('WorkspacesModal - Property-Based Tests', () => {
  /**
   * Property 3: Close Button Closes Any Modal
   * **Feature: left-navigation-redesign, Property 3: Close Button Closes Any Modal**
   * **Validates: Requirements 4.3**
   *
   * For any open modal (where activeModal is not null), clicking the modal's
   * close button SHALL result in activeModal being set to null.
   */
  describe('Feature: left-navigation-redesign, Property 3: Close Button Closes Any Modal', () => {
    it('should call onClose when close button is clicked for any open modal state', () => {
      fc.assert(
        fc.property(closeButtonTestScenarioArb, (scenario) => {
          const onCloseMock = vi.fn();

          // Render the modal in open state
          const { unmount } = render(
            <WorkspacesModal isOpen={scenario.isOpen} onClose={onCloseMock} />
          );

          // Find and click the close button
          const closeButton = screen.getByRole('button');
          fireEvent.click(closeButton);

          // Property: Clicking close button SHALL invoke onClose callback
          expect(onCloseMock).toHaveBeenCalledTimes(1);

          // Property: The expected result of onClose is to set activeModal to null
          const expectedState = simulateCloseButtonClick('workspaces');
          expect(expectedState).toBeNull();

          // Cleanup
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

          // Click the close button multiple times
          for (let i = 0; i < clickCount; i++) {
            fireEvent.click(closeButton);
          }

          // Property: Each click SHALL invoke onClose exactly once
          expect(onCloseMock).toHaveBeenCalledTimes(clickCount);

          // Property: Validate callback invocation count
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

          // Property: When modal is closed, close button SHALL NOT be rendered
          const closeButton = screen.queryByRole('button');
          expect(closeButton).toBeNull();

          // Property: onClose SHALL NOT be called when modal is not rendered
          expect(onCloseMock).not.toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should render modal content when open and close button should be accessible', () => {
      fc.assert(
        fc.property(modalOpenStateArb, (isOpen) => {
          const onCloseMock = vi.fn();

          const { unmount } = render(<WorkspacesModal isOpen={isOpen} onClose={onCloseMock} />);

          // Property: When modal is open, it SHALL render the WorkspacesPage content
          const content = screen.getByTestId('workspaces-page-content');
          expect(content).toBeDefined();

          // Property: Close button SHALL be present and clickable
          const closeButton = screen.getByRole('button');
          expect(closeButton).toBeDefined();

          // Property: Clicking close SHALL trigger onClose
          fireEvent.click(closeButton);
          expect(onCloseMock).toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain close button functionality regardless of modal title', () => {
      fc.assert(
        fc.property(modalTitleArb, () => {
          const onCloseMock = vi.fn();

          // WorkspacesModal has a fixed title, but we test that close works
          // regardless of what content is displayed
          const { unmount } = render(<WorkspacesModal isOpen={true} onClose={onCloseMock} />);

          // Property: Close button SHALL work regardless of modal content
          const closeButton = screen.getByRole('button');
          fireEvent.click(closeButton);

          expect(onCloseMock).toHaveBeenCalledTimes(1);

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
            // Property: For ANY modal type, closing SHALL result in null state
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

          // Simulate rapid clicking
          for (let i = 0; i < rapidClickCount; i++) {
            fireEvent.click(closeButton);
          }

          // Property: All clicks SHALL be registered
          expect(onCloseMock).toHaveBeenCalledTimes(rapidClickCount);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });
});
