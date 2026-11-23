/**
 * HistoryView — the history of the Swarm Out panel (design: Knowledge/Mockups/
 * 2026-11-23-swarm-out-dashboard-redesign-v6.html, the History view).
 *
 * One time range at the top (7, 30, 60, 90 days or all; 7 by default; it stays in view
 * while the page scrolls) drives every number below it, top down:
 *  - the verdict: how many runs were accepted with no one stepping in, of all the runs in
 *    the range, next to the same length of time before it;
 *  - one bar split into accepted, discarded and stopped, the facts (runs, median time,
 *    spend), and the runs per day (per week past a month) in the same colours;
 *  - the cost (./CostSection): model only or with machine, per kind of work and carrier;
 *  - the runs grouped by Brain, Project Agent or where they ran (a click filters the list);
 *  - every run, newest first, each the same row as the run view's "recently done", with
 *    its cost; a row opens the run's big card (RunOverlay; J / K walk the list).
 * The numbers are ./historyStats over /runs (polled while shown). Motion: the verdict
 * counts to its new value, the bars grow, a new list fades in; none under reduced motion.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmOutFleet } from '../../services/swarmOut';
import { RunOverlay } from './RunOverlay';
import { DoneRow } from './RunBoard';
import { useRuns } from './live';
import { CostSection } from './CostSection';
import {
  bars, DEFAULT_RANGE, groups, inRange, matches, previousRange, rangeStart, RANGES, RECORD_FILTERS, summarize, groupKey,
  type GroupDim, type Outcome, type Range, type RecordFilter, type Summary,
} from './historyStats';
import { TONE_VAR } from './tone';
import { minutesText } from './where';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const PAGE = 20;

/** The colours of the three settled outcomes (open runs are counted, not drawn). */
const OUT_COLOR: Record<Exclude<Outcome, 'open'>, string> = {
  ok: TONE_VAR.ok, off: 'color-mix(in srgb, var(--color-text-dim) 70%, transparent)', warn: TONE_VAR.attention,
};
const SPLIT: Exclude<Outcome, 'open'>[] = ['ok', 'off', 'warn'];

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** The value, counting from the last one shown to the new one. */
function useCountUp(value: number): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    from.current = value;
    if (start === value || reduced() || typeof requestAnimationFrame === 'undefined') { setShown(value); return undefined; }
    const t0 = performance.now();
    let id = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / 520);
      setShown(Math.round(start + (value - start) * (1 - Math.pow(1 - k, 3))));
      if (k < 1) id = requestAnimationFrame(step);
    };
    id = requestAnimationFrame(step);
    return () => cancelAnimationFrame(id);
  }, [value]);
  return shown;
}

/** True one frame after `key` changes: what the bars' grow transition starts from. */
function useGrown(key: string): boolean {
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    setGrown(false);
    if (typeof requestAnimationFrame === 'undefined') { setGrown(true); return undefined; }
    let b = 0;
    const a = requestAnimationFrame(() => { b = requestAnimationFrame(() => setGrown(true)); });
    return () => { cancelAnimationFrame(a); cancelAnimationFrame(b); };
  }, [key]);
  return grown;
}

function Segs<T extends string | number | null>({ options, value, onChange, testId, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; testId: string; label?: string;
}) {
  return (
    <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0" role="group" aria-label={label} data-testid={testId}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}
          className={`px-3 py-[3px] rounded-md text-small transition-colors ${value === o.value ? 'bg-[var(--color-card)] text-[var(--color-text)] font-semibold shadow-sm' : `${muted} hover:text-[var(--color-text)]`}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

const costText = (s: Pick<Summary, 'cost' | 'costMissing' | 'n'>): string => {
  if (s.n > 0 && s.costMissing >= s.n) return '—';
  const v = s.cost < 1 && s.cost > 0 ? '<$1' : `$${Math.round(s.cost)}`;
  return `${s.costMissing ? '≥ ' : ''}${v}`;
};

function Verdict({ sum, prev, days, chart, grown }: {
  sum: Summary; prev: Summary | null; days: Range; chart: ReturnType<typeof bars>; grown: boolean;
}) {
  const { t } = useTranslation();
  const shown = useCountUp(sum.unattended);
  const max = Math.max(1, ...chart.bars.map((b) => b.by.ok + b.by.off + b.by.warn));
  return (
    <section className={`${card} px-[18px] py-4`} data-testid="history-summary">
      <div className="flex items-baseline flex-wrap gap-x-3 gap-y-1">
        <span className="text-[28px] leading-[34px] font-semibold tabular-nums" data-testid="history-verdict" data-value={sum.unattended}>
          {shown}<span className={`font-normal ${dim}`}> / {sum.n}</span>
        </span>
        <span className="text-heading">{t('swarmOut.hist.verdict')}</span>
        {days != null && (
          <span className="text-small px-2 py-px rounded-full bg-[var(--color-bg-chrome)] text-[var(--color-text-muted)] whitespace-nowrap" data-testid="history-prev">
            {prev && prev.n ? t('swarmOut.hist.prev', { days, un: prev.unattended, n: prev.n }) : t('swarmOut.hist.prevNone')}
          </span>
        )}
      </div>
      <div className="flex h-2 rounded overflow-hidden bg-[var(--color-hover)] mt-3.5" data-testid="history-split">
        {SPLIT.map((k) => (
          <span key={k} className="so-width block h-full" data-outcome={k} data-count={sum.by[k]}
            style={{ width: grown && sum.n ? `${(sum.by[k] / sum.n) * 100}%` : '0%', background: OUT_COLOR[k] }} />
        ))}
      </div>
      <div className={`flex flex-wrap gap-x-4 gap-y-1 mt-2 text-small ${muted}`}>
        {SPLIT.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5" data-testid={`history-count-${k}`}>
            <span aria-hidden className="w-2 h-2 rounded-sm" style={{ background: OUT_COLOR[k] }} />
            {t(`swarmOut.hist.${k}`)} <b className="font-semibold text-[var(--color-text)] tabular-nums">{sum.by[k]}</b>
          </span>
        ))}
        {sum.by.open > 0 && <span>{t('swarmOut.hist.open')} <b className="font-semibold text-[var(--color-text)] tabular-nums">{sum.by.open}</b></span>}
        <span className="ml-auto" data-testid="history-facts">
          {t('swarmOut.hist.facts', { count: sum.n, min: minutesText(sum.medianMin), cost: costText(sum) })}
        </span>
      </div>
      <div className="flex items-end gap-[3px] h-[84px] mt-[18px] border-b border-[var(--color-border)]" data-testid="history-bars" data-weekly={chart.weekly || undefined}>
        {chart.bars.map((b, i) => {
          const n = b.by.ok + b.by.off + b.by.warn + b.by.open;
          return (
            <div key={b.label} className="flex-1 h-full flex flex-col justify-end min-w-0" title={t('swarmOut.hist.dayTip', { day: b.label, count: n })}
              data-testid="history-bar" data-count={n}>
              {(['warn', 'off', 'ok'] as const).map((k) => b.by[k] > 0 && (
                <span key={k} className="so-grow block first:rounded-t-sm"
                  style={{ height: `${(b.by[k] / max) * 100}%`, background: OUT_COLOR[k], opacity: k === 'ok' ? 0.8 : 1,
                    transform: grown ? 'scaleY(1)' : 'scaleY(0)', transitionDelay: `${Math.min(i, 40) * 14}ms` }} />
              ))}
            </div>
          );
        })}
      </div>
      <div className={`flex justify-between mt-1 text-meta ${dim}`}>
        <span>{chart.from}</span><span>{t(chart.weekly ? 'swarmOut.hist.perWeek' : 'swarmOut.hist.perDay')}</span><span>{chart.to}</span>
      </div>
    </section>
  );
}

function Groups({ runs, fleet, dimKey, setDim, selected, onSelect, grown, compact }: {
  runs: Parameters<typeof groups>[0]; fleet: SwarmOutFleet | null; dimKey: GroupDim; setDim: (d: GroupDim) => void;
  selected: string | null; onSelect: (k: string | null) => void; grown: boolean; compact: boolean;
}) {
  const { t } = useTranslation();
  const rows = groups(runs, dimKey, fleet);
  const cols = compact ? 'grid-cols-[minmax(0,1.4fr)_36px_minmax(60px,1fr)_84px]' : 'grid-cols-[minmax(0,1.6fr)_44px_minmax(80px,1.4fr)_96px_64px_60px]';
  return (
    <section className={`${card} px-[18px] py-4 mt-8`} data-testid="history-breakdown" data-dim={dimKey}>
      <div className="flex items-center gap-2.5 mb-1.5">
        <span className="text-body font-semibold">{t('swarmOut.hist.groups')}</span>
        <span className="ml-auto">
          <Segs testId="history-dim" value={dimKey} label={t('swarmOut.hist.groups')} onChange={(d) => { setDim(d); onSelect(null); }}
            options={(['brain', 'agent', 'where'] as const).map((d) => ({ value: d, label: t(`swarmOut.hist.dim.${d}`) }))} />
        </span>
      </div>
      <div className={`grid ${cols} gap-x-3 px-2 -mx-2 pb-1 text-meta ${dim}`}>
        <span>{t('swarmOut.hist.col.name')}</span><span className="text-right">{t('swarmOut.hist.col.runs')}</span>
        <span>{t('swarmOut.hist.col.result')}</span><span className="text-right">{t('swarmOut.hist.col.unattended')}</span>
        {!compact && <><span className="text-right">{t('swarmOut.hist.col.median')}</span><span className="text-right">{t('swarmOut.hist.col.cost')}</span></>}
      </div>
      {rows.map((g) => {
        const on = selected === g.key;
        return (
          <button key={g.key} type="button" aria-pressed={on} onClick={() => onSelect(on ? null : g.key)}
            className={`w-full text-left grid ${cols} gap-x-3 items-center px-2 -mx-2 py-2 rounded-md text-body transition-colors ${on ? '' : 'hover:bg-[var(--color-hover)]'}`}
            style={on ? { background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' } : undefined}
            data-testid="history-group" data-group={g.key}>
            <span className="truncate font-semibold" title={g.key}>{g.key}</span>
            <span className="text-right tabular-nums">{g.sum.n}</span>
            <span className="flex h-1.5 rounded-sm overflow-hidden bg-[var(--color-hover)]">
              {SPLIT.map((k) => <span key={k} className="so-width block h-full" style={{ width: grown && g.sum.n ? `${(g.sum.by[k] / g.sum.n) * 100}%` : '0%', background: OUT_COLOR[k] }} />)}
            </span>
            <span className="text-right tabular-nums">{g.sum.unattended} / {g.sum.n}</span>
            {!compact && <>
              <span className={`text-right tabular-nums ${muted}`}>{minutesText(g.sum.medianMin)}</span>
              <span className={`text-right tabular-nums ${muted}`}>{costText(g.sum)}</span>
            </>}
          </button>
        );
      })}
    </section>
  );
}

export function HistoryView({ hidden, now, compact = false, fleet = null }: {
  hidden: boolean; now: number; compact?: boolean; fleet?: SwarmOutFleet | null;
}) {
  const { t } = useTranslation();
  const { data, failed, reload } = useRuns(hidden);
  const [days, setDays] = useState<Range>(DEFAULT_RANGE);
  const [dimKey, setDim] = useState<GroupDim>('brain');
  const [group, setGroup] = useState<string | null>(null);
  const [filter, setFilter] = useState<RecordFilter>('all');
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<{ id: string; order: string[] } | null>(null);
  const all = useMemo(() => data?.runs ?? [], [data]);
  const ranged = useMemo(() => [...inRange(all, now, days)].sort((a, b) => Date.parse(b.sent_at) - Date.parse(a.sent_at)), [all, now, days]);
  const sum = useMemo(() => summarize(ranged), [ranged]);
  const prev = useMemo(() => (days == null ? null : summarize(previousRange(all, now, days))), [all, now, days]);
  // every run: bars() keeps only the days of the range (and "all" starts at the first run)
  const chart = useMemo(() => bars(all, now, days), [all, now, days]);
  // the verdict's bars grow again when the range changes; the groups' also on a new dimension
  const grown = useGrown(`${days}|${data ? 1 : 0}`);
  const groupsGrown = useGrown(`${days}|${dimKey}|${data ? 1 : 0}`);
  const inGroup = group == null ? ranged : ranged.filter((r) => groupKey(r, dimKey, fleet) === group);
  const rows = inGroup.filter((r) => matches(r, filter));
  const pick = (d: Range) => { setDays(d); setGroup(null); setShown(PAGE); };
  const rangeLabel = (d: Range) => (d == null ? t('swarmOut.hist.all') : t(`swarmOut.hist.r${d}`));
  return (
    <div data-testid="swarm-out-history">
      <div className={`sticky z-[4] flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-[var(--color-border)] backdrop-blur-md ${compact ? '-top-4 -mx-4 -mt-4 px-4 py-2.5 mb-4' : '-top-5 -mx-6 -mt-5 px-6 py-3 mb-5'}`}
        style={{ background: 'color-mix(in srgb, var(--color-bg) 88%, transparent)' }} data-testid="history-range-bar">
        <span className="text-small font-semibold text-[var(--color-text-secondary)]">{t('swarmOut.hist.range')}</span>
        <Segs testId="history-window" label={t('swarmOut.hist.range')} value={days} onChange={pick}
          options={RANGES.map((d) => ({ value: d, label: rangeLabel(d) }))} />
        <span className={`${compact ? 'w-full' : 'ml-auto'} text-small tabular-nums ${muted}`} data-testid="history-range-text">
          {t('swarmOut.hist.rangeText', { from: chart.from, to: chart.to, count: ranged.length })}
        </span>
      </div>
      {failed && <div className={`text-small mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      {!data ? (!failed && <div className={`text-small ${muted}`}>{t('swarmOut.loading')}</div>) : ranged.length === 0 ? (
        <div className={`text-body ${muted}`} data-testid="history-empty">{t('swarmOut.hist.empty')}</div>
      ) : (
        <>
          <Verdict sum={sum} prev={prev} days={days} chart={chart} grown={grown} />
          <CostSection runs={ranged} machines={data.machines} from={rangeStart(all, now, days)} to={now} grown={grown} compact={compact} />
          <Groups runs={ranged} fleet={fleet} dimKey={dimKey} setDim={setDim} selected={group} grown={groupsGrown} compact={compact}
            onSelect={(k) => { setGroup(k); setShown(PAGE); }} />
          <section className="mt-8" data-testid="history-records">
            <div className="flex items-baseline gap-2 mb-2.5 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]">
              <span>{t('swarmOut.hist.records')}</span>
              <span className="inline-block min-w-[18px] px-1.5 rounded-full bg-[var(--color-bg-chrome)] text-center tabular-nums text-[var(--color-text-secondary)]"
                data-testid="history-record-count">{rows.length}</span>
            </div>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {RECORD_FILTERS.map((f) => (
                <button key={f} type="button" aria-pressed={filter === f} onClick={() => { setFilter(f); setShown(PAGE); }}
                  data-testid="history-filter" data-filter={f}
                  className={`btn-sm rounded-full border transition-colors ${filter === f ? 'border-[var(--color-text-secondary)] text-[var(--color-text)] bg-[var(--color-card)]' : `border-[var(--color-border)] ${muted}`}`}>
                  {t(`swarmOut.hist.filter.${f}`)} <b className="font-semibold tabular-nums ml-0.5">{inGroup.filter((r) => matches(r, f)).length}</b>
                </button>
              ))}
              {group != null && (
                <button type="button" onClick={() => setGroup(null)} data-testid="history-group-chip"
                  className="btn-sm rounded-full border text-[var(--color-primary)]"
                  style={{ borderColor: 'color-mix(in srgb, var(--color-primary) 50%, var(--color-border))' }}>{group} ✕</button>
              )}
            </div>
            {rows.length === 0 ? <div className={`text-body ${muted}`}>{t('swarmOut.hist.none')}</div> : (
              <div key={`${days}|${filter}|${group}`} className="so-view-in flex flex-col divide-y divide-[var(--color-border)]">
                {rows.slice(0, shown).map((r) => (
                  <DoneRow key={r.dispatch_id} r={r} fleet={fleet} now={now} compact={compact}
                    cost={r.result?.cost_usd == null ? null : `${r.result.cost_partial ? '≥ ' : ''}$${r.result.cost_usd < 0.1 ? r.result.cost_usd.toFixed(2) : r.result.cost_usd.toFixed(1)}`}
                    onOpen={() => setOpen({ id: r.dispatch_id, order: rows.slice(0, shown).map((x) => x.dispatch_id) })} />
                ))}
              </div>
            )}
            {rows.length > shown && (
              <button type="button" onClick={() => setShown((s) => s + PAGE)} data-testid="history-more"
                className="mt-2 text-small text-[var(--color-primary)] hover:underline">
                {t('swarmOut.hist.more', { count: Math.min(PAGE, rows.length - shown) })}
              </button>
            )}
          </section>
        </>
      )}
      {data?.refusals && data.refusals.total > 0 && (
        // when a Hive that reaches a private network is worth adding: read this count
        <div className={`mt-6 text-small ${muted}`} data-testid="history-refusals">
          {t('swarmOut.history.refusals', { count: data.refusals.total, private: data.refusals.private })}
        </div>
      )}
      {open && (
        <RunOverlay runs={all} asks={data?.asks ?? []} answered={data?.answered ?? []} order={open.order} walk="records" id={open.id} now={now}
          onSelect={(id) => setOpen((o) => (o ? { ...o, id } : o))}
          onSettled={(id) => setOpen((o) => (o ? { ...o, order: o.order.filter((x) => x !== id) } : o))}
          onClose={() => setOpen(null)} onReload={reload} />
      )}
    </div>
  );
}
