/**
 * Unit tests for ToastContext — ToastProvider and useToast hook.
 *
 * Tests the core toast notification system including:
 * - Adding and removing toasts
 * - Auto-dismiss behavior for success/info vs warning/error
 * - Max 5 visible cap with overflow queuing
 * - Deduplication by id
 * - Action support on toasts
 * - Hook throws outside provider
 *
 * Testing methodology: Unit tests with React Testing Library + vitest.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { type ReactNode } from 'react';
import { ToastProvider, useToast } from './ToastContext';
import type { ToastOptions } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}

function renderToastHook() {
  return renderHook(() => useToast(), { wrapper });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ToastContext', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('crypto', {
      randomUUID: () => 'generated-uuid-' + Math.random().toString(36).slice(2, 8),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('throws when useToast is called outside ToastProvider', () => {
    expect(() => {
      renderHook(() => useToast());
    }).toThrow('useToast must be used within a ToastProvider');
  });

  it('adds a toast and returns its id', () => {
    const { result } = renderToastHook();

    let id: string;
    act(() => {
      id = result.current.addToast({ severity: 'info', message: 'Hello' });
    });

    expect(id!).toBeDefined();
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe('Hello');
    expect(result.current.toasts[0].severity).toBe('info');
  });

  it('uses provided id instead of generating one', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'success', message: 'Custom', id: 'my-id' });
    });

    expect(result.current.toasts[0].id).toBe('my-id');
  });

  it('removes a toast by id', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'info', message: 'A', id: 'a' });
      result.current.addToast({ severity: 'info', message: 'B', id: 'b' });
    });
    expect(result.current.toasts).toHaveLength(2);

    act(() => {
      result.current.removeToast('a');
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].id).toBe('b');
  });

  // ---- Auto-dismiss behavior ----

  it('auto-dismisses success toasts after 5s', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'success', message: 'Done' });
    });
    expect(result.current.toasts).toHaveLength(1);

    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('auto-dismisses info toasts after 5s', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'info', message: 'FYI' });
    });
    expect(result.current.toasts).toHaveLength(1);

    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('does NOT auto-dismiss warning toasts by default', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'warning', message: 'Watch out' });
    });

    act(() => { vi.advanceTimersByTime(10000); });
    expect(result.current.toasts).toHaveLength(1);
  });

  it('does NOT auto-dismiss error toasts by default', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'error', message: 'Oops' });
    });

    act(() => { vi.advanceTimersByTime(10000); });
    expect(result.current.toasts).toHaveLength(1);
  });

  it('auto-dismisses warning toast when autoDismiss is explicitly true', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'warning', message: 'Temp', autoDismiss: true });
    });
    expect(result.current.toasts).toHaveLength(1);

    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('respects custom durationMs for auto-dismiss', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'success', message: 'Quick', durationMs: 2000 });
    });

    act(() => { vi.advanceTimersByTime(1999); });
    expect(result.current.toasts).toHaveLength(1);

    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current.toasts).toHaveLength(0);
  });

  // ---- Max visible cap ----

  it('caps visible toasts at 5, queuing the rest', () => {
    const { result } = renderToastHook();

    act(() => {
      for (let i = 0; i < 8; i++) {
        result.current.addToast({ severity: 'error', message: `Toast ${i}`, id: `t-${i}` });
      }
    });

    expect(result.current.toasts).toHaveLength(5);
    expect(result.current.toasts.map((t) => t.id)).toEqual([
      't-0', 't-1', 't-2', 't-3', 't-4',
    ]);
  });

  it('promotes queued toasts when visible ones are removed', () => {
    const { result } = renderToastHook();

    act(() => {
      for (let i = 0; i < 7; i++) {
        result.current.addToast({ severity: 'error', message: `Toast ${i}`, id: `t-${i}` });
      }
    });
    expect(result.current.toasts).toHaveLength(5);

    act(() => {
      result.current.removeToast('t-0');
    });
    // t-5 should now be visible
    expect(result.current.toasts).toHaveLength(5);
    expect(result.current.toasts[4].id).toBe('t-5');
  });

  // ---- Deduplication ----

  it('replaces existing toast with same id instead of stacking', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'info', message: 'First', id: 'dup' });
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe('First');

    act(() => {
      result.current.addToast({ severity: 'warning', message: 'Updated', id: 'dup' });
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe('Updated');
    expect(result.current.toasts[0].severity).toBe('warning');
  });

  // ---- Action support ----

  it('preserves action field on toast items', () => {
    const { result } = renderToastHook();
    const onClick = vi.fn();

    act(() => {
      result.current.addToast({
        severity: 'error',
        message: 'Action needed',
        action: { label: 'Resolve', onClick },
      });
    });

    expect(result.current.toasts[0].action).toBeDefined();
    expect(result.current.toasts[0].action!.label).toBe('Resolve');
    result.current.toasts[0].action!.onClick();
    expect(onClick).toHaveBeenCalledOnce();
  });

  // ---- Timer cleanup on removal ----

  it('clears auto-dismiss timer when toast is manually removed', () => {
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'success', message: 'Temp', id: 'rm-me' });
    });
    expect(result.current.toasts).toHaveLength(1);

    // Remove before timer fires
    act(() => {
      result.current.removeToast('rm-me');
    });
    expect(result.current.toasts).toHaveLength(0);

    // Advance past the original timer — should not cause errors
    act(() => { vi.advanceTimersByTime(6000); });
    expect(result.current.toasts).toHaveLength(0);
  });

  it('sets createdAt timestamp on toast items', () => {
    const now = Date.now();
    vi.setSystemTime(now);
    const { result } = renderToastHook();

    act(() => {
      result.current.addToast({ severity: 'info', message: 'Timestamped' });
    });

    expect(result.current.toasts[0].createdAt).toBe(now);
  });
});
