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
  it('empty committed → new (untracked)', () => {
    expect(classifyCommitted('')).toBe('new');
  });
  it('non-empty committed → upd (modified)', () => {
    expect(classifyCommitted('some file content')).toBe('upd');
  });
});

describe('useChangeStatus', () => {
  beforeEach(() => {
    mockGet.mockReset();
    // Default: any unexpected call resolves to empty (never returns undefined,
    // which would crash on `.data`). Specific tests override via wire().
    mockGet.mockResolvedValue({ data: {} });
  });

  // helper: resolve returns the same path (abs no-op), committed returns given content
  function wire(committedByResolved: Record<string, string>, failResolve: Set<string> = new Set()) {
    mockGet.mockImplementation((url: string, cfg?: { params?: { path?: string } }) => {
      const p = cfg?.params?.path ?? '';
      if (url === '/workspace/file/resolve') {
        if (failResolve.has(p)) return Promise.reject(new Error('404'));
        return Promise.resolve({ data: { resolved_path: p } });
      }
      if (url === '/workspace/file/committed') {
        return Promise.resolve({ data: { content: committedByResolved[p] ?? '' } });
      }
      return Promise.reject(new Error('unexpected url ' + url));
    });
  }

  it('AC3: modified file (non-empty committed) → upd; untracked (empty) → new', async () => {
    wire({ '/abs/mod.py': 'existing head content', '/abs/new.py': '' });
    const { result } = renderHook(() => useChangeStatus(['/abs/mod.py', '/abs/new.py']));
    await waitFor(() => expect(result.current.size).toBe(2), { timeout: 2000 });
    expect(result.current.get('/abs/mod.py')).toBe('upd');
    expect(result.current.get('/abs/new.py')).toBe('new');
  });

  it('AC3: a path git cannot resolve → omitted (no badge, fail-soft)', async () => {
    wire({ '/abs/ok.py': 'head' }, new Set(['repo/rel/unresolvable.py']));
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
