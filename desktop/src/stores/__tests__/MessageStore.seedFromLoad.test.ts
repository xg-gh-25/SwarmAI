/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * MessageStore.seedFromLoad — OT01 dual-container collapse, rescue (b) at the
 * INITIAL-LOAD seam (run_2aea0237, Scope B).
 *
 * THE GAP THIS CLOSES: `loadSessionMessages` (ChatPage.tsx:515) seeds the store
 * from a paginated DB fetch via an UNCONDITIONAL `store.replace(formatted)`. That
 * call has 6 sites, several NOT gated on an empty store (session-open :1344,
 * task-drag :1656, active-restore :1525/:1777) — so the store can already hold a
 * FULLER last-assistant (a just-streamed answer) when a persist-lagged, SHORTER
 * DB row arrives. `replace` clobbers it → the truncation that TabView rescue (b)
 * (TabView.tsx:301-303) then has to paper over. The fix routes the initial-load
 * seed through a guarded path that applies the SAME "more-complete-wins" rule the
 * reconcile path already uses (`_mergePreservingInteractive` / `_textLen`), so the
 * store is never seeded SHORTER than the fuller content it already holds.
 *
 * Distinct from reconcile() (tail-merge of ChatMessage[] at 200ms turn-end):
 * seedFromLoad is a full INITIAL-LOAD of Message[] that must (1) behave exactly
 * like replace() when the store is empty or the load is not-shorter (the common
 * case), and (2) NOT truncate a fuller same-id last-assistant already in the store.
 *
 * Drives the REAL guard — no mock of the merge logic (GUI32/PIT13).
 * RED on current code (515 = unconditional replace, no seedFromLoad exists);
 * GREEN after the guarded seedFromLoad lands + 515 routes through it.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { MessageStore } from '../MessageStore';
import type { Message } from '../../types';

function makeMsg(id: string, role: 'user' | 'assistant' | 'system' = 'assistant', text = 'hello'): Message {
  return { id, role, content: [{ type: 'text', text }], timestamp: new Date().toISOString() };
}
function asstText(store: MessageStore, id: string): string {
  const m = store.messages.find((x) => x.id === id)!;
  return m.content.filter((b: any) => b.type === 'text').map((b: any) => b.text).join('');
}

describe('MessageStore.seedFromLoad — initial-load persist-lag guard (OT01 rescue b)', () => {
  let store: MessageStore;
  afterEach(() => store?.destroy());

  it('seeds an EMPTY store exactly like replace (common case — full initial load)', () => {
    store = new MessageStore({ sessionId: 'sess-1' });
    const loaded = [makeMsg('u1', 'user', 'q'), makeMsg('a1', 'assistant', 'the full answer')];
    store.seedFromLoad(loaded);
    expect(store.messages.map((m) => m.id)).toEqual(['u1', 'a1']);
    expect(asstText(store, 'a1')).toBe('the full answer');
    expect(store.messages.length).toBe(2);
  });

  it('a SHORTER same-id DB load does NOT clobber a fuller last-assistant already in the store', () => {
    const COMPLETE = 'COMPLETE streamed answer with the full tail intact';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('u1', 'user', 'q'));
    store.append(makeMsg('a1', 'assistant', COMPLETE)); // store holds the full reply (just streamed)
    // persist-lagged paginated load returns a shorter row for the SAME assistant id
    store.seedFromLoad([makeMsg('u1', 'user', 'q'), makeMsg('a1', 'assistant', 'COMPLETE str')]);
    // more-complete-wins: the fuller store content survives (this is the (b) fix)
    expect(asstText(store, 'a1')).toBe(COMPLETE);
  });

  it('a genuinely LONGER same-id DB load DOES win (guard is more-complete-wins, not local-always-wins)', () => {
    const PARTIAL = 'partial';
    const FULLER = 'partial answer plus the rest that the DB now has';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('a1', 'assistant', PARTIAL));
    store.seedFromLoad([makeMsg('a1', 'assistant', FULLER)]);
    expect(asstText(store, 'a1')).toBe(FULLER);
  });

  it('a DIFFERENT session (different last-assistant id) fully replaces — no cross-session merge', () => {
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('old-a', 'assistant', 'old session answer'));
    // switching to a session whose load has a different id set → full replace, old gone
    store.seedFromLoad([makeMsg('new-u', 'user', 'new q'), makeMsg('new-a', 'assistant', 'new answer')]);
    expect(store.messages.map((m) => m.id)).toEqual(['new-u', 'new-a']);
    expect(store.messages.find((m) => m.id === 'old-a')).toBeUndefined();
  });

  it('client_id correlation: a shorter UUID DB row does NOT clobber a fuller `${clientId}-asst` store bubble (PRE-rename race — the common OT01 case)', () => {
    const COMPLETE = 'the COMPLETE just-streamed answer, not yet renamed to its DB UUID';
    store = new MessageStore({ sessionId: 'sess-1' });
    // store holds the just-streamed bubble under its client-side id, before the
    // turn-end reconcile renames it to the persisted UUID
    store.append({ id: 'cid-1-asst', role: 'assistant', content: [{ type: 'text', text: COMPLETE }], timestamp: new Date().toISOString(), metadata: { client_id: 'cid-1-asst' } } as any);
    // persist-lagged paginated load returns the SAME message under its real UUID,
    // shorter, with client_id in metadata (ids DIFFER — exact-id match would miss it)
    store.seedFromLoad([
      { id: '550e8400-e29b-41d4-a716-446655440000', role: 'assistant', content: [{ type: 'text', text: 'the COMPLETE just' }], timestamp: new Date().toISOString(), metadata: { client_id: 'cid-1-asst' } } as any,
    ]);
    // more-complete-wins via client_id correlation: fuller store content survives
    expect(asstText(store, '550e8400-e29b-41d4-a716-446655440000')).toBe(COMPLETE);
  });

  it('NO-OP during streaming (phase-gated like replace)', () => {
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('a1', 'assistant', 'streaming content'));
    store.startStreaming('a1');
    store.seedFromLoad([makeMsg('a1', 'assistant', 'x')]); // must be ignored mid-stream
    expect(asstText(store, 'a1')).toBe('streaming content');
  });
});
