/**
 * Cognition flow — the Cognitive Distillation Flow inside the Need You overlay (design
 * docs/design/2026-10-30-cdf-flow-view.md; layout Knowledge/Mockups/
 * 2026-11-24-needs-you-redesign.html). Read top to bottom:
 *
 * - one status line, the same conclusion the Needs you tab and the north star draw: the
 *   CDF items of the attention queue (stuck, failing) and the flow's own anomalies, each
 *   with "Hand to Swarm"; amber when any, else one calm line;
 * - one number: how many entries it learned (new or added to an entry) against the window
 *   before; under it what was captured, reinforced, already known and moved out of the
 *   every-turn files; one bar per hour (24 h) or day;
 * - where it landed, three columns on one scale (every-turn files, project brains, the
 *   General Brain and Library); a row filters the list below;
 * - what it learned (CdfUnitFeed); the raw audit behind one line (CdfFlowDetails).
 * How each item is decided, with these numbers, is drawn in How it works.
 *
 * Data: GET /ingestion/landed (summary + trend), /ingestion/units (the list) and the
 * attention queue, all read-only. No approvals: a wrong outcome is something to ask Swarm.
 *
 * Key exports: `CognitiveDistillationFlow`, `attentionText`.
 */
import { useMemo, useState } from 'react';
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
import { Segmented } from '../overlay';
import { OverlayError, OverlayLoading } from './OverlayState';
import { CdfUnitFeed } from './CdfUnitFeed';
import { CdfFlowDetails } from './CdfFlowDetails';
import { overlayQuery } from './overlayQueries';
import { itemOpener, needsYouPrompt } from './NeedsYouRow';
import { bytes, fileName, fmt } from './cdfText';
import { Big, CARD, Compare, DIM, MUTED, ZoneLabel, useGrown } from './needYouParts';
import type { DispatchPrompt } from './overlayRegistry';

const EVERY_TURN = ['MEMORY', 'EVOLUTION', 'STEERING', 'USER', 'TOOLS', 'TIMELINE'];
const GENERAL = ['COMMON_DDD', 'KNOWLEDGE'];
const LEARNED = 'color-mix(in srgb, var(--color-status-success) 72%, transparent)';
const REINFORCED = 'var(--color-zone-cognitive)';
const KNOWN = 'color-mix(in srgb, var(--color-text-dim) 50%, transparent)';
const SHOWN_PROJECTS = 4;

type Focus = Partial<UnitFilters> & { label?: string };

export function CognitiveDistillationFlow({ onDispatch, close, onOpenHealth }: {
  onDispatch?: DispatchPrompt; close?: () => void; onOpenHealth?: () => void;
}) {
  const { t } = useTranslation();
  const [window, setWindow] = useState<IngestionWindow>('7d');
  const [focus, setFocus] = useState<Focus>({});
  const landed = useQuery({ queryKey: ['ingestion', 'landed', window], queryFn: () => ingestionService.landed(window) });
  const hand = (text: string) => { if (onDispatch?.(text, { autoSend: true })) close?.(); };

  return (
    <div className="space-y-8 py-2" data-testid="cdf-flow">
      {/* the window on the right; the status beside it, or under it on a narrow window */}
      <div className="flex flex-col-reverse gap-3 md:flex-row md:items-start md:gap-4">
        <Status overview={landed.data} ready={!!landed.data} onHand={onDispatch ? hand : undefined} onOpenHealth={onOpenHealth} />
        <div className="self-end md:self-auto">
          <Segmented
            options={(['24h', '7d', '30d'] as const).map((key) => ({ key, label: t(`cdf.w${key}`) }))}
            value={window}
            onChange={(next) => { setWindow(next); setFocus({}); }}
          />
        </div>
      </div>
      {landed.isPending && <OverlayLoading testid="cdf-landed-loading" />}
      {landed.isError && <OverlayError compact message={t('ingestion.loadFailed')}
        onRetry={() => { void landed.refetch(); }} testid="cdf-landed-error" />}
      {landed.data && <Verdict data={landed.data} window={window} />}
      {landed.data && <Landed data={landed.data} focus={focus} onPick={(next) => setFocus(next)} />}
      <CdfUnitFeed window={window} focus={focus} onClearFocus={() => setFocus({})} />
      <CdfFlowDetails overview={landed.data} window={window} />
    </div>
  );
}

/** One status line: the attention queue's CDF items and the flow's anomalies, or a calm one
 *  once both have loaded. An item only the person can answer keeps its own action (open its
 *  page, or handle it in chat), never "Hand to Swarm": the same item says the same thing here
 *  as in the Needs you tab. */
function Status({ overview, ready, onHand, onOpenHealth }: {
  overview?: LandedOverview; ready: boolean; onHand?: (text: string) => void; onOpenHealth?: () => void;
}) {
  const { t } = useTranslation();
  const q = useQuery(overlayQuery.needsYou());
  const queued = (q.data?.items ?? []).filter((it) => it.source === 'ingestion');
  const flow = (overview?.attention ?? []).map((item) => attentionText(t, item));
  type Line = { key: string; main: string; extra?: string; prompt: string; mine: boolean; open?: { label: string; run: () => void } };
  const lines: Line[] = [
    ...queued.map((it): Line => {
      const opener = it.handler === 'agent' ? null : itemOpener(it);
      return { key: it.id, main: it.title, extra: it.summary && it.summary !== it.title ? it.summary : undefined,
        prompt: needsYouPrompt(it), mine: it.handler !== 'agent',
        open: opener ? { label: opener.kind === 'settings' ? t('needYou.openSettings') : t('needYou.openSwarmOut'), run: opener.open } : undefined };
    }),
    ...flow.map((line, i): Line => ({ key: `flow-${i}`, main: line.main, extra: line.extra, mine: false,
      prompt: t('cdf.att.prompt', { text: line.prompt }) })),
  ];
  if (!lines.length) {
    // "healthy" only once both readings arrived; unknown is never shown as healthy
    if (!ready || q.isError || !q.data) return <span className="min-w-0 flex-1" data-testid="cdf-status" />;
    return (
      <p className={`flex min-w-0 flex-1 items-center gap-2 text-body ${MUTED}`} data-testid="cdf-status" data-ok="true">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--color-status-success)' }} />
        {t('cdf.healthOk')}
      </p>
    );
  }
  return (
    <div className="min-w-0 flex-1 space-y-1.5" data-testid="cdf-status">
      {lines.map((line) => (
        <div key={line.key} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl px-4 py-3" data-testid="cdf-attention"
          data-mine={line.mine} style={{ background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }}>
          <span aria-hidden className="so-live inline-block h-2 w-2 rounded-full" style={{ background: 'var(--color-status-warning)' }} />
          <span className="min-w-0 flex-1 text-body">
            <b className="font-semibold">{line.main}</b>
            {line.extra && <span className="text-[var(--color-text-secondary)]"> · {line.extra}</span>}
          </span>
          {line.open ? (
            <button type="button" className="btn-sm btn-primary" onClick={line.open.run}>{line.open.label}</button>
          ) : onHand && (line.mine ? (
            <button type="button" className="btn-sm btn-primary" onClick={() => onHand(line.prompt)}>{t('needYou.handle')}</button>
          ) : (
            <button type="button" className="btn-sm btn-secondary" style={{ color: 'var(--color-zone-cognitive)' }}
              onClick={() => onHand(line.prompt)}>{t('needYou.hand')}</button>
          ))}
        </div>
      ))}
      {onOpenHealth && (
        <button type="button" className={`text-small ${MUTED} hover:text-[var(--color-text)]`} onClick={onOpenHealth}
          data-testid="cdf-open-health">{t('cdf.openHealth')} ›</button>
      )}
    </div>
  );
}

function bucketLabel(bucket: string, window: IngestionWindow): string {
  if (window === '24h') {
    const date = new Date(`${bucket}:00:00Z`);
    return Number.isNaN(date.getTime()) ? bucket.slice(11) : `${String(date.getHours()).padStart(2, '0')}:00`;
  }
  return bucket.slice(5).replace('-', '/');
}

function Verdict({ data, window }: { data: LandedOverview; window: IngestionWindow }) {
  const { t } = useTranslation();
  const learned = data.totals.new + data.totals.adds;
  const prev = data.previous?.learned ?? null;
  const series = data.series;
  const grown = useGrown(`${window}:${series.map((p) => `${p.learned}/${p.reinforced}/${p.notKept}`).join(',')}`);
  const max = Math.max(1, ...series.map((p) => p.learned + p.reinforced + p.notKept));
  const was = t(`cdf.dash.was${window}`, { n: fmt(prev ?? 0) });
  let change: string | undefined;
  if (prev != null && prev > 0 && learned !== prev) {
    const p = Math.round(((learned - prev) / prev) * 100);
    change = p !== 0 ? `${p > 0 ? '▲' : '▼'} ${Math.abs(p)}%` : undefined;
  }
  return (
    <section className={`${CARD} px-[18px] py-4`} data-testid="cdf-dashboard">
      <Big testid="cdf-learned" value={fmt(learned)} what={t('cdf.dash.learnedWhat')}
        compare={prev == null ? <Compare was={t('cdf.dash.noPrevious')} /> : <Compare change={change} was={was} testid="cdf-learned-was" />} />
      <div className={`mt-2 flex flex-wrap gap-x-[18px] gap-y-1 text-small ${MUTED}`} data-testid="cdf-facts">
        {([['captured', data.total], ['reinforced', data.totals.reinforced], ['known', data.totals.notKept], ['moved', data.steps.moved]] as const).map(([k, n]) => (
          <span key={k}>{t(`cdf.fact.${k}`)} <b className="font-semibold tabular-nums text-[var(--color-text-secondary)]">{fmt(n)}</b></span>
        ))}
      </div>
      <div className="mt-4 flex h-[84px] items-end gap-[3px] border-b border-[var(--color-border)]" role="group" aria-label={t('cdf.dash.barsLabel')}>
        {series.map((p, i) => {
          const tip = t('cdf.dash.barTip', { when: bucketLabel(p.bucket, window), learned: fmt(p.learned), reinforced: fmt(p.reinforced), known: fmt(p.notKept) });
          return (
            <div key={p.bucket} className="flex h-full min-w-0 flex-1 flex-col-reverse" title={tip} role="img" aria-label={tip} data-testid="cdf-bar">
              {([['learned', LEARNED], ['reinforced', REINFORCED], ['notKept', KNOWN]] as const).map(([k, color]) => p[k] > 0 && (
                <span key={k} className="so-grow block" style={{ height: `${Math.max(2, (p[k] / max) * 100)}%`, background: color,
                  transform: grown ? 'scaleY(1)' : 'scaleY(0)', transitionDelay: `${i * 10}ms` }} />
              ))}
            </div>
          );
        })}
      </div>
      {series.length > 0 && (
        <div className={`mt-1 flex justify-between text-meta ${DIM}`} aria-hidden>
          <span>{bucketLabel(series[0].bucket, window)}</span>
          <span>{bucketLabel(series[Math.floor(series.length / 2)].bucket, window)}</span>
          <span>{bucketLabel(series[series.length - 1].bucket, window)}</span>
        </div>
      )}
    </section>
  );
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
    case 'failed': case 'waiting_long': case 'links_skipped':
    {
      const main = t(`cdf.att.${item.code}`, { n: count });
      return { main, prompt: main };
    }
    default:
      return { main: `${item.code} · ${count}`, prompt: `${item.code} · ${count}` };
  }
}

interface DestRow { key: string; label: string; written: number; reinforced: number; sub?: boolean; filter: Focus }
interface Tier { key: 'every_turn' | 'project' | 'general'; total: number; rows: DestRow[] }

function buildTiers(t: TFunction, rows: LandedRow[]): Tier[] {
  const sum = (list: LandedRow[]) => list.reduce((acc, row) => ({
    written: acc.written + row.written, reinforced: acc.reinforced + row.reinforced }), { written: 0, reinforced: 0 });
  const byStore = (stores: string[]): DestRow[] => stores.map((store) => {
    const list = rows.filter((row) => row.store === store);
    return { store, list, ...sum(list) };
  }).filter((entry) => entry.list.length > 0)
    .sort((a, b) => b.written - a.written)
    .map((entry) => ({
      key: entry.store, label: t(`cdf.store.${entry.store}`, { defaultValue: entry.store }),
      written: entry.written, reinforced: entry.reinforced,
      // one file (every every-turn store): filter by where units really landed
      filter: entry.list.length === 1 && entry.list[0].path ? { path: entry.list[0].path } : { store: entry.store },
    }));
  const projects = new Map<string, LandedRow[]>();
  for (const row of rows.filter((item) => item.store === 'DDD')) {
    const name = row.project ?? '—';
    projects.set(name, [...(projects.get(name) ?? []), row]);
  }
  const ranked = [...projects.entries()].map(([name, list]) => ({ name, list, ...sum(list) }))
    .sort((a, b) => b.written - a.written);
  const projectRows: DestRow[] = ranked.slice(0, SHOWN_PROJECTS).flatMap((entry, index) => [
    { key: `p-${entry.name}`, label: entry.name, written: entry.written, reinforced: entry.reinforced,
      filter: { pathPrefix: `Projects/${entry.name}/` } },
    // the largest project's files, one row each
    ...(index === 0 && entry.list.length > 1 ? [...entry.list].filter((row) => row.written > 0).sort((a, b) => b.written - a.written)
      .map((row) => ({ key: `f-${row.path}`, label: fileName(row.path).replace(/\.md$/, ''), written: row.written,
        reinforced: row.reinforced, sub: true, filter: { path: row.path ?? undefined } })) : []),
  ]);
  const rest = ranked.slice(SHOWN_PROJECTS);
  if (rest.length) {
    const r = rest.reduce((acc, e) => ({ written: acc.written + e.written, reinforced: acc.reinforced + e.reinforced }), { written: 0, reinforced: 0 });
    projectRows.push({ key: 'p-rest', label: t('cdf.restProjects', { count: rest.length }), ...r, sub: true, filter: { store: 'DDD' } });
  }
  const tiers: Tier[] = [
    { key: 'every_turn', rows: byStore(EVERY_TURN), total: 0 },
    { key: 'project', rows: projectRows, total: ranked.reduce((n, e) => n + e.written, 0) },
    { key: 'general', rows: byStore(GENERAL), total: 0 },
  ];
  for (const tier of tiers) if (tier.key !== 'project') tier.total = tier.rows.reduce((n, r) => n + r.written, 0);
  return tiers;
}

function Landed({ data, focus, onPick }: { data: LandedOverview; focus: Focus; onPick: (focus: Focus) => void }) {
  const { t } = useTranslation();
  const tiers = useMemo(() => buildTiers(t, data.landed), [t, data.landed]);
  const max = Math.max(1, ...tiers.flatMap((tier) => tier.rows.map((row) => row.written)));
  const grown = useGrown(tiers.flatMap((tier) => tier.rows.map((row) => `${row.key}:${row.written}`)).join('|'));
  const picked = focus.label;
  if (tiers.every((tier) => tier.rows.length === 0)) {
    return <section data-testid="cdf-landed"><ZoneLabel>{t('cdf.landedTitle')}</ZoneLabel>
      <p className={`text-body ${MUTED}`}>{t('cdf.landedEmpty')}</p></section>;
  }
  return (
    <section data-testid="cdf-landed">
      <ZoneLabel>{t('cdf.landedTitle')}</ZoneLabel>
      <div className="grid grid-cols-1 gap-x-7 gap-y-5 md:grid-cols-3">
        {tiers.map((tier) => (
          <div key={tier.key} className="min-w-0" data-testid={`cdf-tier-${tier.key}`}>
            <div className="mb-1.5 flex items-baseline gap-2 border-b border-[var(--color-border)] pb-1.5 text-body font-semibold">
              {t(`cdf.tier.${tier.key}`)}
              <span className="ml-auto tabular-nums text-[var(--color-text-secondary)]">{fmt(tier.total)}</span>
            </div>
            {tier.rows.length === 0 && <p className={`px-1.5 py-1 text-small ${DIM}`}>—</p>}
            {tier.rows.map((row) => (
              <button key={row.key} type="button" data-testid={`cdf-dest-${row.key}`} aria-pressed={picked === row.label}
                title={row.reinforced ? t('cdf.countsTip', { written: fmt(row.written), reinforced: fmt(row.reinforced) }) : undefined}
                onClick={() => onPick(picked === row.label ? {} : { ...row.filter, label: row.label })}
                className={`grid w-full grid-cols-[minmax(0,1fr)_64px_44px] items-center gap-x-2.5 rounded-md px-1.5 py-1 text-left hover:bg-[var(--color-hover)] ${row.sub ? 'text-small text-[var(--color-text-secondary)]' : 'text-body'}`}
                style={picked === row.label ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                <span className={`truncate ${row.sub ? 'pl-3.5' : ''} ${row.written ? '' : DIM}`}>{row.label}</span>
                <span className="h-1.5 overflow-hidden rounded-[3px] bg-[var(--color-bg-chrome)]">
                  <span className="so-width block h-full rounded-[3px]"
                    style={{ width: grown && row.written ? `${Math.max(2, (row.written / max) * 100)}%` : '0%', background: 'var(--color-status-success)' }} />
                </span>
                <span className={`text-right tabular-nums ${row.written ? 'text-[var(--color-text-secondary)]' : DIM}`}>{row.written ? fmt(row.written) : '—'}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      {data.steps.moved > 0 && (
        <p className={`mt-2.5 text-small ${MUTED}`} data-testid="cdf-moved-note">
          {t('cdf.movedNote', { n: fmt(data.steps.moved) })}
        </p>
      )}
    </section>
  );
}
