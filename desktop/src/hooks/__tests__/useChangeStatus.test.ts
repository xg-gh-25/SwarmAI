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

  it('empty paths → empty map, no committed/resolve fetch', async () => {
    wire({});
    const { result } = renderHook(() => useChangeStatus([]));
    // give the debounce window time to (not) fire
    await new Promise((r) => setTimeout(r, 400));
    expect(result.current.size).toBe(0);
    expect(mockGet).not.toHaveBeenCalled();
  });
});
