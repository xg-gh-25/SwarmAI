/**
 * LiveDispatches — the run view of the Swarm Out panel, on real data only
 * (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * It answers two things, in this order: where the work stands (one compact card on
 * top: the questions waiting, the free slots and the runs running as three numbers, and
 * each Hive's memory under them), and the runs I sent, one row each, what waits on me
 * first (RunList; a row opens the run's big card, RunOverlay, where every command is;
 * design docs/design/2026-10-31-swarm-out-run-review.md). A question whose session is
 * none of the listed runs is answered here, above the list; one the Hive refused, or
 * one that never reached here, is handled on the Hive's page. What has no data source
 * yet says TBD and which source is missing; nothing on this view is a sample. What went
 * on in the past (the runs by Brain, type, Hive) is the history view's (HistoryView).
 *
 * RunCard (a Brain's Runs tab's card) shows what a run's Hive recorded (design
 * docs/design/2026-10-28-swarm-out-run-results.md): how it ended, what it did, its
 * numbers. Its "Open card" opens the run's big card (RunOverlay), the one place a run's
 * details (what came back, the reply, autonomy, process, cost, lessons) and commands are:
 * the Run view, the history and a Brain page all open that same card. A run that waits
 * on the person says so. Every card names its Brain and Project Agent as two labelled
 * fields, and its facts (ended, duration, tokens, cost, human loops, failed steps) as a
 * row of cells. A pipeline run's Autonomy Judge tag sits next to how it ended (its
 * verdicts come with /runs). A run whose Hive session this Host knows has an explicit
 * Open button (the title opens it too).
 *
 * Status colours follow ./tone (green done or free, blue running, amber needs you or
 * did not finish, red failed, grey unknown): only the dot and the status word are
 * coloured. Labels come from the locale files (swarmOut.*); everything a Hive wrote
 * (question text, command, reason, the reply, paths) is data, never translated; all of
 * it but the reply is shown as plain text. Polls /api/swarm-out/runs every 30 s while shown;
 * the fleet comes from the panel (one poll of /api/swarm-out/fleet).
 */
import { useEffect, useState } from 'react';
import { awsAccountService, type FleetStatus } from '../../services/awsAccount';
import { OPEN_SETTINGS_EVENT } from '../common/settingsEvents';
import { useOpenRun } from './openSession';
import { useTranslation } from 'react-i18next';
import { type SwarmOutAsk, type SwarmOutFleet, type SwarmOutRun } from '../../services/swarmOut';
import { SwarmOutWaitCard } from './SwarmOutWaitCard';
import { JudgeTag } from './JudgeTag';
import { agentFacts, autonomyLine, checksIncomplete, checksLine, endedLabel, errorLabel, executionLine, gateLine, hiveStatusLabel, lessonsLine, panelNumbers, resultErrorLabel, runJudge, runStats, stateLabel, tokenStats, useRuns, when, workLines, type RunStat } from './live';
import { RunList } from './RunList';
import { RunOverlay } from './RunOverlay';
import { actionsOf, stageLabel, stageOf } from './runReview';
import { waitingUnasked } from './useSwarmOutActivity';
import { dotStyle, hiveTone, runTone, TONE_VAR, type Tone } from './tone';

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

/** A part with no data source yet: its title and what is missing, demoted. */
export function Tbd({ title, missing, testId }: { title: string; missing: string; testId?: string }) {
  const { t } = useTranslation();
  return (
    <section className="mt-5" data-testid={testId ?? 'swarm-out-tbd'}>
      <h3 className={`m-0 text-[13px] font-semibold ${muted}`}>{title}</h3>
      <div className="mt-1 text-[12px] text-[var(--color-text-dim)]">{t('swarmOut.tbd', { missing })}</div>
    </section>
  );
}

/** One headline number inline (value, then its label); its tone colours the value
 *  (none = neutral: a zero or unknown). */
function Num({ value, label, tone, testId }: { value: React.ReactNode; label: string; tone?: Tone; testId: string }) {
  return (
    <div className="flex items-baseline gap-1.5 min-w-0" data-testid={testId} data-tone={tone}>
      <span className="text-[18px] font-semibold leading-none tabular-nums" style={tone ? { color: TONE_VAR[tone] } : undefined}>{value}</span>
      <span className={`text-[12px] ${muted}`}>{label}</span>
    </div>
  );
}

/** The top card: the three numbers in its header, each Hive's memory under them (a
 *  Hive queues a new task only when its memory is under critical pressure). What the
 *  count and the bar mean is explained on hover (the title and every status). */
/** The SwarmAI account's Hive count, when it is short of what the person asked for
 *  (design 2026-10-31-swarmai-aws-account §7.3): one line and a button to the account
 *  page, where the count is raised with its cost named. Silent when ready or unknown. */
function useHiveShortfall(): FleetStatus | null {
  const [st, setSt] = useState<FleetStatus | null>(null);
  useEffect(() => {
    let stop = false;
    awsAccountService.get().then((v) => { if (!stop) setSt(v.hive ?? null); }).catch(() => { /* unknown: say nothing */ });
    return () => { stop = true; };
  }, []);
  return st && (st.state === 'short' || st.state === 'cannot_build') ? st : null;
}

function Fleet({ fleet, failed, numbers }: { fleet: SwarmOutFleet | null; failed?: boolean; numbers: React.ReactNode }) {
  const { t } = useTranslation();
  const hives = fleet?.hives ?? [];
  const note = t('swarmOut.fleet.note');
  const short = useHiveShortfall();
  return (
    <section className={card} data-testid="swarm-out-fleet">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5 px-4 pt-3 pb-2">
        <h3 className="m-0 text-[13px] font-semibold" title={note}>{t('swarmOut.fleet.title')}</h3>
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 min-w-0" data-testid="kpi-grid">{numbers}</div>
      </div>
      {failed && <div className={`px-4 text-[12px] ${muted}`} data-testid="swarm-out-fleet-failed">{t('swarmOut.fleet.failed')}</div>}
      {short && (
        <div className="px-4 pb-2 text-[12px] flex items-center gap-3" data-testid="swarm-out-fleet-short">
          <span style={{ color: TONE_VAR.attention }}>
            {short.state === 'cannot_build' ? t('swarmOut.fleet.cannotBuild') : t('swarmOut.fleet.short', { count: short.short ?? 0 })}
          </span>
          <button type="button" className="underline"
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'aws-account' } }))}>
            {t('swarmOut.fleet.openAccount')}
          </button>
        </div>
      )}
      {fleet === null ? (!failed && <div className={`px-4 pb-3 text-[12px] ${muted}`}>{t('swarmOut.loading')}</div>) : (
        <div className="grid grid-cols-[minmax(80px,220px)_minmax(0,1fr)_minmax(0,max-content)] items-center gap-x-3 gap-y-2 px-4 pt-2.5 pb-3 border-t border-[var(--color-border)]">
          {hives.map((h) => {
            const up = h.status === 'running' && !!h.reachable;
            const count = { count: h.streaming ?? 0 };
            const status = h.status !== 'running' ? hiveStatusLabel(h.status)
              : !h.reachable ? t('swarmOut.fleet.unreachable')
              // an older Hive reports no memory: its saturated still means a full stream cap
              : h.saturated ? t(h.memory_percent == null ? 'swarmOut.fleet.busy' : 'swarmOut.fleet.full', count)
              : t('swarmOut.fleet.used', count);
            const tone = hiveTone(h);
            const mem = up && h.memory_percent != null ? Math.min(100, Math.max(0, h.memory_percent)) : null;
            const memTone: Tone = h.saturated ? 'attention' : 'ok';
            return (
              <div key={h.name} className="contents" data-testid="swarm-out-fleet-hive" data-tone={tone}>
                <div className="font-medium min-w-0 flex items-start gap-2" title={h.name}>
                  <Dot tone={tone} className="mt-[6px]" />
                  <div className="min-w-0">
                    <div className="truncate">{h.name}</div>
                    <div className={`text-[11.5px] font-normal truncate ${muted}`}>{[h.version, h.instance_type].filter(Boolean).join(' · ')}</div>
                  </div>
                </div>
                {/* the Hive's memory in use: green while a new task starts at once, amber
                    once it is under critical pressure (a new task queues) */}
                <div className="min-w-0 h-[12px] rounded-[3px] border overflow-hidden" data-testid="swarm-out-memory"
                  data-percent={mem ?? undefined}
                  title={mem != null ? t('swarmOut.fleet.memory', { percent: Math.round(mem) }) : undefined}
                  style={{ opacity: mem != null ? 1 : 0.4, borderColor: `color-mix(in srgb, ${TONE_VAR[memTone]} 55%, transparent)`,
                    background: `color-mix(in srgb, ${TONE_VAR[memTone]} 10%, transparent)` }}>
                  {mem != null && <div className="h-full" style={{ width: `${mem}%`, background: TONE_VAR[memTone] }} />}
                </div>
                <div className={`text-[12px] text-right truncate min-w-0 ${tone === 'error' || tone === 'attention' ? '' : muted}`}
                  style={tone === 'error' || tone === 'attention' ? { color: TONE_VAR[tone] } : undefined}
                  title={`${status} · ${note}`} data-testid="swarm-out-fleet-status">{status}</div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

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

function OpenHive({ onOpenHive }: { onOpenHive?: () => void }) {
  const { t } = useTranslation();
  return onOpenHive ? <button type="button" className={`ml-2 ${btn}`} onClick={onOpenHive} data-testid="swarm-out-open-hive-admin">{t('swarmOut.live.openHiveAdmin')}</button> : null;
}

export function LiveDispatches({ hidden = false, fleet = null, fleetFailed = false, onOpenHive, now, compact = false }: {
  hidden?: boolean; fleet?: SwarmOutFleet | null; fleetFailed?: boolean; onOpenHive?: () => void; now?: number; compact?: boolean;
}) {
  const { t } = useTranslation();
  const { data, failed, reload } = useRuns(hidden);
  const at = now ?? Date.now();
  const runs = data?.runs ?? [];
  const asks = data?.asks ?? [];
  const answered = data?.answered ?? [];
  // a wait of a listed run is answered on that run's card; the rest here (one answered
  // here stays as its one line while its answer is within the longest wait)
  const listed = new Set(runs.filter((r) => r.session_id).map((r) => `${r.hive_id}/${r.session_id}`));
  const mine = (a: SwarmOutAsk) => listed.has(`${a.hive_id}/${a.session_id}`);
  const loose = [...asks.filter((a) => !a.refused && !mine(a)), ...answered.filter((a) => !mine(a))];
  const refused = asks.filter((a) => a.refused && !mine(a));
  const sourceOf = (a: SwarmOutAsk) => (
    <>{a.hive} · <span data-testid="swarm-out-ask-source">{a.dispatch_title
      ?? (a.possible_dispatch_title ? t('swarmOut.ask.maybe', { title: a.possible_dispatch_title }) : t('swarmOut.ask.otherSession'))}</span>{a.asked_at ? ` · ${when(a.asked_at)}` : ''}</>
  );
  const unasked = data ? waitingUnasked(data) : [];
  const n = panelNumbers(data, fleet);
  const noRuns = !!data && runs.length === 0;
  const dash = '—';
  const auto = autonomyLine(runs, at);
  return (
    <div data-testid="swarm-out-live">
      {data && auto.finished > 0 && (
        <div className={`text-[12px] mb-2 ${muted}`} data-testid="swarm-out-autonomy-line">
          {t('swarmOut.header.autonomy', { finished: auto.finished, unattended: auto.unattended, known: auto.known,
            avg: auto.avg == null ? '—' : auto.avg.toFixed(1) })}
        </div>
      )}
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      {(data?.errors ?? []).map((e) => <div key={e} className="text-[12px] mb-1" style={{ color: TONE_VAR.attention }}>{errorLabel(e)}</div>)}
      <Fleet fleet={fleet} failed={fleetFailed} numbers={<>
        <Num testId="kpi-waiting" label={t('swarmOut.kpi.waiting')} tone={n.waiting || n.onHivePage ? 'attention' : undefined}
             value={<>{n.waiting ?? dash}{n.onHivePage ? <span className="text-[12.5px] font-medium ml-2" data-testid="kpi-on-hive-page">{t('swarmOut.kpi.onHivePage', { count: n.onHivePage })}</span> : null}</>} />
        {/* free slots: green while there is room, amber when every running Hive is full */}
        <Num testId="kpi-free" label={t('swarmOut.kpi.free')} tone={n.capacity ? (n.capacity.free > 0 ? 'ok' : 'attention') : undefined}
             value={n.capacity ? <>{n.capacity.free}<span className={`text-[12.5px] font-medium ml-1 ${muted}`}>/ {n.capacity.total}</span></> : dash} />
        <Num testId="kpi-running" label={t('swarmOut.kpi.running')} tone={n.running ? 'busy' : undefined} value={n.running ?? dash} />
        <button type="button" className={`ml-auto ${btn}`} onClick={reload} data-testid="swarm-out-refresh">{t('swarmOut.waiting.refresh')}</button>
      </>} />

      {loose.length > 0 && (
        <section className="mt-4 flex flex-col gap-2" data-testid="swarm-out-waiting">
          {loose.map((a) => <SwarmOutWaitCard key={`${a.hive_id}/${a.request_id}`} wait={a} onAnswered={reload} source={sourceOf(a)} />)}
        </section>
      )}
      {(unasked.length > 0 || refused.length > 0) && (
        <section className="mt-4" data-testid="swarm-out-on-hive-page">
          <div className={`text-[12px] mb-1.5 ${muted}`}>{t(refused.length ? 'swarmOut.waiting.refused' : 'swarmOut.waiting.unasked')}<OpenHive onOpenHive={onOpenHive} /></div>
          <div className="flex flex-col gap-2">
            {refused.map((a) => <SwarmOutWaitCard key={`${a.hive_id}/${a.request_id}`} wait={a} onAnswered={reload} source={sourceOf(a)} />)}
            {unasked.map((r) => <div key={r.dispatch_id} className={`text-[12.5px] ${muted}`} data-testid="swarm-out-waiting-unasked">{r.title} · {r.hive}</div>)}
          </div>
        </section>
      )}

      {noRuns ? (
        <div className={`mt-5 text-[12px] ${muted}`}>{t('swarmOut.noRuns')}</div>
      ) : <RunList runs={runs} asks={asks} answered={answered} now={at} onReload={reload} compact={compact} />}
      <Tbd title={t('swarmOut.tbdParts.scheduled')} missing={t('swarmOut.tbdParts.scheduledMissing')} />
    </div>
  );
}
