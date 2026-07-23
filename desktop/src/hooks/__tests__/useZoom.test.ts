/**
 * useZoom — verifies the zoom application side-effects.
 *
 * The load-bearing behavior under test: applyZoom must publish BOTH the raw
 * zoom (documentElement.style.zoom, drives the whole app) AND a precomputed
 * RECIPROCAL custom property (--app-zoom-inv) that the terminal counter-zoom
 * reads. The reciprocal is what decouples the terminal from app zoom so its
 * net scale is always 1.0 — fixing xterm selection drift (a scaled
 * getBoundingClientRect().left divided by an unscaled cellWidth).
 *
 * Why a precomputed reciprocal (not calc(1/var(...))): Gate-1 de-risk — a bare
 * `var(--app-zoom-inv)` sidesteps the CSS `zoom: calc(1/var())` support question
 * entirely and is trivially assertable here.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useZoom } from '../useZoom';

describe('useZoom — CSS var publication for terminal counter-zoom', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.zoom = '';
    document.documentElement.style.removeProperty('--app-zoom-inv');
  });
  afterEach(() => {
    localStorage.clear();
  });

  it('publishes the raw zoom AND the reciprocal var on mount (default 1.0)', () => {
    renderHook(() => useZoom());
    expect(document.documentElement.style.zoom).toBe('1');
    // --app-zoom-inv = 1/1 = 1 ; the terminal reads this as its counter-zoom.
    expect(
      document.documentElement.style.getPropertyValue('--app-zoom-inv').trim(),
    ).toBe('1');
  });

  it('updates the reciprocal var when zoom changes (1.25 -> inv 0.8)', () => {
    localStorage.setItem('swarmai-zoom-level', '1.25');
    renderHook(() => useZoom());
    expect(document.documentElement.style.zoom).toBe('1.25');
    // 1 / 1.25 = 0.8 exactly — the terminal counter-zoom.
    expect(
      Number(
        document.documentElement.style.getPropertyValue('--app-zoom-inv'),
      ),
    ).toBeCloseTo(0.8, 6);
  });

  it('keeps raw zoom and reciprocal consistent across a zoomIn step', () => {
    const { result } = renderHook(() => useZoom());
    act(() => result.current.zoomIn()); // 1.0 -> 1.1
    const raw = Number(document.documentElement.style.zoom);
    const inv = Number(
      document.documentElement.style.getPropertyValue('--app-zoom-inv'),
    );
    expect(raw).toBeCloseTo(1.1, 6);
    // raw * inv must equal 1 — that is the invariant that makes the terminal
    // net scale 1.0 (counter-zoom exactly cancels app zoom).
    expect(raw * inv).toBeCloseTo(1.0, 6);
  });

  it('applies BOTH raw zoom and reciprocal together (never one-frame out of phase)', () => {
    // Startup-flash guard (Gate-2 finding): the raw zoom and its reciprocal must
    // always be published together. If only the reciprocal is set while html
    // zoom stays 1.0, the terminal paints one frame at net-scale 1/level. After
    // any zoom application, raw*inv must be 1.0 — never inv-set-but-raw-default.
    const { result } = renderHook(() => useZoom());
    act(() => result.current.zoomOut()); // 1.0 -> 0.9
    const raw = Number(document.documentElement.style.zoom);
    const invStr = document.documentElement.style.getPropertyValue('--app-zoom-inv');
    // Both must be set (neither empty) AND their product is 1.0.
    expect(document.documentElement.style.zoom).not.toBe('');
    expect(invStr).not.toBe('');
    expect(raw * Number(invStr)).toBeCloseTo(1.0, 6);
  });
});
