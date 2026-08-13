/**
 * useReferencedFiles — background-tab capture + pure helpers (run_9dd59523).
 *
 * BUG (from run_9e42c066 meta-review): a run finishing on a NON-active chat tab
 * dispatches a correctly-stamped swarm:file-changed event, but the sole resident
 * useReferencedFiles(activeTabId) listener DROPPED any evtTabId != tabId — so that
 * tab's sessionStorage bucket stayed empty and it showed 0 outputs on switch-in.
 *
 * Fix: route a background-tab write to the OWNING tab's storage (via a staging map)
 * with NO setState; the active-tab path is unchanged. Both run the same pure
 * applyWrite/applyDelete helpers (SSOT).
 *
 * MUTATION (documented): revert the background branch to a bare `return` → the
 * "persists to owning tab" test goes RED (bucket stays empty).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useReferencedFiles,
  applyWrite,
  applyDelete,
  type ReferencedFile,
} from '../useReferencedFiles';

const KEY = (tabId: string) => `swarm:referenced-files:${tabId}`;

function bucket(tabId: string): ReferencedFile[] {
  const raw = sessionStorage.getItem(KEY(tabId));
  return raw ? JSON.parse(raw) : [];
}

function fileChanged(path: string, tabId: string, opts: { kind?: string; operation?: string; absolutePath?: string } = {}) {
  window.dispatchEvent(
    new CustomEvent('swarm:file-changed', {
      detail: { path, tabId, operation: opts.operation ?? 'written', relevance: 'deliverable', kind: opts.kind ?? 'source-final', absolutePath: opts.absolutePath },
    }),
  );
}

const mk = (path: string, firstSeen: number): ReferencedFile => ({
  path, absolutePath: `/ws/${path}`, fileName: path.split('/').pop()!, operation: 'written', firstSeen, count: 1,
});

describe('applyWrite (pure SSOT helper)', () => {
  it('adds a new row', () => {
    const out = applyWrite(new Map(), { path: 'a.py', operation: 'written', kind: 'source-final' }, 1000);
    expect(out.get('a.py')).toMatchObject({ path: 'a.py', count: 1, kind: 'source-final', firstSeen: 1000 });
  });
  it('dedups: bumps count + refreshes absolutePath, does not duplicate', () => {
    let m = applyWrite(new Map(), { path: 'a.py', operation: 'written' }, 1000);
    m = applyWrite(m, { path: 'a.py', operation: 'written', absolutePath: '/real/a.py' }, 2000);
    expect(m.size).toBe(1);
    expect(m.get('a.py')!.count).toBe(2);
    expect(m.get('a.py')!.absolutePath).toBe('/real/a.py');
    expect(m.get('a.py')!.firstSeen).toBe(1000); // firstSeen preserved on dedup
  });
  it('does NOT mutate the input map', () => {
    const orig = new Map();
    const out = applyWrite(orig, { path: 'a.py', operation: 'written' }, 1000);
    expect(orig.size).toBe(0);
    expect(out.size).toBe(1);
  });
  it('caps at MAX_FILES (100), evicting the oldest', () => {
    let m = new Map<string, ReferencedFile>();
    for (let i = 0; i < 100; i++) m.set(`f${i}.py`, mk(`f${i}.py`, i)); // f0 oldest (firstSeen 0)
    m = applyWrite(m, { path: 'new.py', operation: 'written' }, 9999);
    expect(m.size).toBe(100);        // still capped
    expect(m.has('f0.py')).toBe(false); // oldest evicted
    expect(m.has('new.py')).toBe(true);
  });
});

describe('applyDelete (pure SSOT helper)', () => {
  it('MARKS an anchored match deleted (persists) and reports hit (run_5d9178bf)', () => {
    const m = new Map([['a.py', mk('a.py', 1)], ['b.py', mk('b.py', 2)]]);
    const { map, hit } = applyDelete(m, { path: 'a.py' });
    expect(hit).toBe(true);
    // Row PERSISTS, marked deleted — not removed.
    expect(map.has('a.py')).toBe(true);
    expect(map.get('a.py')!.deleted).toBe(true);
    expect(map.get('b.py')!.deleted).toBeFalsy();
  });
  it('reports hit=false on no match (caller can no-op)', () => {
    const m = new Map([['a.py', mk('a.py', 1)]]);
    const { hit } = applyDelete(m, { path: 'zzz.py' });
    expect(hit).toBe(false);
  });
  it('matches an absolute delete-path ending with a stored relative path (marks it)', () => {
    const m = new Map([['src/a.py', mk('src/a.py', 1)]]);
    const { map, hit } = applyDelete(m, { path: '/ws/src/a.py' });
    expect(hit).toBe(true);
    expect(map.size).toBe(1); // row persists
    expect(map.get('src/a.py')!.deleted).toBe(true);
  });
});

describe('useReferencedFiles — BACKGROUND-tab capture (the core fix)', () => {
  beforeEach(() => { sessionStorage.clear(); });

  it('persists a background tab write to the OWNING tab storage, NOT the active in-memory rail', () => {
    const { result } = renderHook(() => useReferencedFiles('A')); // active tab = A
    act(() => {
      fileChanged('backend/bg.py', 'B'); // event owned by background tab B
    });
    // Active tab A's in-memory rail is untouched…
    expect(result.current.files.written).toHaveLength(0);
    // …but tab B's storage bucket now HAS the row (was dropped before the fix).
    const b = bucket('B');
    expect(b).toHaveLength(1);
    expect(b[0].path).toBe('backend/bg.py');
  });

  it('dedups background writes across multiple events (staging map serializes)', () => {
    renderHook(() => useReferencedFiles('A'));
    act(() => {
      fileChanged('x.py', 'B');
      fileChanged('y.py', 'B');
      fileChanged('x.py', 'B'); // dedup
    });
    const b = bucket('B');
    expect(b).toHaveLength(2); // x, y — x deduped not doubled
    expect(b.find((f) => f.path === 'x.py')!.count).toBe(2);
  });

  it('a background DELETE marks the owning tab storage row deleted (persists, run_5d9178bf)', () => {
    renderHook(() => useReferencedFiles('A'));
    act(() => { fileChanged('gone.py', 'B'); });
    expect(bucket('B')).toHaveLength(1);
    act(() => { fileChanged('gone.py', 'B', { operation: 'deleted' }); });
    const b = bucket('B');
    expect(b).toHaveLength(1); // row PERSISTS in background storage
    expect(b[0].deleted).toBe(true); // marked deleted, not removed
  });

  it('switching TO a tab that received background writes loads its populated store', () => {
    let tab = 'A';
    const { result, rerender } = renderHook(() => useReferencedFiles(tab));
    act(() => { fileChanged('made-in-B.py', 'B'); }); // arrives while B is backgrounded
    expect(result.current.files.written).toHaveLength(0); // not in A
    // user switches to B
    tab = 'B';
    rerender();
    expect(result.current.files.written.map((f) => f.path)).toContain('made-in-B.py');
  });

  it('active-tab write still updates in-memory state + storage (unchanged)', () => {
    const { result } = renderHook(() => useReferencedFiles('A'));
    act(() => { fileChanged('active.py', 'A'); });
    expect(result.current.files.written.map((f) => f.path)).toContain('active.py');
    expect(bucket('A')).toHaveLength(1);
  });

  it('active→background CYCLE does NOT lose the active-cycle writes (Gate-2 HIGH regression)', () => {
    // The staging-map design lost data here: B bg-writes → B active (writes to storage
    // only) → B background again used the STALE staging as base and full-overwrote
    // storage, discarding B's active-cycle writes. Storage-direct has no stale copy.
    let tab = 'X'; // start on some other active tab
    const { rerender } = renderHook(() => useReferencedFiles(tab));
    // (1) B is background, receives a write.
    act(() => { fileChanged('b-bg-1.py', 'B'); });
    expect(bucket('B').map((f) => f.path)).toEqual(['b-bg-1.py']);
    // (2) B becomes active; (3) user acts in B while active → goes to storage.
    tab = 'B';
    rerender();
    act(() => { fileChanged('b-active.py', 'B'); }); // active path (evtTabId==tab)
    expect(bucket('B').map((f) => f.path).sort()).toEqual(['b-active.py', 'b-bg-1.py']);
    // (4) B backgrounds again (switch to X), receives another bg write.
    tab = 'X';
    rerender();
    act(() => { fileChanged('b-bg-2.py', 'B'); });
    // All THREE must survive — the active-cycle write (b-active.py) is NOT discarded.
    expect(bucket('B').map((f) => f.path).sort()).toEqual(['b-active.py', 'b-bg-1.py', 'b-bg-2.py']);
  });
});
