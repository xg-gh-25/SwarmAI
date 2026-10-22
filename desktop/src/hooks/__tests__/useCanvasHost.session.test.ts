/**
 * useCanvasHost × canvasSession — the session's persisted outputs (Artifacts and
 * Changes, two requests) are fetched when the session changes and again when a turn
 * ends, and its artifacts join the rail.
 *
 * Methodology: services/api mocked by URL; the store is the real module.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../services/api', () => ({ default: { get: (url: string, o?: unknown) => get(url, o) } }));

import { useCanvasHost } from '../useCanvasHost';
import { refreshCanvasSession, resetCanvasSession } from '../../services/canvasSession';
import { act } from '@testing-library/react';

const ART = { path: 'Knowledge/n.md', absolutePath: '/ws/Knowledge/n.md', kind: 'knowledge', role: 'Knowledge',
  firstAt: '2026-10-22T10:00:00', lastAt: '2026-10-22T10:00:00', writes: 1, deleted: false, exists: true, product: true, starred: false, displayLabel: 'n.md' };

beforeEach(() => {
  sessionStorage.clear();
  resetCanvasSession();
  get.mockReset();
  get.mockImplementation(async (url: string) =>
    url.startsWith('/canvas/session/') ? { data: { artifacts: [ART], changes: [] } } : { data: {} });
});

describe('useCanvasHost — session outputs', () => {
  const parts = () => get.mock.calls.filter(([u]) => String(u).startsWith('/canvas/session/'))
    .map(([u, o]) => `${String(u).split('/').pop()}:${(o as { params: { parts: string } }).params.parts}`);

  it('fetches both parts on session change and on turn end; persisted artifacts join the rail', async () => {
    const { result, rerender } = renderHook((p: { sid: string; streaming: boolean }) =>
      useCanvasHost({ activeTabId: 'tab1', sessionId: p.sid, isStreaming: p.streaming }),
    { initialProps: { sid: 's1', streaming: false } });
    await waitFor(() => expect(result.current.referencedFiles.written.map((f) => f.path)).toEqual(['Knowledge/n.md']));
    expect(parts()).toEqual(['s1:artifacts', 's1:changes']);
    rerender({ sid: 's1', streaming: true });
    rerender({ sid: 's1', streaming: false });
    await waitFor(() => expect(parts()).toHaveLength(4));
    rerender({ sid: 's2', streaming: false });
    await waitFor(() => expect(parts().slice(4)).toEqual(['s2:artifacts', 's2:changes']));
  });

  it('a refresh re-renders the host only for a changed list, never on loading flips; one asked during one runs once more', async () => {
    let renders = 0;
    const { result } = renderHook(() => { renders++; return useCanvasHost({ activeTabId: 'tab1', sessionId: 's1', isStreaming: false }); });
    await waitFor(() => expect(result.current.referencedFiles.written).toHaveLength(1));
    let before = renders;
    let n = get.mock.calls.length;
    await act(async () => { await Promise.all([refreshCanvasSession(), refreshCanvasSession(), refreshCanvasSession()]); });
    expect(get.mock.calls.length - n).toBe(4);           // each part: the request + one more after it
    expect(renders - before).toBe(0);                    // the same answer: nothing to draw
    get.mockImplementation(async (url: string) =>
      url.startsWith('/canvas/session/') ? { data: { artifacts: [ART, { ...ART, path: 'Knowledge/m.md' }], changes: [] } } : { data: {} });
    before = renders; n = get.mock.calls.length;
    await act(async () => { await refreshCanvasSession(); });
    expect(get.mock.calls.length - n).toBe(2);
    expect(renders - before).toBe(1);
    expect(result.current.referencedFiles.written).toHaveLength(2);
  });
});
