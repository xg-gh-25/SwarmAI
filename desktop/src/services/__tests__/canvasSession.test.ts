/**
 * canvasSession store — the one frontend copy of each session's Canvas record.
 *
 * Invariants: a burst of swarm:file-changed for a tab is ONE artifacts-only request
 * for that tab's session (debounced); an event of a background tab refreshes ITS
 * session, an unmapped tab's event refreshes nothing; Artifacts and Changes are
 * separate requests and a slow Changes never holds the Artifacts; one part's answer
 * keeps the other; a refresh asked while one runs runs once more after it; a stale
 * answer after a reset is dropped.
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
import { act, renderHook } from '@testing-library/react';

const art = (path: string) => ({ path, absolutePath: `/ws/${path}`, kind: 'knowledge', role: 'Knowledge', product: true,
  starred: false, displayLabel: path, firstAt: 't', lastAt: 't', writes: 1, deleted: false, exists: true });
const fire = (tabId?: string) => window.dispatchEvent(new CustomEvent('swarm:file-changed', {
  detail: { path: 'x.md', tabId, operation: 'written', kind: 'knowledge' } }));
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => {
  resetCanvasSession();
  get.mockReset();
  get.mockImplementation(async (url: string, o?: { params?: { parts?: string } }) => ({
    data: { artifacts: [art(`${url.split('/').pop()}.md`)], changes: o?.params?.parts === 'changes' ? [] : null },
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

  it('the two parts are separate requests; a slow Changes never holds the Artifacts', async () => {
    setActiveCanvasSession('s1', 'A');
    let releaseChanges!: () => void;
    get.mockImplementation((url: string, o?: { params?: { parts?: string } }) => o?.params?.parts === 'changes'
      ? new Promise((r) => { releaseChanges = () => r({ data: { artifacts: null, changes: [{ run: 'run_1', repos: [] }] } }); })
      : Promise.resolve({ data: { artifacts: [art('a.md')], changes: null } }));
    const { result } = renderHook(() => useCanvasSession());
    let full!: Promise<void>;
    await act(async () => { full = refreshCanvasSession(); await wait(0); });
    expect(result.current.data?.artifacts?.map((a) => a.path)).toEqual(['a.md']);
    expect(result.current.data?.changes).toBeNull();
    expect(result.current.loading).toEqual({ artifacts: false, changes: true });
    await act(async () => { releaseChanges(); await full; });
    expect(result.current.data?.changes?.[0]?.run).toBe('run_1');
    await act(async () => { await refreshCanvasArtifacts(); });   // an Artifacts answer keeps Changes
    expect(result.current.data?.changes?.[0]?.run).toBe('run_1');
  });

  it('a refresh asked while one runs runs once more after it (its answer is read after the ask)', async () => {
    setActiveCanvasSession('s1', 'A');
    let n = 0;
    let release!: () => void;
    get.mockImplementation(() => {
      n += 1;
      return n === 1
        ? new Promise((r) => { release = () => r({ data: { artifacts: [art('old.md')] } }); })
        : Promise.resolve({ data: { artifacts: [art('new.md')] } });
    });
    const ps = [refreshCanvasArtifacts(), refreshCanvasArtifacts(), refreshCanvasArtifacts()];
    await wait(0);
    release();
    await Promise.all(ps);
    expect(n).toBe(2);
    expect(cachedArtifacts('s1')?.map((a) => a.path)).toEqual(['new.md']);
  });

  it('a stale answer after a reset is dropped', async () => {
    setActiveCanvasSession('s1', 'A');
    let release!: () => void;
    get.mockImplementation(() => new Promise((r) => { release = () => r({ data: { artifacts: [art('old.md')], changes: [] } }); }));
    const p = refreshCanvasArtifacts();
    resetCanvasSession();
    await wait(0);
    release();
    await p;
    expect(cachedArtifacts('s1')).toBeUndefined();
  });
});
