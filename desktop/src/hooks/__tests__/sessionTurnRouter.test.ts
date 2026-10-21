/**
 * sessionTurnRouter — which source renders each turn event of a tab.
 *
 * Invariants under test: one render source while the subscription is up (I6),
 * each eventSeq rendered once (I3), buffering while a send is unbound (F3),
 * begin on turn_start, complete exactly once, detach on POST loss (F4), resync.
 */
import { describe, it, expect } from 'vitest';
import {
  createTabTurnState,
  onPostSend,
  routePostEvent,
  routeSubEvent,
  onPostComplete,
  onPostLost,
  onSubDisconnected,
  syncTurnStateSession,
  type TurnEnvelope,
  type TurnAction,
} from '../sessionTurnRouter';

const ev = (turnId: string, eventSeq: number, type = 'text_delta'): TurnEnvelope =>
  ({ type, turnId, eventSeq, sessionId: 'S' }) as TurnEnvelope;
const kinds = (a: TurnAction[]) => a.map((x) => (x.type === 'render' || x.type === 'begin' ? `${x.type}:${x.event.eventSeq}` : x.type));

describe('sessionTurnRouter', () => {
  it('renders a pre-turn POST reply (no turnId) from the POST', () => {
    const st = createTabTurnState();
    onPostSend(st);
    const busy = { type: 'error', code: 'SESSION_BUSY' } as TurnEnvelope;
    expect(kinds(routePostEvent(st, busy, true))).toEqual(['render:undefined']);
    expect(kinds(onPostComplete(st, true))).toEqual(['postComplete']);
  });

  it('subscription connected: POST copies bind but never render; subscription renders each seq once', () => {
    const st = createTabTurnState();
    onPostSend(st);
    expect(routePostEvent(st, ev('T', 1, 'turn_start'), true)).toEqual([]);
    expect(st.bound?.turnId).toBe('T');
    expect(routeSubEvent(st, ev('T', 1, 'turn_start'))).toEqual([{ type: 'render', event: ev('T', 1, 'turn_start'), origin: 'post' }]);
    expect(kinds(routeSubEvent(st, ev('T', 2)))).toEqual(['render:2']);
    expect(routePostEvent(st, ev('T', 2), true)).toEqual([]);
    expect(routeSubEvent(st, ev('T', 2))).toEqual([]); // duplicate seq
    expect(onPostComplete(st, true)).toEqual([]); // waits for turn_end
    expect(kinds(routeSubEvent(st, ev('T', 3, 'turn_end')))).toEqual(['complete']);
    expect(st.bound?.completed).toBe(true);
  });

  it('subscription copies that arrive before the POST binds are buffered, then rendered in order', () => {
    const st = createTabTurnState();
    onPostSend(st);
    expect(routeSubEvent(st, ev('T', 1, 'turn_start'))).toEqual([]);
    expect(routeSubEvent(st, ev('T', 2))).toEqual([]);
    expect(kinds(routePostEvent(st, ev('T', 1, 'turn_start'), true))).toEqual(['render:1', 'render:2']);
    expect(st.buffer).toEqual([]);
    expect(kinds(routeSubEvent(st, ev('T', 3)))).toEqual(['render:3']);
  });

  it('subscription down: the POST renders (fallback) and completes on [DONE]', () => {
    const st = createTabTurnState();
    onPostSend(st);
    expect(kinds(routePostEvent(st, ev('T', 1, 'turn_start'), false))).toEqual(['render:1']);
    expect(kinds(routePostEvent(st, ev('T', 2), false))).toEqual(['render:2']);
    expect(kinds(onPostComplete(st, false))).toEqual(['complete']);
    // the subscription reconnects and replays: nothing renders twice, no second complete
    expect(routeSubEvent(st, ev('T', 2))).toEqual([]);
    expect(routeSubEvent(st, ev('T', 3, 'turn_end'))).toEqual([]);
  });

  it('switching source mid-turn neither repeats nor skips (seq gate)', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), false);
    routePostEvent(st, ev('T', 2), false);
    // subscription comes up and replays 1..3; only 3 is new
    expect(kinds(routeSubEvent(st, ev('T', 1, 'turn_start')))).toEqual([]);
    expect(kinds(routeSubEvent(st, ev('T', 2)))).toEqual([]);
    expect(kinds(routeSubEvent(st, ev('T', 3)))).toEqual(['render:3']);
    // POST copy of 3 while subscription connected: dropped
    expect(routePostEvent(st, ev('T', 3), true)).toEqual([]);
  });

  it('POST lost mid-turn with the subscription up detaches; the subscription finishes the turn', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), true);
    routeSubEvent(st, ev('T', 1, 'turn_start'));
    expect(onPostLost(st, true).verdict).toBe('detach');
    expect(kinds(routeSubEvent(st, ev('T', 2)))).toEqual(['render:2']);
    expect(kinds(routeSubEvent(st, ev('T', 3, 'turn_end')))).toEqual(['complete']);
  });

  it('POST lost with the subscription down falls back to the existing disconnect handling', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), false);
    expect(onPostLost(st, false).verdict).toBe('fallback');
  });

  it('POST lost before binding: detach only when the subscription already brought the turn start', () => {
    const st = createTabTurnState();
    onPostSend(st);
    expect(onPostLost(st, true).verdict).toBe('fallback');
    const st2 = createTabTurnState('S'); // a tab of session S: the turn start is its own
    onPostSend(st2);
    routeSubEvent(st2, ev('T', 1, 'turn_start'));
    const lost = onPostLost(st2, true);
    expect(lost.verdict).toBe('detach');
    expect(kinds(lost.actions)).toEqual(['begin:1']);
  });

  it('an idle tab begins a subscription turn on turn_start and completes it on turn_end', () => {
    const st = createTabTurnState('S');
    expect(kinds(routeSubEvent(st, ev('I', 5, 'turn_start')))).toEqual(['begin:5']);
    expect(routeSubEvent(st, ev('I', 6))).toEqual([{ type: 'render', event: ev('I', 6), origin: 'sub' }]);
    expect(routeSubEvent(st, ev('I', 7, 'turn_end'))).toEqual([{ type: 'complete', origin: 'sub' }]);
    expect(routeSubEvent(st, ev('I', 7, 'turn_end'))).toEqual([]);
  });

  it('a turn whose start was never seen is not rendered (the reconcile shows it)', () => {
    const st = createTabTurnState();
    expect(routeSubEvent(st, ev('X', 9))).toEqual([]);
    expect(routeSubEvent(st, ev('X', 10, 'turn_end'))).toEqual([]);
    expect(st.bound).toBeUndefined();
  });

  it('a second turn arriving while one is running waits, then begins after the first completes', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('U', 1, 'turn_start'), true);
    routeSubEvent(st, ev('U', 1, 'turn_start'));
    // the CLI starts its own turn right after the user turn (no idle in between)
    expect(routeSubEvent(st, ev('U', 3, 'turn_end'))).toEqual([{ type: 'complete', origin: 'post' }]);
    const st2 = createTabTurnState();
    onPostSend(st2);
    routePostEvent(st2, ev('U', 1, 'turn_start'), true);
    routeSubEvent(st2, ev('U', 1, 'turn_start'));
    // out of order: the next turn's start shows up before the user turn's end
    expect(routeSubEvent(st2, ev('I', 4, 'turn_start'))).toEqual([]);
    expect(kinds(routeSubEvent(st2, ev('U', 3, 'turn_end')))).toEqual(['complete', 'begin:4']);
  });

  it('a pre-turn POST reply while a subscription turn runs does not complete that turn', () => {
    const st = createTabTurnState('S');
    routeSubEvent(st, ev('I', 1, 'turn_start'));
    onPostSend(st);
    expect(onPostComplete(st, true)).toEqual([]);
    expect(st.bound?.completed).toBe(false);
  });

  it('resync of a session whose turn ended completes the bound turn and asks for a DB reload', () => {
    const st = createTabTurnState('S');
    routeSubEvent(st, ev('I', 1, 'turn_start'));
    const out = routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 40, activeTurnId: null } as unknown as TurnEnvelope);
    expect(kinds(out)).toEqual(['complete', 'resync']);
    expect(st.lastSeq).toBe(40);
    expect(routeSubEvent(st, ev('I', 39))).toEqual([]);
  });

  it('resync mid-turn, then the replay: the running turn is rebuilt, one completion, one copy (K3)', () => {
    const st = createTabTurnState('S');
    routeSubEvent(st, ev('I', 10, 'turn_start'));
    routeSubEvent(st, ev('I', 11));
    // the server: the cursor fell out of the ring; turn I is still running from seq 10
    const rs = routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 9, activeTurnId: 'I' } as unknown as TurnEnvelope);
    expect(rs).toEqual([{ type: 'reset', origin: 'sub' }, { type: 'resync' }]);
    // the full replay follows: turn_start begins the turn afresh, each event once
    const replay = [ev('I', 10, 'turn_start'), ev('I', 11), ev('I', 12), ev('I', 13, 'turn_end')];
    const acts = replay.flatMap((e) => routeSubEvent(st, e));
    expect(kinds(acts)).toEqual(['begin:10', 'render:11', 'render:12', 'complete']);
    expect(routeSubEvent(st, ev('I', 13, 'turn_end'))).toEqual([]);
  });

  it('a POST turn rebuilt after a resync ignores its POST copies and its [DONE]', () => {
    const st = createTabTurnState('S');
    onPostSend(st);
    routePostEvent(st, ev('T', 5, 'turn_start'), true);
    routeSubEvent(st, ev('T', 5, 'turn_start'));
    const rs = routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 4, activeTurnId: 'T' } as unknown as TurnEnvelope);
    expect(rs).toEqual([{ type: 'reset', origin: 'post' }, { type: 'resync' }]);
    expect(routePostEvent(st, ev('T', 6), true)).toEqual([]); // the subscription replays it
    expect(kinds(routeSubEvent(st, ev('T', 5, 'turn_start')))).toEqual(['begin:5']);
    expect(onPostComplete(st, true)).toEqual([]);
    expect(kinds(routeSubEvent(st, ev('T', 6)))).toEqual(['render:6']);
    expect(kinds(routeSubEvent(st, ev('T', 7, 'turn_end')))).toEqual(['complete']);
  });

  it('a pre-turn reply never begins or counts another session\'s buffered turn (K1)', () => {
    const st = createTabTurnState();
    onPostSend(st); // a new tab: no session yet
    routeSubEvent(st, { ...ev('X', 50, 'turn_start'), sessionId: 'OTHER' } as TurnEnvelope);
    routeSubEvent(st, { ...ev('X', 51), sessionId: 'OTHER' } as TurnEnvelope);
    expect(st.lastSeq).toBe(0);
    // the send gets a pre-turn reply (QUEUE_TIMEOUT): nothing of OTHER may begin
    expect(kinds(onPostComplete(st, true))).toEqual(['postComplete']);
    expect(st.bound).toBeUndefined();
    expect(st.buffer).toEqual([]);
    // and with no send pending, OTHER's later events are not this tab's
    expect(routeSubEvent(st, { ...ev('Y', 60, 'turn_start'), sessionId: 'OTHER' } as TurnEnvelope)).toEqual([]);
    // a lost unbound POST behaves the same
    const st2 = createTabTurnState();
    onPostSend(st2);
    routeSubEvent(st2, { ...ev('X', 50, 'turn_start'), sessionId: 'OTHER' } as TurnEnvelope);
    const lost = onPostLost(st2, true);
    expect(lost.verdict).toBe('fallback');
    expect(lost.actions).toEqual([]);
  });

  it('a lost POST and a subscription blip do not complete a running turn (K2)', () => {
    const st = createTabTurnState('S');
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), true);
    routeSubEvent(st, ev('T', 1, 'turn_start'));
    expect(onPostLost(st, true).verdict).toBe('detach');
    expect(onSubDisconnected(st)).toEqual([]);
    expect(st.bound?.completed).toBe(false);
    // the subscription reconnects and replays: the turn goes on, then ends once
    expect(kinds(routeSubEvent(st, ev('T', 2)))).toEqual(['render:2']);
    expect(kinds(routeSubEvent(st, ev('T', 3, 'turn_end')))).toEqual(['complete']);
  });

  it('a subscription that stalls then drops leaves no gap: undelivered POST copies render (shadow)', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), true);
    routeSubEvent(st, ev('T', 1, 'turn_start'));
    // the subscription stalls: POST keeps receiving 2..4, all suppressed
    routePostEvent(st, ev('T', 2), true);
    routePostEvent(st, ev('T', 3), true);
    routePostEvent(st, ev('T', 4), true);
    // the stall is detected: the subscription is down
    expect(kinds(onSubDisconnected(st))).toEqual(['render:2', 'render:3', 'render:4']);
    expect(kinds(routePostEvent(st, ev('T', 5), false))).toEqual(['render:5']);
    // the subscription comes back and replays 2..6: only 6 is new
    expect(routeSubEvent(st, ev('T', 3))).toEqual([]);
    expect(kinds(routeSubEvent(st, ev('T', 6)))).toEqual(['render:6']);
  });

  it('subscription drops after the POST is done: undelivered copies render, then the turn completes', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), true);
    routeSubEvent(st, ev('T', 1, 'turn_start'));
    routePostEvent(st, ev('T', 2), true);
    expect(onPostComplete(st, true)).toEqual([]);
    expect(kinds(onSubDisconnected(st))).toEqual(['render:2', 'complete']);
    expect(st.bound?.completed).toBe(true);
  });

  it('resync completes a bound POST turn the server says has ended', () => {
    const st = createTabTurnState();
    onPostSend(st);
    routePostEvent(st, ev('T', 1, 'turn_start'), true);
    onPostComplete(st, true);
    const out = routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 30, activeTurnId: null } as unknown as TurnEnvelope);
    expect(out).toEqual([{ type: 'complete', origin: 'post' }, { type: 'resync' }]);
  });

  it('a resync of another session is not this tab\'s', () => {
    const st = createTabTurnState('S');
    routeSubEvent(st, ev('I', 1, 'turn_start'));
    expect(routeSubEvent(st, { type: 'resync', sessionId: 'OTHER', eventSeq: 9, activeTurnId: null } as unknown as TurnEnvelope)).toEqual([]);
    expect(st.bound?.completed).toBe(false);
  });

  it('binding drops buffered events of another session (two new tabs sending at once)', () => {
    const st = createTabTurnState();
    onPostSend(st);
    // while this tab has no session it is handed every new-session event
    routeSubEvent(st, { ...ev('X', 1, 'turn_start'), sessionId: 'OTHER' } as TurnEnvelope);
    routeSubEvent(st, { ...ev('X', 2), sessionId: 'OTHER' } as TurnEnvelope);
    routeSubEvent(st, { ...ev('Y', 1, 'turn_start'), sessionId: 'MINE' } as TurnEnvelope);
    const out = routePostEvent(st, { ...ev('Y', 1, 'turn_start'), sessionId: 'MINE' } as TurnEnvelope, true);
    expect(kinds(out)).toEqual(['render:1']);
    expect(st.buffer).toEqual([]);
    expect(st.sessionId).toBe('MINE');
  });

  it('a tab that moves to another session is re-baselined once no turn is open', () => {
    const st = createTabTurnState('A');
    routeSubEvent(st, { ...ev('a', 800, 'turn_start'), sessionId: 'A' } as TurnEnvelope);
    expect(syncTurnStateSession(st, 'B')).toBe(false); // turn of A still open
    routeSubEvent(st, { ...ev('a', 801, 'turn_end'), sessionId: 'A' } as TurnEnvelope);
    expect(syncTurnStateSession(st, 'B')).toBe(true);
    expect(st.lastSeq).toBe(0);
    expect(kinds(routeSubEvent(st, { ...ev('b', 1, 'turn_start'), sessionId: 'B' } as TurnEnvelope))).toEqual(['begin:1']);
    expect(syncTurnStateSession(st, 'B')).toBe(false);
  });

  it('a rebuild whose turn ended before its replay is finished by the next resync', () => {
    const st = createTabTurnState('S');
    onPostSend(st);
    routePostEvent(st, ev('T', 5, 'turn_start'), true);
    routeSubEvent(st, ev('T', 5, 'turn_start'));
    routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 4, activeTurnId: 'T' } as unknown as TurnEnvelope);
    // the subscription drops before the replay; T ends meanwhile; the reconnect resyncs
    const out = routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 30, activeTurnId: null } as unknown as TurnEnvelope);
    expect(out).toEqual([{ type: 'complete', origin: 'post' }, { type: 'resync' }]);
    expect(st.rebuild).toBeUndefined();
  });

  it('a rebuild falls back to the POST while the subscription is down', () => {
    const st = createTabTurnState('S');
    onPostSend(st);
    routePostEvent(st, ev('T', 5, 'turn_start'), true);
    routeSubEvent(st, ev('T', 5, 'turn_start'));
    routeSubEvent(st, { type: 'resync', sessionId: 'S', eventSeq: 4, activeTurnId: 'T' } as unknown as TurnEnvelope);
    expect(kinds(routePostEvent(st, ev('T', 8), false))).toEqual(['render:8']);
    expect(st.rebuild).toBeUndefined();
    expect(kinds(onPostComplete(st, false))).toEqual(['complete']);
  });
});
