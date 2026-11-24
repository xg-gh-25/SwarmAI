/**
 * What a chat tab shows about the runs its session dispatched (design "the runs this
 * session dispatched, visible in the dispatching tab" r2 §3, layers ① and ②): its runs
 * mark on the tab label and whether its Canvas has a Runs tab.
 *
 * Read from Swarm Out's one shared runs poll (./runsPoll), never a poll of its own, and
 * counted with the run board's own split (runReview.boardParts over runsPoll.sessionScope):
 * the mark's number is the cards the Canvas Runs tab shows under Running, and it is amber
 * exactly when one of them waits on the person (runReview.waitsOnPerson), the answer the
 * Swarm Out board and the pinned badge give for the same runs.
 *
 * Exports: RunsMark, runsMarkOf, useSessionRunsMarks, useSessionRunsMark.
 */
import { useMemo } from 'react';
import type { SwarmOutRuns } from '../../services/swarmOut';
import { boardParts } from './runReview';
import { sessionScope, useRunsSelector } from './runsPoll';

export interface RunsMark {
  /** every run the session dispatched that is listed (the Runs tab shows while > 0) */
  runs: number;
  /** the ones not settled: the board's Running cards (the tab label's number) */
  live: number;
  /** one of them waits on the person */
  waits: boolean;
}

/** The session's mark, or null when it dispatched no listed run (or has no id yet). */
export function runsMarkOf(d: SwarmOutRuns | null, sessionId: string | null | undefined, now: number): RunsMark | null {
  const mine = sessionScope(d, sessionId);
  if (!mine || mine.runs.length === 0) return null;
  const { live, you } = boardParts(mine.runs, mine.asks, now);
  return { runs: mine.runs.length, live: live.length, waits: you.size > 0 };
}

/** The time a mark is counted at: the minute, so two reads of one answer agree. */
const minute = () => Math.floor(Date.now() / 60_000) * 60_000;

const key = (m: RunsMark | null) => (m ? `${m.runs}.${m.live}.${m.waits ? 1 : 0}` : '');
const parse = (k: string): RunsMark | null => {
  if (!k) return null;
  const [runs, live, waits] = k.split('.');
  return { runs: Number(runs), live: Number(live), waits: waits === '1' };
};

/** The marks of several sessions (the tab bar: one subscription for every tab). */
export function useSessionRunsMarks(sessionIds: (string | null | undefined)[]): Record<string, RunsMark> {
  const ids = sessionIds.filter((s): s is string => !!s);
  const joined = useRunsSelector((d) => ids.map((s) => `${s}=${key(runsMarkOf(d, s, minute()))}`).join('|'), ids.length > 0);
  return useMemo(() => {
    const out: Record<string, RunsMark> = {};
    for (const part of joined ? joined.split('|') : []) {
      const i = part.lastIndexOf('=');
      const m = parse(part.slice(i + 1));
      if (m) out[part.slice(0, i)] = m;
    }
    return out;
  }, [joined]);
}

/** One session's mark (the Canvas: its Runs tab), null when it dispatched no listed
 *  run, undefined while the shared poll has no answer yet. */
export function useSessionRunsMark(sessionId: string | null | undefined): RunsMark | null | undefined {
  const k = useRunsSelector((d) => (d ? key(runsMarkOf(d, sessionId, minute())) : NOT_YET), !!sessionId);
  return useMemo(() => (k === NOT_YET ? undefined : parse(k)), [k]);
}

const NOT_YET = '?';
