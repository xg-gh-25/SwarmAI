/**
 * Tests for useUnifiedAttachments.addTerminalText — terminal output joins the
 * ACTIVE tab's attachments (tabMapRef, authoritative), so another tab never gets
 * it; a second add appends to the same chip; the attachment limit is respected.
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { UnifiedTab } from '../useUnifiedTabState';

vi.mock('../../services/workspace', () => ({ workspaceService: { readFileMeta: vi.fn() } }));

import { useUnifiedAttachments } from '../useUnifiedAttachments';
import { MAX_ATTACHMENTS, type UnifiedAttachment } from '../../types';

function tabs(...ids: string[]) {
  const m = new Map<string, UnifiedTab>();
  for (const id of ids) m.set(id, { id, attachments: [] as UnifiedAttachment[] } as unknown as UnifiedTab);
  return { current: m };
}

describe('useUnifiedAttachments.addTerminalText', () => {
  it('lands on the active tab only, one chip, appended to', () => {
    const ref = tabs('A', 'B');
    const { result, rerender } = renderHook(({ tid }) => useUnifiedAttachments(tid, ref), { initialProps: { tid: 'A' } });
    act(() => { expect(result.current.addTerminalText('first', '/r/x')).toBe(true); });
    act(() => { result.current.addTerminalText('second', '/r/x'); });
    expect(ref.current.get('A')!.attachments).toHaveLength(1);
    expect(ref.current.get('A')!.attachments[0].textContent).toBe('first\n\nsecond');
    expect(ref.current.get('B')!.attachments).toHaveLength(0);
    expect(result.current.attachments).toHaveLength(1);
    rerender({ tid: 'B' });
    expect(result.current.attachments).toHaveLength(0);       // B's own (empty) row
  });

  it('respects the attachment limit for a new chip, but always appends to an existing one', () => {
    const ref = tabs('A');
    const full = Array.from({ length: MAX_ATTACHMENTS }, (_, i) => ({
      id: `f${i}`, name: `f${i}`, type: 'text', deliveryStrategy: 'inline_text', size: 1,
      mediaType: 'text/plain', isLoading: false,
    }) as UnifiedAttachment);
    ref.current.get('A')!.attachments = full;
    const { result } = renderHook(() => useUnifiedAttachments('A', ref));
    act(() => { expect(result.current.addTerminalText('x', '')).toBe(false); });
    expect(result.current.error).toMatch(/Maximum/);
    ref.current.get('A')!.attachments = [...full.slice(1), { ...full[0], id: 'term', type: 'terminal',
      deliveryStrategy: 'terminal_context', textContent: 'old', terminal: { cwd: '', lines: 1, truncated: false } }];
    act(() => { expect(result.current.addTerminalText('new', '')).toBe(true); });
    expect(ref.current.get('A')!.attachments.find((a) => a.id === 'term')!.textContent).toBe('old\n\nnew');
  });

  it('no active tab: nothing is added', () => {
    const ref = tabs('A');
    const { result } = renderHook(() => useUnifiedAttachments(null, ref));
    act(() => { expect(result.current.addTerminalText('x', '')).toBe(false); });
    expect(ref.current.get('A')!.attachments).toHaveLength(0);
  });
});
