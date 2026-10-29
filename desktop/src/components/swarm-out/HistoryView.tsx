/**
 * HistoryView — the history of the Swarm Out panel (design
 * docs/design/2026-10-29-swarm-out-history.md, mockup
 * Knowledge/Designs/mockups/2026-10-29-swarm-out-run-card-history.html).
 *
 * It answers, top down: how much of the work sent out came back done with no one
 * stepping in (one sentence, the only number that stands out); where it goes well or
 * not (a table by Brain, type or Hive: outcomes as a stacked bar, the share done
 * unattended, the median time, the spend, the Autonomy Judge, the lessons); what it
 * cost; and the runs themselves, newest first, each row the run card's summary that
 * opens into the card itself (RunCard, the same component).
 *
 * Everything comes from /runs (the whole dispatch log, polled while shown) through
 * the pure functions of ./live (inWindow, tallyRuns, breakdown, dimensionCounts,
 * matchesFilter): a run without a result is "not known", never a zero, and every rate
 * names its denominator (the runs whose ending is known). A clicked table row filters
 * the records; the outcome chips filter them too, the two together. Status colours
 * follow ./tone; labels come from the locale files (swarmOut.history.*). Worker
 * occupancy has no data source: it says TBD.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmOutRun } from '../../services/swarmOut';
import { Dot, RunCard, Tbd } from './LiveDispatches';
import { useOpenRun } from './openSession';
import {
  agentFacts, breakdown, dailyByOutcome, dimensionCounts, durationText, endedLabel, groupOf, historyOutcome, inWindow,
  matchesFilter, runSpend, runType, spendText, tallyRuns, useRuns, when,
  type BreakdownDim, type HistoryOutcome, type OutcomeBucket, type RecordFilter, type RunTally,
} from './live';
import { runTone, TONE_VAR, type Tone } from './tone';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';

/** Records shown at first, and added by each "show earlier". */
const PAGE = 20;
const WINDOWS: { days: number | null; key: string }[] = [{ days: 7, key: 'd7' }, { days: 30, key: 'd30' }, { days: null, key: 'all' }];

/** The outcome segments in stacking order (errors first, so a failure is never buried),
 *  each in its tone's colour: the same colours as the run cards. */
const SEGMENTS: { key: HistoryOutcome; tone: Tone }[] = [
  { key: 'error', tone: 'error' }, { key: 'stopped', tone: 'attention' }, { key: 'completed', tone: 'ok' },
  { key: 'running', tone: 'busy' }, { key: 'other', tone: 'unknown' },
];
const segColor = (tone: Tone): string => (tone === 'unknown' ? 'var(--color-text-dim)' : TONE_VAR[tone]);

function Seg<T extends string | number | null>({ options, value, onChange, testId }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; testId: string;
}) {
  return (
    <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0" data-testid={testId}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}
          className={`px-2.5 py-[2px] rounded-md text-[12px] ${value === o.value ? 'bg-[var(--color-card)] text-[var(--color-text)] font-medium shadow-sm' : muted}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** The outcomes of a group as one stacked bar; the counts on hover. */
function OutcomeBar({ t: tally }: { t: RunTally }) {
  const { t } = useTranslation();
  const parts = SEGMENTS.filter((s) => tally.by[s.key] > 0);
  return (
    <div className="flex h-[8px] w-full max-w-[140px] min-w-[60px] rounded overflow-hidden bg-[var(--color-hover)]" data-testid="history-outcome-bar"
      title={parts.map((s) => `${t(`swarmOut.history.outcome.${s.key}`)} ${tally.by[s.key]}`).join(t('swarmOut.sep.clause'))}>
      {parts.map((s) => <span key={s.key} style={{ width: `${(tally.by[s.key] / tally.runs) * 100}%`, background: segColor(s.tone) }} />)}
    </div>
  );
}

/** "✓2 !1 ✕0" for the pipeline runs of a group; nothing when it has none. */
function JudgeCounts({ t: tally }: { t: RunTally }) {
  const j = tally.judge;
  if (j.PASS + j.WARN + j.BLOCK === 0) return <span className={dim}>—</span>;
  return (
    <span className="inline-flex gap-1.5 tabular-nums">
      {j.PASS > 0 && <span style={{ color: TONE_VAR.ok }}>✓{j.PASS}</span>}
      {j.WARN > 0 && <span style={{ color: TONE_VAR.attention }}>!{j.WARN}</span>}
      {j.BLOCK > 0 && <span style={{ color: TONE_VAR.error }}>✕{j.BLOCK}</span>}
    </span>
  );
}

/** The share done unattended, over the runs whose ending is known. */
const unattendedText = (x: RunTally): string => (x.known ? `${x.unattended}/${x.known}` : '—');

function Summary({ x }: { x: RunTally }) {
  const { t } = useTranslation();
  const outcomes = (['completed', 'error', 'stopped', 'other', 'running'] as const).filter((k) => x.by[k] > 0)
    .map((k) => t(`swarmOut.history.verdictPart.${k}`, { count: x.by[k] })).join(t('swarmOut.sep.list'));
  const pct = x.known ? Math.round((x.unattended / x.known) * 100) : null;
  const judged = x.judge.PASS + x.judge.WARN + x.judge.BLOCK;
  const cell = (testId: string, value: React.ReactNode, label: React.ReactNode, title?: string) => (
    <span className="inline-flex items-baseline gap-1 min-w-0" data-testid={testId} title={title}>
      <b className="text-[14px] font-semibold tabular-nums text-[var(--color-text)]">{value}</b><span>{label}</span>
    </span>
  );
  return (
    <div data-testid="history-summary">
      <div className="text-[15px] leading-snug" data-testid="history-verdict">
        <b className="text-[20px] font-bold tabular-nums" style={{ color: TONE_VAR.ok }}>{x.unattended}</b>
{' '}<span>{t('swarmOut.history.verdict', { count: x.unattended })}</span>
        <span className="text-[var(--color-text-secondary)]">{t('swarmOut.history.verdictRest', { count: x.runs, parts: outcomes })}</span>
      </div>
      <div className={`mt-2.5 flex flex-wrap items-baseline gap-x-7 gap-y-1.5 text-[12px] ${muted}`}>
        {cell('history-sum-unattended', unattendedText(x),
          x.known ? t('swarmOut.history.sum.unattended', { known: x.known, pct }) : t('swarmOut.history.sum.unattendedNone'))}
        {cell('history-sum-human', x.loops, <>{t('swarmOut.history.sum.human', { count: x.loops })}
          {x.unanswered > 0 && <span style={{ color: TONE_VAR.attention }}>{t('swarmOut.history.sum.unanswered', { count: x.unanswered })}</span>}</>)}
        {cell('history-sum-time', durationText(x.medianActive), t('swarmOut.history.sum.median'))}
        {cell('history-sum-spend', spendText(x), t('swarmOut.history.sum.spend'),
          [x.tokensMissing && t('swarmOut.history.sum.tokensMissing', { count: x.tokensMissing }),
            x.costMissing && t('swarmOut.history.sum.costMissing', { count: x.costMissing })].filter(Boolean).join('\n') || undefined)}
        {judged > 0 && cell('history-sum-judge', <JudgeCounts t={x} />, t('swarmOut.history.sum.judge', { count: judged }))}
      </div>
    </div>
  );
}

function DailyBars({ runs, now, days }: { runs: SwarmOutRun[]; now: number; days: number }) {
  const { t } = useTranslation();
  const label = (k: OutcomeBucket) => t(`swarmOut.bucket.${k}`);
  const data = dailyByOutcome(runs, now, days);
  const max = Math.max(1, ...data.map((d) => d.count));
  const order: { key: OutcomeBucket; tone: Tone }[] = [{ key: 'error', tone: 'error' }, { key: 'stopped', tone: 'attention' },
    { key: 'completed', tone: 'ok' }, { key: 'other', tone: 'unknown' }];
  return (
    <div className="mt-3.5" data-testid="swarm-out-daily">
      <div className="flex items-end gap-[4px] h-[44px] border-b border-[var(--color-border)]">
        {data.map((d) => (
          <div key={d.label} className="flex-1 flex flex-col justify-end h-full"
            title={d.count
              ? t('swarmOut.daily.dayWith', { day: d.label, count: d.count, parts: order.filter((b) => d.by[b.key]).map((b) => `${label(b.key)} ${d.by[b.key]}`).join(t('swarmOut.sep.clause')) })
              : t('swarmOut.daily.day', { day: d.label, count: d.count })}
            data-testid="swarm-out-day" data-count={d.count} data-error={d.by.error}>
            {/* top down: errors first, so a failure is never buried */}
            {order.map((b) => d.by[b.key] > 0 && (
              <div key={b.key} style={{ height: `${(d.by[b.key] / max) * 100}%`, background: b.tone === 'unknown' ? 'var(--color-border)' : TONE_VAR[b.tone] }} className="first:rounded-t" />
            ))}
          </div>
        ))}
      </div>
      <div className={`flex justify-between text-[10.5px] mt-1 ${dim}`}>
        <span>{data[0].label}</span>
        <span className="flex gap-2">{order.map((b) => (
          <span key={b.key} className="inline-flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm" style={{ background: b.tone === 'unknown' ? 'var(--color-border)' : TONE_VAR[b.tone] }} />{label(b.key)}</span>
        ))}</span>
        <span>{data[data.length - 1].label}</span>
      </div>
    </div>
  );
}

function Breakdown({ runs, dimKey, setDim, selected, onSelect }: {
  runs: SwarmOutRun[]; dimKey: BreakdownDim; setDim: (d: BreakdownDim) => void;
  selected: string | null; onSelect: (key: string | null) => void;
}) {
  const { t } = useTranslation();
  const rows = breakdown(runs, dimKey);
  const showLessons = dimKey === 'brain' && rows.some((r) => r.tally.lessonsReturned > 0);
  const th = 'font-medium text-[11px] text-[var(--color-text-dim)] px-2 pb-1.5';
  const td = 'px-2 py-[7px] border-t border-[var(--color-border)] tabular-nums';
  return (
    <section className="mt-7" data-testid="history-breakdown" data-dim={dimKey}>
      <div className="flex items-center gap-3 mb-2">
        <h3 className="m-0 text-[13px] font-semibold">{t('swarmOut.history.breakdown.title')}</h3>
        <Seg testId="history-dim" value={dimKey} onChange={(d) => { setDim(d); onSelect(null); }}
          options={(['brain', 'type', 'hive'] as const).map((d) => ({ value: d, label: t(`swarmOut.history.breakdown.${d}`) }))} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr className="text-left">
              <th className={th}>{t(`swarmOut.history.breakdown.${dimKey}`)}</th>
              <th className={`${th} text-right`}>{t('swarmOut.history.col.runs')}</th>
              <th className={th}>{t('swarmOut.history.col.outcome')}</th>
              <th className={`${th} text-right`}>{t('swarmOut.history.col.unattended')}</th>
              <th className={`${th} text-right`}>{t('swarmOut.history.col.median')}</th>
              <th className={`${th} text-right`}>{t('swarmOut.history.col.spend')}</th>
              <th className={`${th} text-right`}>Judge</th>
              {showLessons && <th className={`${th} text-right`}>{t('swarmOut.history.col.lessons')}</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((g) => {
              const on = selected === g.key;
              return (
                <tr key={g.key || '-'} data-testid="history-group" data-group={g.key} aria-selected={on}
                  onClick={() => onSelect(on ? null : g.key)} title={t('swarmOut.history.breakdown.filterHint')}
                  className="cursor-pointer hover:bg-[var(--color-hover)]"
                  style={on ? { background: 'color-mix(in srgb, var(--color-zone-work) 14%, transparent)' } : undefined}>
                  <td className={`${td} max-w-[240px]`}>
                    <span className="font-medium">{g.label}</span>
                    {g.agent && <span className={dim}> · {g.agent}</span>}
                    {g.tally.checksNotRun > 0 && <span className="ml-1.5 text-[11px]" style={{ color: TONE_VAR.attention }}>{t('swarmOut.byBrain.checks', { count: g.tally.checksNotRun })}</span>}
                  </td>
                  <td className={`${td} text-right`}>{g.tally.runs}</td>
                  <td className={td}><OutcomeBar t={g.tally} /></td>
                  <td className={`${td} text-right`}>{unattendedText(g.tally)}</td>
                  <td className={`${td} text-right whitespace-nowrap`}>{durationText(g.tally.medianActive)}</td>
                  <td className={`${td} text-right whitespace-nowrap`}>{spendText(g.tally, true)}</td>
                  <td className={`${td} text-right`}><JudgeCounts t={g.tally} /></td>
                  {showLessons && <td className={`${td} text-right`} title={t('swarmOut.history.col.lessonsTitle')}>
                    {g.tally.lessonsReturned} / {g.tally.lessonsWritten}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Dimensions runs={runs} />
    </section>
  );
}

/** The five audit dimensions over the pipeline runs: worst verdicts first. */
function Dimensions({ runs }: { runs: SwarmOutRun[] }) {
  const { t } = useTranslation();
  const rows = dimensionCounts(runs);
  if (!rows) return null;
  const n = rows[0] ? Object.values(rows[0].counts).reduce((a, b) => a + b, 0) : 0;
  const rank = (c: Record<string, number>) => (c.BLOCK ? 0 : c.WARN ? 1 : c.PASS ? 2 : 3);
  return (
    <div className={`mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] ${muted}`} data-testid="history-dimensions">
      <span>{t('swarmOut.history.dimensions', { count: n })}</span>
      {[...rows].sort((a, b) => rank(a.counts) - rank(b.counts)).map(({ dim: d, counts: c }) => (
        <span key={d} data-testid="history-dimension" data-dim={d}>
          {d}{' '}
          {c.BLOCK > 0 && <b style={{ color: TONE_VAR.error }}>✕{c.BLOCK} </b>}
          {c.WARN > 0 && <b style={{ color: TONE_VAR.attention }}>!{c.WARN} </b>}
          {c.PASS > 0 && <b style={{ color: TONE_VAR.ok }}>✓{c.PASS} </b>}
          {(c.NA + c.PENDING) > 0 && <span className={dim}>N/A {c.NA + c.PENDING}</span>}
        </span>
      ))}
    </div>
  );
}

/** One record: the run card's summary (title, Brain · Agent, type, ending, time, spend,
 *  human loops, sent); a click opens the card itself in place. */
function HistoryRow({ r, open, onToggle, compact, onReload }: {
  r: SwarmOutRun; open: boolean; onToggle: () => void; compact: boolean; onReload: () => void;
}) {
  const { t } = useTranslation();
  const openRun = useOpenRun(r);
  const tone = runTone(r);
  const res = r.result;
  const f = agentFacts(r);
  const ended = historyOutcome(r) === 'running' ? { text: t('swarmOut.history.outcome.running'), tone: 'busy' as Tone }
    : res ? endedLabel(res) : { text: t('swarmOut.history.outcome.other'), tone: 'unknown' as Tone };
  const loops = res ? res.work.human.approved + res.work.human.denied + res.work.human.unanswered + res.work.human.answered_questions : null;
  const spend = res ? spendText(runSpend(res), true) : '—';
  const cells = [
    <span key="agent" className={`truncate ${f.none ? dim : muted}`} title={f.title}>{f.none ? f.agent : `${f.brain} · ${f.agent}`}</span>,
    <span key="type" className={`truncate ${res ? muted : dim}`}>{runType(r).label}</span>,
    <span key="ended" className="truncate" style={ended.tone === 'unknown' ? { color: 'var(--color-text-muted)' } : { color: TONE_VAR[ended.tone] }}
      title={ended.text} data-testid="history-run-ended">{ended.tone === 'unknown' ? t('swarmOut.history.outcome.other') : ended.text}</span>,
    <span key="time" className="text-right tabular-nums">{durationText(res?.active_s ?? null)}</span>,
    <span key="spend" className="text-right tabular-nums whitespace-nowrap" data-testid="history-run-spend">{spend}</span>,
    <span key="human" className="text-right tabular-nums" style={res?.work.human.unanswered ? { color: TONE_VAR.attention } : undefined}>
      {loops == null ? '—' : res!.work.human.unanswered ? t('swarmOut.history.unanswered', { count: res!.work.human.unanswered }) : loops}</span>,
  ];
  const openBtn = openRun ? (
    <button type="button" onClick={(e) => { e.stopPropagation(); openRun(); }} data-testid="history-run-open"
      title={t('swarmOut.hiveTab.open', { title: r.title })} aria-label={t('swarmOut.hiveTab.open', { title: r.title })}
      className={`material-symbols-outlined text-[16px] ${muted} hover:text-[var(--color-text)]`}>open_in_new</button>
  ) : <span />;
  return (
    <div className="border-t border-[var(--color-border)] first:border-t-0" data-testid="history-run" data-tone={tone} data-open={open || undefined}>
      <div role="button" tabIndex={0} aria-expanded={open} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
        className={`px-3 py-2 text-[12px] cursor-pointer hover:bg-[var(--color-hover)] ${open ? 'bg-[var(--color-hover)]' : ''}`}>
        {compact ? (
          <>
            <div className="flex items-center gap-2 min-w-0">
              <Dot tone={tone} />
              <span className="font-medium truncate flex-1 min-w-0" title={r.title}>{r.title}</span>
              {cells[2]}
              <span className={`tabular-nums whitespace-nowrap ${muted}`}>{when(r.sent_at)}</span>
              {openBtn}
            </div>
            <div className={`mt-0.5 pl-[15px] flex gap-2 min-w-0 ${muted}`}>
              {cells[0]}<span>·</span>{cells[1]}<span>·</span>{cells[3]}<span>·</span>{cells[4]}<span>·</span>{cells[5]}
            </div>
          </>
        ) : (
          <div className="grid items-center gap-x-2.5 min-w-0" style={{ gridTemplateColumns: ROW_COLUMNS }}>
            <Dot tone={tone} />
            <span className="font-medium truncate" title={r.title}>{r.title}</span>
            {cells}
            <span className={`text-right tabular-nums whitespace-nowrap ${muted}`}>{when(r.sent_at)}</span>
            {openBtn}
          </div>
        )}
      </div>
      {open && <div className="px-3 pb-3 pt-1" data-testid="history-run-card"><RunCard r={r} onReload={onReload} /></div>}
    </div>
  );
}

const ROW_COLUMNS = '7px minmax(0,2.4fr) minmax(0,1.4fr) minmax(0,1.1fr) minmax(0,0.8fr) minmax(0,0.6fr) minmax(0,0.8fr) minmax(0,0.6fr) minmax(0,0.9fr) 18px';

function Records({ runs, filter, setFilter, group, groupLabel, onClearGroup, compact, onReload }: {
  runs: SwarmOutRun[]; filter: RecordFilter; setFilter: (f: RecordFilter) => void;
  group: string | null; groupLabel: string; onClearGroup: () => void; compact: boolean; onReload: () => void;
}) {
  const { t } = useTranslation();
  const [shown, setShown] = useState(PAGE);
  const [openId, setOpenId] = useState<string | null>(null);
  const rows = runs.filter((r) => matchesFilter(r, filter));
  const filters: RecordFilter[] = ['all', 'error', 'stopped', 'human', 'unknown'];
  const chip = 'px-2.5 py-[2px] rounded-full border text-[11.5px]';
  return (
    <section className="mt-8" data-testid="history-records">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h3 className="m-0 mr-1 text-[13px] font-semibold">{t('swarmOut.history.records')}</h3>
        {filters.map((f) => {
          const n = runs.filter((r) => matchesFilter(r, f)).length;
          return (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => { setFilter(f); setShown(PAGE); }}
              data-testid="history-filter" data-filter={f}
              className={`${chip} border-[var(--color-border)] ${filter === f ? 'bg-[var(--color-card)] text-[var(--color-text)]' : muted}`}>
              {t(`swarmOut.history.filter.${f}`)} <span className="tabular-nums">{n}</span>
            </button>
          );
        })}
        {group != null && (
          <button type="button" onClick={onClearGroup} data-testid="history-group-chip"
            className={`${chip} text-[var(--color-text)]`} style={{ borderColor: 'color-mix(in srgb, var(--color-zone-work) 60%, var(--color-border))' }}>
            {groupLabel} ✕
          </button>
        )}
      </div>
      {rows.length === 0 ? <div className={`text-[12.5px] ${muted}`}>{t('swarmOut.history.noRecords')}</div> : (
        <div className={card}>
          {!compact && (
            <div className={`grid gap-x-2.5 px-3 pt-2 pb-1 text-[10.5px] ${dim}`} style={{ gridTemplateColumns: ROW_COLUMNS }}>
              <span /><span>{t('swarmOut.history.col.run')}</span><span>{t('swarmOut.history.col.agent')}</span><span>{t('swarmOut.history.col.type')}</span>
              <span>{t('swarmOut.history.col.ended')}</span><span className="text-right">{t('swarmOut.history.col.time')}</span>
              <span className="text-right">{t('swarmOut.history.col.spend')}</span><span className="text-right">{t('swarmOut.history.col.human')}</span>
              <span className="text-right">{t('swarmOut.history.col.sent')}</span><span />
            </div>
          )}
          {rows.slice(0, shown).map((r) => (
            <HistoryRow key={r.dispatch_id} r={r} compact={compact} onReload={onReload}
              open={openId === r.dispatch_id} onToggle={() => setOpenId((o) => (o === r.dispatch_id ? null : r.dispatch_id))} />
          ))}
          {rows.length > shown && (
            <button type="button" onClick={() => setShown((s) => s + PAGE)} data-testid="history-more"
              className={`w-full py-2 border-t border-[var(--color-border)] text-[12px] ${muted} hover:text-[var(--color-text)]`}>
              {t('swarmOut.history.more', { count: Math.min(PAGE, rows.length - shown) })}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export function HistoryView({ hidden, now, compact = false }: { hidden: boolean; now: number; compact?: boolean }) {
  const { t } = useTranslation();
  const { data, failed, reload } = useRuns(hidden);
  const [days, setDays] = useState<number | null>(7);
  const [dimKey, setDim] = useState<BreakdownDim>('brain');
  const [group, setGroup] = useState<string | null>(null);
  const [filter, setFilter] = useState<RecordFilter>('all');
  const windowed = useMemo(() => {
    const rs = inWindow(data?.runs ?? [], now, days);
    return [...rs].sort((a, b) => Date.parse(b.sent_at) - Date.parse(a.sent_at));
  }, [data, now, days]);
  const tally = useMemo(() => tallyRuns(windowed), [windowed]);
  const inGroup = group == null ? windowed : windowed.filter((r) => groupOf(r, dimKey).key === group);
  const inGroupFirst = inGroup[0];
  const groupLabel = group == null || !inGroupFirst ? '' : `${t(`swarmOut.history.breakdown.${dimKey}`)}: ${groupOf(inGroupFirst, dimKey).label}`;
  return (
    <div data-testid="swarm-out-history">
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      <Seg testId="history-window" value={days} onChange={(d) => { setDays(d); setGroup(null); }}
        options={WINDOWS.map((w) => ({ value: w.days, label: t(`swarmOut.history.window.${w.key}`) }))} />
      {!data ? (!failed && <div className={`mt-3 text-[12px] ${muted}`}>{t('swarmOut.loading')}</div>) : windowed.length === 0 ? (
        <div className={`mt-4 text-[12.5px] ${muted}`} data-testid="history-empty">{t('swarmOut.history.empty')}</div>
      ) : (
        <>
          <div className="mt-4"><Summary x={tally} /></div>
          <DailyBars runs={windowed} now={now} days={days ?? 30} />
          <Breakdown runs={windowed} dimKey={dimKey} setDim={setDim} selected={group} onSelect={setGroup} />
          <Records runs={inGroup} filter={filter} setFilter={setFilter} group={group} groupLabel={groupLabel}
            onClearGroup={() => setGroup(null)} compact={compact} onReload={reload} />
        </>
      )}
      <Tbd title={t('swarmOut.tbdParts.occupancy')} missing={t('swarmOut.tbdParts.occupancyMissing')} testId="swarm-out-tbd-occupancy" />
    </div>
  );
}
