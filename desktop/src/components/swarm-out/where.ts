/**
 * Where a dispatched run ran, and its time facts, as every Swarm Out row writes them
 * (design: Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v6.html).
 *
 *  - ``runWhere``: the execution surface (a Hive on EC2, Elastic Workers on AgentCore
 *    Runtime, or an SSH machine of the person's own) and the name shown next to it. The run's own last round says it; a dispatch
 *    from before rounds were recorded falls back to its Hive's carrier in the fleet, then
 *    to the Elastic Workers location's name, then to EC2 (every older Hive is an EC2 one).
 *  - ``stamp``: a start time as MM/DD HH:mm, local time.
 *  - ``runTime``: the run's minutes, each saying what it measures, by where the run is:
 *    - running (the Hive works): the running time of its open round (a re-run counts
 *      from its own start, not the first send), else since it was sent;
 *    - waiting (a question, or a result back and not settled): the Hive work so far
 *      (``active_s``: the sum of the rounds' own times, never the gaps between them) and
 *      how long it has waited on the agent or on the person: since the first question
 *      asked, else since the result became final (``final_at``, else its ``ended_at``);
 *    - done (settled, or stopped: listed with the done runs): the Hive work, the rounds
 *      when more than one ran, and whether it is a lower bound (``active_partial``: a
 *      turn without a CLI duration, counted up to its end). Work is null when no result
 *      says it.
 *  - ``minutesText`` / ``waitText``: "18 min" and "2 h" / "3 d" for a longer wait.
 *
 * Pure: nothing is fetched or decided here.
 */
import type { SwarmOutAsk, SwarmOutFleet, SwarmOutRun } from '../../services/swarmOut';
import { asksOf, DONE_STAGES, stageOf } from './runReview';
import { dispatchTrack } from './track';

export type Carrier = 'ec2' | 'agentcore' | 'ssh';

/** The i18n key of a carrier's short label ("EC2", "AgentCore", "SSH"). */
export const carrierLabelKey = (c: Carrier | null | undefined): string =>
  c === 'agentcore' ? 'swarmOut.board.elastic' : c === 'ssh' ? 'swarmOut.board.ssh' : 'swarmOut.board.ec2';

/** The name the backend gives an Elastic Workers location that has none of its own
 *  (core/carriers/agentcore). */
const ELASTIC_LOCATION = 'elastic';
export const ELASTIC_NAME = 'Elastic Workers';

export function runWhere(r: SwarmOutRun, fleet: SwarmOutFleet | null | undefined): { carrier: Carrier; name: string } {
  const rounds = r.rounds ?? [];
  const last = rounds.length ? rounds[rounds.length - 1]?.carrier : null;
  const inFleet = fleet?.hives.find((h) => h.name === r.hive)?.carrier ?? null;
  const carrier: Carrier = last ?? inFleet ?? (r.hive === ELASTIC_LOCATION ? 'agentcore' : 'ec2');
  return { carrier, name: carrier === 'agentcore' ? ELASTIC_NAME : r.hive };
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "11/23 14:02" in local time; empty when the time is missing or unreadable. */
export function stamp(iso: string | null | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return '';
  const d = new Date(t);
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export type RunTime =
  | { phase: 'running'; min: number | null }
  | { phase: 'waiting'; worked: number | null; partial: boolean; rounds: number; who: 'agent' | 'person'; waited: number | null }
  | { phase: 'done'; worked: number | null; partial: boolean; rounds: number };

const sinceMin = (iso: string | null | undefined, now: number): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 60000));
};

/** The run's minutes by where it is (see the header). */
export function runTime(r: SwarmOutRun, now: number, asks: SwarmOutAsk[] = []): RunTime {
  const stage = stageOf(r);
  const res = r.result ?? null;
  if (stage === 'running') {
    const last = res?.turn_spans?.length ? res.turn_spans[res.turn_spans.length - 1] : null;
    return { phase: 'running', min: sinceMin(last && last.end == null && last.start ? last.start : r.sent_at, now) };
  }
  const work = {
    worked: res?.active_s != null ? Math.round(res.active_s / 60) : null,
    partial: res?.active_partial === true,
    rounds: res?.turns ?? 0,
  };
  // a stopped run is listed with the done ones ("Recently done", the history): its work,
  // not a wait that would grow for as long as the row is shown
  if (DONE_STAGES.includes(stage) || stage === 'stopped') return { phase: 'done', ...work };
  // whom it waits on: the dispatch track's chip (the person's when it is "you" or a stop
  // nobody takes on, else the dispatching agent's)
  const { kind } = dispatchTrack(r);
  const who = kind === 'you' || kind === 'stop' ? 'person' : 'agent';
  const asked = stage === 'needs_answer' ? asksOf(r, asks).map((a) => a.asked_at).filter(Boolean).sort()[0] : undefined;
  const since = asked ?? res?.final_at ?? res?.ended_at ?? r.sent_at;
  return { phase: 'waiting', ...work, who, waited: sinceMin(since, now) };
}

/** "18 min", "<1 min", or "—" when not known. */
export function minutesText(m: number | null): string {
  if (m == null) return '—';
  return m < 1 ? '<1 min' : `${m} min`;
}

/** "5332 6741 2361": an AWS Account ID in groups of four, to read it back. */
export const groupedId = (id: string): string => id.replace(/(\d{4})(?=\d)/g, '$1 ');

/** A wait: minutes under an hour, whole hours under two days, else whole days. */
export function waitText(m: number | null): string {
  if (m == null) return '—';
  if (m < 60) return minutesText(m);
  return m < 48 * 60 ? `${Math.floor(m / 60)} h` : `${Math.floor(m / 1440)} d`;
}
