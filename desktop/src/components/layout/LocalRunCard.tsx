/**
 * LocalRunCard — a Run of this machine's pipeline (a chat tab drove it), drawn with the
 * same facts, in the same order and with the same cells as a Hive run's card (Swarm
 * Out's RunCard), so a Brain's Runs tab reads one way whichever lane ran the Run
 * (design docs/design/2026-10-30-brain-runs-one-card.md D1):
 *   the status dot, the title, when it started;
 *   where it ran (Chat tab) and its state;
 *   Brain · Project Agent (the profile the Run used);
 *   how it ended (landed / not landed and why / running / needs you) and the Autonomy
 *   Judge tag when the pipeline wrote its audit;
 *   cells: active time (and when it ended), human decisions, cost, billed tokens, and
 *   the stages re-run when any were.
 * A fact the Run never recorded is "—" with the reason on hover, never a zero.
 * Clicking the title or Open opens the Run's drawer in Pipelines.
 *
 * Data: PipelineRunSummary (GET /api/pipelines/analytics → routers.pipelines._summary).
 * Exports: LocalRunCard, localRunStats, localJudge, localTone
 */
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import type { PipelineRunSummary } from '../../services/pipelines';
import { Dot, Stats } from '../swarm-out/LiveDispatches';
import { JudgeTag } from '../swarm-out/JudgeTag';
import { DIMENSIONS, type RunJudge, type Verdict } from '../swarm-out/shapes';
import { durationText, tokenCount, when, type RunStat } from '../swarm-out/live';
import { TONE_VAR, type Tone } from '../swarm-out/tone';
import { runTone, type RunTone } from './brainMapModel';
import { openOverlayAt } from '../../utils/overlayTarget';

/** A local Run's state in Swarm Out's colours: green landed, blue running, amber waits on
 *  you or stopped, red did not land. */
const TONE: Record<RunTone, Tone> = { running: 'busy', needs: 'attention', landed: 'ok', failed: 'error', stopped: 'attention' };
export const localTone = (r: PipelineRunSummary): Tone => TONE[runTone(r)];

/** The card's cells, as Swarm Out's runStats + the billed-tokens cell, from what the
 *  local record holds. */
export function localRunStats(r: PipelineRunSummary): RunStat[] {
  const dash = '—';
  const missing = i18n.t('swarmOut.stats.notRecorded');
  const ended = when(r.finishedAt);
  const out: RunStat[] = [
    { key: 'time', label: i18n.t('swarmOut.stats.time'), value: durationText(r.activeS),
      sub: ended ? i18n.t('swarmOut.stats.endedAt', { when: ended }) : undefined,
      title: r.activeS == null ? missing : undefined },
    { key: 'human', label: i18n.t('swarmOut.stats.human'), value: r.humanDecisions == null ? dash : String(r.humanDecisions),
      title: r.humanDecisions == null ? missing : i18n.t('swarmOut.stats.humanLocalTitle') },
    { key: 'cost', label: i18n.t('swarmOut.stats.cost'), strong: true,
      value: r.costUsd == null ? dash : `$${r.costUsd.toFixed(2)}`,
      title: r.costUsd == null ? i18n.t('swarmOut.stats.costNotRecorded') : undefined },
    { key: 'tokens', label: i18n.t('swarmOut.stats.tokensBilled'), strong: true,
      value: r.billedTokens == null ? dash : tokenCount(r.billedTokens),
      title: r.billedTokens == null ? i18n.t('swarmOut.stats.tokensNotRecorded') : r.billedTokens.toLocaleString() },
  ];
  if (r.rework) out.push({ key: 'rework', label: i18n.t('swarmOut.stats.rework'), value: String(r.rework), title: i18n.t('swarmOut.stats.reworkTitle') });
  return out;
}

const VERDICTS: readonly string[] = ['PASS', 'WARN', 'BLOCK', 'NA', 'PENDING'];

/** The audit rows as the Autonomy Judge tag's shape (Swarm Out's runJudge, for a local
 *  record), or null when the pipeline wrote no audit. */
export function localJudge(r: PipelineRunSummary): RunJudge | null {
  if (!r.audit.length) return null;
  const row = (d: string) => r.audit.find((a) => a.dim === d);
  const norm = (v: string | undefined): Verdict => (v === 'N/A' ? 'NA' : VERDICTS.includes(v ?? '') ? v as Verdict : 'NA');
  return {
    outcome: [r.status, r.landing].filter(Boolean).join(' · ').toUpperCase(),
    verdicts: DIMENSIONS.map((d) => norm(row(d)?.verdict)) as RunJudge['verdicts'],
    numbers: DIMENSIONS.map((d) => row(d)?.detail ?? '') as RunJudge['numbers'],
    live: r.status === 'running' || undefined,
  };
}

/** How the Run ended, in the card's words, and its tone. */
function endedText(r: PipelineRunSummary): { text: string; tone: Tone } {
  const state = runTone(r);
  if (state === 'needs') return { text: r.checkpointReason || i18n.t('swarmOut.local.state.needs'), tone: 'attention' };
  if (state === 'failed' && r.status === 'completed') {
    return { text: r.notLandedReason ? i18n.t('swarmOut.local.notLanded', { reason: r.notLandedReason }) : i18n.t('swarmOut.local.notLandedBare'), tone: 'error' };
  }
  return { text: i18n.t(`swarmOut.local.state.${state}`), tone: TONE[state] };
}

const muted = 'text-[var(--color-text-muted)]';

export function LocalRunCard({ r, brain }: { r: PipelineRunSummary; brain: string }) {
  const { t } = useTranslation();
  const tone = localTone(r);
  const ended = endedText(r);
  const judge = localJudge(r);
  const title = r.requirement || r.id;
  const open = () => openOverlayAt('pipeline', r.id);
  const field = (icon: string, label: string, value: string, testId: string) => (
    <span className="inline-flex items-center gap-1 min-w-0 max-w-full" data-testid={testId}>
      <span className="material-symbols-outlined text-[14px] text-[var(--color-text-dim)]" aria-hidden>{icon}</span>
      <span className="text-[var(--color-text-dim)]">{label}</span>
      <span className="truncate text-[var(--color-text)] font-medium">{value}</span>
    </span>
  );
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-3 min-w-0"
      style={tone === 'error' || (tone === 'attention' && r.needsYou)
        ? { borderColor: `color-mix(in srgb, ${TONE_VAR[tone]} 45%, var(--color-border))` } : undefined}
      data-testid={`bm-runcard-${r.id}`} data-tone={tone}>
      <div className="flex items-start gap-2 min-w-0">
        <span className="h-[18px] flex items-center"><Dot tone={tone} className={tone === 'busy' ? 'swarm-out-icon--busy' : ''} /></span>
        <button type="button" onClick={open} title={title}
          className="font-medium min-w-0 flex-1 line-clamp-2 leading-[18px] text-left hover:underline">{title}</button>
        <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.createdAt)}</span>
      </div>
      <div className={`text-[11.5px] mt-0.5 truncate ${muted}`}>
        {t('swarmOut.local.chatTab')} · {t(`swarmOut.local.state.${runTone(r)}`)} · {r.id}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] min-w-0">
        {field('psychology', t('swarmOut.card.brain'), brain, 'bm-runcard-brain')}
        {field('smart_toy', t('swarmOut.card.agent'), r.profile || t('swarmOut.card.agentDefault'), 'bm-runcard-agent')}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <div className="text-[12.5px] font-medium break-words" style={ended.tone === 'busy' ? undefined : { color: TONE_VAR[ended.tone] }}
          data-testid="bm-runcard-ended" data-tone={ended.tone}>{ended.text}</div>
        {judge && <JudgeTag judge={judge} title={title} />}
      </div>
      <Stats stats={localRunStats(r)} testId="bm-runcard-stats" />
      <div className="mt-2 flex items-center">
        <button type="button" onClick={open} data-testid="bm-runcard-open"
          className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)]">
          <span className="material-symbols-outlined text-[15px]" aria-hidden>open_in_new</span>{t('swarmOut.local.open')}
        </button>
      </div>
    </div>
  );
}
