/**
 * useCanvasHost × canvasSession — the session's persisted outputs are fetched when
 * the session changes and again when a turn ends, and its artifacts join the rail.
 *
 * Methodology: services/api mocked by URL; the store is the real module.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../services/api', () => ({ default: { get: (url: string, o?: unknown) => get(url, o) } }));

import { useCanvasHost } from '../useCanvasHost';
import { resetCanvasSession } from '../../services/canvasSession';

const ART = { path: 'Knowledge/n.md', absolutePath: '/ws/Knowledge/n.md', kind: 'knowledge', role: 'Knowledge',
  firstAt: '2026-10-22T10:00:00', lastAt: '2026-10-22T10:00:00', turns: 1, exists: true };

beforeEach(() => {
  sessionStorage.clear();
  resetCanvasSession();
  get.mockReset();
  get.mockImplementation(async (url: string) =>
    url.startsWith('/canvas/session/') ? { data: { artifacts: [ART], changes: [] } } : { data: {} });
});

describe('useCanvasHost — session outputs', () => {
  it('fetches on session change and on turn end; persisted artifacts join the rail', async () => {
    const { result, rerender } = renderHook((p: { sid: string; streaming: boolean }) =>
      useCanvasHost({ activeTabId: 'tab1', sessionId: p.sid, isStreaming: p.streaming }),
    { initialProps: { sid: 's1', streaming: false } });
    await waitFor(() => expect(result.current.referencedFiles.written.map((f) => f.path)).toEqual(['Knowledge/n.md']));
    const calls = () => get.mock.calls.filter(([u]) => String(u).startsWith('/canvas/session/')).map(([u]) => u);
    expect(calls()).toEqual(['/canvas/session/s1']);
    rerender({ sid: 's1', streaming: true });
    rerender({ sid: 's1', streaming: false });
    await waitFor(() => expect(calls()).toHaveLength(2));
    rerender({ sid: 's2', streaming: false });
    await waitFor(() => expect(calls()).toEqual(['/canvas/session/s1', '/canvas/session/s1', '/canvas/session/s2']));
  });
});
