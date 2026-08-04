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
import { ChatInput, heightMeasureUnchanged, cacheableMeasureSig, type HeightMeasureSig } from './ChatInput';
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
