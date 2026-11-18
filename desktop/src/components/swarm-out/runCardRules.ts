/**
 * The run card's layout rules (design: Knowledge/Designs/2026-11-18-swarm-out-run-card-redesign-mockup.html).
 * Pure: what the card shows is decided here from the run, nothing is fetched.
 *
 *  - ``acceptanceClauses``: the acceptance as the rows of "Done when", one per clause;
 *  - ``visibleTabs``: the tabs that hold something (an empty tab is never drawn; a
 *    running run's work so far stays one click away);
 *  - ``railState``: the command panel's first sentence, from where the run stands
 *    (./track's chip kind): nothing needed, the dispatching agent has it, your turn,
 *    stopped, settled.
 */
import type { SwarmOutResult, SwarmOutRun } from '../../services/swarmOut';
import { dispatchTrack, type ChipKind } from './track';
import { stageOf } from './runReview';

/** The acceptance's clauses, split on semicolons, each trimmed and otherwise unchanged;
 *  empty pieces dropped. One string without a semicolon is one row. */
export function acceptanceClauses(text: string | null | undefined): string[] {
  if (typeof text !== 'string') return [];
  return text.split(';').map((x) => x.trim()).filter(Boolean);
}

export type CardTab = 'deliverable' | 'autonomy' | 'process' | 'cost' | 'lessons' | 'timings';

/** Whether a result holds work: files, commits, or a reply. */
function hasWork(res: SwarmOutResult | null): boolean {
  if (!res) return false;
  return (res.work.files?.length ?? 0) > 0 || (res.work.repos ?? []).some((x) => (x.commits_total ?? 0) > 0)
    || (typeof res.report === 'string' && res.report.length > 0);
}

/** The tabs that hold something, in their order: what came back once the run is not
 *  running, or while it runs once some work came back (files, commits, a reply);
 *  decisions when there are decisions or a result; process when steps were counted;
 *  cost when a result was read; lessons when some came back; how it was sent when the
 *  dispatch's steps were recorded. */
export function visibleTabs(r: SwarmOutRun, res: SwarmOutResult | null, decisions: number | null): CardTab[] {
  const out: CardTab[] = [];
  if (stageOf(r) !== 'running' || hasWork(res)) out.push('deliverable');
  if ((decisions ?? 0) > 0 || res) out.push('autonomy');
  if ((res?.work.steps.total ?? 0) > 0) out.push('process');
  if (res) out.push('cost');
  if ((res?.lessons?.returned ?? 0) > 0) out.push('lessons');
  if ((r.timings?.length ?? 0) > 0) out.push('timings');
  return out;
}

/** The panel's first sentence: its i18n key under ``swarmOut.review.card.state``. */
export function railState(r: SwarmOutRun): ChipKind {
  return dispatchTrack(r).kind;
}
