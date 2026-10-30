/**
 * Cognitive Distillation Flow audit view inside the Need You overlay.
 *
 * Shows bounded overview counts, filterable candidates, full card decisions,
 * write plans, relations, health, and aggregate-only history. No approvals.
 *
 * Key export: `CognitiveDistillationFlow`.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ingestionService,
  type IngestionFilters,
  type IngestionWindow,
} from '../../services/ingestion';
import { Hint, SectionLabel, Segmented, Stat, StatusBadge, tableHeadClass } from '../overlay';
import { OverlayEmpty, OverlayError, OverlayLoading } from './OverlayState';

const WINDOWS: Array<{ key: IngestionWindow; label: string }> = [
  { key: '24h', label: '24h' }, { key: '7d', label: '7d' }, { key: '30d', label: '30d' },
];
const STORE_OPTIONS = { KNOWLEDGE: 0, COMMON_DDD: 0, TIMELINE: 0, STEERING: 0, USER: 0, TOOLS: 0, EVOLUTION: 0, DDD: 0, MEMORY: 0 };
const STAGE_LABEL: Record<string, string> = {
  inbox: 'Inbox', organized: '整理', card_0: '卡 0', card_1: '卡 1', card_2: '卡 2',
  destination_rules: '目的地规则', card_3: '卡 3', write: '写入',
};

function statusTone(value: string): 'success' | 'warning' | 'error' | 'neutral' {
  if (value === 'written' || value === 'reinforced' || value === 'done') return 'success';
  if (value === 'deferred' || value === 'writing' || value === 'partial_pending') return 'warning';
  if (value === 'failed_poison') return 'error';
  return 'neutral';
}

function statusLabel(value: string): string {
  return value === 'reinforced' ? '强化' : value;
}

function json(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function readString(value: Record<string, unknown>, key: string): string {
  const found = value[key];
  return typeof found === 'string' ? found : '';
}

function weekOf(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  const weekday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - weekday);
  return date.toISOString().slice(0, 10);
}

export function CognitiveDistillationFlow() {
  const { t } = useTranslation();
  const [window, setWindow] = useState<IngestionWindow>('24h');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [detailOffset, setDetailOffset] = useState(0);
  const [filters, setFilters] = useState<IngestionFilters>({ time: 30, pageSize: 25 });

  const overview = useQuery({ queryKey: ['ingestion', 'flow', window], queryFn: () => ingestionService.flow(window) });
  const items = useQuery({ queryKey: ['ingestion', 'items', filters, page], queryFn: () => ingestionService.items(filters, page) });
  const detail = useQuery({ queryKey: ['ingestion', 'detail', selected, detailOffset], queryFn: () => ingestionService.detail(selected!, detailOffset), enabled: selected !== null });
  const health = useQuery({ queryKey: ['ingestion', 'health'], queryFn: ingestionService.health });
  const stats = useQuery({ queryKey: ['ingestion', 'stats', 365], queryFn: () => ingestionService.stats(365) });

  const weeks = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of stats.data?.rows ?? []) {
      const day = readString(row, 'utcDay');
      const units = Number(row.units ?? 0);
      if (day) totals.set(weekOf(day), (totals.get(weekOf(day)) ?? 0) + units);
    }
    return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-12);
  }, [stats.data]);
  const maxWeek = Math.max(1, ...weeks.map(([, count]) => count));

  const updateFilter = (key: keyof IngestionFilters, value: string) => {
    setPage(1);
    setSelected(null);
    setDetailOffset(0);
    setFilters((current) => ({ ...current, [key]: value || undefined }));
  };

  return (
    <div className="space-y-5 py-4" data-testid="cdf-flow">
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <SectionLabel>{t('ingestion.overview')}</SectionLabel>
          <Segmented options={WINDOWS} value={window} onChange={setWindow} />
        </div>
        {overview.isPending && <OverlayLoading label={t('ingestion.loadingOverview')} testid="cdf-overview-loading" />}
        {overview.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void overview.refetch(); }} testid="cdf-overview-error" />}
        {overview.data && (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            {Object.entries(overview.data.stages).map(([stage, count]) => (
              <Stat key={stage} label={STAGE_LABEL[stage] ?? stage} value={count} hint={window} />
            ))}
          </div>
        )}
        {overview.data && (
          <>
            <Hint>{t('ingestion.rates', { written: (overview.data.writtenRate * 100).toFixed(1), reinforced: ((overview.data.reinforcedRate ?? 0) * 100).toFixed(1), denominator: String(overview.data.denominatorUnits) })}</Hint>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
              {Object.entries(overview.data.outcomes ?? {}).map(([outcome, count]) => (
                <Stat key={outcome} label={statusLabel(outcome)} value={count} />
              ))}
            </div>
            <Hint>{t('ingestion.sameOrigin', { count: overview.data.sameOriginRerouteCount ?? 0 })}</Hint>
          </>
        )}
      </section>

      {overview.data && (
        <section className="space-y-2">
          <SectionLabel>{t('ingestion.extractionCoverage')}</SectionLabel>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {Object.entries(overview.data.extractionCoverage ?? {}).map(([source, value]) => (
              <Stat key={source} label={source} value={value.unitsEmitted}
                hint={`${value.zeroUnitAttempts}/${value.attempts} zero · ${value.unitsInvalid} invalid`} />
            ))}
            {Object.entries(overview.data.linkStatus ?? {}).map(([state, count]) => (
              <Stat key={`link-${state}`} label={`link · ${state}`} value={count} />
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <SectionLabel right={items.data ? `${items.data.total}` : undefined}>{t('ingestion.candidates')}</SectionLabel>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
          <Filter label={t('ingestion.source')} value={filters.source} onChange={(v) => updateFilter('source', v)} options={overview.data?.sources} />
          <Filter label={t('ingestion.destination')} value={filters.store} onChange={(v) => updateFilter('store', v)} options={STORE_OPTIONS} />
          <Filter label={t('ingestion.state')} value={filters.state} onChange={(v) => updateFilter('state', v)} options={overview.data?.states} />
          <Filter label={t('ingestion.reason')} value={filters.reason} onChange={(v) => updateFilter('reason', v)} options={overview.data?.reasons} />
          <label className="flex flex-col gap-1 text-meta text-[var(--color-text-muted)]">
            {t('ingestion.keyword')}
            <input className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-body text-[var(--color-text)]" value={filters.keyword ?? ''} onChange={(event) => updateFilter('keyword', event.target.value)} />
          </label>
          <Filter label={t('ingestion.time')} value={String(filters.time ?? 30)} onChange={(v) => updateFilter('time', v)} options={{ '1': 0, '7': 0, '30': 0 }} />
        </div>
        {items.isPending && <OverlayLoading label={t('ingestion.loadingItems')} testid="cdf-items-loading" />}
        {items.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void items.refetch(); }} testid="cdf-items-error" />}
        {items.data?.items.length === 0 && <OverlayEmpty testid="cdf-items-empty">{t('ingestion.empty')}</OverlayEmpty>}
        {items.data && items.data.items.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full border-collapse text-body">
              <thead className="bg-[var(--color-card)]"><tr>
                {[t('ingestion.when'), t('ingestion.source'), t('ingestion.lane'), t('ingestion.content'), t('ingestion.state'), t('ingestion.destination'), t('ingestion.reason')].map((label) => <th key={label} className={`${tableHeadClass} px-3 py-2`}>{label}</th>)}
              </tr></thead>
              <tbody>
                {items.data.items.map((item) => (
                  <tr key={item.id} className="border-t border-[var(--color-border)]">
                    <td className="whitespace-nowrap px-3 py-2 text-meta text-[var(--color-text-muted)]">{item.createdAt.slice(0, 16).replace('T', ' ')}</td>
                    <td className="px-3 py-2">{item.source}</td><td className="px-3 py-2">{item.lane}</td>
                    <td className="max-w-sm px-3 py-2"><button type="button" className="w-full text-left text-[var(--color-primary)]" onClick={() => { setSelected(item.id); setDetailOffset(0); }}>{item.preview}</button></td>
                    <td className="px-3 py-2"><StatusBadge tone={statusTone(item.unitStates[0] ?? item.state)}>{item.unitStates.length > 0 ? item.unitStates.map(statusLabel).join(', ') : statusLabel(item.state)}</StatusBadge></td>
                    <td className="px-3 py-2">{item.stores.join(', ') || '—'}</td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">{item.reasons.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {items.data && items.data.total > items.data.pageSize && (
          <div className="flex items-center justify-end gap-2">
            <button className="btn-sm btn-secondary" disabled={page === 1} onClick={() => setPage((v) => Math.max(1, v - 1))}>{t('ingestion.previous')}</button>
            <span className="text-meta text-[var(--color-text-muted)]">{page}</span>
            <button className="btn-sm btn-secondary" disabled={!items.data.hasNext} onClick={() => setPage((v) => v + 1)}>{t('ingestion.next')}</button>
          </div>
        )}
      </section>

      {selected && <DetailPanel loading={detail.isPending} error={detail.isError} value={detail.data} retry={() => { void detail.refetch(); }} close={() => setSelected(null)} loadMore={() => setDetailOffset(detail.data?.nextOffset ?? 0)} />}

      <section className="space-y-2">
        <SectionLabel>{t('ingestion.health')}</SectionLabel>
        {health.isPending && <OverlayLoading label={t('ingestion.loadingHealth')} testid="cdf-health-loading" />}
        {health.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void health.refetch(); }} testid="cdf-health-error" />}
        {health.data && <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Inbox" value={health.data.inboxCount} hint={`${health.data.inboxBytes} B`} />
          <Stat label="MEMORY.md" value={health.data.memoryBytes} hint="bytes" />
          <Stat label="EVOLUTION.md" value={health.data.evolutionBytes} hint="bytes" />
          <Stat label={t('ingestion.lastCleanup')} value={health.data.lastCleanupAt?.slice(0, 10) ?? '—'} />
          {Object.entries(health.data.lanes).map(([lane, value]) => <Stat key={lane} label={lane} value={value.backlog} hint={value.oldestWaitSeconds == null ? '—' : `${Math.floor(value.oldestWaitSeconds / 3600)}h`} />)}
        </div>}
        {health.data && health.data.llmBySource.length > 0 && <HealthTable
          title="LLM 调用 / Token（按 source）"
          headers={['日期', 'Source', '调用', '输入 Token', '输出 Token']}
          rows={health.data.llmBySource.map((row) => [row.utcDay, row.source, String(row.calls), String(row.inputTokens), String(row.outputTokens)])}
        />}
        {health.data?.review && <HealthTable
          title={`会后复盘（30 天，Agent 自身认知写入 EVOLUTION / MEMORY：${health.data.review.agentSelfWritten}，`
            + `被撤回：${health.data.review.agentSelfWithdrawn}${health.data.review.agentSelfWithdrawnRate == null ? ''
              : `（${(health.data.review.agentSelfWithdrawnRate * 100).toFixed(0)}%）`}）`}
          headers={['会话类型', '复盘', '产出单元', '被拒']}
          rows={(['code', 'files', 'conversation'] as const).map((kind) => {
            const row = health.data.review!.byType[kind];
            const label = { code: '改过代码', files: '写过非代码文件', conversation: '只有对话' }[kind];
            return [label, String(row.reviews), String(row.units), String(row.invalid)];
          })}
        />}
      </section>

      <section className="space-y-2">
        <SectionLabel>{t('ingestion.history')}</SectionLabel>
        {stats.isPending && <OverlayLoading label={t('ingestion.loadingHistory')} testid="cdf-history-loading" />}
        {stats.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void stats.refetch(); }} testid="cdf-history-error" />}
        {stats.data && weeks.length === 0 && <OverlayEmpty testid="cdf-history-empty">{t('ingestion.noHistory')}</OverlayEmpty>}
        {weeks.length > 0 && <div className="flex h-32 items-end gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
          {weeks.map(([week, count]) => <div key={week} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1" title={`${week}: ${count}`}>
            <span className="text-meta tabular-nums text-[var(--color-text-muted)]">{count}</span>
            <div className="w-full rounded-sm bg-[var(--color-primary)]" style={{ height: `${Math.max(4, count / maxWeek * 72)}px` }} />
            <span className="text-meta text-[var(--color-text-faint)]">{week.slice(5)}</span>
          </div>)}
        </div>}
      </section>
    </div>
  );
}

function Filter({ label, value, onChange, options }: { label: string; value?: string; onChange: (value: string) => void; options?: Record<string, number> }) {
  return <label className="flex flex-col gap-1 text-meta text-[var(--color-text-muted)]">{label}
    <select className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-body text-[var(--color-text)]" value={value ?? ''} onChange={(event) => onChange(event.target.value)}>
      <option value="">全部</option>{Object.keys(options ?? {}).filter((v) => v !== '-').map((option) => <option key={option} value={option}>{option}</option>)}
    </select>
  </label>;
}

function HealthTable({ title, headers, rows }: { title: string; headers: string[]; rows: string[][] }) {
  return <div className="space-y-2">
    <SectionLabel>{title}</SectionLabel>
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full border-collapse text-body">
        <thead className="bg-[var(--color-card)]"><tr>{headers.map((header) =>
          <th key={header} className={`${tableHeadClass} px-3 py-2`}>{header}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={`${row[0]}-${rowIndex}`} className="border-t border-[var(--color-border)]">
          {row.map((cell, index) => <td key={`${index}-${cell}`} className="px-3 py-2">{cell}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}

function DetailPanel({ loading, error, value, retry, close, loadMore }: { loading: boolean; error: boolean; value: Awaited<ReturnType<typeof ingestionService.detail>> | undefined; retry: () => void; close: () => void; loadMore: () => void }) {
  const { t } = useTranslation();
  return <section className="space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4" data-testid="cdf-detail">
    <div className="flex items-center"><SectionLabel>{t('ingestion.detail')}</SectionLabel><button type="button" className="ml-auto icon-btn" onClick={close} aria-label={t('common.button.close')}><span className="material-symbols-outlined icon-sm">close</span></button></div>
    {loading && <OverlayLoading testid="cdf-detail-loading" />}
    {error && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={retry} testid="cdf-detail-error" />}
    {value && <>
      <AuditBlock title={t('ingestion.rawPayload')} value={value.candidate.payload} />
      <AuditBlock title={t('ingestion.envelope')} value={value.envelope} />
      {value.units.map((unit, index) => <div key={readString(unit, 'unitId') || index} className="space-y-2 border-t border-[var(--color-border)] pt-3">
        <SectionLabel>{readString(unit, 'unitId') || `Unit ${index + 1}`}</SectionLabel>
        <AuditBlock title={t('ingestion.claimGroup')} value={unit.claimGroup} />
        <AuditBlock title={t('ingestion.cards')} value={unit.decision} />
        <AuditBlock title={t('ingestion.destination')} value={unit.destination} />
        <Hint>{readString(unit, 'action')} · {readString(unit, 'reason')}</Hint>
        {unit.canRedact === true && <button type="button" className="btn-sm btn-secondary" onClick={() => {
          const reason = window.prompt(t('ingestion.redactReason'));
          const anchor = readString(unit, 'redactionAnchor');
          if (reason?.trim() && anchor) void ingestionService.redact(anchor, reason.trim()).then(retry);
        }}>{t('ingestion.redact')}</button>}
      </div>)}
      <AuditBlock title={t('ingestion.writePlans')} value={value.writes} />
      <AuditBlock title={t('ingestion.relations')} value={value.relations} />
      {value.hasMore && <button type="button" className="btn-sm btn-secondary" onClick={loadMore}>{t('ingestion.loadMore')}</button>}
    </>}
  </section>;
}

function AuditBlock({ title, value }: { title: string; value: unknown }) {
  return <div><SectionLabel>{title}</SectionLabel><pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-small text-[var(--color-text)]">{json(value)}</pre></div>;
}
