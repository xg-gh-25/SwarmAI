/**
 * Where a dispatched run stands on its way back, as one station of the dispatch track
 * SENT · ON HIVE · RETURN · LAND · JUDGE, and what its chip says (design: the factory-line
 * overlays, Knowledge/Designs/2026-11-18-swarmai-factory-line-overlays-mockup.html).
 *
 * RETURN, LAND and JUDGE are the funnel the backend records (core/swarm_out_record.funnel:
 * review on this host → land on this host → judge); SENT and ON HIVE come before it, from
 * the run's state. Read by the Swarm Out run list (each row's track) and the Pipelines Line
 * (a Hive run's part back on this machine). Pure: nothing here is fetched or decided.
 *
 * The chip's kind, one meaning each (./tone):
 *  - ``run``: an agent is working on it (on its Hive, or the dispatching agent on this host);
 *  - ``judge``: the dispatching agent holds it at a funnel gate (it reviews, lands, judges);
 *  - ``you``: the person has something to do (a question, a result the agent handed over);
 *  - ``stop``: it stopped and nobody is taking it on;
 *  - ``done``: it ended (accepted or closed).
 */
import type { SwarmOutGateKey, SwarmOutRun } from '../../services/swarmOut';
import { DONE_STAGES, stageOf } from './runReview';

export const DISPATCH_STATIONS = ['sent', 'on_hive', 'return', 'land', 'judge'] as const;
export type DispatchStation = typeof DISPATCH_STATIONS[number];
export type ChipKind = 'run' | 'judge' | 'you' | 'stop' | 'done';

const GATE_STATION: Record<SwarmOutGateKey, DispatchStation> = { review: 'return', land: 'land', judge: 'judge' };

export interface DispatchTrack { at: DispatchStation; kind: ChipKind }

/** The station the run is at and its chip's kind. */
export function dispatchTrack(r: SwarmOutRun): DispatchTrack {
  const stage = stageOf(r);
  const f = r.funnel;
  const gate = f?.current ? f.gates.find((g) => g.key === f.current) : undefined;
  const youNow = r.needs_person === true || gate?.state === 'person' || f?.ask?.state === 'open';
  if (DONE_STAGES.includes(stage)) {
    // an ended run stands at the last gate it reached (JUDGE once it was judged)
    const last = f?.gates.length ? f.gates[f.gates.length - 1].key : null;
    return { at: last ? GATE_STATION[last] : 'judge', kind: 'done' };
  }
  if (f?.current) {
    return { at: GATE_STATION[f.current], kind: youNow ? 'you' : 'judge' };
  }
  if (r.session_unknown || (!r.session_id && r.state === 'unknown')) return { at: 'sent', kind: youNow ? 'you' : 'run' };
  if (youNow || (stage === 'needs_answer' && r.needs_person !== false)) return { at: 'on_hive', kind: 'you' };
  if (stage === 'stopped' || stage === 'awaiting') {
    // stopped (or come back, not reviewed yet) with the agent on it: the agent's; else stopped
    return { at: stage === 'awaiting' ? 'return' : 'on_hive', kind: r.needs_person === false ? 'judge' : stage === 'awaiting' ? 'you' : 'stop' };
  }
  return { at: 'on_hive', kind: 'run' };
}
