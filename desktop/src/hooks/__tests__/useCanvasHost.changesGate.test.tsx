/**
 * useCanvasHost × Canvas Changes gate (docs/design/canvas-changes-from-turns.md D5).
 *
 * What is tested: a host that mounts while its tab's turn streams sends no Changes
 * request (the streaming flag is set before the session refresh); when the turn ends
 * exactly one Changes request goes out; Artifacts keep refreshing as before.
 *
 * Methodology: the real host and store; services/api `get` mocked and its calls
 * read by `parts`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../services/api', () => ({ default: { get: (...a: unknown[]) => get(...a), post: vi.fn() } }));

import { useCanvasHost } from '../useCanvasHost';
import { resetCanvasSession } from '../../services/canvasSession';

const parts = (p: string) => get.mock.calls.filter((c) => (c[1] as { params?: { parts?: string } })?.params?.parts === p).length;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  resetCanvasSession();
  get.mockReset();
  get.mockImplementation(async () => ({ data: { artifacts: [], changes: { turns: [], commits: [] } } }));
});

describe('useCanvasHost Changes gate', () => {
  it('mid-turn mount: no Changes request; turn end: exactly one', async () => {
    const { rerender } = renderHook((p: { isStreaming: boolean }) =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's1', isStreaming: p.isStreaming }), { initialProps: { isStreaming: true } });
    await flush();
    expect(parts('changes')).toBe(0);
    expect(parts('artifacts')).toBeGreaterThan(0);
    rerender({ isStreaming: false });
    await flush();
    expect(parts('changes')).toBe(1);
  });

  it('an idle mount loads Changes once', async () => {
    renderHook(() => useCanvasHost({ activeTabId: 'A', sessionId: 's1', isStreaming: false }));
    await flush();
    expect(parts('changes')).toBe(1);
  });
});
