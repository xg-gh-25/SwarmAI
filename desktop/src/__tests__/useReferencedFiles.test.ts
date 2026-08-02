/**
 * Tests for useReferencedFiles hook.
 *
 * Covers: add files, deduplication, operation promotion, cap at 100, grouping, persistence.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useReferencedFiles } from '../hooks/useReferencedFiles';

function dispatchFileRef(path: string, operation: 'read' | 'written' | 'searched' = 'read') {
  document.dispatchEvent(
    new CustomEvent('swarm:file-referenced', { detail: { path, operation } }),
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
    expect(result.current.files.read).toHaveLength(0);
    expect(result.current.files.written).toHaveLength(0);
    expect(result.current.files.searched).toHaveLength(0);
  });

  it('adds a file on event', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchFileRef('~/file.py', 'read'));
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.read[0].path).toBe('~/file.py');
    expect(result.current.files.read[0].fileName).toBe('file.py');
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
    act(() => dispatchFileRef('/a.py', 'written'));
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
    act(() => dispatchFileRef('/a.py', 'written'));
    expect(result.current.totalCount).toBe(1);

    // A different, defined session → must reload (empty storage for it).
    rerender({ sid: 'other-session-999' });
    expect(result.current.totalCount).toBe(0);
  });

  it('deduplicates same path — increments count', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/src/main.ts', 'read');
      dispatchFileRef('/src/main.ts', 'read');
      dispatchFileRef('/src/main.ts', 'read');
    });
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.read[0].count).toBe(3);
  });

  it('promotes operation from read to written', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/src/app.tsx', 'read');
      dispatchFileRef('/src/app.tsx', 'written');
    });
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].path).toBe('/src/app.tsx');
    expect(result.current.files.read).toHaveLength(0);
  });

  it('groups files by operation', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/a.py', 'read');
      dispatchFileRef('/b.py', 'written');
      dispatchFileRef('/c/', 'searched');
    });
    expect(result.current.files.read).toHaveLength(1);
    expect(result.current.files.written).toHaveLength(1);
    expect(result.current.files.searched).toHaveLength(1);
  });

  it('caps at 100 files by evicting oldest', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      // Add 100 files
      for (let i = 0; i < 100; i++) {
        dispatchFileRef(`/file${i}.py`, 'read');
      }
    });
    expect(result.current.totalCount).toBe(100);

    // Add 101st — oldest (file0) should be evicted
    act(() => dispatchFileRef('/file_new.py', 'read'));
    expect(result.current.totalCount).toBe(100);
    // file0 was evicted (oldest firstSeen)
    const allPaths = result.current.files.read.map((f) => f.path);
    expect(allPaths).not.toContain('/file0.py');
    expect(allPaths).toContain('/file_new.py');
  });

  it('persists to sessionStorage', () => {
    const { unmount } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => dispatchFileRef('/persist.ts', 'written'));
    unmount();

    // Re-render — should load from storage
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    expect(result.current.totalCount).toBe(1);
    expect(result.current.files.written[0].path).toBe('/persist.ts');
  });

  it('ignores events when no sessionId', () => {
    const { result } = renderHook(() => useReferencedFiles(undefined));
    act(() => dispatchFileRef('/ghost.py', 'read'));
    expect(result.current.totalCount).toBe(0);
  });

  it('clear removes all files', () => {
    const { result } = renderHook(() => useReferencedFiles(SESSION_ID));
    act(() => {
      dispatchFileRef('/a.py', 'read');
      dispatchFileRef('/b.py', 'written');
    });
    expect(result.current.totalCount).toBe(2);
    act(() => result.current.clear());
    expect(result.current.totalCount).toBe(0);
  });
});
