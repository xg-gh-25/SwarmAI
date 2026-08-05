/**
 * useReferencedFiles — delete removal (G1, run_5a7be540).
 *
 * When the agent DELETES a file (Bash rm / mv-SRC), the backend now emits a
 * swarm:file-changed with operation:"deleted". The rail must REMOVE the matching
 * row (and persist the removal) instead of leaving a stale "ghost" entry that
 * opens to "Resource not found".
 *
 * Match is CONSERVATIVE/anchored (path === OR absolutePath === OR endsWith('/'+path)),
 * never bare-basename — a delete that matches nothing is a harmless no-op (safe
 * direction: a lingering row == the old behavior).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useReferencedFiles } from '../useReferencedFiles';

const TID = 'tab-del'; // hook key = tabId now (run_26aa6caa)

function fire(detail: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail }));
  });
}

beforeEach(() => sessionStorage.clear());
afterEach(() => sessionStorage.clear());

describe('useReferencedFiles — operation:deleted removes the rail row', () => {
  it('removes a previously-written file when a delete event arrives', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/b.ts', absolutePath: '/ws/src/b.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    expect(result.current.totalCount).toBe(2);

    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written.map((f) => f.path)).toEqual(['src/b.ts']);
  });

  it('removes by absolutePath match (delete path form differs from display path)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    // delete event carries only the absolute form
    fire({ path: '/ws/src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    expect(result.current.totalCount).toBe(0);
  });

  it('a delete that matches nothing is a harmless no-op (no crash, no add)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/gone.ts', absolutePath: '/ws/src/gone.ts', operation: 'deleted', tabId: TID });
    expect(result.current.totalCount).toBe(1); // still just a.ts; delete did NOT add a row
  });

  it('persists the removal to sessionStorage', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    const raw = sessionStorage.getItem(`swarm:referenced-files:${TID}`);
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string)).toEqual([]);
  });
});
