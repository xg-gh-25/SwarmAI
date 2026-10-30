/**
 * "What it learned" — the Cognition flow's item list (CognitiveDistillationFlow).
 *
 * One row per unit, as a person reads it: kind/type, the whole title, its text (clamped to
 * two lines, whole once opened), and one line read backward — where it landed, what the
 * decision was (new / added to «entry» / reinforced / moved here from / not kept: why).
 * Opening a row shows the trace: landed → why this place → trust → compared → came from,
 * and the raw record behind a disclosure (GET /ingestion/items/{candidate}).
 *
 * Filters: outcome (segmented, with counts from the summary), source, place, keyword, and
 * the landed row the user clicked above (`focus`). Pages of 25, "Show more" appends.
 *
 * Key export: `CdfUnitFeed`.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ingestionService,
  type FlowUnit,
  type IngestionWindow,
  type LandedOverview,
  type UnitFilters,
  type UnitOutcome,
  type UnitPlace,
} from '../../services/ingestion';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';
import { SectionLabel, Segmented } from '../overlay';
import { OverlayEmpty, OverlayError, OverlayLoading } from './OverlayState';
import { fileName, fmt } from './cdfText';

const OUTCOMES: UnitOutcome[] = ['all', 'new', 'adds', 'reinforced', 'not_kept', 'waiting'];
const PLACES: UnitPlace[] = ['every_turn', 'project', 'general'];
const EVERY_TURN = new Set(['MEMORY', 'EVOLUTION', 'STEERING', 'USER', 'TOOLS', 'TIMELINE']);
const PAGE = 25;
const selectClass = 'h-7 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-small text-[var(--color-text-muted)]';

function outcomeCount(outcome: UnitOutcome, overview?: LandedOverview): number | undefined {
  if (!overview) return undefined;
  const totals = overview.totals;
  return { all: overview.total, new: totals.new, adds: totals.adds, reinforced: totals.reinforced,
    not_kept: totals.notKept, waiting: totals.waiting }[outcome];
}

export function CdfUnitFeed({ window, overview, focus, onClearFocus }: {
  window: IngestionWindow;
  overview?: LandedOverview;
  focus: Partial<UnitFilters> & { label?: string };
  onClearFocus: () => void;
}) {
  const { t } = useTranslation();
  const [outcome, setOutcome] = useState<UnitOutcome>('all');
  const [source, setSource] = useState('');
  const [place, setPlace] = useState<UnitPlace | ''>('');
  const [typed, setTyped] = useState('');
  const [keyword, setKeyword] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(typed), 250);
    return () => clearTimeout(timer);
  }, [typed]);

  const { label: focusLabel, ...focusFilters } = focus;
  const filters: UnitFilters = {
    window, outcome, source: source || undefined, place: place || undefined,
    keyword: keyword || undefined, ...focusFilters,
  };
  const units = useInfiniteQuery({
    queryKey: ['ingestion', 'units', filters],
    queryFn: ({ pageParam }) => ingestionService.units(filters, pageParam, PAGE),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.hasNext ? last.page + 1 : undefined),
  });
  const items = units.data?.pages.flatMap((page) => page.items) ?? [];
  const sources = Object.keys(overview?.sources ?? {}).sort();

  return (
    <section data-testid="cdf-feed">
      <SectionLabel>{t('cdf.feedTitle')}</SectionLabel>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Segmented
          options={OUTCOMES.map((key) => {
            const count = outcomeCount(key, overview);
            return { key, label: count == null ? t(`cdf.outcome.${key}`) : `${t(`cdf.outcome.${key}`)} ${fmt(count)}`, testid: `cdf-outcome-${key}` };
          })}
          value={outcome}
          onChange={setOutcome}
        />
        <select aria-label={t('cdf.everySource')} className={selectClass} value={source} onChange={(event) => setSource(event.target.value)}>
          <option value="">{t('cdf.everySource')}</option>
          {sources.map((key) => <option key={key} value={key}>{t(`cdf.source.${key}`, { defaultValue: key })}</option>)}
        </select>
        <select aria-label={t('cdf.everyPlace')} className={selectClass} value={place} onChange={(event) => setPlace(event.target.value as UnitPlace | '')}>
          <option value="">{t('cdf.everyPlace')}</option>
          {PLACES.map((key) => <option key={key} value={key}>{t(`cdf.tier.${key}`)}</option>)}
        </select>
        <input aria-label={t('cdf.search')} placeholder={t('cdf.search')} value={typed} onChange={(event) => setTyped(event.target.value)}
          className="ml-auto h-7 w-56 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-small text-[var(--color-text)]" />
      </div>
      {focusLabel && (
        <div className="mt-2 flex items-center gap-2 text-small text-[var(--color-text-muted)]" data-testid="cdf-focus">
          <span>{t('cdf.showing', { label: focusLabel })}</span>
          <button type="button" className="text-[var(--color-primary)]" onClick={onClearFocus}>{t('cdf.clearFilter')}</button>
        </div>
      )}
      <div className="mt-2">
        {units.isPending && <OverlayLoading testid="cdf-units-loading" />}
        {units.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void units.refetch(); }} testid="cdf-units-error" />}
        {units.data && items.length === 0 && <OverlayEmpty testid="cdf-units-empty">{t('cdf.feedEmpty')}</OverlayEmpty>}
        {items.map((unit) => <UnitRow key={unit.unitId} unit={unit} />)}
        {units.data?.pages[0]?.searchCapped && <p className="px-2.5 py-2 text-small text-[var(--color-text-muted)]">{t('cdf.searchCapped', { n: '5,000' })}</p>}
        {units.hasNextPage && (
          <div className="mt-1 px-2.5 py-2">
            <button type="button" className="text-small text-[var(--color-primary)]" disabled={units.isFetchingNextPage}
              onClick={() => { void units.fetchNextPage(); }}>{t('cdf.showMore')}</button>
          </div>
        )}
      </div>
    </section>
  );
}

function kindLabel(t: TFunction, unit: FlowUnit): string {
  if (unit.kind === 'cognition' && unit.type) return t(`cdf.type.${unit.type}`, { defaultValue: unit.type });
  return t(`cdf.kind.${unit.kind}`, { defaultValue: unit.kind });
}

export function placeLabel(t: TFunction, unit: Pick<FlowUnit, 'store' | 'project' | 'path' | 'section'>): string {
  if (!unit.store) return '';
  if (unit.store === 'DDD') {
    return [unit.project, fileName(unit.path).replace(/\.md$/, ''), unit.section].filter(Boolean).join(' › ');
  }
  const store = t(`cdf.store.${unit.store}`, { defaultValue: unit.store });
  const file = unit.store === 'COMMON_DDD' || unit.store === 'KNOWLEDGE' ? fileName(unit.path).replace(/\.md$/, '') : '';
  return [store, file, unit.section].filter(Boolean).join(' › ');
}

function reasonLabel(t: TFunction, unit: FlowUnit): string {
  return t(`cdf.group.${unit.group}`, { defaultValue: unit.reason ?? unit.group });
}

function aboutLabel(t: TFunction, scope: string | null): string {
  if (!scope) return t('cdf.about.none');
  if (scope.startsWith('project:')) return t('cdf.about.project', { name: scope.slice('project:'.length) });
  return t(`cdf.about.${scope}`, { defaultValue: scope });
}

function Quote({ children }: { children: string }) {
  return <span className="text-[var(--color-text-muted)]">“{children}”</span>;
}

function DecisionLine({ unit }: { unit: FlowUnit }) {
  const { t } = useTranslation();
  const place = placeLabel(t, unit);
  const kept = unit.group === 'new' || unit.group === 'adds' || unit.group === 'reinforced';
  if (!kept) {
    const key = unit.group === 'waiting' ? 'cdf.verbWaiting' : 'cdf.verbNotKept';
    const warn = unit.group === 'place' || unit.group === 'failed';
    return (
      <div className="text-small" style={{ color: warn ? 'var(--color-status-warning)' : 'var(--color-text-muted)' }}>
        {t(key, { reason: reasonLabel(t, unit) })}
        {unit.group === 'known' && unit.target && <> <Quote>{unit.target}</Quote></>}
        {unit.group === 'place' && unit.scope?.startsWith('project:') && <> · {aboutLabel(t, unit.scope)}</>}
      </div>
    );
  }
  const verb = unit.group === 'reinforced'
    ? <><span className="text-[var(--color-zone-cognitive)]">{t('cdf.verbReinforced')}</span> {unit.target ? <Quote>{unit.target}</Quote> : t('cdf.verbAnEntry')}</>
    : unit.movedFrom
      ? <span className="text-[var(--color-status-warning)]">{t('cdf.verbMoved', { store: t(`cdf.store.${unit.movedFrom}`, { defaultValue: unit.movedFrom }) })}</span>
      : unit.group === 'adds'
        ? <><span className="text-[var(--color-zone-cognitive)]">{t('cdf.verbAdded')}</span> {unit.target ? <Quote>{unit.target}</Quote> : t('cdf.verbAnEntry')}</>
        : <span className="text-[var(--color-zone-cognitive)]">{t('cdf.verbNew')}</span>;
  return (
    <div className="flex flex-wrap items-baseline gap-1.5 text-small text-[var(--color-text-dim)]">
      <span aria-hidden>→</span><span className="text-[var(--color-text)]">{place}</span><span>·</span><span>{verb}</span>
    </div>
  );
}

function UnitRow({ unit }: { unit: FlowUnit }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const when = new Date(unit.createdAt);
  const time = Number.isNaN(when.getTime()) ? unit.createdAt.slice(11, 16)
    : when.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  return (
    <div className="border-t border-[var(--color-border)]" data-testid="cdf-unit">
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}
        className="block w-full px-2.5 py-3 text-left hover:bg-[var(--color-hover)]">
        <div className="flex items-baseline gap-2.5">
          <span className="shrink-0 rounded border border-[var(--color-border)] px-1.5 text-meta text-[var(--color-text-muted)]">{kindLabel(t, unit)}</span>
          <span className="min-w-0 flex-1 text-body font-semibold text-[var(--color-text)]">{unit.title}</span>
          <span className="shrink-0 text-small text-[var(--color-text-dim)]">{t(`cdf.source.${unit.source}`, { defaultValue: unit.source })} · {time}</span>
        </div>
        {unit.text && <p className={`mt-1 text-body text-[var(--color-text-muted)] ${open ? 'whitespace-pre-wrap' : 'line-clamp-2'}`}>{unit.text}</p>}
        <div className="mt-1"><DecisionLine unit={unit} /></div>
      </button>
      {open && <Trace unit={unit} time={time} />}
    </div>
  );
}

function Trace({ unit, time }: { unit: FlowUnit; time: string }) {
  const { t } = useTranslation();
  const [offset, setOffset] = useState(0);
  const detail = useQuery({
    queryKey: ['ingestion', 'detail', unit.candidateId, offset],
    queryFn: () => ingestionService.detail(unit.candidateId, offset),
  });
  const kept = unit.group === 'new' || unit.group === 'adds' || unit.group === 'reinforced';
  const place = placeLabel(t, unit);
  const kind = kindLabel(t, unit).toLowerCase();
  const about = aboutLabel(t, unit.scope);
  const basis = t(`cdf.basis.${unit.basis ?? 'none'}`, { defaultValue: unit.basis ?? '—' });
  const movedFrom = unit.movedFrom ? t(`cdf.store.${unit.movedFrom}`, { defaultValue: unit.movedFrom }) : '';
  const write = detail.data?.writes.find((row) => row.unitId === unit.unitId) ?? detail.data?.writes[0];
  const openFile = typeof write?.openFile === 'string' ? write.openFile : null;
  const record = detail.data?.units.find((row) => row.unitId === unit.unitId);
  const anchor = typeof record?.redactionAnchor === 'string' ? record.redactionAnchor : null;
  const redact = () => {
    const reason = globalThis.prompt(t('ingestion.redactReason'));
    if (reason?.trim() && anchor) void ingestionService.redact(anchor, reason.trim()).then(() => detail.refetch());
  };

  const why = !unit.store
    ? t('cdf.whyDropped', { kind, about, reason: reasonLabel(t, unit) })
    : unit.movedFrom ? t('cdf.whyMoved', { kind, about, from: movedFrom, place }) : t('cdf.whyRoute', { kind, about, place });
  const trust = !kept && !unit.store ? '—'
    : unit.movedFrom ? t('cdf.trustMoved', { from: movedFrom, basis, place })
      : EVERY_TURN.has(unit.store ?? '') ? t('cdf.trustEvery', { basis }) : t('cdf.trustRecalled', { basis });
  let compared: string;
  if (unit.group === 'adds') compared = unit.target ? t('cdf.cmpAdds', { target: unit.target }) : t('cdf.cmpAddsNoTarget');
  else if (unit.group === 'reinforced') compared = unit.target ? t('cdf.cmpSame', { target: unit.target }) : t('cdf.cmpSameText');
  else if (unit.group === 'known') compared = unit.target ? t('cdf.cmpKnown', { target: unit.target }) : t('cdf.cmpKnownText');
  else if (unit.group === 'withdrawn') compared = t('cdf.cmpWithdrawn');
  else if (unit.group === 'older') compared = t('cdf.cmpOlder');
  else if (unit.group === 'new') compared = t('cdf.cmpNew');
  else compared = t('cdf.cmpNone');

  const rows: Array<[string, ReactNode]> = [
    ['landed', kept ? <>{place}{openFile && <button type="button" className="ml-2 text-small text-[var(--color-primary)]"
      onClick={() => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: openFile } }))}>{t('cdf.trace.openFile')}</button>}</>
      : <span className="text-[var(--color-text-muted)]">{t('cdf.trace.notWritten')}</span>],
    ['why', why],
    ['trust', trust],
    ['compared', compared],
    ['from', <>{t('cdf.fromLine', { source: t(`cdf.source.${unit.source}`, { defaultValue: unit.source }), when: time })}
      {unit.sourceRef && <span className="ml-2 text-small text-[var(--color-text-dim)]">{unit.sourceRef}</span>}</>],
  ];
  return (
    <div className="mx-2.5 mb-3 rounded-lg bg-[var(--color-card)] px-4 py-3.5" data-testid="cdf-trace">
      <div className="grid grid-cols-[120px_1fr] gap-x-4 gap-y-1.5">
        {rows.map(([key, value]) => (
          <div key={key} className="contents">
            <span className="pt-px text-small text-[var(--color-text-dim)]">{t(`cdf.trace.${key}`)}</span>
            <span className="text-body text-[var(--color-text)]">{value}</span>
          </div>
        ))}
      </div>
      {record?.canRedact === true && anchor && (
        <button type="button" className="btn-sm btn-secondary mt-3" onClick={redact}>{t('ingestion.redact')}</button>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-small text-[var(--color-text-dim)]">{t('cdf.trace.raw')}</summary>
        {detail.isPending && <p className="mt-2 text-small text-[var(--color-text-muted)]">{t('cdf.trace.loading')}</p>}
        {detail.isError && <OverlayError compact message={t('ingestion.loadFailed')} onRetry={() => { void detail.refetch(); }} testid="cdf-trace-error" />}
        {detail.data && (
          <>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--color-bg)] p-3 text-small text-[var(--color-text-muted)]">
              {JSON.stringify({ unit: detail.data.units.find((row) => row.unitId === unit.unitId) ?? detail.data.units[0],
                envelope: detail.data.envelope, writes: detail.data.writes, relations: detail.data.relations }, null, 2)}
            </pre>
            {detail.data.hasMore && <button type="button" className="mt-1 text-small text-[var(--color-primary)]"
              onClick={() => setOffset(detail.data?.nextOffset ?? 0)}>{t('cdf.trace.loadMore')}</button>}
          </>
        )}
      </details>
    </div>
  );
}
