/**
 * The Swarm Out panel's status colours — one meaning per colour, used by every part
 * (run cards, history rows, the fleet, the numbers, the daily bars):
 *
 *  - ok        green  done, healthy, a free slot
 *  - busy      blue   running now
 *  - attention amber  needs the person (a question) or did not finish normally
 *                     (stopped, paused, a full Hive, checks not run)
 *  - error     red    failed, or a Hive that is up but cannot be reached
 *  - unknown   grey   not known yet, or off (a stopped Hive is off, not a problem)
 *
 * Only the dot and the status word take the colour; titles and numbers stay neutral,
 * so a red or amber card is what stands out in a list of green ones.
 */
import type { SwarmOutEnded, SwarmOutHive, SwarmOutResult, SwarmOutRun } from '../../services/swarmOut';

export type Tone = 'ok' | 'busy' | 'attention' | 'error' | 'unknown';

export const TONE_VAR: Record<Tone, string> = {
  ok: 'var(--color-status-success)',
  busy: 'var(--color-primary)',
  attention: 'var(--color-status-warning)',
  error: 'var(--color-status-error)',
  unknown: 'var(--color-text-dim)',
};

const ENDED_TONE: Record<SwarmOutEnded, Tone> = {
  completed: 'ok',
  turn_limit: 'attention',
  error: 'error',
  crashed: 'error',
  gave_up: 'error',
  paused: 'attention',
  stopped: 'attention',
  superseded: 'attention',
  restarted: 'attention',
  no_terminal: 'unknown',
  no_record: 'unknown',
};

/** How a recorded ending reads: a provisional ending is not known yet. */
export function endedTone(res: SwarmOutResult): Tone {
  const tone = ENDED_TONE[res.ended] ?? 'unknown';
  return res.provisional && tone !== 'error' ? 'unknown' : tone;
}

/** A run's tone: waiting on the person, running, else how it ended (unknown when its
 *  Hive has not said). */
export function runTone(r: SwarmOutRun): Tone {
  const state = r.state ?? (r.waiting ? 'waiting' : r.running === true ? 'running' : r.running === false ? 'stopped' : 'unknown');
  if (state === 'waiting') return 'attention';
  if (state === 'running') return 'busy';
  if (r.result) return endedTone(r.result);
  return 'unknown';
}

/** A Hive's tone in the fleet: up and answering is ok (full is attention), up but not
 *  answering or in error is error, anything on its way up or down or off is unknown. */
export function hiveTone(h: Pick<SwarmOutHive, 'status'> & { reachable?: boolean | null; saturated?: boolean | null }): Tone {
  if (h.status === 'error' || h.status === 'failed') return 'error';
  if (h.status !== 'running') return 'unknown';
  if (!h.reachable) return 'error';
  return h.saturated ? 'attention' : 'ok';
}

/** A small round status dot in a tone. */
export const dotStyle = (tone: Tone): { background: string } => ({ background: TONE_VAR[tone] });
