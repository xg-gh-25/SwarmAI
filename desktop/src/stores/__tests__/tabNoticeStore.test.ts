/**
 * tabNoticeStore — per-tab notices must never cross tabs or sessions.
 *
 * Methodology: a fast-check property over random interleavings of
 * post / dismiss / session change / tab close / timer fire, plus pinned
 * race cases. Invariants:
 * - every notice in a tab's list was written for that tab;
 * - what a tab shows (visibleNotices) belongs to the tab's CURRENT session;
 * - a write whose captured session no longer matches the tab is dropped;
 * - an old auto-dismiss timer never removes a newer notice with the same id;
 * - a closed tab keeps no notices and no timers.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { createTabNoticeStore, visibleNotices, MAX_NOTICES_PER_TAB } from '../tabNoticeStore';

function fakeTimers() {
  let next = 1;
  const pending = new Map<number, () => void>();
  return {
    timers: {
      setTimeout: (fn: () => void) => { const h = next++; pending.set(h, fn); return h; },
      clearTimeout: (h: unknown) => { pending.delete(h as number); },
    },
    fire(h?: number) {
      const key = h ?? [...pending.keys()][0];
      const fn = pending.get(key);
      if (!fn) return;
      pending.delete(key);
      fn();
    },
    keys: () => [...pending.keys()],
  };
}

describe('tabNoticeStore race cases', () => {
  it('drops a write captured for a session the tab has left', () => {
    const s = createTabNoticeStore(fakeTimers().timers);
    const tab = { id: 'A', sessionId: 's2' };
    expect(s.post(tab, 's1', { id: 'x', severity: 'error', message: 'old' })).toBeNull();
    expect(s.getSnapshot('A')).toHaveLength(0);
  });

  it('drops a write for a closed tab', () => {
    const s = createTabNoticeStore(fakeTimers().timers);
    expect(s.post(undefined, 's1', { id: 'x', severity: 'error', message: 'm' })).toBeNull();
  });

  it('an old timer does not remove the newer notice with the same id', () => {
    const t = fakeTimers();
    const s = createTabNoticeStore(t.timers);
    const tab = { id: 'A', sessionId: 's' };
    s.post(tab, 's', { id: 'w', severity: 'info', message: 'one', autoDismissMs: 100 });
    const oldTimer = t.keys()[0];
    const token2 = s.post(tab, 's', { id: 'w', severity: 'info', message: 'two', autoDismissMs: 100 });
    t.fire(oldTimer); // already cleared → no-op
    expect(s.getSnapshot('A').map((n) => n.message)).toEqual(['two']);
    expect(s.getSnapshot('A')[0].token).toBe(token2);
    s.dismiss('A', 'w', token2! - 1); // stale caller token
    expect(s.getSnapshot('A')).toHaveLength(1);
  });

  it('a notice written before the tab had a session is adopted by the first session', () => {
    const s = createTabNoticeStore(fakeTimers().timers);
    const tab: { id: string; sessionId?: string } = { id: 'A' };
    s.post(tab, undefined, { id: 'mcp', severity: 'warning', message: 'mcp down' });
    tab.sessionId = 's1';
    s.syncSession('A', 's1');
    expect(visibleNotices(s.getSnapshot('A'), 's1')).toHaveLength(1);
    tab.sessionId = 's2';
    s.syncSession('A', 's2');
    expect(visibleNotices(s.getSnapshot('A'), 's2')).toHaveLength(0);
  });

  it('renameSession carries notices across an SDK compaction id change', () => {
    const s = createTabNoticeStore(fakeTimers().timers);
    s.post({ id: 'A', sessionId: 'old' }, 'old', { id: 'x', severity: 'warning', message: 'm' });
    s.renameSession('A', 'old', 'new');
    expect(visibleNotices(s.getSnapshot('A'), 'new')).toHaveLength(1);
  });

  it('clearTab drops notices and timers; another tab is untouched', () => {
    const t = fakeTimers();
    const s = createTabNoticeStore(t.timers);
    s.post({ id: 'A', sessionId: 's' }, 's', { id: 'x', severity: 'info', message: 'a', autoDismissMs: 10 });
    s.post({ id: 'B', sessionId: 's' }, 's', { id: 'x', severity: 'info', message: 'b' });
    s.clearTab('A');
    expect(s.getSnapshot('A')).toHaveLength(0);
    expect(s.timerCount()).toBe(0);
    expect(s.getSnapshot('B')).toHaveLength(1);
  });

  it('keeps at most MAX_NOTICES_PER_TAB', () => {
    const s = createTabNoticeStore(fakeTimers().timers);
    const tab = { id: 'A', sessionId: 's' };
    for (let i = 0; i < MAX_NOTICES_PER_TAB + 3; i++) s.post(tab, 's', { id: `n${i}`, severity: 'info', message: String(i) });
    expect(s.getSnapshot('A')).toHaveLength(MAX_NOTICES_PER_TAB);
  });
});

const TABS = ['A', 'B', 'C'] as const;
const SESSIONS = [undefined, 's1', 's2', 's3'] as const;
const IDS = ['x', 'y'] as const;

type Op =
  | { k: 'post'; tab: number; captured: number; id: number; auto: boolean }
  | { k: 'session'; tab: number; to: number }
  | { k: 'close'; tab: number }
  | { k: 'dismiss'; tab: number; id: number }
  | { k: 'fire' };

const opArb: fc.Arbitrary<Op> = fc.oneof(
  fc.record({ k: fc.constant('post' as const), tab: fc.nat(2), captured: fc.nat(3), id: fc.nat(1), auto: fc.boolean() }),
  fc.record({ k: fc.constant('session' as const), tab: fc.nat(2), to: fc.nat(3) }),
  fc.record({ k: fc.constant('close' as const), tab: fc.nat(2) }),
  fc.record({ k: fc.constant('dismiss' as const), tab: fc.nat(2), id: fc.nat(1) }),
  fc.record({ k: fc.constant('fire' as const) }),
);

describe('tabNoticeStore property: no cross-tab / cross-session notices', () => {
  it('holds over random interleavings', () => {
    fc.assert(fc.property(fc.array(opArb, { maxLength: 60 }), (ops) => {
      const t = fakeTimers();
      const s = createTabNoticeStore(t.timers);
      const live = new Map<string, { id: string; sessionId?: string }>();
      TABS.forEach((id) => live.set(id, { id }));
      for (const op of ops) {
        const tabId = 'tab' in op ? TABS[op.tab] : '';
        switch (op.k) {
          case 'post': {
            const tab = live.get(tabId);
            const captured = SESSIONS[op.captured];
            const r = s.post(tab, captured, { id: IDS[op.id], severity: 'info', message: 'm', autoDismissMs: op.auto ? 10 : undefined });
            if (!tab || tab.sessionId !== captured) expect(r).toBeNull();
            break;
          }
          case 'session': {
            const tab = live.get(tabId);
            if (tab) { tab.sessionId = SESSIONS[op.to]; s.syncSession(tabId, tab.sessionId); }
            break;
          }
          case 'close':
            live.delete(tabId); s.clearTab(tabId); break;
          case 'dismiss':
            s.dismiss(tabId, IDS[op.id]); break;
          case 'fire':
            t.fire(); break;
        }
        for (const id of TABS) {
          const list = s.getSnapshot(id);
          for (const n of list) expect(n.tabId).toBe(id);
          const tab = live.get(id);
          if (!tab) { expect(list).toHaveLength(0); continue; }
          for (const n of visibleNotices(list, tab.sessionId)) {
            expect(n.sessionId === undefined || n.sessionId === tab.sessionId).toBe(true);
          }
          expect(new Set(list.map((n) => n.id)).size).toBe(list.length);
        }
      }
    }), { numRuns: 300 });
  });
});
