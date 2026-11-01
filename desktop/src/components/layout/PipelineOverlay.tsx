/**
 * PipelineOverlay — every pipeline run, wherever it ran (design
 * docs/design/2026-11-01-brain-eval-and-runs.md §4): this machine's runs and the Hive
 * runs Swarm Out dispatched that ran a pipeline (a Hive's native runs stay in Swarm Out).
 *
 * Two tabs under one filter row (everywhere / this machine / Hive, and a Brain made
 * from the rows, with counts; switching tabs keeps it):
 * - **Runs** (default): Swarm Out's list. Waiting for you (to answer > to confirm or
 *   accept > stopped, the longest waiting first) always on top, Running, Done (folded,
 *   paged). A row: status dot, title, where it ran (this machine or the Hive's name, in
 *   the distribution chart's colours), Brain, profile, the one autonomy word, what it
 *   asks of you, how long it waited, and Open session. The tab's label carries the
 *   waiting count; no row of totals (a number never shows twice).
 * - **Metrics**: PipelineMetrics (the run distribution and five measures with their
 *   drill-down charts).
 * A click on a row opens its big card: a local run the new shell (PipelineRunCard,
 * GET /pipelines/{id}/view, its commands from the backend), a Hive run Swarm Out's card
 * (RunOverlay, with its asks). J / K in a card walk the runs waiting for you there.
 *
 * Data: the 30-day analytics (one cached overlay query: this machine's runs) and Swarm
 * Out's runs (polled every 30 s while open, as Swarm Out reads them). Writes go through
 * the run's own routes; a run only moves when its session drives it, so answering,
 * resuming and continuing open the owner session with a continue prompt
 * (docs/design/pipeline-v1-human-loop.md). Local state only (never MessageStore).
 *
 * @exports PipelineContent, PipelineContentProps
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery } from './overlayQueries';
import { classifyLoadError } from '../../services/api';
import type { PipelineAnalytics } from '../../services/pipelines';
import type { SwarmOutRun } from '../../services/swarmOut';
import { Tabs } from '../overlay';
import { OverlayError } from './OverlayState';
import { BRAIN_TARGET, takeOverlayTarget } from '../../utils/overlayTarget';
import { useRuns } from '../swarm-out/live';
import { Dot } from '../swarm-out/LiveDispatches';
import { RunOverlay } from '../swarm-out/RunOverlay';
import { useOpenRun } from '../swarm-out/openSession';
import { ago } from '../swarm-out/runReview';
import { TONE_VAR, type Tone } from '../swarm-out/tone';
import { PipelineRunCard } from './PipelineRunCard';
import { PipelineMetrics } from './PipelineMetrics';
import { allRows, autonomyOf, brainChips, filterRows, sections, type PipelineRow, type RowFilter } from './pipelineRows';

export interface PipelineContentProps {
  /** Hand a prompt to a chat tab (land+activate a tab, THEN inject). Returns true
   *  if it landed (→ host closes the overlay) or false on needs-close. MUST mirror
   *  ChatPage's dispatch — a bare inject no-ops with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close (called after a successful dispatch). */
  close: () => void;
  /** Open *sessionId* in a tab with *prompt* in its composer (not sent). Returns
   *  false when the session cannot be opened (busy tabs, deleted session). */
  onContinue?: (sessionId: string, prompt: string) => Promise<boolean>;
}

const muted = 'text-[var(--color-text-muted)]';
const WHERE_FILL = { local: 'var(--viz-1)', hive: 'var(--viz-2)' } as const;
const DONE_PAGE = 20;
const STAGE_TONE: Record<PipelineRow['stage'], Tone> = {
  needs_answer: 'attention', awaiting: 'attention', stopped: 'error', running: 'busy', done: 'unknown',
};

function rowTone(r: PipelineRow): Tone {
  if (r.stage !== 'done') return STAGE_TONE[r.stage];
  return r.outcome === 'landed' || r.outcome === 'manual' ? 'ok' : r.outcome === 'discarded' ? 'unknown' : 'error';
}

const TAG_STYLE = {
  answer: { background: TONE_VAR.attention, color: 'var(--color-bg)' },
  soft: { color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 13%, transparent)` },
  stopped: { color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 13%, transparent)` },
} as const;

function HiveOpen({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const open = useOpenRun(r);
  if (!open) return null;
  return (
    <button type="button" className="btn-sm btn-ghost whitespace-nowrap" data-testid="pipeline-row-open"
      onClick={(e) => { e.stopPropagation(); open(); }}>{t('swarmOut.review.cmd.open.name')} ↗</button>
  );
}

function Row({ r, now, onOpen, onOpenSession }: {
  r: PipelineRow; now: number; onOpen: () => void; onOpenSession: (r: PipelineRow) => void;
}) {
  const { t } = useTranslation();
  const word = autonomyOf(r);
  const tone = rowTone(r);
  const where = r.where === 'local' ? t('ui.PipelineOverlay.local') : r.hive ?? 'Hive';
  return (
    <div role="button" tabIndex={0} onClick={onOpen} aria-label={r.title}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
      className="grid items-center gap-2.5 px-2.5 py-[7px] rounded-lg hover:bg-[var(--color-hover)] cursor-pointer min-w-0"
      style={{ gridTemplateColumns: '10px minmax(0,1fr) auto auto auto 44px' }}
      data-testid={`pipeline-run-${r.key}`} data-stage={r.stage} data-where={r.where}>
      <Dot tone={tone} className={tone === 'busy' ? 'swarm-out-icon--busy' : ''} />
      <div className="min-w-0">
        <div className="text-body truncate" title={r.title}>{r.title}</div>
        <div className={`text-meta truncate ${muted}`}>
          <span data-testid="pipeline-row-where" style={{ color: WHERE_FILL[r.where] }}>{where}</span>
          {[r.brain, r.profile, r.runId].filter(Boolean).map((x) => ` · ${x}`).join('')}
        </div>
      </div>
      {word ? <span className="text-meta px-[7px] py-[1px] rounded-[5px] border whitespace-nowrap" data-testid="pipeline-row-autonomy"
        style={{ color: word.full ? TONE_VAR.ok : TONE_VAR.attention, borderColor: `color-mix(in srgb, ${word.full ? TONE_VAR.ok : TONE_VAR.attention} 45%, var(--color-border))` }}>
        {word.full ? t('swarmOut.review.autonomy.full') : t('swarmOut.review.autonomy.steppedIn', { count: word.n })}</span> : <span />}
      {r.task ? <span className="text-small px-[9px] py-[2px] rounded-md whitespace-nowrap font-semibold" data-testid="pipeline-row-tag"
        style={r.task === 'answer' ? TAG_STYLE.answer : r.task === 'stopped' || r.task === 'resume' ? TAG_STYLE.stopped : TAG_STYLE.soft}>
        {t(`ui.PipelineOverlay.task_${r.task}`)}</span>
        : <span className={`text-small ${muted}`}>{t(`ui.PipelineOverlay.outcome_${r.stage === 'running' ? 'running' : r.outcome}`)}</span>}
      <span className="flex items-center">
        {r.where === 'hive' && r.hive_run ? <HiveOpen r={r.hive_run} />
          : r.ownerSessionId ? <button type="button" className="btn-sm btn-ghost whitespace-nowrap" data-testid="pipeline-row-open"
            onClick={(e) => { e.stopPropagation(); onOpenSession(r); }}>{t('swarmOut.review.cmd.open.name')} ↗</button> : null}
      </span>
      <span className={`text-meta text-right tabular-nums ${muted}`}>{ago(r.since, now)}</span>
    </div>
  );
}

export function PipelineContent({ close, onContinue }: PipelineContentProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const analyticsQ = overlayQuery.pipelineAnalytics('30d');
  const aQuery = useQuery(analyticsQ);
  const analytics: PipelineAnalytics | null = aQuery.data ?? null;
  const loadErr: unknown = aQuery.isError && !aQuery.data ? aQuery.error : null;
  const hive = useRuns(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(id); }, []);

  const [tab, setTab] = useState<'runs' | 'metrics'>('runs');
  const [filter, setFilter] = useState<RowFilter>({ where: 'all', brain: null });
  const [donePage, setDonePage] = useState(0);
  const [showDone, setShowDone] = useState(false);
  const [open, setOpen] = useState<{ key: string; order: string[] } | null>(null);

  const rows = useMemo(() => allRows(analytics?.byProject, hive.data?.runs, hive.data?.asks ?? []),
    [analytics, hive.data]);
  const shown = useMemo(() => filterRows(rows, filter), [rows, filter]);
  const chips = useMemo(() => brainChips(filterRows(rows, { ...filter, brain: null })), [rows, filter]);
  const s = useMemo(() => sections(shown), [shown]);
  const reload = useCallback(() => { void qc.invalidateQueries({ queryKey: analyticsQ.queryKey }); hive.reload(); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, hive.reload]);

  const openKey = useCallback((key: string) => {
    const row = rows.find((r) => r.key === key || r.runId === key);
    if (!row) return;
    setOpen({ key: row.key, order: s.you.filter((r) => r.where === row.where).map((r) => r.key) });
  }, [rows, s.you]);

  // opened AT a run (Brain Hub, Need You): its card, once the rows have it; opened at a
  // Brain ("brain:<name>", the Brain map's Runs): the list filtered to that Brain
  const [target] = useState(() => takeOverlayTarget('pipeline'));
  useEffect(() => { if (target?.startsWith(BRAIN_TARGET)) setFilter({ where: 'all', brain: target.slice(BRAIN_TARGET.length) }); },
    [target]);
  useEffect(() => { if (target && !target.startsWith(BRAIN_TARGET) && rows.length && !open) openKey(target); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [target, rows.length]);

  const openSession = useCallback(async (r: PipelineRow) => {
    if (r.ownerSessionId && onContinue && await onContinue(r.ownerSessionId, '')) close();
  }, [onContinue, close]);

  const openRow = open ? rows.find((r) => r.key === open.key) : undefined;
  const section = (key: string, title: string, n: number, accent?: string) => (
    <div className="flex items-baseline gap-2 mt-4 mb-1.5" data-testid={`pipeline-section-${key}`}>
      <h3 className="m-0 text-body font-semibold" style={accent ? { color: accent } : undefined}>{title}</h3>
      <span className={`text-small tabular-nums ${muted}`}>{n}</span>
    </div>
  );
  const row = (r: PipelineRow) => <Row key={r.key} r={r} now={now} onOpen={() => openKey(r.key)} onOpenSession={openSession} />;
  const doneShown = s.done.slice(0, (donePage + 1) * DONE_PAGE);
  const segment = (key: RowFilter['where'], label: string) => (
    <button key={key} type="button" role="radio" aria-checked={filter.where === key} onClick={() => setFilter((f) => ({ ...f, where: key, brain: null }))}
      data-testid={`pipeline-where-${key}`}
      className={`px-2.5 h-7 rounded-md text-small ${filter.where === key ? 'font-semibold text-[var(--color-text)] bg-[color-mix(in_srgb,var(--color-text)_8%,transparent)]' : muted}`}>{label}</button>
  );

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="pipeline-overlay">
      <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-[var(--color-border)]">
        <Tabs value={tab} onChange={setTab} testid="pipeline-tabs" items={[
          { key: 'runs', label: s.you.length ? t('ui.PipelineOverlay.tabRunsWaiting', { count: s.you.length }) : t('ui.PipelineOverlay.tabRuns'), testid: 'pipeline-tab-runs' },
          { key: 'metrics', label: t('ui.PipelineOverlay.tabMetrics'), testid: 'pipeline-tab-metrics' },
        ]} />
        <span className={`text-small ${muted}`}>{t('ui.PipelineOverlay.window')}</span>
        <div className="ml-auto flex flex-wrap items-center gap-2" data-testid="pipeline-filter">
          <div role="radiogroup" aria-label={t('ui.PipelineOverlay.whereLabel')} className="inline-flex gap-0.5 rounded-lg p-[3px] bg-[color-mix(in_srgb,var(--color-text)_5%,transparent)]">
            {segment('all', t('ui.PipelineOverlay.whereAll'))}{segment('local', t('ui.PipelineOverlay.local'))}{segment('hive', 'Hive')}
          </div>
          {chips.length > 1 && (
            <select value={filter.brain ?? ''} onChange={(e) => setFilter((f) => ({ ...f, brain: e.target.value || null }))}
              aria-label={t('ui.PipelineOverlay.brainFilter')} data-testid="pipeline-brain-filter"
              className="h-7 rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 text-small">
              <option value="">{t('ui.PipelineOverlay.brainAll')}</option>
              {chips.map((c) => <option key={c.brain} value={c.brain}>{c.brain} ({c.count})</option>)}
            </select>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
        {!!loadErr && (
          <OverlayError testid="pipeline-load-error" retryTestid="pipeline-load-retry"
            message={classifyLoadError(loadErr, 'pipeline analytics')} onRetry={() => { void aQuery.refetch(); }}
            className="mx-auto my-8 max-w-sm" />
        )}
        {hive.failed && !hive.data && <div className={`text-small mb-2 ${muted}`} data-testid="pipeline-hive-unreadable">{t('ui.PipelineOverlay.hiveUnreadable')}</div>}
        {tab === 'metrics' ? <PipelineMetrics rows={shown} now={now} onOpen={openKey} /> : (
          <div data-testid="pipeline-run-list">
            {analytics && rows.length === 0 && !loadErr && <div className="text-center py-10 text-body text-[var(--color-text-faint)]">{t('ui.PipelineOverlay.empty')}</div>}
            {section('you', t('swarmOut.review.section.you'), s.you.length, s.you.length ? TONE_VAR.attention : undefined)}
            {s.you.length === 0 ? <div className={`px-2.5 text-small ${muted}`}>{t('swarmOut.waiting.none')}</div>
              : <div className="flex flex-col">{s.you.map(row)}</div>}
            {section('running', t('swarmOut.review.section.running'), s.running.length)}
            {s.running.length === 0 ? <div className={`px-2.5 text-small ${muted}`}>{t('swarmOut.groups.runningEmpty')}</div>
              : <div className="flex flex-col">{s.running.map(row)}</div>}
            <button type="button" className="flex items-baseline gap-2 mt-4 mb-1.5" aria-expanded={showDone} onClick={() => setShowDone((v) => !v)}
              data-testid="pipeline-section-done">
              <span className="text-body font-semibold">{t('ui.PipelineOverlay.sectionDone')}</span>
              <span className={`text-small tabular-nums ${muted}`}>{s.done.length}</span>
              <span className={`text-small ${muted}`}>{showDone ? t('swarmOut.review.fold') : t('swarmOut.review.unfold')}</span>
            </button>
            {showDone && <div className="flex flex-col">{doneShown.map(row)}</div>}
            {showDone && s.done.length > doneShown.length && (
              <button type="button" className="text-left px-2.5 py-1.5 text-small text-[var(--color-primary)]" onClick={() => setDonePage((p) => p + 1)}
                data-testid="pipeline-done-more">{t('ui.PipelineOverlay.moreDone', { count: s.done.length - doneShown.length })} ›</button>
            )}
          </div>
        )}
      </div>

      {open && openRow?.where === 'local' && (
        <PipelineRunCard id={open.key} order={open.order} onSelect={(key) => setOpen((o) => (o ? { ...o, key } : o))}
          onClose={() => setOpen(null)} onContinue={onContinue} onReload={reload} onLeave={close}
          onSettled={(key) => setOpen((o) => (o ? { ...o, order: o.order.filter((x) => x !== key) } : o))} />
      )}
      {open && openRow?.where === 'hive' && hive.data && (
        <RunOverlay runs={hive.data.runs} asks={hive.data.asks ?? []} order={open.order} id={open.key} now={now}
          onSelect={(key) => setOpen((o) => (o ? { ...o, key } : o))}
          onSettled={(key) => setOpen((o) => (o ? { ...o, order: o.order.filter((x) => x !== key) } : o))}
          onClose={() => setOpen(null)} onFileOpened={close} onReload={reload} />
      )}
    </div>
  );
}
