/**
 * The Swarm Out run card's shared parts (design docs/design/2026-10-28-swarm-out-run-results.md):
 * ``Dot`` (a status dot in a tone), ``Stats`` (a run's facts as cells, read by the run's
 * big card, RunOverlay), ``RunTitle`` (a title that opens the run's Hive session) and
 * ``RunCard`` (a run's summary card: how it ended, what it did, its numbers; its "Open
 * card" opens RunOverlay). The Swarm Out run view itself is RunBoard.
 *
 * Status colours follow ./tone (green done or free, blue running, amber needs you or
 * did not finish, red failed, grey unknown): only the dot and the status word are
 * coloured. Labels come from the locale files (swarmOut.*); everything a Hive wrote is
 * data, never translated, and shown as plain text.
 */
import { useState } from 'react';
import { useOpenRun } from './openSession';
import { useTranslation } from 'react-i18next';
import { type SwarmOutRun } from '../../services/swarmOut';
import { JudgeTag } from './JudgeTag';
import { agentFacts, checksIncomplete, checksLine, endedLabel, executionLine, gateLine, lessonsLine, resultErrorLabel, runJudge, runStats, stateLabel, tokenStats, when, workLines, type RunStat } from './live';
import { RunOverlay } from './RunOverlay';
import { actionsOf, stageLabel, stageOf } from './runReview';
import { dotStyle, runTone, TONE_VAR, type Tone } from './tone';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';


/** A small status dot in a tone (./tone). */
export function Dot({ tone, className = '' }: { tone: Tone; className?: string }) {
  return <span aria-hidden className={`inline-block w-[7px] h-[7px] rounded-full flex-shrink-0 ${className}`} style={dotStyle(tone)} data-tone={tone} />;
}

/** A card border tinted for a tone that should stand out (attention, error); others keep the plain border. */
const cardTint = (tone: Tone): React.CSSProperties | undefined =>
  tone === 'error' || tone === 'attention' ? { borderColor: `color-mix(in srgb, ${TONE_VAR[tone]} 45%, var(--color-border))` } : undefined;

/** The card's facts as cells (label over value), wrapping to as many columns as fit; a
 *  strong cell (the total cost, the billed tokens) reads a size up. */
export function Stats({ stats, testId = 'swarm-out-run-numbers', min = 104 }: { stats: RunStat[]; testId?: string; min?: number }) {
  return (
    <dl className="mt-2 mb-0 grid gap-x-3 gap-y-1.5 pt-2 border-t border-[var(--color-border)]"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))` }} data-testid={testId}>
      {stats.map((x) => (
        <div key={x.key} className="min-w-0" title={x.title} data-testid={`swarm-out-stat-${x.key}`}>
          <dt className="text-[10.5px] text-[var(--color-text-dim)] truncate">{x.label}</dt>
          <dd className={`m-0 tabular-nums truncate ${x.strong ? 'text-[14px] font-semibold' : 'text-[12.5px]'}`}
            style={x.tone ? { color: TONE_VAR[x.tone] } : undefined}>{x.value}</dd>
          {x.sub && <dd className="m-0 text-[10.5px] truncate" style={{ color: TONE_VAR[x.tone ?? 'unknown'] }}>{x.sub}</dd>}
        </div>
      ))}
    </dl>
  );
}

/** The Brain and the Project Agent, each a labelled field. */
function AgentFacts({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const f = agentFacts(r);
  const field = (icon: string, label: string, value: string, testId: string, title?: string) => (
    <span className="inline-flex items-center gap-1 min-w-0 max-w-full" title={title} data-testid={testId}>
      <span className="material-symbols-outlined icon-xs text-[var(--color-text-dim)]" aria-hidden>{icon}</span>
      <span className="text-[var(--color-text-dim)]">{label}</span>
      <span className={`truncate ${f.none ? muted : 'text-[var(--color-text)] font-medium'}`}>{value}</span>
    </span>
  );
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] min-w-0" data-testid="swarm-out-run-brain">
      {field('psychology', t('swarmOut.card.brain'), f.brain, 'swarm-out-run-brain-name')}
      {field('smart_toy', t('swarmOut.card.agent'), f.agent, 'swarm-out-run-agent', f.title)}
    </div>
  );
}

function RunResult({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const res = r.result;
  if (!res) {
    if (r.result_status === 'pending') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">{t('swarmOut.result.pending')}</div>;
    if (r.result_status === 'failed') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">{t('swarmOut.result.failed', { reason: resultErrorLabel(r.result_error) })}</div>;
    return null;
  }
  const ended = endedLabel(res);
  const work = workLines(res);
  const checks = checksLine(res);
  // the verdicts come with /runs (the numbers behind them: the big card's Autonomy tab)
  const judge = runJudge(res);
  return (
    <div className="mt-1.5" data-testid="swarm-out-run-result">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <div className="text-[12.5px] font-medium" style={ended.tone === 'busy' ? undefined : { color: TONE_VAR[ended.tone] }}
          data-testid="swarm-out-run-ended" data-tone={ended.tone}>{ended.text}</div>
        {judge && <JudgeTag judge={judge} title={r.title} />}
      </div>
      {work.map((w) => <div key={w} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-work">{w}</div>)}
      {executionLine(res) && (
        <div className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-execution"
          style={{ color: TONE_VAR.attention }}>{executionLine(res)}</div>
      )}
      {[checks, gateLine(res), lessonsLine(res)].filter(Boolean).map((l) => (
        <div key={l!} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-checks"
          style={l === checks && checksIncomplete(res) ? { color: TONE_VAR.attention } : undefined}>{l}</div>
      ))}
      <Stats stats={runStats(res)} />
      <Stats stats={tokenStats(res)} testId="swarm-out-run-tokens" min={72} />
      {r.result_error && <div className={`text-[11.5px] mt-0.5 ${muted}`}>{t('swarmOut.result.lastCheck', { reason: resultErrorLabel(r.result_error) })}</div>}
    </div>
  );
}

/** A run's title: a button that opens its Hive session when the run has one this Host
 *  knows (design docs/design/2026-10-29-swarm-out-hive-session-tab.md §4), else text. */
export function RunTitle({ r, className }: { r: SwarmOutRun; className: string }) {
  const { t } = useTranslation();
  const open = useOpenRun(r);
  if (!open) return <span className={className} title={r.title}>{r.title}</span>;
  return (
    <button type="button" className={`${className} text-left hover:underline cursor-pointer`}
      title={t('swarmOut.hiveTab.open', { title: r.title })} data-testid="swarm-out-run-open"
      onClick={open}>{r.title}</button>
  );
}

/** The card's explicit way into the run's Hive session (the title is a button too, but
 *  nothing about a title says it can be clicked). */
function OpenRunButton({ r, open }: { r: SwarmOutRun; open: () => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" className={`${btn} inline-flex items-center gap-1`} onClick={open}
      title={t('swarmOut.hiveTab.open', { title: r.title })} data-testid="swarm-out-run-open-button">
      <span className="material-symbols-outlined icon-sm" aria-hidden>open_in_new</span>
      {t('swarmOut.card.open')}
    </button>
  );
}

/** A run that waits on the person (its stage asks for a command) says what; the card's
 *  "Open card" button opens its big card, the one place commands are given. */
function ReviewLine({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const stage = stageOf(r);
  const asks = actionsOf(r).some((a) => a.key !== 'open_session');
  if (!asks || !['needs_answer', 'awaiting', 'stopped', 'running'].includes(stage)) return null;
  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)] flex items-center gap-2 text-[12.5px]" data-testid="swarm-out-review-line" data-stage={stage}>
      <span className="font-medium" style={{ color: stage === 'stopped' ? TONE_VAR.error : stage === 'running' ? TONE_VAR.busy : TONE_VAR.attention }}>{stageLabel(stage, r.why)}</span>
      {r.cleanup_soon && <span className="text-[12px]" style={{ color: TONE_VAR.attention }} data-testid="swarm-out-cleanup-soon">{t('swarmOut.review.cleanupSoon')}</span>}
    </div>
  );
}

export function RunCard({ r, onReload, onLeave }: {
  r: SwarmOutRun; onReload?: () => void;
  /** a file was opened from the run's big card: the host overlay steps aside so the
   *  Canvas is in view (a Brain page) */
  onLeave?: () => void;
}) {
  const { t } = useTranslation();
  // the run's big card (RunOverlay): the one place a run's details and commands are
  const [open, setOpen] = useState(false);
  const canOpen = !!r.result || r.result_status === 'failed' || actionsOf(r).some((a) => a.key !== 'open_session');
  const openRun = useOpenRun(r);
  const tone = runTone(r);
  return (
    // only a failed run, or one waiting on the person, tints its border: a stopped run keeps
    // its amber dot and word but does not need anything, so it does not compete with them
    <div className={`${card} px-3.5 py-3 min-w-0`}
      style={tone === 'error' || r.state === 'waiting' ? cardTint(tone) : undefined}
      data-testid="swarm-out-run" data-state={r.state ?? 'unknown'} data-tone={tone}>
      <div className="flex items-start gap-2 min-w-0">
        <span className="h-[18px] flex items-center"><Dot tone={tone} className={tone === 'busy' ? 'swarm-out-icon--busy' : ''} /></span>
        <RunTitle r={r} className="font-medium min-w-0 flex-1 line-clamp-2 leading-[18px]" />
        <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.sent_at)}</span>
      </div>
      <div className={`text-[11.5px] mt-0.5 truncate ${muted}`}>
        {r.hive} · <span data-testid={r.session_unknown ? 'swarm-out-run-unknown' : 'swarm-out-run-state'}>{stateLabel(r)}</span>
      </div>
      <AgentFacts r={r} />
      <RunResult r={r} />
      <ReviewLine r={r} />
      {(canOpen || openRun) && <div className="mt-2 flex items-center gap-2 min-w-0">
        {canOpen && (
          <button type="button" className={`${btn} inline-flex items-center gap-1`} aria-haspopup="dialog"
            onClick={() => setOpen(true)} data-testid="swarm-out-run-toggle">
            <span className="material-symbols-outlined icon-sm" aria-hidden>open_in_full</span>
            {t('swarmOut.review.openCard')}
          </button>
        )}
        {openRun && <span className="ml-auto"><OpenRunButton r={r} open={openRun} /></span>}
      </div>}
      {open && <RunOverlay runs={[r]} asks={[]} order={[]} id={r.dispatch_id} now={Date.now()}
        onSelect={() => {}} onClose={() => setOpen(false)} onFileOpened={onLeave} onReload={onReload ?? (() => {})} />}
    </div>
  );
}
