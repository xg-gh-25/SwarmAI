/**
 * "What it learned" — the Cognition flow's item list (CognitiveDistillationFlow).
 *
 * One row per unit, as a person reads it: its type on the left, the whole title, its text
 * (one line, whole once opened), then where it landed, where it came from and when; on
 * the right one mark for the decision (new / adds / +1 / already known / not kept /
 * waiting), the entry it was compared with in that mark's tooltip.
 * Opening a row shows the trace: landed → why this place → trust → compared → came from,
 * and the raw record behind a disclosure (GET /ingestion/items/{candidate}).
 *
 * Filters: outcome, keyword, and the landed row the user clicked above (`focus`). Pages of
 * 25, "Show more" appends.
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
  type UnitFilters,
  type UnitOutcome,
} from '../../services/ingestion';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';
import { Segmented } from '../overlay';
import { ZoneLabel } from './needYouParts';
import { OverlayEmpty, OverlayError, OverlayLoading } from './OverlayState';
import { fileName, fmt } from './cdfText';

const OUTCOMES: UnitOutcome[] = ['all', 'new', 'adds', 'reinforced', 'not_kept', 'waiting'];
const EVERY_TURN = new Set(['MEMORY', 'EVOLUTION', 'STEERING', 'USER', 'TOOLS', 'TIMELINE']);
const PAGE = 25;

export function CdfUnitFeed({ window, focus, onClearFocus }: {
  window: IngestionWindow;
  focus: Partial<UnitFilters> & { label?: string };
  onClearFocus: () => void;
}) {
  const { t } = useTranslation();
  const [outcome, setOutcome] = useState<UnitOutcome>('all');
  const [typed, setTyped] = useState('');
  const [keyword, setKeyword] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(typed), 250);
    return () => clearTimeout(timer);
  }, [typed]);

  const { label: focusLabel, ...focusFilters } = focus;
  const filters: UnitFilters = {
    window, outcome, keyword: keyword || undefined, ...focusFilters,
  };
  const units = useInfiniteQuery({
    queryKey: ['ingestion', 'units', filters],
    queryFn: ({ pageParam }) => ingestionService.units(filters, pageParam, PAGE),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.hasNext ? last.page + 1 : undefined),
  });
  const items = units.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section data-testid="cdf-feed">
      <ZoneLabel>{t('cdf.feedTitle')}</ZoneLabel>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          options={OUTCOMES.map((key) => ({ key, label: t(`cdf.outcome.${key}`), testid: `cdf-outcome-${key}` }))}
          value={outcome}
          onChange={setOutcome}
        />
        <input aria-label={t('cdf.search')} placeholder={t('cdf.search')} value={typed} onChange={(event) => setTyped(event.target.value)}
          className="ml-auto h-7 w-56 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-small text-[var(--color-text)]" />
      </div>
      {units.data && (outcome !== 'all' || keyword || focusLabel) && (
        // unfiltered, the count is the dashboard's "captured": not said twice
        <p className="mt-2 text-small text-[var(--color-text-muted)]" data-testid="cdf-feed-count">
          {t('cdf.feedCount', { n: fmt(units.data.pages[0]?.total ?? 0) })}
        </p>
      )}
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

const MARK_TONE: Record<string, { color: string; bg: string }> = {
  new: { color: 'var(--color-status-success)', bg: 'color-mix(in srgb, var(--color-status-success) 12%, transparent)' },
  adds: { color: 'var(--color-status-running)', bg: 'color-mix(in srgb, var(--color-status-running) 12%, transparent)' },
  reinforced: { color: 'var(--color-zone-cognitive)', bg: 'color-mix(in srgb, var(--color-zone-cognitive) 13%, transparent)' },
};
const OTHER_TONE = { color: 'var(--color-text-muted)', bg: 'var(--color-bg-chrome)' };

/** The decision as one short mark, and its detail (the entry compared with, the reason). */
function decision(t: TFunction, unit: FlowUnit): { mark: string; tip: string; tone: { color: string; bg: string } } {
  const tone = MARK_TONE[unit.group] ?? (unit.group === 'place' || unit.group === 'failed'
    ? { color: 'var(--color-status-warning)', bg: 'color-mix(in srgb, var(--color-status-warning) 12%, transparent)' } : OTHER_TONE);
  const mark = t(`cdf.mark.${unit.group}`, { defaultValue: t('cdf.mark.notKept') });
  const kept = unit.group === 'new' || unit.group === 'adds' || unit.group === 'reinforced';
  const tip = kept
    ? (unit.target ? `${t(unit.group === 'reinforced' ? 'cdf.verbReinforced' : 'cdf.verbAdded')} “${unit.target}”` : mark)
    : t(unit.group === 'waiting' ? 'cdf.verbWaiting' : 'cdf.verbNotKept', { reason: reasonLabel(t, unit) })
      + (unit.target ? ` “${unit.target}”` : '');
  return { mark, tip, tone };
}

function UnitRow({ unit }: { unit: FlowUnit }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const when = new Date(unit.createdAt);
  const time = Number.isNaN(when.getTime()) ? unit.createdAt.slice(11, 16)
    : when.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const place = placeLabel(t, unit);
  const d = decision(t, unit);
  return (
    <div className="border-t border-[var(--color-border)] first:border-t-0" data-testid="cdf-unit">
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}
        className="grid w-full grid-cols-[40px_minmax(0,1fr)_auto] gap-x-3 rounded-lg px-2.5 py-2.5 text-left hover:bg-[var(--color-hover)]">
        <span className="mt-px h-[18px] rounded bg-[var(--color-bg-chrome)] text-center text-meta font-semibold leading-[18px] text-[var(--color-text-secondary)]">{kindLabel(t, unit)}</span>
        <span className="min-w-0">
          <span className="block text-body font-semibold text-[var(--color-text)]">{unit.title}</span>
          {unit.text && <span className={`mt-px text-small text-[var(--color-text-secondary)] ${open ? 'block whitespace-pre-wrap' : 'line-clamp-1'}`}>{unit.text}</span>}
          <span className="mt-0.5 flex flex-wrap gap-x-3 text-small text-[var(--color-text-muted)]" data-testid="cdf-unit-meta">
            {place && <span className="text-[var(--color-text-secondary)]">→ {place}</span>}
            {unit.movedFrom && <span style={{ color: 'var(--color-status-warning)' }}>{t('cdf.verbMoved', { store: t(`cdf.store.${unit.movedFrom}`, { defaultValue: unit.movedFrom }) })}</span>}
            <span>{t(`cdf.source.${unit.source}`, { defaultValue: unit.source })}</span>
            <span className="tabular-nums">{time}</span>
          </span>
        </span>
        <span className="mt-0.5 self-start whitespace-nowrap rounded-full px-[7px] py-px text-meta font-semibold"
          style={{ color: d.tone.color, background: d.tone.bg }} title={d.tip} data-testid="cdf-unit-mark">
          {d.mark}<span className="sr-only"> · {d.tip}</span>
        </span>
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
