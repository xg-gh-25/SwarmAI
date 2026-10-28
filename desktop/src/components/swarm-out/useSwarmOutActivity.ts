/**
 * What the pinned Swarm Out tab shows about the work on the Hives: how many dispatched
 * runs are running there now, and how many questions are waiting for the person.
 *
 * Read from /api/swarm-out/runs, the same source as the dashboard's 真实派发 section:
 * `waiting` = open questions (asks), `running` = runs whose session is streaming on its
 * Hive (`running === true`). A run waiting on a question is not running, and a run
 * whose Hive could not be asked (`running` null) is not counted: the tab never claims
 * work it has not seen.
 *
 * It polls while the tab exists, also when Swarm Out is not the active tab — that is
 * when the signal matters. Null until the first answer; a failed poll keeps the last
 * answer (an unreachable daemon is not "nothing running").
 */
import { useEffect, useState } from 'react';
import { swarmOutService, type SwarmOutRuns } from '../../services/swarmOut';

export const ACTIVITY_POLL_MS = 30_000;

export interface SwarmOutActivity {
  running: number;
  waiting: number;
}

export function activityOf(d: SwarmOutRuns): SwarmOutActivity {
  return {
    waiting: d.asks.length,
    running: d.runs.filter((r) => r.running === true && !r.waiting).length,
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
