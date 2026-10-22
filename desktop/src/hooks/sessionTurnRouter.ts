/**
 * Session turn router — decides which source renders each turn event of a tab.
 *
 * A turn reaches a tab from up to two sources: the POST stream of the send that
 * started it (when this window sent it), and the window's session event
 * subscription (services/sessionEvents.ts), which carries EVERY turn of the
 * session — drained pending messages, CLI-initiated turns after a background
 * task, channel turns, continuations — and keeps going when a POST stream drops.
 * Both deliver the same backend envelopes (`sessionId`, `turnId`, `eventSeq`), so
 * this module is pure bookkeeping over one record per tab:
 *
 * - **One render target.** Every turn renders into the bubble the turn's begin
 *   chose (its own, or — a continuation, a CLI-initiated turn — the assistant
 *   bubble before it), through one handler, whichever source delivers an event.
 *   A send's turn begins at the first of its `turn_start` copies: the
 *   subscription's, or the POST's (the backend yields the stamped turn_start to
 *   the POST too). Only a POST turn whose turn_start this tab never saw renders
 *   through the POST's own handler.
 * - **One render source (I6).** While the subscription is connected it delivers
 *   every turn event; the POST copy only binds the send to its `turnId`. POST
 *   copies are delivered only while the subscription is down (fallback). Events
 *   without a `turnId` (pre-turn replies: validation, SESSION_BUSY, queued,
 *   POST-side errors) always render from the POST that received them.
 * - **Each eventSeq once (I3).** `lastSeq` is the highest eventSeq rendered for
 *   the tab's session from either source; anything at or below it is dropped, so
 *   a switch of source mid-turn (subscription drops / reconnects and replays)
 *   neither repeats nor skips.
 * - **Buffer while unbound (F3).** A subscription event that arrives while this
 *   tab's own POST send has not bound its `turnId` yet, or while another turn is
 *   still running in the tab, is held; it is replayed once the POST binds (same
 *   turn → rendered in order) or the running turn completes (a new turn → begun).
 * - **Begin.** An idle tab that receives a `turn_start` begins a turn of its own
 *   (the caller creates the placeholder bubble and handler). An event of a turn
 *   whose start this tab never saw is not rendered: the polling reconcile shows it.
 * - **Complete once.** A turn completes on the subscription's `turn_end`, or on
 *   the POST's [DONE] when the POST is the render source; the second signal is a
 *   no-op. A POST [DONE] while the subscription renders waits for `turn_end`.
 * - **Detach (F4).** A POST stream lost mid-turn while the subscription is
 *   connected is not a disconnect: the subscription keeps rendering the turn.
 * - **Shadow.** POST copies the subscription is expected to render are kept (not
 *   rendered) until the subscription renders past them. If the subscription goes
 *   down mid-turn, `onSubDisconnected` renders the ones it never delivered, so a
 *   subscription that stalled before dropping cannot leave a gap.
 * - **Ownership (I7).** The caller hands every subscription event to every tab;
 *   `ownership` decides: an event of the tab's bound turn or of its session is
 *   its own; while the tab has no session and a send is pending, another
 *   session's event is only a candidate (held, never counted or begun); anything
 *   else is foreign and dropped. Every drain re-applies it.
 * - **Resync.** A resync names the session's active turn. The bound turn, if it is
 *   that turn, is not completed but rebuilt: the subscription replays it from its
 *   turn_start (its POST copies are ignored meanwhile); a bound turn that is not
 *   the active one has ended and completes.
 * - **Session change.** `syncTurnStateSession` re-baselines the record when the
 *   tab moves to another session (eventSeq is per session) once no turn is open.
 *
 * Every function mutates the given record and returns the actions the caller
 * performs, in order. No React, no I/O.
 */
import type { StreamEvent } from '../types';

export interface TurnEnvelope extends StreamEvent {
  sessionId?: string;
  turnId?: string;
  turnClientId?: string;
  eventSeq?: number;
}

export interface BoundTurn {
  turnId: string;
  /** who started the turn in this tab: its own POST send, or the subscription */
  origin: TurnOrigin;
  completed: boolean;
  /** the POST of this turn delivered [DONE] */
  postDone: boolean;
  /** the POST of this turn was lost before [DONE] (the turn may still be running) */
  postLost?: boolean;
}

export interface TabTurnState {
  /** the session lastSeq counts in */
  sessionId?: string;
  lastSeq: number;
  /** a POST send of this tab is in flight and has not bound a turnId yet */
  postPending: boolean;
  bound?: BoundTurn;
  buffer: TurnEnvelope[];
  /** POST copies of the bound turn not rendered because the subscription renders them */
  shadow: TurnEnvelope[];
  /** a turn the subscription is replaying from its turn_start after a resync */
  rebuild?: string;
  rebuildOrigin?: TurnOrigin;
  /** the turn this tab's latest POST bound */
  postTurnId?: string;
}

export type TurnOrigin = 'post' | 'sub';

/** turn_start.source — mirrors the backend's session_events.TURN_SOURCES */
export type TurnSource = 'chat' | 'drain' | 'channel' | 'injected' | 'answer' | 'permission';

export type TurnAction =
  /** render with the handler of the turn's origin (the POST's or the subscription's) */
  | { type: 'render'; event: TurnEnvelope; origin: TurnOrigin }
  | { type: 'begin'; event: TurnEnvelope }
  /** complete the bound turn with the completion handler of its origin */
  | { type: 'complete'; origin: TurnOrigin }
  /** run the POST's own completion for a pre-turn reply (no turn was bound) */
  | { type: 'postComplete' }
  /** reload the session from the DB: the subscription could not replay it */
  | { type: 'resync' }
  /** drop the handlers of a turn that will be replayed from its start (no completion) */
  | { type: 'reset'; origin: TurnOrigin };

export function createTabTurnState(sessionId?: string): TabTurnState {
  return { sessionId, lastSeq: 0, postPending: false, buffer: [], shadow: [] };
}

/**
 * Keep the record on the tab's current session. Returns true when it was reset
 * (the tab moved to another session and no turn of the old one is still open);
 * the caller then drops its subscription-turn handlers too.
 */
export function syncTurnStateSession(st: TabTurnState, sessionId: string | undefined): boolean {
  if (!sessionId || st.sessionId === sessionId) return false;
  if (st.sessionId === undefined) { st.sessionId = sessionId; return false; }
  if (st.bound && !st.bound.completed) return false;
  const pending = st.postPending;
  Object.assign(st, createTabTurnState(sessionId), { postPending: pending });
  return true;
}

const isSeq = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);

/** A new POST send of this tab is starting. */
export function onPostSend(st: TabTurnState): void {
  st.postPending = true;
  if (st.bound?.completed) st.bound = undefined;
}

/** Whose event is it, for this tab (I7). */
export function ownership(st: TabTurnState, ev: TurnEnvelope): 'own' | 'candidate' | 'foreign' {
  if (ev.turnId && st.bound?.turnId === ev.turnId) return 'own';
  if (st.sessionId) return ev.sessionId === st.sessionId ? 'own' : 'foreign';
  return st.postPending ? 'candidate' : 'foreign';
}

function drainBuffer(st: TabTurnState): TurnAction[] {
  const held = st.buffer;
  st.buffer = [];
  const out: TurnAction[] = [];
  for (const ev of held) out.push(...routeSubEvent(st, ev));
  return out;
}

function complete(st: TabTurnState): TurnAction[] {
  if (!st.bound || st.bound.completed) return [];
  st.bound.completed = true;
  return [{ type: 'complete', origin: st.bound.origin }, ...drainBuffer(st)];
}

/** One event from this tab's own POST stream. */
export function routePostEvent(
  st: TabTurnState,
  ev: TurnEnvelope,
  subConnected: boolean,
): TurnAction[] {
  if (!ev.turnId) return [{ type: 'render', event: ev, origin: 'post' }];
  st.postTurnId = ev.turnId;
  if (st.rebuild === ev.turnId) {
    if (subConnected) return []; // the subscription is replaying it
    // the subscription went away before its replay: the POST carries the turn again
    st.rebuild = undefined;
    st.rebuildOrigin = undefined;
  }
  const out: TurnAction[] = [];
  if (!st.bound || st.bound.turnId !== ev.turnId) {
    st.postPending = false;
    st.shadow = [];
    if (ev.sessionId) st.sessionId = ev.sessionId;
    // subscription copies of this turn that arrived first; events another
    // session's new turn left here while this send had no session are dropped
    const mine = st.buffer.filter((b) => b.turnId === ev.turnId);
    st.buffer = st.buffer.filter((b) => b.turnId !== ev.turnId && (!ev.sessionId || b.sessionId === ev.sessionId));
    if (mine.length && mine[0].type === 'turn_start') {
      // the subscription saw the start first: it begins the turn (one target);
      // it is still this POST's send, whose [DONE] is still to come
      for (const b of mine) out.push(...routeSubEvent(st, b));
      if (st.bound && st.bound.turnId === ev.turnId) st.bound.postDone = false;
    } else if (ev.type === 'turn_start') {
      // this POST copy is the turn's start: begin it as the subscription would
      if (isSeq(ev.eventSeq) && ev.eventSeq > st.lastSeq) st.lastSeq = ev.eventSeq;
      st.bound = { turnId: ev.turnId, origin: 'sub', completed: false, postDone: false };
      out.push({ type: 'begin', event: ev });
      for (const b of mine) out.push(...routeSubEvent(st, b));
      return out;
    } else {
      st.bound = { turnId: ev.turnId, origin: 'post', completed: false, postDone: false };
      for (const b of mine) out.push(...routeSubEvent(st, b));
    }
  }
  if (!st.bound || st.bound.turnId !== ev.turnId || st.bound.completed) return out;
  if (subConnected) {
    // the subscription delivers it (I6); keep the copy until it has
    if (isSeq(ev.eventSeq) && ev.eventSeq > st.lastSeq) st.shadow.push(ev);
    return out;
  }
  if (isSeq(ev.eventSeq)) {
    if (ev.eventSeq <= st.lastSeq) return out;
    st.lastSeq = ev.eventSeq;
  }
  if (ev.type === 'turn_end') return [...out, ...complete(st)];
  out.push({ type: 'render', event: ev, origin: st.bound.origin });
  return out;
}

/** One event from the window's session subscription, for this tab's session. */
export function routeSubEvent(st: TabTurnState, ev: TurnEnvelope): TurnAction[] {
  const who = ownership(st, ev);
  if (who === 'foreign') return [];
  if (ev.type === 'resync') {
    if (who !== 'own') return [];
    st.buffer = [];
    st.shadow = [];
    if (isSeq(ev.eventSeq)) st.lastSeq = ev.eventSeq;
    const out: TurnAction[] = [];
    const active = (ev as { activeTurnId?: string | null }).activeTurnId ?? undefined;
    if (st.rebuild && st.rebuild !== active) {
      // the turn awaiting its replay ended meanwhile: finish it now
      out.push({ type: 'complete', origin: st.rebuildOrigin ?? 'sub' });
      st.rebuild = undefined;
      st.rebuildOrigin = undefined;
    }
    const b = st.bound;
    if (b && !b.completed) {
      if (active && b.turnId === active) {
        // still running: replayed from its turn_start right after this resync
        st.rebuild = active;
        st.rebuildOrigin = b.origin;
        st.bound = undefined;
        out.push({ type: 'reset', origin: b.origin });
      } else {
        b.completed = true;
        out.push({ type: 'complete', origin: b.origin });
      }
    }
    out.push({ type: 'resync' });
    return out;
  }
  if (!ev.turnId || !isSeq(ev.eventSeq)) return [];
  if (who === 'candidate') {
    st.buffer.push(ev);
    return [];
  }
  if (ev.eventSeq <= st.lastSeq) return [];
  const b = st.bound;
  if (b && b.turnId === ev.turnId) {
    st.lastSeq = ev.eventSeq;
    if (st.shadow.length > 200) st.shadow = st.shadow.filter((x) => (x.eventSeq ?? 0) > st.lastSeq);
    if (b.completed) return [];
    if (ev.type === 'turn_end') return complete(st);
    return [{ type: 'render', event: ev, origin: b.origin }];
  }
  if (st.postPending || (b && !b.completed)) {
    st.buffer.push(ev);
    return [];
  }
  st.lastSeq = ev.eventSeq;
  if (ev.type === 'turn_start') {
    if (st.rebuild === ev.turnId) { st.rebuild = undefined; st.rebuildOrigin = undefined; }
    st.bound = { turnId: ev.turnId, origin: 'sub', completed: false, postDone: true };
    return [{ type: 'begin', event: ev }];
  }
  return [];
}

/** The POST stream ended with [DONE]. Returns what to do instead of completing blindly. */
export function onPostComplete(st: TabTurnState, subConnected: boolean): TurnAction[] {
  if (st.rebuild && st.rebuild === st.postTurnId) {
    if (subConnected) return []; // turn_end completes the rebuilt turn
    st.rebuild = undefined;
    st.rebuildOrigin = undefined;
    return [{ type: 'postComplete' }];
  }
  if (st.postPending || !st.bound || st.bound.turnId !== st.postTurnId) {
    // a pre-turn reply (validation, SESSION_BUSY): no turn of this send was bound
    st.postPending = false;
    // a turn the subscription is rendering in this tab owns its completion
    if (st.bound && !st.bound.completed) return [];
    return [{ type: 'postComplete' }, ...drainBuffer(st)];
  }
  st.bound.postDone = true;
  if (st.bound.completed) return [];
  if (subConnected) return []; // turn_end on the subscription completes it
  return complete(st);
}

/**
 * The POST stream was lost without [DONE] (transport error, stall cancel,
 * premature close). `detach` → the subscription carries on, show nothing;
 * `fallback` → run the existing disconnect / error handling.
 */
export function onPostLost(
  st: TabTurnState,
  subConnected: boolean,
): { verdict: 'detach' | 'fallback'; actions: TurnAction[] } {
  if (st.bound && st.bound.origin === 'post') st.bound.postLost = true;
  const unbound = st.postPending;
  st.postPending = false;
  if (!subConnected) return { verdict: 'fallback', actions: [] };
  if (st.bound?.completed) return { verdict: 'detach', actions: [] };
  if (unbound && !st.bound) {
    // The send's turn never bound. If the subscription already brought its start,
    // it renders the turn; otherwise the send may not have reached the backend,
    // and the existing disconnect handling (DB recovery) shows what happened.
    const actions = drainBuffer(st);
    const began = actions.some((a) => a.type === 'begin');
    return { verdict: began ? 'detach' : 'fallback', actions };
  }
  return { verdict: 'detach', actions: [] };
}

/**
 * The subscription went down. A POST turn it was rendering continues from the
 * POST: render the POST copies it never delivered, in order; if that POST is
 * already done, the turn is complete (no turn_end will come).
 */
export function onSubDisconnected(st: TabTurnState): TurnAction[] {
  const b = st.bound;
  if (!b || b.completed || b.turnId !== st.postTurnId) { st.shadow = []; return []; }
  const out: TurnAction[] = [];
  for (const ev of st.shadow) {
    if (!isSeq(ev.eventSeq) || ev.eventSeq <= st.lastSeq) continue;
    st.lastSeq = ev.eventSeq;
    if (ev.type === 'turn_end') { st.shadow = []; return [...out, ...complete(st)]; }
    out.push({ type: 'render', event: ev, origin: b.origin });
  }
  st.shadow = [];
  if (b.postDone) out.push(...complete(st));
  return out;
}
