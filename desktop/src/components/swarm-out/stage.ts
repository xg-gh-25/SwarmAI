/**
 * A run's status in the person's words, the one source every surface reads: the Swarm
 * Out panel (runReview re-exports these with the app's i18n), the board, and the chat's
 * dispatch card (with its own ``t``). The backend decides each run's ``stage`` and
 * ``why`` (core/swarm_out_settle derive), how its session ended (``end``) and what still
 * keeps it from settling (``settle.why_not``); this file only names them.
 *
 * Pure: it binds no i18n instance (a chat message must not load the app's at import),
 * every word goes through the ``t`` it is given. Labels: swarmOut.review.* in the locales.
 */
import type { TFunction } from 'i18next';
import type { SwarmOutClosedWhy, SwarmOutEndReason, SwarmOutRun, SwarmOutStage, SwarmOutWhyNot } from '../../services/swarmOut';
import type { Tone } from './tone';

type T = TFunction | ((key: string, o?: Record<string, unknown>) => unknown);

/** ``t(key)``, or ``fallback`` when it gives nothing or the bare key (a test mock, a
 *  key this build lacks): the UI never shows a raw key. */
function word(t: T, key: string, fallback: string, vars: Record<string, unknown> = {}): string {
  const out = (t as (k: string, o?: Record<string, unknown>) => unknown)(key, { ...vars, defaultValue: fallback });
  return typeof out === 'string' && out !== key ? out : fallback;
}

/** The run's stage: the backend's, else (an older dispatch) from its Hive state. */
export function stageOf(r: SwarmOutRun): SwarmOutStage {
  if (r.stage) return r.stage;
  if (r.state === 'waiting') return 'needs_answer';
  if (r.state === 'running') return 'running';
  return 'stopped';
}

/** Why a closed run is closed, each with its own name (its ``why``). */
export const CLOSED_WHYS: readonly SwarmOutClosedWhy[] = ['discarded', 'reverted', 'expired', 'hive_gone'];

/** A stage in the person's words. A closed run is named by its reason (Discarded,
 *  Reverted, Expired, Hive gone); a reason this build does not know reads "Closed". */
export function stageWord(t: T, stage: SwarmOutStage, why?: string | null): string {
  // a Swarm Run's child its lead run took, or that went with the run's end
  if (stage === 'accepted' && why === 'swarm_run') return word(t, 'swarmOut.review.handled', why);
  if (stage === 'closed') {
    return why && (CLOSED_WHYS as readonly string[]).includes(why)
      ? word(t, `swarmOut.review.closed.${why}`, why) : word(t, 'swarmOut.review.stage.closed', stage);
  }
  return word(t, `swarmOut.review.stage.${stage}`, stage);
}

/** Each stage's tone (./tone): what the dot and the status word take. */
export const STAGE_TONE_KIND: Record<SwarmOutStage, Tone> = {
  running: 'busy', needs_answer: 'attention', awaiting: 'attention', stopped: 'error', accepted: 'ok', closed: 'unknown',
};

/** Every way a run's session ends (backend core/swarm_out_settle END_REASONS). */
export const END_REASONS: readonly SwarmOutEndReason[] = ['completed', 'turn_limit', 'stopped_by_agent',
  'stopped_by_person', 'crashed', 'carrier_expired', 'hive_stopped', 'lost'];

/** "Ended: completed" for a run whose end is recorded, else null. */
export function endWord(t: T, r: SwarmOutRun): string | null {
  const reason = r.end?.reason;
  if (!reason) return null;
  return (END_REASONS as readonly string[]).includes(reason)
    ? word(t, `swarmOut.review.end.${reason}`, reason)
    : word(t, 'swarmOut.review.end.other', reason, { reason });
}

/** What can keep a run from settling (``settle.why_not``), in their order. */
export const WHY_NOT: readonly SwarmOutWhyNot[] = ['verdict', 'code', 'files', 'lessons'];

/** What still keeps an unsettled run from settling, one phrase each; empty while it runs
 *  or waits on an answer (everything is still to come then), once it settled, or when
 *  nothing holds it. */
export function whyNotWords(t: T, r: SwarmOutRun): string[] {
  const s = r.settle;
  const stage = stageOf(r);
  if (!s || s.settled || stage === 'running' || stage === 'needs_answer') return [];
  return (s.why_not ?? []).map((w) => ((WHY_NOT as readonly string[]).includes(w)
    ? word(t, `swarmOut.review.whyNot.${w}`, w) : w));
}

/** "Not settled yet: ..." for an unsettled run that something holds, else null. */
export function whyNotLine(t: T, r: SwarmOutRun): string | null {
  const parts = whyNotWords(t, r);
  if (parts.length === 0) return null;
  return word(t, 'swarmOut.review.whyNot.line', parts.join(' · '),
    { list: parts.join(word(t, 'swarmOut.sep.list', ' · ')) });
}
