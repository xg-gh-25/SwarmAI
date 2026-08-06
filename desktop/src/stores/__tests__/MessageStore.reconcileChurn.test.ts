/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * MessageStore — rapid-reconcile-CHURN guard (#1 recurring "答到一半/白屏" class).
 *
 * THE GAP THIS CLOSES: MessageStore.test.ts covers a SINGLE queued reconcile and
 * the single in-flight-fetch-race drain. It does NOT cover the persist-lag guard
 * (`_mergePreservingInteractive` "MORE-COMPLETE CONTENT WINS", MessageStore.ts:647)
 * firing under MULTIPLE rapidly-arriving reconciles — which is the real shape of
 * the reconcile race: the 200ms turn-end reconcile, a tab-switch reconcile, and a
 * result-path reconcile can all fire within a few ms, several carrying a STALE,
 * shorter (or empty) DB row because the backend persist hasn't caught up.
 *
 * The invariant under test: no matter how many stale-shorter reconciles churn
 * through, the complete streamed answer already in the store is NEVER truncated/
 * blanked. The merge keeps the DB's canonical id/metadata but the longer (local)
 * content. This is the structural defense (94b87db8) that ended a 33-patch class.
 *
 * Drives the REAL merge: every reconcile goes through the real reconcile() →
 * _applyMerge → _mergePreservingInteractive. The ONLY seam is the fetchMessages
 * function (the DB boundary). No mock of the merge logic itself (GUI32/PIT13).
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { MessageStore } from '../MessageStore';
import { toDisplayMessage } from '../../pages/chat/utils';
import type { Message, ChatMessage } from '../../types';

function makeMsg(id: string, role: 'user' | 'assistant' | 'system' = 'assistant', text = 'hello'): Message {
  return { id, role, content: [{ type: 'text', text }], timestamp: new Date().toISOString() };
}
function makeChatMsg(id: string, role: 'user' | 'assistant' | 'system' = 'assistant', text = 'hello'): ChatMessage {
  return { id, sessionId: 'sess-1', role, content: [{ type: 'text', text }] as any, createdAt: new Date().toISOString() };
}
function asstText(store: MessageStore, id: string): string {
  const m = store.messages.find((x) => x.id === id)!;
  return m.content.filter((b: any) => b.type === 'text').map((b: any) => b.text).join('');
}

describe('MessageStore — rapid reconcile churn (persist-lag / more-complete-wins)', () => {
  let store: MessageStore;
  afterEach(() => store?.destroy());

  it('a stale shorter DB row does NOT clobber the complete streamed answer (single reconcile, at idle)', () => {
    const COMPLETE = 'COMPLETE streamed answer with the full tail intact';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('a1', 'assistant', COMPLETE)); // store holds the full reply
    // phase is idle → reconcile applies synchronously via _applyMerge
    store.reconcile([makeChatMsg('a1', 'assistant', 'COMPLETE str')]); // stale, shorter
    // more-complete-wins: local content survives, DB id/metadata kept
    expect(asstText(store, 'a1')).toBe(COMPLETE);
  });

  it('an EMPTY (not-yet-persisted) DB row does NOT blank the streamed answer', () => {
    const COMPLETE = 'the answer the user already sees on screen';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('a1', 'assistant', COMPLETE));
    store.reconcile([makeChatMsg('a1', 'assistant', '')]); // persist lag → empty row
    expect(asstText(store, 'a1')).toBe(COMPLETE); // not blanked
  });

  it('MANY rapid stale reconciles in a row never truncate — only a genuinely-longer row wins', () => {
    const COMPLETE = 'rapid churn answer body that is the complete streamed content';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('a1', 'assistant', COMPLETE));

    // 6 reconciles fire back-to-back (turn-end + tab-switch + result-path + retries),
    // each carrying a progressively-different STALE shorter/empty row.
    const staleRows = ['rapid churn', '', 'rapid churn answer', 'r', 'rapid churn answer body that is', ''];
    for (const t of staleRows) {
      store.reconcile([makeChatMsg('a1', 'assistant', t)]);
      // invariant holds after EVERY single churn step, not just at the end
      expect(asstText(store, 'a1')).toBe(COMPLETE);
    }

    // A legitimate server-side edit that ADDS content (strictly longer) DOES win —
    // proves the guard is "more-complete-wins", not "local-always-wins" (would be a
    // different, wrong invariant that ignores real DB edits).
    const LONGER = COMPLETE + ' — plus a server-appended correction.';
    store.reconcile([makeChatMsg('a1', 'assistant', LONGER)]);
    expect(asstText(store, 'a1')).toBe(LONGER);
  });

  it('reconcile-tail (#4): a 50-row TAIL that omits an older turn does NOT drop it, and a shorter tail row does NOT truncate the streamed answer', () => {
    // Models the #4 tail-fetch: reconcile now fetches only the newest RECONCILE_TAIL
    // rows, not full history. Two properties must hold together:
    //  (1) an OLDER local message absent from the tail is PRESERVED (_applyMerge
    //      "DB may be paginated" invariant, MessageStore.ts:652) — not dropped;
    //  (2) if the tail's row for the just-streamed turn is stale/shorter (persist
    //      lag, or a >50-raw-row turn whose START fell outside the tail), the
    //      complete local content still wins.
    const OLD = 'an older turn that is NOT in the newest-50 tail';
    const COMPLETE = 'the just-streamed complete answer for the current turn';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg('old-1', 'assistant', OLD));   // older history, outside the tail
    store.append(makeMsg('cur-1', 'assistant', COMPLETE)); // current turn, fully streamed

    // The tail fetch returns ONLY the current turn's row (old-1 not in the tail),
    // and that row is stale/shorter (persist lag or truncated >50-row turn).
    store.reconcile([makeChatMsg('cur-1', 'assistant', 'the just-streamed')]);

    // (1) older message survives despite being absent from the partial tail
    expect(store.messages.find((m) => m.id === 'old-1')).toBeDefined();
    expect(asstText(store, 'old-1')).toBe(OLD);
    // (2) current turn's complete content is not truncated by the shorter tail row
    expect(asstText(store, 'cur-1')).toBe(COMPLETE);
  });

  it('reconcile-tail MID-TURN CUT (#4 residual): a tail whose merged id DIFFERS from the local bubble id must NOT duplicate — client_id fallback matches', () => {
    // THE SCENARIO external review flagged my tests missed: the backend persists
    // ONE row per assistant SSE event, all carrying the SAME metadata.client_id
    // ("<clientId>-asst"). The endpoint's _merge_consecutive_assistant_messages
    // folds consecutive rows and uses the FIRST-IN-RESULT row's id as the bubble
    // id. So a 50-row tail that cuts mid-turn (returns A3..A5 instead of A1..A5)
    // yields a merged bubble with a DIFFERENT id (A3, not the full-load's A1) —
    // BUT it still carries the same client_id, because every row does.
    //
    // The frontend bubble's id is `local-<clientId>-asst` (never renamed to any
    // DB id). Matching is by CLIENT_ID (MessageStore:825-826), not by id — so the
    // id change is irrelevant and the fallback still correlates → ONE bubble.
    // If matching were id-based, this would produce two bubbles (the bug).
    const clientLocalId = 'local-XYZ-asst';
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg(clientLocalId, 'assistant', 'the complete streamed turn answer'));

    // Tail-cut merged DB row: id is the mid-turn A3 (NOT clientLocalId), but the
    // same client_id rides in metadata (every persisted row carried it).
    const midTurnRow: ChatMessage = {
      id: 'A3', sessionId: 'sess-1', role: 'assistant',
      content: [{ type: 'text', text: 'the complete streamed turn answer' }] as any,
      createdAt: new Date().toISOString(),
      metadata: { client_id: 'local-XYZ-asst' } as any,
    };
    store.reconcile([midTurnRow]);

    const assistants = store.messages.filter((m) => m.role === 'assistant');
    expect(assistants).toHaveLength(1); // NOT two — the residual duplicate does not occur
  });

  it('reconcile-tail MID-TURN CUT on a NO-client_id continuation turn: numeric placeholder dropped by H2, no duplicate', () => {
    // The one path where client_id CANNOT rescue: continuation turns
    // (continue_with_answer / continue_with_permission) persist assistant rows
    // WITHOUT a client_id (session_router.py:2477/2501), and the frontend
    // placeholder is a NUMERIC id (Date.now()+1). A tail cut here yields a merged
    // DB row with a fresh mid-turn id and NO client_id — so neither id-match nor
    // client_id fallback correlates. The H2 backstop (MessageStore:876-880) is
    // the guard: a numeric-id assistant placeholder is dropped once a real DB
    // assistant row exists (hasRealAssistant). A mid-turn cut still returns real
    // non-empty rows → hasRealAssistant=true → placeholder dropped → ONE bubble.
    const numericPlaceholderId = String(Date.now() + 1);
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg(numericPlaceholderId, 'assistant', 'continuation answer text'));

    // tail-cut merged DB row: fresh mid-turn id, NO client_id (continuation path)
    store.reconcile([makeChatMsg('A7', 'assistant', 'continuation answer text (canonical)')]);

    const assistants = store.messages.filter((m) => m.role === 'assistant');
    expect(assistants).toHaveLength(1); // H2 drops the numeric placeholder — no dup
    expect(assistants[0].id).toBe('A7'); // the canonical DB row is the surviving bubble
  });

  it('reconcile-tail INITIAL-LOAD entrance (run_03d6ee38): a bubble loaded via toDisplayMessage must carry its client_id so the FIRST mid-cut reconcile correlates — ONE bubble', () => {
    // The entrance the run_f62f4b80 fix MISSED: initial-load / tab-switch bubbles
    // come through toDisplayMessage, which USED TO strip metadata → they arrived
    // KEYLESS (id=canonical DB id, no client_id). A first mid-turn-cut reconcile
    // (merged DB id A3 ≠ the loaded A1, because the backend uses the first-in-tail
    // row id) then matched nothing (id≠, no key, A3 is a UUID so H2 skips) →
    // duplicate on the VERY FIRST reconcile after opening the session (no
    // accumulation needed). Fix: toDisplayMessage now preserves metadata.client_id.
    // This test drives the REAL initial-load converter (toDisplayMessage), not a
    // hand-built message — if the converter drops the key again, this goes RED.
    const dbRowCamel = {
      id: 'A1', sessionId: 'sess-1', role: 'assistant',
      content: [{ type: 'text', text: 'the full turn answer' }],
      createdAt: new Date().toISOString(),
      metadata: { client_id: 'local-XYZ-asst' },
    } as unknown as Parameters<typeof toDisplayMessage>[0];
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(toDisplayMessage(dbRowCamel));          // initial-load path (real converter)
    // the loaded bubble must have retained the correlation key
    expect(store.messages.find((m) => m.role === 'assistant')?.metadata?.client_id).toBe('local-XYZ-asst');

    // first tail reconcile cuts mid-turn → merged id A3, same client_id
    const midCut: ChatMessage = {
      id: 'A3', sessionId: 'sess-1', role: 'assistant',
      content: [{ type: 'text', text: 'answer tail only' }] as any,
      createdAt: new Date().toISOString(), metadata: { client_id: 'local-XYZ-asst' } as any,
    };
    store.reconcile([midCut]);
    expect(store.messages.filter((m) => m.role === 'assistant')).toHaveLength(1); // ONE — no dup
  });

  it('reconcile-tail SECOND mid-turn-cut (run_f62f4b80): rename on #1 must NOT consume the correlation key — #2 cut still matches, ONE bubble', () => {
    // THE BUG two prior test-passes missed (both stopped at ONE reconcile): the
    // client_id fallback is one-shot unless the correlation key SURVIVES the
    // rename. Reconcile #1 (full group in tail) matches local-XYZ-asst and
    // _mergePreservingInteractive renames the bubble to the DB id A1. If A1 does
    // not RETAIN client_id, the clientId index (which held only local-*) no longer
    // has it → reconcile #2 (mid-turn cut, merged id A3, same client_id) matches
    // nothing (id≠, client_id-gone, A3 is a UUID so H2 skips) → A3 added new + A1
    // kept = TWO bubbles. Fix: carry client_id onto the merged msg + index by
    // carried client_id + exclude-from-Pass2 by matched index.
    const localId = 'local-XYZ-asst';
    const withCid = (id: string, text: string): ChatMessage => ({
      id, sessionId: 'sess-1', role: 'assistant',
      content: [{ type: 'text', text }] as any, createdAt: new Date().toISOString(),
      metadata: { client_id: localId } as any,
    });
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg(localId, 'assistant', 'the full turn answer'));

    // #1: full group in tail → renames local-XYZ-asst → A1
    store.reconcile([withCid('A1', 'the full turn answer')]);
    const afterFirst = store.messages.filter((m) => m.role === 'assistant');
    expect(afterFirst).toHaveLength(1);
    expect(afterFirst[0].id).toBe('A1');            // renamed to DB id (display id stays canonical)
    expect(afterFirst[0].metadata?.client_id).toBe(localId); // but client_id RETAINED as correlation key

    // #2: tail cuts mid-turn → merged id A3 (≠A1), same client_id
    store.reconcile([withCid('A3', 'answer tail only')]);
    const afterSecond = store.messages.filter((m) => m.role === 'assistant');
    expect(afterSecond).toHaveLength(1);            // ONE bubble — the residual duplicate is closed
  });

  // Coverage map (run_f62f4b80 fix has 4 coordinated changes): the test ABOVE
  // ("SECOND mid-turn-cut") guards change-2 (index by carried client_id) — RED if
  // disabled. The test BELOW guards change-3 (carryCid on the id-match branch) —
  // a no-op id-match reconcile must not re-drop the key. Change-4 (matchedLocalIdx
  // Pass-2 exclusion) is exercised by BOTH (a renamed bubble must not re-insert).
  it('reconcile-tail cut after a NO-OP id-match reconcile (skeptic ordering, guards change-3): clientid→id-match→cut still ONE bubble', () => {
    // The ordering that defeated the plan's literal 2-change version: after #1
    // renames to A1, a #2 no-cut reconcile hits the ID-MATCH branch (A1==A1) which
    // ALSO returns {...dbMsg} — if that branch drops client_id, the key is
    // re-armed-then-lost and #3's cut duplicates. The fix carries client_id in the
    // id-match branch too.
    const localId = 'local-QRS-asst';
    const withCid = (id: string, text: string): ChatMessage => ({
      id, sessionId: 'sess-1', role: 'assistant',
      content: [{ type: 'text', text }] as any, createdAt: new Date().toISOString(),
      metadata: { client_id: localId } as any,
    });
    store = new MessageStore({ sessionId: 'sess-1' });
    store.append(makeMsg(localId, 'assistant', 'full turn'));
    store.reconcile([withCid('B1', 'full turn')]);   // #1 clientid → rename to B1
    store.reconcile([withCid('B1', 'full turn')]);   // #2 id-match no-op (must keep client_id)
    store.reconcile([withCid('B4', 'turn tail')]);   // #3 mid-turn cut
    expect(store.messages.filter((m) => m.role === 'assistant')).toHaveLength(1);
  });

  it('churn across an async in-flight fetch: stale read loses, drained re-run with full DB lands (drive real _fetchAndReconcile)', async () => {
    // Couples the churn with the async fetch path: the first (in-flight) fetch
    // returns a STALE shorter row; a reconcile re-queues behind it; the drained
    // re-run sees the FULL DB content. The complete local content must survive the
    // whole sequence regardless of resolution order.
    const COMPLETE = 'FULL streamed reply that must never truncate mid-churn';
    let call = 0;
    let firstResolve!: (m: ChatMessage[]) => void;
    const firstFetch = new Promise<ChatMessage[]>((r) => { firstResolve = r; });
    const fetchMessages = () => {
      call += 1;
      if (call === 1) return firstFetch;                       // in-flight: stale
      return Promise.resolve([makeChatMsg('a1', 'assistant', COMPLETE + ' [db-final]')]);
    };
    store = new MessageStore({ sessionId: 'sess-1', fetchMessages });
    store.append(makeMsg('a1', 'assistant', COMPLETE));
    store.startStreaming('a1');
    store.reconcile([makeChatMsg('a1', 'assistant', 'queued')]); // queued thunk
    store.endStreaming();                                         // flush → inFlight=1
    store.reconcile([makeChatMsg('a1', 'assistant', COMPLETE + ' [db-final]')]); // inFlight>0 → re-queue
    firstResolve([makeChatMsg('a1', 'assistant', 'trunc')]);      // stale in-flight resolves

    await vi.waitFor(() => {
      // drained re-run lands the strictly-longer DB content
      expect(asstText(store, 'a1')).toBe(COMPLETE + ' [db-final]');
    });
    // at no point did the truncated 'trunc' row win
    expect(asstText(store, 'a1')).not.toBe('trunc');
    expect(call).toBeGreaterThanOrEqual(2); // proves the re-queued thunk drained
  });
});
