/**
 * Cognition flow — the Cognitive Distillation Flow inside the Need You overlay, written
 * for a person to read (design docs/design/2026-10-30-cdf-flow-view.md). One level of
 * tabs: the overlay holds Needs you · Cognition flow · How it works (CdfGuideline).
 *
 * Read from the result backward:
 * - the dashboard (CdfDashboard): captured, learned, reinforced, not kept, each with its
 *   change against the window before and its trend; the anomalies worth a look (each can
 *   be sent to chat);
 * - side by side, WHERE kept items landed (every-turn files, project brains, General Brain
 *   & Library, one shared scale; a click filters the list) and HOW each item is decided
 *   (the six steps top to bottom, their numbers in one column);
 * - WHAT it learned (CdfUnitFeed), and a one-line health strip whose Details keep the raw
 *   audit (CdfFlowDetails).
 *
 * Data: GET /ingestion/landed (summary + trend) and /ingestion/units (the list), both
 * read-only. The backend decides which step produced an outcome (flow_view.reason_group);
 * this view only phrases it. No approvals: a wrong outcome is something to ask Swarm about.
 *
 * Key export: `CognitiveDistillationFlow`.
 */
import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { bytes, fileName, fmt } from './cdfText';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ingestionService,
  type FlowAttention,
  type IngestionWindow,
  type LandedOverview,
  type LandedRow,
  type UnitFilters,
} from '../../services/ingestion';
import { SectionLabel, Segmented } from '../overlay';
import { OverlayError, OverlayLoading } from './OverlayState';
import { CdfUnitFeed } from './CdfUnitFeed';
import { CdfFlowDetails } from './CdfFlowDetails';
import { CdfDashboard } from './CdfDashboard';
import type { DispatchPrompt } from './overlayRegistry';

const EVERY_TURN = ['TIMELINE', 'EVOLUTION', 'STEERING', 'USER', 'TOOLS', 'MEMORY'];
const GENERAL = ['COMMON_DDD', 'KNOWLEDGE'];
const NOT_KEPT_GROUPS = ['known', 'withdrawn', 'older', 'backlog', 'place', 'no_project', 'retired', 'basic', 'other', 'expired'];
const KEPT = 'var(--color-zone-cognitive)';
const KEPT_SOFT = 'color-mix(in srgb, var(--color-zone-cognitive) 40%, transparent)';
const WARN_TINT = 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)';

export function CognitiveDistillationFlow({ onDispatch, close }: {
  onDispatch?: DispatchPrompt; close?: () => void;
}) {
  const { t } = useTranslation();
  const [window, setWindow] = useState<IngestionWindow>('24h');
  const [focus, setFocus] = useState<Partial<UnitFilters> & { label?: string }>({});
  const landed = useQuery({ queryKey: ['ingestion', 'landed', window], queryFn: () => ingestionService.landed(window) });

  const ask = (text: string) => {
    if (onDispatch?.(t('cdf.att.prompt', { text }), { autoSend: false })) close?.();
  };

  return (
    <div className="space-y-8 py-5" data-testid="cdf-flow">
      <section className="space-y-3" data-testid="cdf-verdict">
        <div className="flex items-center gap-4">
          {landed.data ? <Status data={landed.data} /> : <span className="flex-1" />}
          <Segmented
            options={(['24h', '7d', '30d'] as const).map((key) => ({ key, label: key }))}
            value={window}
            onChange={(next) => { setWindow(next); setFocus({}); }}
          />
        </div>
        {landed.isPending && <OverlayLoading testid="cdf-landed-loading" />}
        {landed.isError && <OverlayError compact message={t('ingestion.loadFailed')}
          onRetry={() => { void landed.refetch(); }} testid="cdf-landed-error" />}
        {landed.data && <CdfDashboard data={landed.data} window={window} notKeptHint={notKeptHint(t, landed.data)} />}
        {landed.data && landed.data.attention.length > 0 && (
          <div className="space-y-0.5" data-testid="cdf-attention">
            {landed.data.attention.map((item) => {
              const line = attentionText(t, item);
              return (
                <div key={item.code} className="flex items-center gap-3 rounded-lg px-3 py-2" style={{ background: WARN_TINT }}>
                  <span className="material-symbols-outlined icon-sm text-[var(--color-status-warning)]" aria-hidden>error</span>
                  <span className="min-w-0 flex-1 text-body text-[var(--color-text)]">
                    {line.main}
                    {line.extra && <span className="text-[var(--color-text-muted)]"> · {line.extra}</span>}
                  </span>
                  {onDispatch && <button type="button" className="btn-sm btn-secondary"
                    onClick={() => ask(line.prompt)}>{t('cdf.att.ask')}</button>}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {landed.data && (
        <div className="grid grid-cols-2 gap-10">
          <Landed data={landed.data} onPick={(next) => setFocus(next)} />
          <HowItDecides data={landed.data} />
        </div>
      )}

      <CdfUnitFeed window={window} overview={landed.data} focus={focus} onClearFocus={() => setFocus({})} />

      <CdfFlowDetails overview={landed.data} window={window} />
    </div>
  );
}

/** One line of state: whether anything waits or failed (the numbers are in the dashboard). */
function Status({ data }: { data: LandedOverview }) {
  const { t } = useTranslation();
  const waiting = data.totals.waiting;
  return (
    <p className="flex-1 text-body text-[var(--color-text-muted)]" data-testid="cdf-status">
      <span className="mr-2 inline-block h-2 w-2 rounded-full align-middle"
        style={{ background: waiting > 0 ? 'var(--color-status-warning)' : 'var(--color-zone-cognitive)' }} />
      {waiting > 0 ? t('cdf.healthWaiting', { n: fmt(waiting) }) : t('cdf.healthOk')}
    </p>
  );
}

/** Why most items were not kept: the largest reason. */
function notKeptHint(t: TFunction, data: LandedOverview): string {
  const top = NOT_KEPT_GROUPS
    .map((group) => [group, data.groups[group] ?? 0] as const)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 1)          // the largest reason; the rest is in the steps and the list
    .map(([group, count]) => t('cdf.groupCount', { label: t(`cdf.group.${group}`), n: fmt(count) }));
  return top.length ? t('cdf.dash.notKeptMostly', { top: top.join(t('cdf.and')) }) : t('cdf.notKeptNone');
}

export function attentionText(t: TFunction, item: FlowAttention): { main: string; extra?: string; prompt: string } {
  const count = fmt(item.count ?? 0);
  switch (item.code) {
    case 'cleanup_overdue':
    {
      const main = t('cdf.att.cleanup_overdue', { n: count, size: bytes(item.bytes ?? 0) });
      return { main, prompt: main };
    }
    case 'spelling':
      return { main: t('cdf.att.spelling', { example: item.example ?? '' }), prompt: t('cdf.att.spellingPrompt') };
    case 'failed': case 'waiting_long': case 'breaker': case 'links_skipped':
    {
      const main = t(`cdf.att.${item.code}`, { n: count });
      return { main, prompt: main };
    }
    default:
      return { main: `${item.code} · ${count}`, prompt: `${item.code} · ${count}` };
  }
}

interface DestRow { key: string; label: string; hint?: string; written: number; reinforced: number; indent?: boolean; filter: Partial<UnitFilters>; flag?: string }

function Landed({ data, onPick }: { data: LandedOverview; onPick: (focus: Partial<UnitFilters> & { label?: string }) => void }) {
  const { t } = useTranslation();
  const tiers = useMemo(() => buildTiers(t, data.landed), [t, data.landed]);
  const max = Math.max(1, ...tiers.flatMap((tier) => tier.rows.map((row) => row.written + row.reinforced)));
  const movedTo = data.moved.reduce((sum, row) => sum + row.count, 0);
  const movedFrom = [...new Set(data.moved.map((row) => t(`cdf.store.${row.from}`, { defaultValue: row.from })))].join(' / ');
  const spelling = data.attention.find((item) => item.code === 'spelling')?.example?.split(', ') ?? [];
  return (
    <section data-testid="cdf-landed" className="min-w-0">
      <SectionLabel right={t('cdf.landedHint')}>{t('cdf.landedTitle')}</SectionLabel>
      {tiers.every((tier) => tier.rows.length === 0) && <p className="mt-3 text-body text-[var(--color-text-muted)]">{t('cdf.landedEmpty')}</p>}
      <div className="mt-3 space-y-5">
        {tiers.filter((tier) => tier.rows.length > 0).map((tier) => (
          <div key={tier.key}>
            <div className="mb-1 flex items-baseline gap-2.5">
              <span className="text-body font-semibold text-[var(--color-text)]">{t(`cdf.tier.${tier.key}`)}</span>
              <span className="text-small text-[var(--color-text-muted)]">{t(`cdf.tier.${tier.key}Hint`)}</span>
            </div>
            {tier.rows.map((row) => (
              <Fragment key={row.key}>
                <button type="button" data-testid={`cdf-dest-${row.key}`}
                  onClick={() => onPick({ ...row.filter, label: row.label })}
                  className="grid w-full grid-cols-[minmax(0,170px)_minmax(0,1fr)_170px] items-center gap-4 rounded-md px-2.5 py-1.5 text-left hover:bg-[var(--color-hover)]">
                  <span className={`truncate text-body ${row.indent ? 'pl-4 text-[var(--color-text-muted)]' : 'text-[var(--color-text)]'}`}>
                    {row.label}{row.hint && <span className="ml-1.5 text-small text-[var(--color-text-dim)]">{row.hint}</span>}
                  </span>
                  <span className="flex h-2 overflow-hidden rounded-sm" style={{ background: 'color-mix(in srgb, var(--color-border) 60%, transparent)' }}>
                    <span style={{ width: `${(row.written / max) * 100}%`, background: KEPT }} />
                    <span style={{ width: `${(row.reinforced / max) * 100}%`, background: KEPT_SOFT }} />
                  </span>
                  <span className="whitespace-nowrap text-right text-body tabular-nums text-[var(--color-text-muted)]">
                    {countsText(t, row.written, row.reinforced)}
                  </span>
                </button>
                {spelling.includes(row.label) && row.label === spelling[spelling.length - 1] && (
                  <p className="pl-7 text-small text-[var(--color-status-warning)]">{t('cdf.att.spelling', { example: spelling.join(', ') })}</p>
                )}
              </Fragment>
            ))}
            {tier.key === 'every_turn' && movedTo > 0 && (
              <p className="mt-1.5 border-l-2 border-[var(--color-border)] px-2.5 py-1 text-small text-[var(--color-text-muted)]" data-testid="cdf-moved-note">
                {t('cdf.movedNote', { n: fmt(movedTo), stores: movedFrom })}
              </p>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-4 text-small text-[var(--color-text-dim)]">
        <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2.5 rounded-sm" style={{ background: KEPT }} />{t('cdf.legendKept')}</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2.5 rounded-sm" style={{ background: KEPT_SOFT }} />{t('cdf.legendReinforced')}</span>
        <span>{t('cdf.legendScale')}</span>
      </div>
    </section>
  );
}

function countsText(t: TFunction, written: number, reinforced: number): string {
  if (written && reinforced) return t('cdf.counts', { written: fmt(written), reinforced: fmt(reinforced) });
  return written ? t('cdf.countsNew', { written: fmt(written) }) : t('cdf.countsReinforced', { reinforced: fmt(reinforced) });
}

function buildTiers(t: TFunction, rows: LandedRow[]): Array<{ key: 'every_turn' | 'project' | 'general'; rows: DestRow[] }> {
  const sum = (list: LandedRow[]) => list.reduce((acc, row) => ({
    written: acc.written + row.written, reinforced: acc.reinforced + row.reinforced }), { written: 0, reinforced: 0 });
  const byStore = (stores: string[]) => stores.map((store) => {
    const list = rows.filter((row) => row.store === store);
    return { store, list, ...sum(list) };
  }).filter((entry) => entry.list.length > 0)
    .sort((a, b) => (b.written + b.reinforced) - (a.written + a.reinforced));
  const storeRow = (entry: { store: string; list: LandedRow[]; written: number; reinforced: number }): DestRow => ({
    key: entry.store, label: t(`cdf.store.${entry.store}`, { defaultValue: entry.store }),
    hint: t(`cdf.storeHint.${entry.store}`, { defaultValue: '' }) || undefined,
    written: entry.written, reinforced: entry.reinforced,
    // one file (every every-turn store): filter by where units really landed
    filter: entry.list.length === 1 && entry.list[0].path ? { path: entry.list[0].path } : { store: entry.store },
  });
  const projects = new Map<string, LandedRow[]>();
  for (const row of rows.filter((item) => item.store === 'DDD')) {
    const name = row.project ?? '—';
    projects.set(name, [...(projects.get(name) ?? []), row]);
  }
  const projectRows: DestRow[] = [...projects.entries()]
    .map(([name, list]) => ({ name, list, ...sum(list) }))
    .sort((a, b) => (b.written + b.reinforced) - (a.written + a.reinforced))
    .flatMap((entry) => [
      { key: `p-${entry.name}`, label: entry.name, written: entry.written, reinforced: entry.reinforced,
        filter: { pathPrefix: `Projects/${entry.name}/` } },
      ...(entry.list.length > 1 ? [...entry.list].sort((a, b) => (b.written + b.reinforced) - (a.written + a.reinforced))
        .map((row) => ({ key: `f-${row.path}`, label: fileName(row.path), written: row.written, reinforced: row.reinforced,
          indent: true, filter: { path: row.path ?? undefined } })) : []),
    ]);
  return [
    { key: 'every_turn', rows: byStore(EVERY_TURN).map(storeRow) },
    { key: 'project', rows: projectRows },
    { key: 'general', rows: byStore(GENERAL).map(storeRow) },
  ];
}

function HowItDecides({ data }: { data: LandedOverview }) {
  const { t } = useTranslation();
  const s = data.steps;
  const sources = Object.entries(data.sources).sort((a, b) => b[1] - a[1]);
  const retired = data.groups.retired ?? 0;
  const backlog = data.groups.backlog ?? 0;
  const g = (name: string) => data.groups[name] ?? 0;
  const also = [
    retired > 0 ? t('cdf.alsoRetired', { n: fmt(retired) }) : null,
    backlog > 0 ? t('cdf.alsoBacklog', { n: fmt(backlog) }) : null,
    g('no_project') > 0 ? t('cdf.alsoNoProject', { n: fmt(g('no_project')) }) : null,
    g('other') > 0 ? t('cdf.alsoOther', { n: fmt(g('other')) }) : null,
    g('expired') > 0 ? t('cdf.alsoExpired', { n: fmt(g('expired')) }) : null,
    data.totals.waiting > 0 ? t('cdf.alsoWaiting', { n: fmt(data.totals.waiting) }) : null,
  ].filter(Boolean).join('; ');
  // one row per step, the numbers in one column so they read top to bottom
  const steps: Array<{ key: string; out: ReactNode }> = [
    { key: 'captured', out: <Out tone="text">{fmt(s.captured)}</Out> },
    { key: 'basic', out: <Out tone="dim">{t('cdf.out.dropped', { n: fmt(s.basicDropped) })}</Out> },
    { key: 'place', out: s.placeDropped > 0 ? <Out tone="warn">{t('cdf.out.place', { n: fmt(s.placeDropped) })}</Out>
      : <Out tone="dim">{t('cdf.out.dropped', { n: '0' })}</Out> },
    { key: 'trust', out: <Out tone="accent">{t('cdf.out.moved', { n: fmt(s.moved) })}</Out> },
    { key: 'compare', out: <>
      <Out tone="kept">{t('cdf.out.reinforced', { n: fmt(s.reinforced) })}</Out>
      <Out tone="dim">{t('cdf.out.known', { n: fmt(s.known) })}</Out>
      {s.withdrawn > 0 && <Out tone="dim">{t('cdf.out.withdrawn', { n: fmt(s.withdrawn) })}</Out>}
      {s.older > 0 && <Out tone="dim">{t('cdf.out.older', { n: fmt(s.older) })}</Out>}</> },
    { key: 'written', out: <>
      <Out tone="kept">{t('cdf.out.written', { n: fmt(s.written) })}</Out>
      <Out tone="dim">{t('cdf.out.writtenSplit', { new: fmt(data.totals.new), adds: fmt(data.totals.adds) })}</Out></> },
  ];
  return (
    <section data-testid="cdf-decide" className="min-w-0">
      <SectionLabel right={t('cdf.decideHint')}>{t('cdf.decideTitle')}</SectionLabel>
      <ol className="mt-3">
        {steps.map((step, index) => (
          <li key={step.key} className="grid grid-cols-[22px_minmax(0,1fr)_230px] gap-x-4 py-2.5">
            <span className="text-small tabular-nums text-[var(--color-text-dim)]">{index + 1}</span>
            <div className="min-w-0">
              <div className="text-body font-semibold text-[var(--color-text)]">{t(`cdf.step.${step.key}`)}</div>
              <p className="mt-0.5 text-small text-[var(--color-text-muted)]">{t(`cdf.step.${step.key}Rule`)}</p>
              {step.key === 'captured' && (
                <p className="mt-1 text-small text-[var(--color-text-dim)]">
                  {sources.map(([source, count]) => `${t(`cdf.source.${source}`, { defaultValue: source })} ${fmt(count)}`).join(' · ')}
                </p>
              )}
            </div>
            <div className="text-right">{step.out}</div>
          </li>
        ))}
      </ol>
      {also && <p className="mt-2 text-small text-[var(--color-text-dim)]">{t('cdf.alsoPrefix')}{also}.</p>}
    </section>
  );
}

function Out({ tone, children }: { tone: 'dim' | 'warn' | 'accent' | 'kept' | 'text'; children: ReactNode }) {
  const color = { dim: 'var(--color-text-dim)', warn: 'var(--color-status-warning)', accent: 'var(--color-primary)',
    kept: KEPT, text: 'var(--color-text)' }[tone];
  return <span className="block whitespace-nowrap text-small tabular-nums" style={{ color }}>{children}</span>;
}
