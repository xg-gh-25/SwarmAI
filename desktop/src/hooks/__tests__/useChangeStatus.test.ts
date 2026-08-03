/**
 * Tests for useChangeStatus — the resolve-first committed-based NEW/UPD source
 * (Run-B fix for source-repo files getting no badge).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { classifyCommitted, useChangeStatus } from '../useChangeStatus';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn() },
}));
import api from '../../services/api';
const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe('classifyCommitted', () => {
  it('in_head false → new (untracked)', () => {
    expect(classifyCommitted(false)).toBe('new');
  });
  it('in_head true → upd (modified, even if committed content is empty/binary)', () => {
    expect(classifyCommitted(true)).toBe('upd');
  });
  it('in_head null/undefined → null (undetermined → no badge)', () => {
    expect(classifyCommitted(null)).toBeNull();
    expect(classifyCommitted(undefined)).toBeNull();
  });
});

describe('useChangeStatus', () => {
  beforeEach(() => {
    mockGet.mockReset();
    // Default: any unexpected call resolves to empty (never returns undefined,
    // which would crash on `.data`). Specific tests override via wire().
    mockGet.mockResolvedValue({ data: {} });
  });

  // helper: resolve returns the same path (abs no-op), committed returns the
  // given {content, in_head} record. in_head drives the badge, not content.
  type Committed = { content: string; in_head: boolean | null };
  function wire(committedByResolved: Record<string, Committed>, failResolve: Set<string> = new Set()) {
    mockGet.mockImplementation((url: string, cfg?: { params?: { path?: string } }) => {
      const p = cfg?.params?.path ?? '';
      if (url === '/workspace/file/resolve') {
        if (failResolve.has(p)) return Promise.reject(new Error('404'));
        return Promise.resolve({ data: { resolved_path: p } });
      }
      if (url === '/workspace/file/committed') {
        return Promise.resolve({ data: committedByResolved[p] ?? { content: '', in_head: null } });
      }
      return Promise.reject(new Error('unexpected url ' + url));
    });
  }

  it('AC3: modified (in_head true) → upd; untracked (in_head false) → new', async () => {
    wire({
      '/abs/mod.py': { content: 'existing head content', in_head: true },
      '/abs/new.py': { content: '', in_head: false },
    });
    const { result } = renderHook(() => useChangeStatus(['/abs/mod.py', '/abs/new.py']));
    await waitFor(() => expect(result.current.size).toBe(2), { timeout: 2000 });
    expect(result.current.get('/abs/mod.py')).toBe('upd');
    expect(result.current.get('/abs/new.py')).toBe('new');
  });

  it('Gate-2 regression: tracked binary (empty content, in_head true) → upd NOT new', async () => {
    // The exact bug the discriminator fixes: content is "" but the file IS in
    // HEAD (binary/decode-fail). Keying off content-length would mislabel 'new'.
    wire({ '/abs/logo.png': { content: '', in_head: true } });
    const { result } = renderHook(() => useChangeStatus(['/abs/logo.png']));
    await waitFor(() => expect(result.current.size).toBe(1), { timeout: 2000 });
    expect(result.current.get('/abs/logo.png')).toBe('upd');
  });

  it('undetermined (in_head null) → omitted (no badge, fail-soft)', async () => {
    wire({ '/abs/ok.py': { content: 'head', in_head: true }, '/abs/nogit.py': { content: '', in_head: null } });
    const { result } = renderHook(() => useChangeStatus(['/abs/ok.py', '/abs/nogit.py']));
    await waitFor(() => expect(result.current.size).toBe(1), { timeout: 2000 });
    expect(result.current.get('/abs/ok.py')).toBe('upd');
    expect(result.current.has('/abs/nogit.py')).toBe(false);
  });

  it('AC3: a path git cannot resolve → omitted (no badge, fail-soft)', async () => {
    wire({ '/abs/ok.py': { content: 'head', in_head: true } }, new Set(['repo/rel/unresolvable.py']));
    const { result } = renderHook(() => useChangeStatus(['/abs/ok.py', 'repo/rel/unresolvable.py']));
    await waitFor(() => expect(result.current.size).toBe(1), { timeout: 2000 });
    expect(result.current.get('/abs/ok.py')).toBe('upd');
    expect(result.current.has('repo/rel/unresolvable.py')).toBe(false);
  });

  it('PERF (run_aee19d4f): a known path is NOT re-fetched when a new path is appended', async () => {
    wire({
      '/abs/a.py': { content: 'x', in_head: true },
      '/abs/b.py': { content: '', in_head: false },
    });
    const { result, rerender } = renderHook(
      ({ paths }) => useChangeStatus(paths),
      { initialProps: { paths: ['/abs/a.py'] } },
    );
    await waitFor(() => expect(result.current.get('/abs/a.py')).toBe('upd'), { timeout: 2000 });
    // committed calls so far are for a.py only. Count them.
    const committedCallsForA = mockGet.mock.calls.filter(
      (c) => c[0] === '/workspace/file/committed' && c[1]?.params?.path === '/abs/a.py',
    ).length;
    expect(committedCallsForA).toBe(1);

    // Append b.py — a.py is already resolved and must NOT be queried again.
    rerender({ paths: ['/abs/a.py', '/abs/b.py'] });
    await waitFor(() => expect(result.current.get('/abs/b.py')).toBe('new'), { timeout: 2000 });
    expect(result.current.get('/abs/a.py')).toBe('upd'); // still there (from cache)
    const committedCallsForAAfter = mockGet.mock.calls.filter(
      (c) => c[0] === '/workspace/file/committed' && c[1]?.params?.path === '/abs/a.py',
    ).length;
    expect(committedCallsForAAfter).toBe(1); // NOT re-fetched — cache hit
  });

  it('cache self-heals: re-writing a listed path re-fetches its badge (Gate-2 MEDIUM)', async () => {
    // a.py starts untracked (new). Later it gets committed → in_head flips true.
    let inHead = false;
    mockGet.mockImplementation((url: string, cfg?: { params?: { path?: string } }) => {
      const p = cfg?.params?.path ?? '';
      if (url === '/workspace/file/resolve') return Promise.resolve({ data: { resolved_path: p } });
      if (url === '/workspace/file/committed') return Promise.resolve({ data: { content: 'x', in_head: inHead } });
      return Promise.reject(new Error('unexpected ' + url));
    });
    const { result } = renderHook(() => useChangeStatus(['/abs/a.py']));
    await waitFor(() => expect(result.current.get('/abs/a.py')).toBe('new'), { timeout: 2000 });

    // File gets committed, then written again → the write event must invalidate
    // the cached 'new' and re-resolve to 'upd'.
    inHead = true;
    window.dispatchEvent(new CustomEvent('swarm:file-changed', {
      detail: { path: '/abs/a.py', operation: 'written', relevance: 'deliverable' },
    }));
    await waitFor(() => expect(result.current.get('/abs/a.py')).toBe('upd'), { timeout: 2000 });
  });

  it('empty paths → empty map, no committed/resolve fetch', async () => {
    wire({});
    const { result } = renderHook(() => useChangeStatus([]));
    // give the debounce window time to (not) fire
    await new Promise((r) => setTimeout(r, 400));
    expect(result.current.size).toBe(0);
    expect(mockGet).not.toHaveBeenCalled();
  });
});
