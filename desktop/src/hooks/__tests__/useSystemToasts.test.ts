/**
 * useSystemToasts — the chat page's named system-level toasts.
 *
 * Tested: each raiser sends the expected severity + message to the app toast
 * stack, and the returned object is stable across renders (it sits in
 * ChatPage useCallback deps).
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const addToast = vi.hoisted(() => vi.fn());
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => ({ addToast }) }));

import { useSystemToasts } from '../useSystemToasts';

describe('useSystemToasts', () => {
  it('raises the named system-level toasts', () => {
    const { result } = renderHook(() => useSystemToasts());
    result.current.tabLimitReached();
    result.current.allTabsBusy();
    result.current.promptCannotLand('The current tab is busy');
    expect(addToast.mock.calls.map((c) => [c[0].severity, c[0].message])).toEqual([
      ['info', expect.stringContaining('Memory usage is high')],
      ['warning', 'Could not open a new tab — all tabs are busy.'],
      ['warning', 'The current tab is busy'],
    ]);
  });

  it('is stable across renders', () => {
    const { result, rerender } = renderHook(() => useSystemToasts());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
