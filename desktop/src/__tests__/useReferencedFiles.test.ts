/**
 * Tests for useReferencedFiles hook.
 *
 * Covers: add files, deduplication, cap at 100, grouping (written-only), persistence.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useReferencedFiles } from '../hooks/useReferencedFiles';

function dispatchFileRef(
  path: string,
  absolutePath?: string,
) {
  // Unified backend event (run_e626e121): swarm:file-changed on window, carries a
  // resolved physical absolutePath used for copy-path. The backend emits ONLY
  // operation:"written" (detection scoped to write sources — directive #6), so the
  // helper always dispatches a write; 'read'/'searched' are no longer part of the
  // FileOperation contract (dead-code cleanup run_0c9338a1).
  window.dispatchEvent(
    new CustomEvent('swarm:file-changed', {
      detail: { path, operation: 'written', absolutePath: absolutePath ?? path, relevance: 'deliverable' },
    }),
  );
}

/** Dispatch with an explicit review `kind` (run_dcce7023). */
function dispatchWithKind(path: string, kind: string) {
  window.dispatchEvent(
    new CustomEvent('swarm:file-changed', {
      detail: { path, operation: 'written', absolutePath: path, relevance: 'deliverable', kind },
    }),
  );
}

describe('useReferencedFiles', () => {
  const SESSION_ID = 'test-session-123';

  beforeEach(() => {
    if (typeof globalThis.sessionStorage !== 'undefined') {
      globalThis.sessionStorage.clear();
    }
  });

  afterEach(() => {
    if (typeof globalThis.sessionStorage !== 'undefined') {
      globalThis.sessionStorage.clear();
    }
  });

  it('starts empty', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    expect(result.current.totalCount).toBe(0);
    expect(result.current.files.written).toHaveLength(0);
  });

  it('adds a file on event', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchFileRef('~/file.py'));
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].path).toBe('~/file.py');
    expect(result.current.files.written[0].fileName).toBe('file.py');
  });

  // ── the unified review `kind` gates rail membership ──
  it('EXCLUDES kind=source from the rail (a mid-run coding edit — suppressed)', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchWithKind('backend/core/foo.py', 'source'));
    expect(result.current.totalCount).toBe(0);
  });

  it('ADMITS kind=source-final (the pipeline-finish PR-review batch, run_b8ea6d5c)', () => {
    // source is dropped mid-run; source-final is the DISTINCT finish-batch kind the
    // surface_run_outputs tool emits — it MUST land as a persistent rail row.
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchWithKind('backend/core/foo.py', 'source-final'));
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].path).toBe('backend/core/foo.py');
  });

  it('EXCLUDES kind=process from the rail (machine noise)', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchWithKind('Projects/X/.artifacts/runs/y/REPORT.md', 'process'));
    expect(result.current.totalCount).toBe(0);
  });

  it('ADMITS kind=content and kind=knowledge, carrying the kind onto the file', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchWithKind('Knowledge/Designs/foo.md', 'content'));
    act(() => dispatchWithKind('MEMORY.md', 'knowledge'));
    expect(result.current.totalCount).toBe(2);
    const byName = Object.fromEntries(result.current.files.written.map((f) => [f.fileName, f.kind]));
    expect(byName['foo.md']).toBe('content');
    expect(byName['MEMORY.md']).toBe('knowledge');
  });

  it('undefined kind (older backend) still lands in the rail (migration fallback)', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchFileRef('~/legacy.py'));  // no kind
    expect(result.current.totalCount).toBe(1);
  });

  it('does NOT clear the list on a transient sessionId=undefined (tab-switch flicker)', () => {
    // BUG1 (run_26981f66): ChatPage.sessionId briefly flips to undefined during
    // tab-switch/canvas-open. The old code wiped the map on !sessionId → the
    // Canvas outputs rail flashed empty and had to rebuild. Fix: undefined is
    // treated as transient — retain the current list until a NEW defined session
    // arrives.
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | undefined }) => useReferencedFiles(sid),
      { initialProps: { sid: SESSION_ID as string | undefined } },
    );
    act(() => dispatchFileRef('/a.py'));
    expect(result.current.totalCount).toBe(1);

    // Transient undefined — MUST retain the list, not clear it.
    rerender({ sid: undefined });
    expect(result.current.totalCount).toBe(1);

    // Same session returns — still there.
    rerender({ sid: SESSION_ID });
    expect(result.current.totalCount).toBe(1);
  });

  it('reloads (does not leak) when a genuinely DIFFERENT session arrives', () => {
    // Cross-session safety: switching to a real different session must NOT show
    // the previous session's outputs.
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | undefined }) => useReferencedFiles(sid),
      { initialProps: { sid: SESSION_ID as string | undefined } },
    );
    act(() => dispatchFileRef('/a.py'));
    expect(result.current.totalCount).toBe(1);

    // A different, defined session → must reload (empty storage for it).
    rerender({ sid: 'other-session-999' });
    expect(result.current.totalCount).toBe(0);
  });

  it('deduplicates same path — increments count', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/src/main.ts');
      dispatchFileRef('/src/main.ts');
      dispatchFileRef('/src/main.ts');
    });
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].count).toBe(3);
  });

  it('groups all files under the written operation', () => {
    // Backend emits only operation:"written" (directive #6) — every referenced
    // file lands in the single `written` group.
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/a.py');
      dispatchFileRef('/b.py');
    });
    expect(result.current.files.written).toHaveLength(2);
  });

  it('caps at 100 files by evicting oldest', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      // Add 100 files
      for (let i = 0; i < 100; i++) {
        dispatchFileRef(`/file${i}.py`);
      }
    });
    expect(result.current.totalCount).toBe(100);

    // Add 101st — oldest (file0) should be evicted
    act(() => dispatchFileRef('/file_new.py'));
    expect(result.current.totalCount).toBe(100);
    // file0 was evicted (oldest firstSeen)
    const allPaths = result.current.files.written.map((f) => f.path);
    expect(allPaths).not.toContain('/file0.py');
    expect(allPaths).toContain('/file_new.py');
  });

  it('persists to sessionStorage', () => {
    const { unmount } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchFileRef('/persist.ts'));
    unmount();

    // Re-render — should load from storage
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].path).toBe('/persist.ts');
  });

  it('ignores events when no sessionId', () => {
    const { result } = renderHook(() => useReferencedFiles(undefined));
    act(() => dispatchFileRef('/ghost.py'));
    expect(result.current.totalCount).toBe(0);
  });

  it('clear removes all files', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/a.py');
      dispatchFileRef('/b.py');
    });
    expect(result.current.totalCount).toBe(2);
    act(() => result.current.clear());
    expect(result.current.totalCount).toBe(0);
  });
});
