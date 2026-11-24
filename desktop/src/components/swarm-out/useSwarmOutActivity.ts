/**
 * What the pinned Swarm Out tab shows about the work on the Hives: how many dispatched
 * runs are running there now, and how many questions are waiting for the person.
 *
 * Read from /api/swarm-out/runs, the same source as the panel (live.ts uses this
 * function for its own numbers):
 * - `waiting` = what waits on the person: the open questions (asks), plus a run the
 *   Hive says is waiting whose question has not been mirrored here (no ask at all). An
 *   ask whose answer the Hive refused is listed as not delivered but does not count
 *   here: it is settled on the Hive's own page or expires. Plus every other run that
 *   waits on the person (`toSettle`), by the same judgment as the panel's "Waiting for
 *   you" (runReview.waitsOnPerson): when a live session answers for the run (its
 *   `needs_person` is a boolean) only when that is true, so a run the dispatching agent
 *   is reviewing, landing or judging is not counted; else (null) its stage is awaiting
 *   acceptance, or stopped with a command to give (re-run or discard; backend
 *   core/swarm_out_settle), or its records this Host removes soon.
 * - `running` = runs whose state is running on their Hive (backend run_state: streaming,
 *   preparing, starting or stopping). A run waiting on the person is not running, and a
 *   run whose state is unknown (`running` null) is not counted: the tab never claims
 *   work it has not seen.
 *
 * It polls while the tab exists, also when Swarm Out is not the active tab — that is
 * when the signal matters. Null until the first answer; a failed poll keeps the last
 * answer (an unreachable daemon is not "nothing running").
 */
import { useEffect, useState } from 'react';
import { swarmOutService, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { stageOf, waitsOnPerson } from './runReview';

export const ACTIVITY_POLL_MS = 30_000;

export interface SwarmOutActivity {
  running: number;
  waiting: number;
}

/** Runs the Hive says wait on the person whose question has not reached this Host. */
export function waitingUnasked(d: SwarmOutRuns) {
  const asked = new Set(d.asks.map((a) => `${a.hive_id}/${a.session_id}`));
  return d.runs.filter((r) => r.state === 'waiting' && !asked.has(`${r.hive_id}/${r.session_id}`));
}

/** A run that waits on the person (runReview.waitsOnPerson, the panel's judgment) and
 *  is not waiting on a question: that one is counted by its ask, or by waitingUnasked. */
export function toSettle(r: SwarmOutRun): boolean {
  if (r.state === 'waiting' || stageOf(r) === 'needs_answer') return false;
  return waitsOnPerson(r);
}

export function activityOf(d: SwarmOutRuns): SwarmOutActivity {
  return {
    waiting: d.asks.filter((a) => !a.refused).length + waitingUnasked(d).length + d.runs.filter(toSettle).length,
    running: d.runs.filter((r) => r.state === 'running').length,
  };
}

export function useSwarmOutActivity(): SwarmOutActivity | null {
  const [state, setState] = useState<SwarmOutActivity | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => {
      swarmOutService.runs().then((d) => { if (alive) setState(activityOf(d)); }).catch(() => { /* keep the last answer */ });
    };
    load();
    const id = window.setInterval(load, ACTIVITY_POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return state;
}
