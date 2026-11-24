/**
 * PipelineOverlay — every pipeline run, wherever it ran (design
 * docs/design/2026-11-01-brain-eval-and-runs.md §4): this machine's runs and the Hive
 * runs Swarm Out dispatched that ran a pipeline (a Hive's native runs stay in Swarm Out).
 *
 * Two tabs under one filter row (everywhere / this machine / Hive, and a Brain made
 * from the rows, with counts; switching tabs keeps it):
 * - **Runs** (default): PipelineLine (design: Knowledge/Mockups/2026-11-24-pipeline-
 *   overlay-redesign.html): every run in flight as a card with its stages, the ones waiting
 *   for you first and marked on the card; the runs that ended in the 30 days as rows; the
 *   median minutes per stage. The tab's label carries the waiting count.
 * - **Eval**: PipelineEval (design: Knowledge/Mockups/2026-11-24-pipeline-eval-redesign.html):
 *   the share nobody stepped into, four numbers against the 15 days before, the runs
 *   someone stepped into, and the same by Brain.
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
import { Tabs } from '../overlay';
import { OverlayError } from './OverlayState';
import { BRAIN_TARGET, takeOverlayTarget } from '../../utils/overlayTarget';
import { useRuns } from '../swarm-out/live';
import { RunOverlay } from '../swarm-out/RunOverlay';
import { PipelineRunCard } from './PipelineRunCard';
import { PipelineEval } from './PipelineEval';
import { PipelineLine } from './PipelineLine';
import { allRows, brainChips, filterRows, sections, waitingCounts, type PipelineRow, type RowFilter } from './pipelineRows';

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
const COMPACT_PX = 720;

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

  const [tab, setTab] = useState<'line' | 'eval'>('line');
  // narrow: the finished rows stack their time (Swarm Out's rule, SwarmOutDashboard)
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    if (!box || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([e]) => setCompact(e.contentRect.width < COMPACT_PX));
    ro.observe(box);
    return () => ro.disconnect();
  }, [box]);
  const [filter, setFilter] = useState<RowFilter>({ where: 'all', brain: null });
  const [open, setOpen] = useState<{ key: string; order: string[] } | null>(null);

  const rows = useMemo(() => allRows(analytics?.byProject, hive.data?.runs, hive.data?.asks ?? []),
    [analytics, hive.data]);
  const shown = useMemo(() => filterRows(rows, filter), [rows, filter]);
  const chips = useMemo(() => brainChips(filterRows(rows, { ...filter, brain: null })), [rows, filter]);
  const s = useMemo(() => sections(shown), [shown]);
  const waiting = useMemo(() => waitingCounts(shown).waiting, [shown]);
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
  const segment = (key: RowFilter['where'], label: string) => (
    <button key={key} type="button" role="radio" aria-checked={filter.where === key} onClick={() => setFilter((f) => ({ ...f, where: key, brain: null }))}
      data-testid={`pipeline-where-${key}`}
      className={`px-2.5 h-7 rounded-md text-small ${filter.where === key ? 'font-semibold text-[var(--color-text)] bg-[color-mix(in_srgb,var(--color-text)_8%,transparent)]' : muted}`}>{label}</button>
  );

  return (
    <div ref={setBox} className="flex-1 min-h-0 flex flex-col relative" data-testid="pipeline-overlay">
      <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-[var(--color-border)]">
        <Tabs value={tab} onChange={setTab} testid="pipeline-tabs" items={[
          { key: 'line', label: waiting ? t('ui.PipelineOverlay.tabLineWaiting', { count: waiting }) : t('ui.PipelineOverlay.tabLine'), testid: 'pipeline-tab-line' },
          { key: 'eval', label: t('ui.PipelineOverlay.tabEval'), testid: 'pipeline-tab-eval' },
        ]} />
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
        {tab === 'eval' ? <PipelineEval rows={shown} now={now} onOpen={openKey} compact={compact} /> : (
          <div data-testid="pipeline-run-list">
            {analytics && rows.length === 0 && !loadErr
              ? <div className="text-center py-10 text-body text-[var(--color-text-faint)]">{t('ui.PipelineOverlay.empty')}</div>
              : <PipelineLine rows={shown} now={now} onOpen={openKey} onOpenSession={openSession} compact={compact} />}
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
