/**
 * canvasSession store — the one frontend copy of each session's Canvas record.
 *
 * Invariants: a burst of swarm:file-changed for a tab is ONE artifacts-only request
 * for that tab's session (debounced); an event of a background tab refreshes ITS
 * session, an unmapped tab's event refreshes nothing; an artifacts-only answer keeps
 * the last Changes; a full refresh while an artifacts one is in flight still runs
 * (after it); a stale answer after a reset is dropped.
 *
 * Methodology: services/api mocked; real timers (the debounce is LIVE_REFRESH_MS).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const get = vi.fn();
vi.mock('../api', () => ({ default: { get: (u: string, o?: unknown) => get(u, o) } }));

import {
  cachedArtifacts, refreshCanvasArtifacts, refreshCanvasSession, resetCanvasSession,
  setActiveCanvasSession, useCanvasSession, LIVE_REFRESH_MS,
} from '../canvasSession';
import { renderHook } from '@testing-library/react';

const art = (path: string) => ({ path, absolutePath: `/ws/${path}`, kind: 'knowledge', role: 'Knowledge', product: true,
  starred: false, displayLabel: path, firstAt: 't', lastAt: 't', writes: 1, deleted: false, exists: true });
const fire = (tabId?: string) => window.dispatchEvent(new CustomEvent('swarm:file-changed', {
  detail: { path: 'x.md', tabId, operation: 'written', kind: 'knowledge' } }));
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => {
  resetCanvasSession();
  get.mockReset();
  get.mockImplementation(async (url: string, o?: { params?: { parts?: string } }) => ({
    data: { artifacts: [art(`${url.split('/').pop()}.md`)], changes: o?.params?.parts === 'artifacts' ? null : [] },
  }));
});

describe('canvasSession store', () => {
  it('a burst of events is one artifacts-only refresh of the owning session', async () => {
    renderHook(() => useCanvasSession());          // subscribes the store to the events
    setActiveCanvasSession('s1', 'A');
    setActiveCanvasSession('s2', 'B');              // B is active now, A in the background
    fire('A'); fire('A'); fire('A');
    fire('Z');                                       // a tab never reported: nothing
    await wait(LIVE_REFRESH_MS + 50);
    expect(get.mock.calls).toEqual([['/canvas/session/s1', { params: { parts: 'artifacts' } }]]);
    expect(cachedArtifacts('s1')?.map((a) => a.path)).toEqual(['s1.md']);
  });

  it('an artifacts-only answer keeps the last Changes; a full refresh after one still runs', async () => {
    setActiveCanvasSession('s1', 'A');
    get.mockImplementationOnce(async () => ({ data: { artifacts: [], changes: [{ root: '/r', repo: 'r', files: [], commits: [] }] } }));
    await refreshCanvasSession();
    const p1 = refreshCanvasArtifacts();
    const p2 = refreshCanvasSession();
    await Promise.all([p1, p2]);
    expect(get.mock.calls.map(([, o]) => (o as { params?: { parts?: string } } | undefined)?.params?.parts ?? 'all'))
      .toEqual(['all', 'artifacts', 'all']);
    const { result } = renderHook(() => useCanvasSession());
    expect(result.current.data?.changes).toEqual([]);   // the last full answer
  });

  it('keeps Changes across an artifacts-only answer', async () => {
    setActiveCanvasSession('s1', 'A');
    get.mockImplementationOnce(async () => ({ data: { artifacts: [], changes: [{ root: '/r', repo: 'r', files: [], commits: [] }] } }));
    await refreshCanvasSession();
    await refreshCanvasArtifacts();
    const { result } = renderHook(() => useCanvasSession());
    expect(result.current.data?.changes?.[0]?.root).toBe('/r');
  });

  it('a stale answer after a reset is dropped', async () => {
    setActiveCanvasSession('s1', 'A');
    let release!: () => void;
    get.mockImplementationOnce(() => new Promise((r) => { release = () => r({ data: { artifacts: [art('old.md')], changes: [] } }); }));
    const p = refreshCanvasSession();
    resetCanvasSession();
    await wait(0);
    release();
    await p;
    expect(cachedArtifacts('s1')).toBeUndefined();
  });
});
