/**
 * Property-Based Tests for ChatHeader Visual Indicator Consistency
 *
 * **Feature: right-sidebar-mutual-exclusion**
 * **Property 3: Visual Indicator State Consistency**
 * **Validates: Requirements 2.3, 2.4, 5.1, 5.2, 5.3, 5.4**
 *
 * These property tests verify that the visual indicator state (highlighted vs muted)
 * for sidebar toggle buttons is consistent with the active sidebar state.
 * Specifically: isHighlighted(button) === (activeSidebar === button.sidebarId)
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import fc from 'fast-check';
import { ChatHeader } from './ChatHeader';
import { RIGHT_SIDEBAR_IDS, type RightSidebarId } from '../constants';

// ============== Test Setup ==============

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string) => fallback,
  }),
}));

// Mock useHealth to avoid needing HealthProvider in tests
vi.mock('../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

// Default props for ChatHeader
const createDefaultProps = (activeSidebar: RightSidebarId) => ({
  openTabs: [],
  activeTabId: null,
  onTabSelect: vi.fn(),
  onTabClose: vi.fn(),
  onNewSession: vi.fn(),
  activeSidebar,
  onOpenSidebar: vi.fn(),
});

// Sidebar button configuration for testing
const SIDEBAR_BUTTON_CONFIG: Record<RightSidebarId, { label: string }> = {
  todoRadar: { label: 'ToDo Radar' },
  chatHistory: { label: 'Chat History' },
  fileBrowser: { label: 'File Browser' },
};

// ============== Arbitraries ==============

// Arbitrary for sidebar IDs
const sidebarIdArb = fc.constantFrom<RightSidebarId>('todoRadar', 'chatHistory', 'fileBrowser');

// ============== Helper Functions ==============

/**
 * Check if a button element has the highlighted styling class.
 * Highlighted buttons have 'text-primary' and 'bg-primary/10' classes.
 */
function isButtonHighlighted(button: HTMLElement): boolean {
  const classList = button.className;
  return classList.includes('text-primary') && classList.includes('bg-primary/10');
}

/**
 * Check if a button element has the muted (non-highlighted) styling.
 * Muted buttons have 'text-[var(--color-text-muted)]' class.
 */
function isButtonMuted(button: HTMLElement): boolean {
  const classList = button.className;
  return classList.includes('text-[var(--color-text-muted)]');
}

// ============== Property Tests ==============

describe('Property 3: Visual Indicator State Consistency', () => {
  /**
   * Property Test: Only the active sidebar button is highlighted
   *
   * For any active sidebar state, the toggle button's visual state
   * (highlighted vs muted) shall match whether that sidebar is the
   * active sidebar. Specifically: isHighlighted(button) === (activeSidebar === button.sidebarId)
   *
   * **Validates: Requirements 2.3, 2.4, 5.1, 5.2, 5.3, 5.4**
   */
  it('should highlight only the active sidebar button', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const props = createDefaultProps(activeSidebar);
        const { unmount } = render(<ChatHeader {...props} />);

        // Check each sidebar button
        for (const id of RIGHT_SIDEBAR_IDS) {
          const buttonLabel = SIDEBAR_BUTTON_CONFIG[id].label;
          const button = screen.getByLabelText(buttonLabel);

          const shouldBeHighlighted = id === activeSidebar;
          const actuallyHighlighted = isButtonHighlighted(button);

          // Verify: isHighlighted(button) === (activeSidebar === button.sidebarId)
          expect(actuallyHighlighted).toBe(shouldBeHighlighted);
        }

        // Clean up for next iteration
        unmount();
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Non-active sidebar buttons are muted
   *
   * For any active sidebar state, all non-active sidebar buttons
   * should have the muted styling class.
   *
   * **Validates: Requirements 2.4, 5.4**
   */
  it('should show muted styling for non-active sidebar buttons', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const props = createDefaultProps(activeSidebar);
        const { unmount } = render(<ChatHeader {...props} />);

        // Check each sidebar button
        for (const id of RIGHT_SIDEBAR_IDS) {
          const buttonLabel = SIDEBAR_BUTTON_CONFIG[id].label;
          const button = screen.getByLabelText(buttonLabel);

          const shouldBeMuted = id !== activeSidebar;
          const actuallyMuted = isButtonMuted(button);

          // Non-active buttons should be muted
          if (shouldBeMuted) {
            expect(actuallyMuted).toBe(true);
          }
        }

        // Clean up for next iteration
        unmount();
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Exactly one button is highlighted at any time
   *
   * For any active sidebar state, exactly one sidebar toggle button
   * should be highlighted (not zero, not more than one).
   *
   * **Validates: Requirements 5.1, 5.2, 5.3**
   */
  it('should have exactly one highlighted button at any time', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const props = createDefaultProps(activeSidebar);
        const { unmount } = render(<ChatHeader {...props} />);

        // Count highlighted buttons
        let highlightedCount = 0;
        for (const id of RIGHT_SIDEBAR_IDS) {
          const buttonLabel = SIDEBAR_BUTTON_CONFIG[id].label;
          const button = screen.getByLabelText(buttonLabel);

          if (isButtonHighlighted(button)) {
            highlightedCount++;
          }
        }

        // Exactly one button should be highlighted
        expect(highlightedCount).toBe(1);

        // Clean up for next iteration
        unmount();
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: aria-pressed attribute matches highlight state
   *
   * For any active sidebar state, the aria-pressed attribute should
   * match the visual highlight state for accessibility consistency.
   *
   * **Validates: Requirements 2.3, 2.4**
   */
  it('should have aria-pressed match the highlight state', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const props = createDefaultProps(activeSidebar);
        const { unmount } = render(<ChatHeader {...props} />);

        // Check each sidebar button
        for (const id of RIGHT_SIDEBAR_IDS) {
          const buttonLabel = SIDEBAR_BUTTON_CONFIG[id].label;
          const button = screen.getByLabelText(buttonLabel);

          const isHighlighted = isButtonHighlighted(button);
          const ariaPressed = button.getAttribute('aria-pressed') === 'true';

          // aria-pressed should match highlight state
          expect(ariaPressed).toBe(isHighlighted);
        }

        // Clean up for next iteration
        unmount();
      }),
      { numRuns: 100 }
    );
  });

  /**
   * Property Test: Highlighted button matches activeSidebar prop
   *
   * For any active sidebar state, the highlighted button should
   * correspond to the activeSidebar prop value.
   *
   * **Validates: Requirements 5.1, 5.2, 5.3**
   */
  it('should highlight the button matching activeSidebar prop', () => {
    fc.assert(
      fc.property(sidebarIdArb, (activeSidebar) => {
        const props = createDefaultProps(activeSidebar);
        const { unmount } = render(<ChatHeader {...props} />);

        // Find the button for the active sidebar
        const activeButtonLabel = SIDEBAR_BUTTON_CONFIG[activeSidebar].label;
        const activeButton = screen.getByLabelText(activeButtonLabel);

        // The active sidebar's button should be highlighted
        expect(isButtonHighlighted(activeButton)).toBe(true);

        // Clean up for next iteration
        unmount();
      }),
      { numRuns: 100 }
    );
  });
});
