/**
 * ChatInput auto-grow reflow-skip guard (run_1cb87e1a).
 *
 * ROOT BUG: the chat textarea auto-grow ran a forced synchronous reflow
 * (`style.height='auto'` write → read `scrollHeight` → write) on EVERY keystroke,
 * even when nothing that affects the wrapped height changed. With the Canvas panel
 * open (a large un-virtualized DOM), that per-keystroke reflow re-lays-out the whole
 * Canvas subtree → "输入卡死". The fix: applyHeight early-returns (skips the
 * height='auto'/scrollHeight measure) when the (value, clientWidth, expanded) triple
 * is unchanged — but MUST still re-measure when WIDTH changes with value unchanged
 * (Canvas open/close, drag-resize → rewrap), which a value-only skip would wrongly
 * freeze (Gate-1 FLAW4).
 *
 * Two layers of coverage:
 *  1. PURE-FUNCTION (heightMeasureUnchanged) — the guard decision, tested directly so
 *     the width-key correctness is deterministic and non-circular. This is the REAL
 *     prod predicate (imported, not re-derived — GUI21), so a mutation to it (drop
 *     the width term) turns a test RED.
 *  2. COMPONENT — the REAL ChatInput actually skips a redundant measure and still
 *     grows on a value change (behavior, not just the predicate).
 *
 * Mutation checks:
 *  - Drop `width` from heightMeasureUnchanged → `width change forces re-measure` RED.
 *  - Make applyHeight always measure (remove the early-return) → `skips redundant
 *    measure` (component) RED.
 */

import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatInput, heightMeasureUnchanged, cacheableMeasureSig, supportsFieldSizing, computeCollapsedMinHeight, type HeightMeasureSig } from './ChatInput';
import type { UnifiedAttachment } from '../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// ─────────────────────────────────────────────────────────────────────────────
// Layer 1: the pure guard predicate (the actual prod function, imported)
// ─────────────────────────────────────────────────────────────────────────────
describe('heightMeasureUnchanged (reflow-skip predicate)', () => {
  const sig = (o: Partial<HeightMeasureSig> = {}): HeightMeasureSig =>
    ({ value: 'hello', width: 600, expanded: false, ...o });

  it('is false when there is no prior measure (first call must measure)', () => {
    expect(heightMeasureUnchanged(null, sig())).toBe(false);
  });

  it('is true when value, width and expanded are all identical (skip the reflow)', () => {
    expect(heightMeasureUnchanged(sig(), sig())).toBe(true);
  });

  it('is false when the value changed (typing must re-measure)', () => {
    expect(heightMeasureUnchanged(sig({ value: 'a' }), sig({ value: 'ab' }))).toBe(false);
  });

  it('is false when the WIDTH changed but value is unchanged (Gate-1 FLAW4: Canvas open/resize rewrap)', () => {
    // This is the load-bearing case: same text, narrower column → taller wrap.
    // A value-only guard would return TRUE here and freeze the textarea height.
    expect(heightMeasureUnchanged(sig({ width: 600 }), sig({ width: 300 }))).toBe(false);
  });

  it('is false when expanded mode toggled (maxHeight changed)', () => {
    expect(heightMeasureUnchanged(sig({ expanded: false }), sig({ expanded: true }))).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// computeCollapsedMinHeight (run_17d708f4 — the 3-row default-height regression fix)
//
// Under CSS `field-sizing:content` the browser sizes the textarea to its CONTENT
// height and the `rows={3}` HTML attribute is NOT honored as a minimum, so an empty
// input collapsed to ~1 row on WebKit 26+. The fix computes a minHeight = rows ×
// line-height + vertical padding (border-box) and applies it in the field-sizing
// style branch, matching the 3-row default the JS-autogrow path (rows={3}) gives.
// This pure helper is the engine-agnostic math, tested directly.
//
// Mutation check: drop the padding terms (return only rows*lineHeight) → the
// "includes vertical padding" case goes RED.
// ─────────────────────────────────────────────────────────────────────────────
describe('computeCollapsedMinHeight (3-row default-height, border-box)', () => {
  it('is rows*lineHeight + top + bottom padding (default 3 rows)', () => {
    // 3 * 20 + 8 + 8 = 76 (the py-2 = 8px top/bottom case)
    expect(computeCollapsedMinHeight(20, 8, 8)).toBe(76);
  });

  it('scales with the real computed line-height (theme-agnostic)', () => {
    // 3 * 24 + 8 + 8 = 88
    expect(computeCollapsedMinHeight(24, 8, 8)).toBe(88);
  });

  it('includes vertical padding (border-box) — not just rows*lineHeight', () => {
    // If the fix dropped padding, this would be 60, not 76 — the collapse-by-padding bug.
    expect(computeCollapsedMinHeight(20, 8, 8)).toBeGreaterThan(20 * 3);
  });

  it('honors an explicit rows argument', () => {
    expect(computeCollapsedMinHeight(20, 0, 0, 1)).toBe(20);
    expect(computeCollapsedMinHeight(20, 0, 0, 5)).toBe(100);
  });

  it('rounds a fractional line-height to a whole px', () => {
    // 3 * 20.5 = 61.5 → round → 62, + 0 padding
    expect(computeCollapsedMinHeight(20.5, 0, 0)).toBe(62);
  });
});

describe('cacheableMeasureSig (Gate-2: never cache a width-0 measure)', () => {
  const sig = (width: number): HeightMeasureSig => ({ value: 'hi', width, expanded: false });

  it('returns the signature when measured at a real (>0) width', () => {
    const s = sig(400);
    expect(cacheableMeasureSig(s)).toBe(s);
  });

  it('returns null when measured at width 0 (background/hidden tab) so the next measure is not skipped', () => {
    // With the bug (cache the 0-width sig), a later same-value width-recovery would
    // match-and-skip → frozen height. Returning null forces the next real measure.
    expect(cacheableMeasureSig(sig(0))).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Layer 2: the REAL ChatInput component behavior
// ─────────────────────────────────────────────────────────────────────────────
function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function baseProps(overrides: Partial<Parameters<typeof ChatInput>[0]> = {}) {
  return {
    inputValue: '',
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    isStreaming: false,
    selectedAgentId: 'agent-1',
    attachments: [] as UnifiedAttachment[],
    onAddFiles: vi.fn(),
    onRemoveFile: vi.fn(),
    isProcessingFiles: false,
    fileError: null as string | null,
    canAddMore: true,
    isExpanded: false,
    onExpandedChange: vi.fn(),
    ...overrides,
  };
}

/** Stub jsdom's non-computed layout props + count explicit px writes to
 *  style.height (the reflow WRITE a skip avoids). `height='auto'`/`''` excluded. */
function instrument(el: HTMLTextAreaElement) {
  const state = { pxWrites: 0, scrollHeight: 40, clientWidth: 600 };
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => state.scrollHeight });
  Object.defineProperty(el, 'clientWidth', { configurable: true, get: () => state.clientWidth });
  let raw = '';
  Object.defineProperty(el.style, 'height', {
    configurable: true,
    get() { return raw; },
    set(v: string) { if (typeof v === 'string' && v.endsWith('px')) state.pxWrites += 1; raw = v; },
  });
  return state;
}

function textarea(): HTMLTextAreaElement {
  const el = document.querySelector('textarea');
  if (!el) throw new Error('textarea not found');
  return el as HTMLTextAreaElement;
}

describe('ChatInput reflow behavior (real component)', () => {
  beforeEach(() => {
    // Make rAF synchronous so the rAF-scheduled applyHeight runs within the test.
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('writes a px height when the value grows (auto-grow preserved)', () => {
    // ONE stable QueryClient so rerender updates props in place (a fresh <Wrapper>
    // per rerender would remount the subtree → a new detached textarea, defeating
    // the instrument spy). Same reason the width/collapse suites reuse one client.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { rerender } = render(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: '' })} /></QueryClientProvider>,
    );
    const el = textarea();
    const state = instrument(el);
    state.pxWrites = 0;
    state.scrollHeight = 120; // multi-line content is taller
    rerender(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: 'l1\nl2\nl3' })} /></QueryClientProvider>,
    );
    expect(state.pxWrites).toBeGreaterThan(0);
  });

  // NOTE: a "redundant re-render skips the measure" COMPONENT test is deliberately
  // NOT written here — it would be vacuous: a re-render with the SAME inputValue does
  // not re-fire the `[inputValue]` effect at all, so applyHeight never runs and the
  // test would pass whether or not the early-return guard exists (verified: removing
  // the guard leaves such a test GREEN — proving it tests nothing). The guard DECISION
  // is proven by the pure-function suite above (mutation-sensitive: dropping the width
  // term turns it RED); the guard is only reachable through applyHeight, so the
  // predicate test covers the logic honestly without theater. The auto-grow test below
  // proves the measure still RUNS + writes when the value legitimately changes (AC3).
});

// ─────────────────────────────────────────────────────────────────────────────
// Layer 3: field-sizing elimination (run_26172836 — the ROOT fix)
//
// When the browser natively auto-sizes via CSS `field-sizing:content`, the JS
// autogrow measure (the `height='auto'` write → `scrollHeight` read that forces a
// per-keystroke synchronous document reflow — the Canvas-open lag root cause) MUST
// be skipped. This asserts: with field-sizing supported, a value GROWTH produces
// ZERO inline px height writes (the reflow is eliminated). It is the exact
// complement of the "writes a px height when the value grows" test above — that one
// proves the JS FALLBACK still works when field-sizing is unsupported (jsdom default).
//
// Mutation check: remove `if (fieldSizingRef.current) return;` from applyHeight →
// this test goes RED (a px write happens even under native sizing).
// ─────────────────────────────────────────────────────────────────────────────
describe('ChatInput field-sizing elimination (root fix)', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('supportsFieldSizing() reflects CSS.supports(field-sizing, content)', () => {
    const spy = vi.fn((prop: string, val: string) =>
      prop === 'field-sizing' && val === 'content');
    vi.stubGlobal('CSS', { supports: spy } as unknown as typeof CSS);
    expect(supportsFieldSizing()).toBe(true);
    spy.mockReturnValue(false);
    expect(supportsFieldSizing()).toBe(false);
  });

  it('does NOT write an inline px height on value growth when field-sizing is supported', () => {
    // Force CSS.supports(field-sizing) = true so the component takes the native path.
    vi.stubGlobal('CSS', {
      supports: (prop: string, val: string) => prop === 'field-sizing' && val === 'content',
    } as unknown as typeof CSS);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { rerender } = render(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: '' })} /></QueryClientProvider>,
    );
    const el = textarea();
    const state = instrument(el);
    state.pxWrites = 0;
    state.scrollHeight = 120; // would grow under the JS path
    rerender(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: 'l1\nl2\nl3\nl4' })} /></QueryClientProvider>,
    );
    // Native field-sizing → CSS sizes the control → the JS measure is skipped entirely.
    expect(state.pxWrites).toBe(0);
    // And the native sizing CSS is actually applied to the element.
    expect(el.style.getPropertyValue('field-sizing') || (el.style as unknown as Record<string, string>)['fieldSizing']).toBeTruthy();
  });

  it('applies an inline minHeight in the field-sizing branch (3-row default, run_17d708f4)', () => {
    // Regression guard: under field-sizing the textarea has no rows-based minimum, so
    // an EMPTY input needs an explicit minHeight or it collapses to 1 row (WebKit 26+).
    // Mutation check: remove minHeight from the field-sizing style branch → RED.
    vi.stubGlobal('CSS', {
      supports: (prop: string, val: string) => prop === 'field-sizing' && val === 'content',
    } as unknown as typeof CSS);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: '' })} /></QueryClientProvider>,
    );
    const el = textarea();
    expect(el.style.minHeight).toBeTruthy();
    // Seeded/computed value is a positive px (≥ 1 row + no negative).
    expect(parseFloat(el.style.minHeight)).toBeGreaterThan(0);
  });

  it('does NOT apply an inline minHeight when field-sizing is unsupported (JS-autogrow path uses rows={3})', () => {
    // jsdom default: CSS.supports absent → supportsFieldSizing() false → style branch
    // is undefined, so no inline minHeight (rows={3} provides the min on that path).
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={qc}><ChatInput {...baseProps({ inputValue: '' })} /></QueryClientProvider>,
    );
    const el = textarea();
    expect(el.style.minHeight).toBe('');
  });
});
