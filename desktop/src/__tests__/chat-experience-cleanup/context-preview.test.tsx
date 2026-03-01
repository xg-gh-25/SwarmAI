/**
 * Unit tests for ContextPreviewPanel Phase C changes.
 *
 * What is being tested:
 * - ``ContextPreviewPanel`` from ``components/workspace/ContextPreviewPanel`` —
 *   verifying visibility-based polling pause/resume (Req 14), debounce timer
 *   cleanup on unmount (Req 15), and no concurrent fetch requests during
 *   rapid toggling (Req 15).
 *
 * Testing methodology: Unit testing with Vitest + React Testing Library
 *
 * Key behaviors verified:
 * - Polling resumes on visibility change (Req 14.1, 14.2)
 * - Debounce timer cleanup on unmount (Req 15.2)
 * - No concurrent fetch requests during rapid toggling (Req 15.1, 15.2)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
// Unit Tests: Phase C — Visibility Polling & Debounce
// ---------------------------------------------------------------------------

describe('ContextPreviewPanel — Phase C unit tests', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockGetContextPreview.mockResolvedValue(makePreview());
    // Start with page visible
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  /**
   * Req 14.1, 14.2: Polling resumes on visibility change.
   *
   * When the page goes hidden then visible again, the panel should
   * resume fetching after the debounce + poll interval elapses.
   */
  it('resumes polling when page becomes visible again', async () => {
    const { unmount } = render(
      React.createElement(ContextPreviewPanel, {
        projectId: 'proj-1',
        threadId: 'thread-1',
      }),
    );

    // Expand the panel
    act(() => { clickToggle(); });

    // Wait for debounce to fire initial fetch
    await act(async () => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });
    const initialFetches = mockGetContextPreview.mock.calls.length;
    expect(initialFetches).toBeGreaterThanOrEqual(1);

    // Verify polling works while visible
    await act(async () => { vi.advanceTimersByTime(POLL_INTERVAL_MS); });
    const fetchesAfterOnePoll = mockGetContextPreview.mock.calls.length;
    expect(fetchesAfterOnePoll).toBeGreaterThan(initialFetches);

    // Hide the page — polling should stop
    act(() => { setPageVisibility(true); });
    const fetchesWhenHidden = mockGetContextPreview.mock.calls.length;

    await act(async () => { vi.advanceTimersByTime(POLL_INTERVAL_MS * 3); });
    expect(mockGetContextPreview.mock.calls.length).toBe(fetchesWhenHidden);

    // Make page visible again — polling should resume
    act(() => { setPageVisibility(false); });

    // Wait for debounce + one poll interval
    await act(async () => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });
    await act(async () => { vi.advanceTimersByTime(POLL_INTERVAL_MS); });

    expect(mockGetContextPreview.mock.calls.length).toBeGreaterThan(
      fetchesWhenHidden,
    );

    unmount();
  });

  /**
   * Req 15.2: Debounce timer cleanup on unmount.
   *
   * When the component unmounts during the debounce window, no
   * additional fetch should fire after unmount.
   */
  it('cleans up debounce timer on unmount before fetch fires', async () => {
    const { unmount } = render(
      React.createElement(ContextPreviewPanel, {
        projectId: 'proj-1',
        threadId: 'thread-1',
      }),
    );

    // Expand the panel
    act(() => { clickToggle(); });

    // Advance partway through debounce (not enough to trigger the
    // debounced fetch for the expand toggle)
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS / 2); });
    const callsBeforeUnmount = mockGetContextPreview.mock.calls.length;

    // Unmount before the expand-toggle debounce completes
    unmount();

    // Advance well past the debounce window — no NEW fetch should fire
    await act(async () => { vi.advanceTimersByTime(DEBOUNCE_MS * 2 + POLL_INTERVAL_MS); });
    expect(mockGetContextPreview.mock.calls.length).toBe(callsBeforeUnmount);
  });

  /**
   * Req 15.1, 15.2: No concurrent fetch requests during rapid toggling.
   *
   * Rapidly toggling expand/collapse should not produce multiple
   * concurrent fetch requests — the debounce ensures only one fires.
   */
  it('does not fire concurrent fetches during rapid toggling', async () => {
    let activeFetches = 0;
    let maxConcurrent = 0;
    mockGetContextPreview.mockImplementation(async () => {
      activeFetches++;
      maxConcurrent = Math.max(maxConcurrent, activeFetches);
      // Simulate async delay
      await new Promise((r) => setTimeout(r, 200));
      activeFetches--;
      return makePreview();
    });

    const { unmount } = render(
      React.createElement(ContextPreviewPanel, {
        projectId: 'proj-1',
        threadId: 'thread-1',
      }),
    );

    // Rapidly toggle 7 times (odd = ends expanded)
    for (let i = 0; i < 7; i++) {
      act(() => { clickToggle(); });
      act(() => { vi.advanceTimersByTime(50); }); // < DEBOUNCE_MS
    }

    // Wait for debounce + fetch to complete
    await act(async () => { vi.advanceTimersByTime(DEBOUNCE_MS + 50); });
    await act(async () => { vi.advanceTimersByTime(300); });

    // At most 1 concurrent fetch at any point
    expect(maxConcurrent).toBeLessThanOrEqual(1);

    unmount();
  });
});
