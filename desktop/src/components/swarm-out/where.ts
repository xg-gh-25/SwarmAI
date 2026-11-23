/**
 * Where a dispatched run ran, and its time facts, as every Swarm Out row writes them
 * (design: Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v6.html).
 *
 *  - ``runWhere``: the execution surface (a Hive on EC2, or Elastic Workers on AgentCore
 *    Runtime) and the name shown next to it. The run's own last round says it; a dispatch
 *    from before rounds were recorded falls back to its Hive's carrier in the fleet, then
 *    to the Elastic Workers location's name, then to EC2 (every older Hive is an EC2 one).
 *  - ``stamp``: a start time as MM/DD HH:mm, local time.
 *  - ``runMinutes``: how long a run took: its active time on the Hive once it ended,
 *    the time since it was sent while it is still open. null when nothing says.
 *
 * Pure: nothing is fetched or decided here.
 */
import type { SwarmOutFleet, SwarmOutRun } from '../../services/swarmOut';

export type Carrier = 'ec2' | 'agentcore';

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

/** Whole minutes the run took (see the header). */
export function runMinutes(r: SwarmOutRun, now: number, open: boolean): number | null {
  if (!open && r.result?.active_s != null) return Math.round(r.result.active_s / 60);
  if (open) {
    const t = Date.parse(r.sent_at);
    return Number.isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 60000));
  }
  return null;
}

/** "18 min", "<1 min", or "—" when not known. */
export function minutesText(m: number | null): string {
  if (m == null) return '—';
  return m < 1 ? '<1 min' : `${m} min`;
}

/** "5332 6741 2361": an AWS Account ID in groups of four, to read it back. */
export const groupedId = (id: string): string => id.replace(/(\d{4})(?=\d)/g, '$1 ');
