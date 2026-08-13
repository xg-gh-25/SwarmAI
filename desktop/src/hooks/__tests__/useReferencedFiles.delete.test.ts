/**
 * useReferencedFiles — delete MARKS the row struck-through (run_5d9178bf; supersedes
 * the run_5a7be540 "remove the row" contract).
 *
 * When the agent DELETES a file, the rail now MARKS the matching row `deleted:true`
 * (persistent, struck-through, Show-Changes disabled) instead of removing it — "the
 * user should see what I deleted" (XG 2026-08-13). The row PERSISTS for the session.
 *
 * Match is CONSERVATIVE/anchored (railSsot.matchesPath: path === OR absolutePath ===
 * OR '/'+rel suffix), never bare-basename — a delete that matches nothing is a
 * harmless no-op (no row marked, no row added).
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

describe('useReferencedFiles — operation:deleted MARKS the row struck-through (persists)', () => {
  it('marks a previously-written file deleted (row persists, count unchanged)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/b.ts', absolutePath: '/ws/src/b.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    expect(result.current.totalCount).toBe(2);

    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    // Row PERSISTS (not removed) — count is still 2.
    expect(result.current.totalCount).toBe(2);
    const a = result.current.files.written.find((f) => f.path === 'src/a.ts');
    const b = result.current.files.written.find((f) => f.path === 'src/b.ts');
    expect(a?.deleted).toBe(true);
    expect(b?.deleted).toBeFalsy();
  });

  it('marks by absolutePath match (delete path form differs from display path)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    // delete event carries only the absolute form
    fire({ path: '/ws/src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0]?.deleted).toBe(true);
  });

  it('a delete that matches nothing is a harmless no-op (no crash, no add, no mark)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/gone.ts', absolutePath: '/ws/src/gone.ts', operation: 'deleted', tabId: TID });
    expect(result.current.totalCount).toBe(1); // still just a.ts
    expect(result.current.files.written[0]?.deleted).toBeFalsy(); // a.ts NOT marked
  });

  it('does NOT mark a same-basename file in a different repo (D3 anchored match)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'repo1/src/a.ts', absolutePath: '/repo1/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    // a delete of a DIFFERENT repo's same-named file must NOT mark our row
    fire({ path: '/repo2/pkg/a.ts', absolutePath: '/repo2/pkg/a.ts', operation: 'deleted', tabId: TID });
    expect(result.current.files.written[0]?.deleted).toBeFalsy();
  });

  it('a WRITE after a DELETE of the same path CLEARS the deleted mark (Gate-2 HIGH)', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', kind: 'content', tabId: TID });
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    expect(result.current.files.written[0]?.deleted).toBe(true);
    // Recreate the file — it must become live again (not stuck struck-through).
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', kind: 'content', tabId: TID });
    expect(result.current.files.written[0]?.deleted).toBe(false);
    expect(result.current.totalCount).toBe(1);
  });

  it('persists the deleted-mark to sessionStorage', () => {
    renderHook(() => useReferencedFiles(TID));
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'written', relevance: 'deliverable', tabId: TID });
    fire({ path: 'src/a.ts', absolutePath: '/ws/src/a.ts', operation: 'deleted', tabId: TID });
    const raw = sessionStorage.getItem(`swarm:referenced-files:${TID}`);
    expect(raw).toBeTruthy();
    const arr = JSON.parse(raw as string);
    expect(arr).toHaveLength(1);
    expect(arr[0].deleted).toBe(true);
  });
});

describe('useReferencedFiles — read-only NEVER surfaces (AC5, §0′ principle)', () => {
  // The emit gate only emits write/delete operations — a Read/Grep tool produces NO
  // swarm:file-changed event at all, so nothing reaches this hook. This locks the
  // ALREADY-structural invariant: even if a stray event arrived with a non-write/
  // delete operation, the hook adds no row. (The real guard is server-side: the
  // orchestrator's pending-write map is keyed on Edit/Write/NotebookEdit + Bash write
  // targets only — Read/Grep never enter it.)
  it('a non-write/delete operation adds no rail row', () => {
    const { result } = renderHook(() => useReferencedFiles(TID));
    // Simulate a hypothetical stray "read" event — must be ignored.
    fire({ path: 'src/read-only.ts', absolutePath: '/ws/src/read-only.ts', operation: 'read', tabId: TID });
    expect(result.current.totalCount).toBe(0);
  });
});
