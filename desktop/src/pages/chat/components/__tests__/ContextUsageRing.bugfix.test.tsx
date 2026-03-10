/**
 * Bug Condition Exploration Tests for ContextUsageRing.
 *
 * Property 1: Bug Condition — Context Ring SVG Clamping and Null State Bugs
 *
 * These tests encode the EXPECTED (correct) behavior. On unfixed code they
 * MUST FAIL, proving the bugs exist. After the fix is applied they should
 * pass, confirming the bugs are resolved.
 *
 * Bug 2 (SVG overflow): pct values outside [0, 100] (and non-finite values)
 *   must be clamped so fillPct ∈ [0, 100] and strokeDashoffset ∈ [0, circumference].
 *
 * Bug 3 (null indistinguishable): pct === null must render a visually distinct
 *   ring (dashed stroke / reduced opacity) compared to pct === 0.
 *
 * Validates: Requirements 1.2, 1.3, 2.2, 2.3
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import fc from 'fast-check';
import { ContextUsageRing } from '../ContextUsageRing';

// ── Constants matching the component defaults ──────────────────────────
const SIZE = 18;
const STROKE_WIDTH = 2.5;
const RADIUS = (SIZE - STROKE_WIDTH) / 2;           // 7.75
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;          // ≈ 48.6947

// ── Helpers ────────────────────────────────────────────────────────────

/** Render the ring and return both SVG circle elements. */
function renderRing(pct: number | null) {
  const { container } = render(<ContextUsageRing pct={pct} />);
  const circles = container.querySelectorAll('circle');
  // First circle = background, second = foreground (fill arc)
  return { bg: circles[0], fg: circles[1], container };
}

/** Read the foreground circle's stroke-dashoffset (kebab-case in jsdom). */
function getFgOffset(fg: Element): number {
  return Number(fg.getAttribute('stroke-dashoffset'));
}

// ── Bug 2: SVG Overflow — fillPct must be clamped to [0, 100] ─────────

describe('Bug 2: SVG ring clamping for out-of-range pct values', () => {
  /**
   * Property test: For any pct value outside [0, 100] (or non-finite),
   * the foreground circle's strokeDashoffset must still be in
   * [0, circumference].
   *
   * On unfixed code fillPct passes through unclamped, producing negative
   * offsets (pct > 100) or offsets > circumference (pct < 0).
   *
   * **Validates: Requirements 2.2**
   */
  it('should clamp fillPct so strokeDashoffset ∈ [0, circumference] for pct > 100', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 101, max: 500 }),
        (pct) => {
          const { fg, container } = renderRing(pct);
          const offset = getFgOffset(fg);

          // offset must be finite and within [0, circumference]
          expect(offset).toBeGreaterThanOrEqual(0);
          expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE);

          // cleanup
          container.remove();
        },
      ),
      { numRuns: 50 },
    );
  });

  it('should clamp fillPct so strokeDashoffset ∈ [0, circumference] for pct < 0', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -500, max: -1 }),
        (pct) => {
          const { fg, container } = renderRing(pct);
          const offset = getFgOffset(fg);

          expect(offset).toBeGreaterThanOrEqual(0);
          expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE);

          container.remove();
        },
      ),
      { numRuns: 50 },
    );
  });

  it('should handle NaN pct by clamping to valid range', () => {
    const { fg } = renderRing(NaN as unknown as number);
    const offset = getFgOffset(fg);

    expect(Number.isFinite(offset)).toBe(true);
    expect(offset).toBeGreaterThanOrEqual(0);
    expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE);
  });

  it('should handle Infinity pct by clamping to valid range', () => {
    const { fg } = renderRing(Infinity as unknown as number);
    const offset = getFgOffset(fg);

    expect(Number.isFinite(offset)).toBe(true);
    expect(offset).toBeGreaterThanOrEqual(0);
    expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE);
  });

  it('should handle -Infinity pct by clamping to valid range', () => {
    const { fg } = renderRing(-Infinity as unknown as number);
    const offset = getFgOffset(fg);

    expect(Number.isFinite(offset)).toBe(true);
    expect(offset).toBeGreaterThanOrEqual(0);
    expect(offset).toBeLessThanOrEqual(CIRCUMFERENCE);
  });
});

// ── Bug 3: Null state visually indistinguishable from zero ────────────

describe('Bug 3: Null pct must be visually distinct from zero pct', () => {
  /**
   * When pct === null the background circle should have a dashed stroke
   * pattern and/or reduced opacity so users can distinguish "no data"
   * from "0% usage". On unfixed code both render identically.
   *
   * **Validates: Requirements 2.3**
   */
  it('should render different background circle attributes for null vs 0', () => {
    const nullRing = renderRing(null);
    const zeroRing = renderRing(0);

    const nullBgDash = nullRing.bg.getAttribute('stroke-dasharray');
    const zeroBgDash = zeroRing.bg.getAttribute('stroke-dasharray');

    const nullBgOpacity = nullRing.bg.getAttribute('opacity');
    const zeroBgOpacity = zeroRing.bg.getAttribute('opacity');

    // At least one visual differentiator must be present for null state
    const nullHasDash = nullBgDash !== null && nullBgDash !== '';
    const nullHasReducedOpacity =
      nullBgOpacity !== null && Number(nullBgOpacity) < 1;

    // null state must have SOME visual distinction
    expect(nullHasDash || nullHasReducedOpacity).toBe(true);

    // And that distinction must NOT be present on the zero state
    const zeroHasDash = zeroBgDash !== null && zeroBgDash !== '';
    const zeroHasReducedOpacity =
      zeroBgOpacity !== null && Number(zeroBgOpacity) < 1;

    expect(nullHasDash !== zeroHasDash || nullHasReducedOpacity !== zeroHasReducedOpacity).toBe(true);

    nullRing.container.remove();
    zeroRing.container.remove();
  });

  /**
   * Property test: For any numeric pct value, the background circle
   * should NOT have the null-state visual markers (dashed stroke or
   * reduced opacity). Only null gets those markers.
   *
   * **Validates: Requirements 2.3**
   */
  it('should NOT apply null-state visual markers when pct is a number', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 100 }),
        (pct) => {
          const { bg, container } = renderRing(pct);

          const dash = bg.getAttribute('stroke-dasharray');
          const opacity = bg.getAttribute('opacity');

          // Numeric pct should NOT have dashed background
          const hasDash = dash !== null && dash !== '';
          expect(hasDash).toBe(false);

          // Numeric pct should NOT have reduced opacity on background
          if (opacity !== null) {
            expect(Number(opacity)).toBe(1);
          }

          container.remove();
        },
      ),
      { numRuns: 50 },
    );
  });
});
