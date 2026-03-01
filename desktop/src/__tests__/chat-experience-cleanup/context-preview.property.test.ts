/**
 * Property-based tests for ContextPreviewPanel polling and debounce behavior.
 *
 * What is being tested:
 * - ``ContextPreviewPanel`` from ``components/workspace/ContextPreviewPanel`` —
 *   verifying that polling pauses when the page is not visible (Property 5)
 *   and that rapid expand/collapse toggling is debounced (Property 6).
 *
 * Testing methodology: Property-based testing with fast-check + Vitest
 *
 * Key properties verified:
 * - Property 5: Polling Pauses When Not Visible — no fetch requests fire
 *   while ``document.hidden`` is true; polling resumes within one interval
 *   after visibility returns.
 * - Property 6: Debounce Limits Fetch Calls — at most one fetch request
 *   per 300ms window of toggle inactivity.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fc from 'fast-check';
import { render, fireEvent, act, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { ContextPreviewPanel, DEBOUNCE_MS } from '../../components/workspace/ContextPreviewPanel';
import type { ContextPreview } from '../../types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockGetContextPreview = vi.fn<
  (projectId: string, threadId?: string) => Promise<ContextPreview | null>
>();

vi.mock('../../services/context', () => ({
  getContextPreview: (...args: unknown[]) =>
    mockGetContextPreview(args[0] as string, args[1] as string | undefined),
}));

vi.mock('../../services/tauri', () => ({
  getBackendPort: () => 8000,
}));

const POLL_INTERVAL_MS = 5_000;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal valid ContextPreview for mock returns. */
function makePreview(overrides: Partial<ContextPreview> = {}): ContextPreview {
  return {
    projectId: 'proj-1',
    threadId: null,
    layers: [],
    totalTokenCount: 100,
    budgetExceeded: false,
    tokenBudget: 8000,
    truncationSummary: '',
    etag: 'etag-1',
    ...overrides,
  };
}

/** Set document.hidden and dispatch visibilitychange event. */
function setPageVisibility(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

/** Click the "Context Preview" header button to toggle expand/collapse. */
function clickToggle(): void {
  const btn = screen.getByText('Context Preview').closest('button');
  if (!btn) throw new Error('Could not find Context Preview toggle button');
  fireEvent.click(btn);
}

// ---------------------------------------------------------------------------
// Property 5: Polling Pauses When Not Visible
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 5: Polling Pauses When Not Visible', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockGetContextPreview.mockResolvedValue(makePreview());
    setPageVisibility(false);
    // Reset to visible for a clean start
    setPageVisibility(false);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    // Restore document.hidden to default
    setPageVisibility(false);
  });

  /**
   * **Validates: Requirements 14.1, 14.2**
   *
   * For any number of polling intervals that elapse while the page is hidden,
   * no fetch requests should fire beyond the initial debounced fetch.
   */
  it('does not fire fetch requests while document.hidden is true', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 10 }),
        (pollCycles) => {
          cleanup();
          mockGetContextPreview.mockClear();
          mockGetContextPreview.mockResolvedValue(makePreview());

          // Start visible so the panel can expand and fetch
          setPageVisibility(false);
          const { unmount } = render(
            React.createElement(ContextPreviewPanel, {
              projectId: 'proj-1',
              threadId: 'thread-1',
            }),
          );

          // Expand the panel while visible
          setPageVisibility(false);
          act(() => { setPageVisibility(false); });
          // Make page visible first, then expand
          act(() => { setPageVisibility(false); });

          // Reset: start fresh — visible, expand panel
          unmount();
          mockGetContextPreview.mockClear();
          setPageVisibility(false);

          // Re-render with page visible
          Object.defineProperty(document, 'hidden', {
            configurable: true,
            get: () => false,
          });
          document.dispatchEvent(new Event('visibilitychange'));

          const { unmount: unmount2 } = render(
            React.createElement(ContextPreviewPanel, {
              projectId: 'proj-1',
              threadId: 'thread-1',
            }),
          );

          // Expand the panel
          act(() => { clickToggle(); });

          // Wait for debounce
          act(() => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });
          const fetchesAfterExpand = mockGetContextPreview.mock.calls.length;

          // Now hide the page
          act(() => { setPageVisibility(true); });

          // Record call count right after hiding
          const fetchesWhenHidden = mockGetContextPreview.mock.calls.length;

          // Advance through multiple poll cycles while hidden
          for (let i = 0; i < pollCycles; i++) {
            act(() => { vi.advanceTimersByTime(POLL_INTERVAL_MS); });
          }

          // No new fetches should have fired while hidden
          const fetchesAfterHiddenPolling = mockGetContextPreview.mock.calls.length;
          expect(fetchesAfterHiddenPolling).toBe(fetchesWhenHidden);

          unmount2();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 14.1, 14.2**
   *
   * After the page becomes visible again, polling resumes within one
   * polling interval — at least one new fetch fires.
   */
  it('resumes polling within one interval after visibility returns', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (hiddenCycles) => {
          cleanup();
          mockGetContextPreview.mockClear();
          mockGetContextPreview.mockResolvedValue(makePreview());

          // Start with page visible
          Object.defineProperty(document, 'hidden', {
            configurable: true,
            get: () => false,
          });
          document.dispatchEvent(new Event('visibilitychange'));

          const { unmount } = render(
            React.createElement(ContextPreviewPanel, {
              projectId: 'proj-1',
              threadId: 'thread-1',
            }),
          );

          // Expand the panel
          act(() => { clickToggle(); });

          // Wait for debounce + initial fetch
          act(() => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });

          // Hide the page
          act(() => { setPageVisibility(true); });

          // Advance through hidden cycles (no fetches expected)
          for (let i = 0; i < hiddenCycles; i++) {
            act(() => { vi.advanceTimersByTime(POLL_INTERVAL_MS); });
          }
          const fetchesWhileHidden = mockGetContextPreview.mock.calls.length;

          // Make page visible again
          act(() => { setPageVisibility(false); });

          // Wait for debounce of the re-expand effect
          act(() => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });

          // Advance one full poll interval
          act(() => { vi.advanceTimersByTime(POLL_INTERVAL_MS); });

          // At least one new fetch should have fired after becoming visible
          expect(mockGetContextPreview.mock.calls.length).toBeGreaterThan(
            fetchesWhileHidden,
          );

          unmount();
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 6: Debounce Limits Fetch Calls
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 6: Debounce Limits Fetch Calls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockGetContextPreview.mockResolvedValue(makePreview());
    // Start with page visible
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  /**
   * **Validates: Requirements 15.1, 15.2**
   *
   * For any sequence of N rapid expand/collapse toggles within a 300ms
   * window, at most one fetch request is initiated. The fetch fires only
   * after 300ms of toggle inactivity.
   */
  it('fires at most one fetch per 300ms window of toggle inactivity', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 20 }),
        (toggleCount) => {
          cleanup();
          mockGetContextPreview.mockClear();
          mockGetContextPreview.mockResolvedValue(makePreview());

          // Ensure page is visible
          Object.defineProperty(document, 'hidden', {
            configurable: true,
            get: () => false,
          });

          const { unmount } = render(
            React.createElement(ContextPreviewPanel, {
              projectId: 'proj-1',
              threadId: 'thread-1',
            }),
          );

          // Rapidly toggle expand/collapse N times within a short window.
          // Each toggle happens with a small time gap (< DEBOUNCE_MS)
          // so the debounce timer keeps resetting.
          for (let i = 0; i < toggleCount; i++) {
            act(() => { clickToggle(); });
            // Advance less than DEBOUNCE_MS between toggles
            if (i < toggleCount - 1) {
              act(() => { vi.advanceTimersByTime(50); });
            }
          }

          // At this point, no fetch should have fired yet because
          // the debounce timer keeps resetting with each toggle.
          // The panel ends in expanded state if toggleCount is odd,
          // collapsed if even.
          const isExpanded = toggleCount % 2 === 1;

          // Now wait for the full debounce window to elapse
          act(() => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });

          // If the panel ended expanded, exactly one fetch should fire
          // after the debounce settles. If collapsed, zero fetches.
          if (isExpanded) {
            expect(mockGetContextPreview.mock.calls.length).toBe(1);
          } else {
            expect(mockGetContextPreview.mock.calls.length).toBe(0);
          }

          unmount();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 15.1, 15.2**
   *
   * Even with many toggles, no concurrent fetch requests are initiated.
   * After debounce settles on an expanded state, only one fetch fires.
   */
  it('never fires concurrent fetches during rapid toggling', () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 10, max: 100 }), { minLength: 2, maxLength: 10 }),
        (delays) => {
          cleanup();
          mockGetContextPreview.mockClear();
          // Use a slow-resolving mock to detect concurrency
          let activeFetches = 0;
          let maxConcurrent = 0;
          mockGetContextPreview.mockImplementation(async () => {
            activeFetches++;
            maxConcurrent = Math.max(maxConcurrent, activeFetches);
            await new Promise((r) => setTimeout(r, 100));
            activeFetches--;
            return makePreview();
          });

          Object.defineProperty(document, 'hidden', {
            configurable: true,
            get: () => false,
          });

          const { unmount } = render(
            React.createElement(ContextPreviewPanel, {
              projectId: 'proj-1',
              threadId: 'thread-1',
            }),
          );

          // Rapid toggles with varying sub-debounce delays
          for (const delay of delays) {
            act(() => { clickToggle(); });
            act(() => { vi.advanceTimersByTime(delay); });
          }

          // Let debounce + fetch settle
          act(() => { vi.advanceTimersByTime(DEBOUNCE_MS + 200); });
          act(() => { vi.advanceTimersByTime(200); });

          // At most 1 concurrent fetch at any point
          expect(maxConcurrent).toBeLessThanOrEqual(1);

          unmount();
        },
      ),
      { numRuns: 100 },
    );
  });
});
