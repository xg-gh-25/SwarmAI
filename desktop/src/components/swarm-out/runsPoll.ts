/**
 * The one poll of /api/swarm-out/runs (and of /api/swarm-out/fleet) that every reader
 * of the dispatched runs shares (design "the runs this session dispatched, visible in
 * the dispatching tab" r2 §3): the Swarm Out panel (live.useRuns / useFleet: RunBoard,
 * HistoryView, BrainMapView, PipelineOverlay), the pinned tab's badge
 * (useSwarmOutActivity), the dispatch and Swarm Run cards in a chat (DispatchCard),
 * each chat tab's runs mark and the Canvas's Runs tab. A new reader adds no request.
 *
 * Each store polls while anything subscribes, every POLL_MS:
 *  - the first subscriber starts it with a load; a later one loads again only when the
 *    last load is older than FRESH_MS and none is in flight (a panel opened late is
 *    fresh, two readers mounted together make one request);
 *  - every tick asks again, also while an earlier request has not answered (one that
 *    never answers must not stop the poll for every reader); the newest answer wins;
 *  - ``reload`` asks again now (after a command): its answer wins over an older one
 *    still in flight;
 *  - a failed poll keeps the last answer (an unreachable daemon is not "nothing") and
 *    says so (``failed``);
 *  - when the last subscriber leaves, the poll stops; its last answer stays, so a reader
 *    that comes back (the Swarm Out tab opened again) shows it at once instead of an
 *    empty header and a list that jumps, while its first subscribe asks again.
 *
 * No i18n and nothing about how a run is worded: a chat message (DispatchCard) and a
 * tab label load this module without the panel's locale-bound modules.
 *
 * Exports: useSharedRuns, useRunsSelector, useSharedFleet, reloadRuns, reloadFleet,
 * sessionScope, POLL_MS,
 * FRESH_MS, _resetSharedPolls.
 */
import { useSyncExternalStore } from 'react';
import { swarmOutService, type SwarmOutFleet, type SwarmOutRuns } from '../../services/swarmOut';

export const POLL_MS = 30_000;
/** A subscriber arriving later than this after the last load asks again. */
export const FRESH_MS = 5_000;

export interface Shared<T> {
  data: T | null;
  /** the last poll failed (``data`` is the answer before it) */
  failed: boolean;
}

interface Store<T> {
  subscribe: (l: () => void) => () => void;
  get: () => Shared<T>;
  reload: () => void;
  reset: () => void;
}

const EMPTY: Shared<never> = { data: null, failed: false };

function sharedPoll<T>(fetch: () => Promise<T>): Store<T> {
  let state: Shared<T> = EMPTY;
  let timer: number | null = null;
  let inflight = false;
  let lastAt = 0;
  let seq = 0;
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());

  const load = (force: boolean) => {
    if (inflight && !force) return;
    const mine = ++seq;   // an older answer arriving late never overwrites a newer one
    inflight = true;
    lastAt = Date.now();
    fetch().then((d) => {
      if (mine !== seq) return;
      state = { data: d, failed: false };
      emit();
    }, () => {
      if (mine !== seq) return;
      if (!state.failed) { state = { ...state, failed: true }; emit(); }
    }).finally(() => { if (mine === seq) inflight = false; });
  };

  return {
    subscribe(l) {
      listeners.add(l);
      if (listeners.size === 1) {
        load(true);
        timer = window.setInterval(() => load(true), POLL_MS);
      } else if (!inflight && Date.now() - lastAt > FRESH_MS) {
        load(false);
      }
      return () => {
        listeners.delete(l);
        if (listeners.size === 0) {
          if (timer != null) window.clearInterval(timer);
          timer = null;
          seq += 1;          // an answer still in flight is for nobody
          inflight = false;
        }
      };
    },
    get: () => state,
    reload: () => { if (listeners.size > 0) load(true); },
    reset: () => { seq += 1; inflight = false; lastAt = 0; state = EMPTY; },
  };
}

const runsStore = sharedPoll<SwarmOutRuns>(() => swarmOutService.runs());
const fleetStore = sharedPoll<SwarmOutFleet>(() => swarmOutService.fleet());

const noSubscribe = () => () => {};

/** The shared runs answer. ``enabled`` false: read whatever is there without keeping
 *  the poll going (a hidden panel). */
export function useSharedRuns(enabled = true): Shared<SwarmOutRuns> {
  return useSyncExternalStore(enabled ? runsStore.subscribe : noSubscribe, runsStore.get);
}

/** One value read from the shared runs answer, re-rendering only when it changes:
 *  ``select`` returns a primitive (a count, a joined key), so a poll that brings the
 *  same answer re-renders nothing (a tab label, the Canvas header). */
export function useRunsSelector<T extends string | number | boolean | null>(
  select: (d: SwarmOutRuns | null) => T, enabled = true,
): T {
  return useSyncExternalStore(enabled ? runsStore.subscribe : noSubscribe, () => select(runsStore.get().data));
}

/** The shared fleet answer (the Swarm Out panel's pills, a run's "where" tag). */
export function useSharedFleet(enabled = true): Shared<SwarmOutFleet> {
  return useSyncExternalStore(enabled ? fleetStore.subscribe : noSubscribe, fleetStore.get);
}

/** Ask the shared runs poll again now (after a command on a run). */
export function reloadRuns(): void {
  runsStore.reload();
}

export function reloadFleet(): void {
  fleetStore.reload();
}

/** Test hook: forget both answers (a subscriber still there polls on). */
export function _resetSharedPolls(): void {
  runsStore.reset();
  fleetStore.reset();
}

/** The part of the runs answer one chat session dispatched: its runs (their
 *  ``dispatched_from`` is the session's app id, which self-healing never changes), the
 *  asks and answered asks of those runs' Hive sessions; no error lines and no asks of
 *  any other session. An empty session id has none. */
export function sessionScope(d: SwarmOutRuns | null, sessionId: string | null | undefined): SwarmOutRuns | null {
  if (!d) return null;
  if (!sessionId) return { ...d, runs: [], asks: [], answered: [], errors: [] };
  const runs = d.runs.filter((r) => r.dispatched_from === sessionId);
  const own = new Set(runs.filter((r) => r.session_id).map((r) => `${r.hive_id}/${r.session_id}`));
  const mine = (a: { hive_id: string; session_id: string | null }) => own.has(`${a.hive_id}/${a.session_id}`);
  return { ...d, runs, asks: d.asks.filter(mine), answered: (d.answered ?? []).filter(mine), errors: [] };
}
