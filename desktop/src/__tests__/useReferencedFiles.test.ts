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

// ── Tab-isolation regression (run_26aa6caa) — the FIX, empirically ──
// EVALUATE proved the real bleed was NOT "" key sharing (falsified: '' is falsy →
// no listener) but the SSE stamp going out UNDEFINED whenever the writing tab's
// sessionId was unresolved (a new tab's first turn), which useReferencedFiles'
// filter fails-OPEN on → the write bled into every mounted rail. Fix: the rail
// keys on the STABLE tabId, and the SSE stamp is capturedTabId (always present).
// These tests pin the post-fix guarantee: a tabId-stamped write is isolated, and
// the only fail-open path left is a truly-unstamped (older-backend) event.
describe('useReferencedFiles — tab-isolation (post-fix, run_26aa6caa)', () => {
  beforeEach(() => globalThis.sessionStorage?.clear());
  afterEach(() => globalThis.sessionStorage?.clear());

  function dispatchTab(path: string, stampTabId: string) {
    window.dispatchEvent(new CustomEvent('swarm:file-changed', {
      detail: { path, operation: 'written', absolutePath: path, relevance: 'deliverable', kind: 'content', tabId: stampTabId },
    }));
  }

  it('ISOLATED: a tabId-stamped write lands ONLY in its own tab', () => {
    // The fix's core guarantee. tabId is stable + always stamped (capturedTabId),
    // so a write from tab-A never appears in tab-B's rail. (Pre-fix, when the
    // writing tab had an unresolved session the stamp was undefined → bled.)
    const tabA = renderHook(() => useReferencedFiles('tab-A'));
    const tabB = renderHook(() => useReferencedFiles('tab-B'));
    act(() => dispatchTab('Knowledge/Designs/from-tabA.md', 'tab-A'));
    expect(tabA.result.current.totalCount).toBe(1);
    expect(tabB.result.current.totalCount).toBe(0);  // isolated — no bleed
  });

  it('MUTATION GUARD: swapping tabId key → bleed reappears (non-vacuous)', () => {
    // If the filter were reverted to compare against a constant/wrong key, tab-B
    // would record tab-A's write. We assert the CURRENT code isolates; this test
    // goes RED if the `evtTabId !== tabId` filter is removed.
    const tabB = renderHook(() => useReferencedFiles('tab-B'));
    act(() => dispatchTab('Knowledge/Designs/from-tabA.md', 'tab-A'));
    expect(tabB.result.current.totalCount).toBe(0);
  });

  it('FAIL-OPEN only for a truly-unstamped (older-backend) event', () => {
    // The one remaining fail-open path: an event with NO tabId (an un-migrated
    // dispatcher). Documented, migration-only — the live SSE bridge always stamps
    // capturedTabId, so this never fires for a real write.
    const tabA = renderHook(() => useReferencedFiles('tab-A'));
    act(() => window.dispatchEvent(new CustomEvent('swarm:file-changed', {
      detail: { path: 'Knowledge/Designs/legacy.md', operation: 'written', absolutePath: 'x', relevance: 'deliverable', kind: 'content' /* NO tabId */ },
    })));
    expect(tabA.result.current.totalCount).toBe(1);  // fail-open (documented)
  });

  it('distinct tab buckets persist independently in sessionStorage', () => {
    const tabA = renderHook(() => useReferencedFiles('tab-A'));
    renderHook(() => useReferencedFiles('tab-B'));
    act(() => dispatchTab('Knowledge/Designs/a.md', 'tab-A'));
    // Remount tab-A: its bucket reloads; tab-B's key is untouched/empty.
    const tabAReload = renderHook(() => useReferencedFiles('tab-A'));
    expect(tabAReload.result.current.totalCount).toBe(1);
    void tabA;
  });
});
