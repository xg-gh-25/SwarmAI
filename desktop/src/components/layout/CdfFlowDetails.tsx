/**
 * The Cognition flow's health strip and its Details disclosure (CognitiveDistillationFlow).
 *
 * One line by default (whether anything waits or failed is said once, at the top of the
 * view): the oldest item kept and the store size, model calls and
 * tokens today, Memory and Evolution size. Details keeps the raw audit a maintainer
 * reads: extraction by source, waiting by lane, model calls by source and day, the
 * post-session review table and the weekly history of kept units.
 *
 * Data: GET /ingestion/health, /ingestion/flow (extraction coverage), /ingestion/stats;
 * the two heavier ones load only once Details is opened.
 *
 * Key export: `CdfFlowDetails`.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ingestionService, type IngestionWindow, type LandedOverview } from '../../services/ingestion';
import { SectionLabel, tableHeadClass } from '../overlay';
import { OverlayError } from './OverlayState';
import { bytes, fmt } from './cdfText';

function weekOf(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return date.toISOString().slice(0, 10);
}

export function CdfFlowDetails({ overview, window }: { overview?: LandedOverview; window: IngestionWindow }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const health = useQuery({ queryKey: ['ingestion', 'health'], queryFn: ingestionService.health });
  const flow = useQuery({ queryKey: ['ingestion', 'flow', window], queryFn: () => ingestionService.flow(window), enabled: open });
  const stats = useQuery({ queryKey: ['ingestion', 'stats', 365], queryFn: () => ingestionService.stats(365), enabled: open });

  const today = new Date().toISOString().slice(0, 10);
  const todayRows = (health.data?.llmBySource ?? []).filter((row) => row.utcDay === today);
  const calls = todayRows.reduce((sum, row) => sum + row.calls, 0);
  const tokens = todayRows.reduce((sum, row) => sum + row.inputTokens + row.outputTokens, 0);
  const oldest = overview?.oldestAt ?? null;
  const overdue = (overview?.attention ?? []).some((item) => item.code === 'cleanup_overdue');
  const storeBytes = overview?.storeBytes ?? health.data?.inboxBytes ?? 0;

  const weeks = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of stats.data?.rows ?? []) {
      const day = typeof row.utcDay === 'string' ? row.utcDay : '';
      const state = typeof row.state === 'string' ? row.state : '';
      if (day && (state === 'written' || state === 'reinforced')) {
        totals.set(weekOf(day), (totals.get(weekOf(day)) ?? 0) + Number(row.units ?? 0));
      }
    }
    return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-12);
  }, [stats.data]);
  const maxWeek = Math.max(1, ...weeks.map(([, count]) => count));

  const Num = ({ value, warn }: { value: string; warn?: boolean }) => (
    <span className="font-semibold tabular-nums" style={{ color: warn ? 'var(--color-status-warning)' : 'var(--color-text)' }}>{value}</span>
  );
  return (
    <section className="border-t border-[var(--color-border)] pt-4" data-testid="cdf-health">
      <div className="flex flex-wrap items-center gap-x-7 gap-y-2 text-small text-[var(--color-text-muted)]">
        <span>{t('cdf.health.oldest')} <Num value={oldest ? oldest.slice(0, 10) : '—'} warn={overdue} />
          {' · '}{t('cdf.health.store')} <Num value={bytes(storeBytes)} /></span>
        <span>{t('cdf.health.calls')} <Num value={fmt(calls)} /> · {t('cdf.health.tokens')} <Num value={tokens >= 1e6 ? `${(tokens / 1e6).toFixed(1)}M` : fmt(tokens)} /></span>
        {health.data && <span>{t('cdf.health.memory')} <Num value={bytes(health.data.memoryBytes)} /> · {t('cdf.health.evolution')} <Num value={bytes(health.data.evolutionBytes)} /></span>}
        <button type="button" className="ml-auto text-small text-[var(--color-primary)]" aria-expanded={open}
          onClick={() => setOpen((value) => !value)} data-testid="cdf-details-toggle">
          {open ? t('cdf.health.hide') : t('cdf.health.details')}
        </button>
      </div>
      {health.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void health.refetch(); }} testid="cdf-health-error" />}

      {open && (
        <div className="mt-5 space-y-6" data-testid="cdf-details">
          {flow.data && Object.keys(flow.data.extractionCoverage ?? {}).length > 0 && (
            <div>
              <SectionLabel>{t('cdf.details.coverage')}</SectionLabel>
              <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1 md:grid-cols-4">
                {Object.entries(flow.data.extractionCoverage).map(([source, value]) => (
                  <div key={source} className="text-small">
                    <div className="text-[var(--color-text)]">{t(`cdf.source.${source}`, { defaultValue: source })}
                      <span className="ml-2 font-semibold tabular-nums">{fmt(value.unitsEmitted)}</span></div>
                    <div className="text-[var(--color-text-dim)]">{t('cdf.details.coverageHint', {
                      zero: value.zeroUnitAttempts, attempts: value.attempts, invalid: value.unitsInvalid })}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {health.data && Object.keys(health.data.lanes).length > 0 && (
            <div>
              <SectionLabel>{t('cdf.details.backlog')}</SectionLabel>
              <div className="mt-2 flex flex-wrap gap-x-8 text-small text-[var(--color-text-muted)]">
                {Object.entries(health.data.lanes).map(([lane, value]) => (
                  <span key={lane}>{t(`cdf.details.lanes.${lane}`, { defaultValue: lane })} <span className="font-semibold text-[var(--color-text)]">{value.backlog}</span>
                    {value.oldestWaitSeconds != null && <> · {t('cdf.details.backlogHint', { hours: Math.floor(value.oldestWaitSeconds / 3600) })}</>}</span>
                ))}
              </div>
            </div>
          )}
          {health.data && health.data.llmBySource.length > 0 && (
            <Table title={t('cdf.details.llm')} headers={['day', 'source', 'calls', 'input', 'output'].map((key) => t(`cdf.details.llmHead.${key}`))}
              rows={health.data.llmBySource.map((row) => [row.utcDay, t(`cdf.source.${row.source}`, { defaultValue: row.source }),
                fmt(row.calls), fmt(row.inputTokens), fmt(row.outputTokens)])} />
          )}
          {health.data?.review && (
            <Table title={t('cdf.details.review', { written: health.data.review.agentSelfWritten, withdrawn: health.data.review.agentSelfWithdrawn })}
              headers={['session', 'reviews', 'units', 'rejected'].map((key) => t(`cdf.details.reviewHead.${key}`))}
              rows={(['code', 'files', 'conversation'] as const).map((kind) => {
                const row = health.data.review!.byType[kind];
                return [t(`cdf.details.reviewType.${kind}`), String(row.reviews), String(row.units), String(row.invalid)];
              })} />
          )}
          {weeks.length > 0 && (
            <div>
              <SectionLabel>{t('cdf.details.history')}</SectionLabel>
              <div className="mt-2 flex h-28 items-end gap-2">
                {weeks.map(([week, count]) => (
                  <div key={week} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1" title={`${week}: ${count}`}>
                    <span className="text-meta tabular-nums text-[var(--color-text-muted)]">{fmt(count)}</span>
                    <div className="w-full rounded-sm" style={{ height: `${Math.max(3, (count / maxWeek) * 64)}px`, background: 'var(--color-zone-cognitive)' }} />
                    <span className="text-meta text-[var(--color-text-dim)]">{week.slice(5)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Table({ title, headers, rows }: { title: string; headers: string[]; rows: string[][] }) {
  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <table className="mt-2 w-full border-collapse text-small">
        <thead><tr>{headers.map((header) => <th key={header} className={`${tableHeadClass} py-1.5 pr-4`}>{header}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => (
          <tr key={`${row[0]}-${index}`} className="border-t border-[var(--color-border)]">
            {row.map((cell, cellIndex) => <td key={cellIndex} className="py-1.5 pr-4 tabular-nums text-[var(--color-text)]">{cell}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
