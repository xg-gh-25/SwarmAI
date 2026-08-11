/**
 * Regression tests for TSCC popover sizing at the edges of the viewport.
 *
 * The panel is sized to a fraction of the "safe region" — the space left after
 * the left-nav and the tab bar — and hard-capped at that region so it can never
 * overlap the chrome. Two failure modes have to stay distinguished:
 *
 * - A DEGENERATE viewport (innerWidth ~0 during Tauri boot frames) reports
 *   nothing real, so the last good box must be kept rather than thrashing the
 *   panel closed.
 * - A REAL viewport that is too small must CLEAR the box. An early return there
 *   retains the previous, larger box, which then overlaps the left-nav and tab
 *   bar — contradicting the no-overlap guarantee (review run_abab234c, LOW #12).
 *
 * Testing methodology: React Testing Library, driving window.innerWidth/Height
 * and dispatching resize, with the tscc service layer mocked so the panel body
 * issues no requests.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { TSCCPopoverButton } from '../TSCCPopoverButton';
import type { SystemPromptMetadata } from '../../../../types';

vi.mock('../../../../services/tscc', () => ({
  getSystemPromptMetadata: vi.fn().mockResolvedValue(null),
  getRecallSnapshot: vi.fn().mockResolvedValue(null),
  getSecurityScan: vi.fn().mockResolvedValue(null),
}));

const metadata: SystemPromptMetadata = {
  files: [{ filename: 'SWARMAI.md', tokens: 100, truncated: false }],
  totalTokens: 100,
  fullText: '# prompt',
};

const originalW = window.innerWidth;
const originalH = window.innerHeight;

function setViewport(w: number, h: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true });
  Object.defineProperty(window, 'innerHeight', { value: h, configurable: true, writable: true });
}

/** jsdom returns an all-zero rect from getBoundingClientRect, which would put
 *  the anchor at the far window edge and make the safe region negative for any
 *  viewport. Report a plausible button position instead: bottom-right of the
 *  current viewport, where the composer's TSCC button actually sits. */
const originalRect = Element.prototype.getBoundingClientRect;
function stubButtonRect() {
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    const right = window.innerWidth - 20;
    const top = window.innerHeight - 60;
    return {
      x: right - 32, y: top, width: 32, height: 32,
      top, right, bottom: top + 32, left: right - 32,
      toJSON: () => ({}),
    } as DOMRect;
  };
}

const panel = () => screen.queryByRole('dialog', { name: 'TSCC context panel' });

beforeEach(() => {
  setViewport(1600, 1000);
  stubButtonRect();
});
afterEach(() => {
  setViewport(originalW, originalH);
  Element.prototype.getBoundingClientRect = originalRect;
});

describe('TSCC popover sizing', () => {
  it('opens sized within the safe region', () => {
    render(<TSCCPopoverButton sessionId="s1" metadata={metadata} />);
    fireEvent.click(screen.getByLabelText('TSCC context'));

    const el = panel();
    expect(el).toBeInTheDocument();
    // Must fit the region left of the chrome, never spill over it.
    const width = parseInt(el!.style.width, 10);
    const height = parseInt(el!.style.height, 10);
    expect(width).toBeGreaterThan(0);
    expect(width).toBeLessThanOrEqual(1600);
    expect(height).toBeLessThanOrEqual(1000);
  });

  it('closes instead of keeping an oversized box when the window gets too small', () => {
    render(<TSCCPopoverButton sessionId="s1" metadata={metadata} />);
    fireEvent.click(screen.getByLabelText('TSCC context'));
    const before = parseInt(panel()!.style.width, 10);
    expect(before).toBeGreaterThan(120);

    // A real, tiny window: the safe region can no longer hold a usable panel.
    act(() => {
      setViewport(200, 180);
      window.dispatchEvent(new Event('resize'));
    });

    expect(panel()).not.toBeInTheDocument();
  });

  it('keeps the last good box through a degenerate boot-frame viewport', () => {
    render(<TSCCPopoverButton sessionId="s1" metadata={metadata} />);
    fireEvent.click(screen.getByLabelText('TSCC context'));
    const before = panel()!.style.width;

    // Tauri reports 0x0 for a frame or two during startup — not a real resize.
    act(() => {
      setViewport(0, 0);
      window.dispatchEvent(new Event('resize'));
    });

    expect(panel()).toBeInTheDocument();
    expect(panel()!.style.width).toBe(before);
  });

  it('reopens at the right size after the window grows back', () => {
    render(<TSCCPopoverButton sessionId="s1" metadata={metadata} />);
    fireEvent.click(screen.getByLabelText('TSCC context'));

    act(() => {
      setViewport(200, 180);
      window.dispatchEvent(new Event('resize'));
    });
    expect(panel()).not.toBeInTheDocument();

    // Still flagged open, so a resize back must re-derive a box and show it —
    // clearing the box must not strand the panel permanently closed.
    act(() => {
      setViewport(1600, 1000);
      window.dispatchEvent(new Event('resize'));
    });
    expect(panel()).toBeInTheDocument();
  });
});
