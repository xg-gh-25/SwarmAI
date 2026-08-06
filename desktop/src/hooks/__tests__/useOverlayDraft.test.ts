/**
 * Tests for useOverlayDraft — an in-memory (NOT localStorage) per-overlay draft
 * store. An overlay surface unmounts on close (OverlayHost renderedId→null), so
 * component-local useState is destroyed; this hook parks the form snapshot in a
 * module-level Map keyed by overlayId so a re-open restores it. clear() is called
 * only when the work is dispatched (landed) — Esc/backdrop/failed-land preserve.
 *
 * Verifies: (1) value persists across unmount→remount, (2) clear() wipes it so the
 * next mount is fresh, (3) distinct overlayIds don't bleed, (4) NOT written to
 * localStorage (privacy: NewBrain items are local absolute paths).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useOverlayDraft } from '../useOverlayDraft';

interface Form { name: string; items: string[] }
const INITIAL: Form = { name: '', items: [] };

describe('useOverlayDraft', () => {
  beforeEach(() => {
    // Ensure a clean module store between tests via clear() in each test's arrange.
    localStorage.clear();
  });

  it('persists the value across unmount → remount (same overlayId)', () => {
    const first = renderHook(() => useOverlayDraft<Form>('ov-a', INITIAL));
    act(() => first.result.current[1]({ name: 'Acme', items: ['/abs/x.md'] }));
    first.unmount();

    const second = renderHook(() => useOverlayDraft<Form>('ov-a', INITIAL));
    expect(second.result.current[0]).toEqual({ name: 'Acme', items: ['/abs/x.md'] });
  });

  it('clear() wipes the store so the next mount starts from initial', () => {
    const first = renderHook(() => useOverlayDraft<Form>('ov-clear', INITIAL));
    act(() => first.result.current[1]({ name: 'Temp', items: ['a'] }));
    act(() => first.result.current[2]()); // clear()
    first.unmount();

    const second = renderHook(() => useOverlayDraft<Form>('ov-clear', INITIAL));
    expect(second.result.current[0]).toEqual(INITIAL);
  });

  it('distinct overlayIds do not bleed into each other', () => {
    const a = renderHook(() => useOverlayDraft<Form>('ov-1', INITIAL));
    act(() => a.result.current[1]({ name: 'One', items: [] }));
    const b = renderHook(() => useOverlayDraft<Form>('ov-2', INITIAL));
    expect(b.result.current[0]).toEqual(INITIAL); // ov-2 unaffected by ov-1
  });

  it('does NOT write to localStorage (in-memory only — privacy)', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const { result } = renderHook(() => useOverlayDraft<Form>('ov-priv', INITIAL));
    act(() => result.current[1]({ name: 'Secret', items: ['/Users/me/private/path'] }));
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});
