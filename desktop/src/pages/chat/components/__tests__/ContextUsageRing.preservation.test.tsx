/**
 * Preservation Property Tests for ContextUsageRing.
 *
 * Property 2: Preservation — Ring Rendering and Color Thresholds for Valid Percentages
 *
 * These tests capture baseline behavior on UNFIXED code. They MUST PASS
 * on the current (unfixed) code and continue to pass after the fix is applied,
 * ensuring no regressions in normal ring rendering.
 *
 * Methodology: observation-first — test what the code currently does for
 * valid pct values in [0, 100] and null.
 *
 * Properties verified:
 * - strokeDashoffset ∈ [0, circumference] and equals circumference - (pct/100)*circumference
 * - Color thresholds: green < 70%, amber [70, 85), red [85, 100]
 * - Tooltip text: "{pct}% context used" for non-null, "No context data yet" for null
 *
 * **Validates: Requirements 3.3, 3.4**
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import fc from 'fast-check';
import { ContextUsageRing } from '../ContextUsageRing';

// ── Constants matching the component defaults ──────────────────────────
const SIZE = 18;
const STROKE_WIDTH = 2.5;
const RADIUS = (SIZE - STROKE_WIDTH) / 2;            // 7.75
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;           // ≈ 48.6947

// Floating-point tolerance for offset comparisons
const EPSILON = 1e-6;

// ── Helpers ────────────────────────────────────────────────────────────

/** Render the ring and return both SVG circle elements plus the wrapper div. */
function renderRing(pct: number | null) {
  const { container } = render(<ContextUsageRing pct={pct} />);
  const circles = container.querySelectorAll('circle');
  const wrapper = container.querySelector('div')!;
  // First circle = background, second = foreground (fill arc)
  return { bg: circles[0], fg: circles[1], wrapper, container };
}

/** Read the foreground circle's stroke-dashoffset as a number. */
function getFgOffset(fg: Element): number {
  return Number(fg.getAttribute('stroke-dashoffset'));
}

/** Read the foreground circle's stroke color. */
function getFgStroke(fg: Element): string {
  return fg.getAttribute('stroke') ?? '';
}

/** Read the wrapper div's title attribute (tooltip text). */
function getTooltip(wrapper: Element): string {
  return wrapper.getAttribute('title') ?? '';
}

// ── Arbitraries ────────────────────────────────────────────────────────

/** Integer pct in [0, 100] — the valid range for normal ring rendering. */
const validPctArb = fc.integer({ min: 0, max: 100 });

/** Integer pct in [0, 70) — green zone. */
const greenPctArb = fc.integer({ min: 0, max: 69 });

/** Integer pct in [70, 85) — amber zone. */
const amberPctArb = fc.integer({ min: 70, max: 84 });

/** Integer pct in [85, 100] — red zone. */
const redPctArb = fc.integer({ min: 85, max: 100 });

// ── Preservation Tests ─────────────────────────────────────────────────

describe('Preservation Property Tests — ContextUsageRing', () => {

  /**
   * For all pct in [0, 100]: strokeDashoffset is in [0, circumference]
   * and equals circumference - (pct / 100) * circumference.
   *
   * **Validates: Requirements 3.3**
   */
  it('strokeDashoffset equals circumference - (pct/100)*circumference for pct in [0, 100]', () => {
    fc.assert(
      fc.property(validPctArb, (pct) => {
        const { fg, container } = renderRing(pct);
        const offset = getFgOffset(fg);
        const expected = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE;

        // offset must be in valid range
        expect(offset).toBeGreaterThanOrEqual(-EPSILON);
        expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE + EPSILON);

        // offset must match the formula
        expect(Math.abs(offset - expected)).toBeLessThan(EPSILON);

        container.remove();
      }),
      { numRuns: 100 },
    );
  });

  /**
   * For all pct in [0, 70): strokeColor is #10b981 (green).
   *
   * **Validates: Requirements 3.3**
   */
  it('strokeColor is green (#10b981) for pct in [0, 70)', () => {
    fc.assert(
      fc.property(greenPctArb, (pct) => {
        const { fg, container } = renderRing(pct);
        const stroke = getFgStroke(fg);

        expect(stroke).toBe('#10b981');

        container.remove();
      }),
      { numRuns: 70 },
    );
  });

  /**
   * For all pct in [70, 85): strokeColor is #f59e0b (amber).
   *
   * **Validates: Requirements 3.3**
   */
  it('strokeColor is amber (#f59e0b) for pct in [70, 85)', () => {
    fc.assert(
      fc.property(amberPctArb, (pct) => {
        const { fg, container } = renderRing(pct);
        const stroke = getFgStroke(fg);

        expect(stroke).toBe('#f59e0b');

        container.remove();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * For all pct in [85, 100]: strokeColor is #ef4444 (red).
   *
   * **Validates: Requirements 3.3**
   */
  it('strokeColor is red (#ef4444) for pct in [85, 100]', () => {
    fc.assert(
      fc.property(redPctArb, (pct) => {
        const { fg, container } = renderRing(pct);
        const stroke = getFgStroke(fg);

        expect(stroke).toBe('#ef4444');

        container.remove();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * For all non-null pct in [0, 100]: tooltip text is "{pct}% context used".
   *
   * **Validates: Requirements 3.4**
   */
  it('tooltip shows "{pct}% context used" for non-null pct', () => {
    fc.assert(
      fc.property(validPctArb, (pct) => {
        const { wrapper, container } = renderRing(pct);
        const tooltip = getTooltip(wrapper);

        expect(tooltip).toBe(`${pct}% context used`);

        container.remove();
      }),
      { numRuns: 100 },
    );
  });

  /**
   * For null pct: tooltip text is "No context data yet".
   *
   * **Validates: Requirements 3.4**
   */
  it('tooltip shows "No context data yet" for null pct', () => {
    const { wrapper, container } = renderRing(null);
    const tooltip = getTooltip(wrapper);

    expect(tooltip).toBe('No context data yet');

    container.remove();
  });
});
