/**
 * PipelineEval — the Pipelines overlay's Eval tab (design: Knowledge/Mockups/
 * 2026-11-24-pipeline-eval-redesign.html): how SwarmAI's own pipeline runs did, the last
 * 15 days against the 15 before, both cut at local midnight (pipelineRows.evalHalves; the
 * analytics window is 30 days), over the rows the shared filter left.
 *
 * - One verdict: the share of finished runs nobody stepped into (k of n, its 95% Wilson
 *   range in the title and for a screen reader), next to the 15 days before; under it the 30 days as bars (green:
 *   nobody stepped in, amber: someone did, grey: not finished), the earlier half faded.
 * - Four numbers in one row, each with its change against the 15 days before (amber when
 *   it got worse): cost per run ($, else tokens), median minutes, runs started, knowledge
 *   entries given / written back per run.
 * - Stepped in: the finished runs of the last 15 days someone stepped into, how (answers,
 *   corrections, rescues), each opening its big card.
 * - By Brain: runs, the share nobody stepped into, median minutes and cost per run.
 * Which runs count as autonomous, and every count behind them, are the summary's
 * (core.run_interventions, run_protocol.usage): read as they are. A measure nothing
 * records says so; it is never shown as 0.
 *
 * Exports: PipelineEval
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TONE_VAR } from '../swarm-out/tone';
import { Meta, When } from './PipelineLine';
import {
  EVAL_HALF_DAYS, evalByBrain, evalDays, evalMeasures, steppedIn, tokenM, type EvalPeriod, type PipelineRow,
} from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const pct = (x: number | null) => (x == null ? null : Math.round(x * 100));
const usd = (x: number) => `$${x.toFixed(2)}`;
const AUTO = `color-mix(in srgb, ${TONE_VAR.ok} 75%, transparent)`;
const OTHER = 'color-mix(in srgb, var(--color-text-dim) 45%, transparent)';

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

/** The change against the 15 days before: ▲/▼ and a percent (an absolute change under 10%),
 *  amber when it went the wrong way, then what it was. */
function Change({ cur, prev, fmt, upGood, testId, what }: {
  cur: number | null; prev: number | null; fmt: (v: number) => string; upGood: boolean; testId: string;
  /** which of the value's numbers it compares, when the value shows more than one */
  what?: string;
}) {
  const { t } = useTranslation();
  if (cur == null) return null;
  const lead = what ? <span className={muted}>{what} </span> : null;
  if (prev == null) return <div className={`text-small ${muted}`} data-testid={testId}>{lead}{t('ui.PipelineOverlay.ev2_prevNone', { days: EVAL_HALF_DAYS })}</div>;
  const d = cur - prev;
  const share = prev ? Math.round((Math.abs(d) / prev) * 100) : null;
  // the change as a percent from 10% up, else in the value's own unit; one too small to
  // show in either is "the same"
  const shown = share != null && share >= 10 ? `${share}%` : fmt(Math.abs(d)) !== fmt(0) ? fmt(Math.abs(d)) : share ? `${share}%` : null;
  if (Math.abs(d) < 1e-9 || shown == null) {
    return <div className={`text-small ${muted}`} data-testid={testId} data-good="same">{lead}{t('ui.PipelineOverlay.ev2_same', { days: EVAL_HALF_DAYS })}</div>;
  }
  const good = (d > 0) === upGood;
  return (
    <div className="text-small tabular-nums" data-testid={testId} data-good={good}>
      {lead}
      <span className="font-semibold" style={{ color: good ? TONE_VAR.ok : TONE_VAR.attention }}>
        <span aria-hidden>{d > 0 ? '▲' : '▼'} </span>{shown}<span className="sr-only"> {t(good ? 'ui.PipelineOverlay.ev2_better' : 'ui.PipelineOverlay.ev2_worse')}</span></span>
      <span className={muted}> {t('ui.PipelineOverlay.ev2_was', { days: EVAL_HALF_DAYS, value: fmt(prev) })}</span>
    </div>
  );
}

function Kpi({ label, value, change, testId, first }: { label: string; value: React.ReactNode | null; change: React.ReactNode; testId: string; first?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className={`min-w-0 pt-3.5 pb-0.5 ${first ? 'pr-4' : 'px-4 border-l border-[var(--color-border)]'}`} data-testid={testId}>
      <div className={`text-small ${muted}`}>{label}</div>
      <div className={`mt-0.5 font-semibold tabular-nums whitespace-nowrap ${value == null ? `text-body ${muted}` : 'text-[22px] leading-7'}`} data-testid={`${testId}-value`}>
        {value ?? t('ui.PipelineOverlay.ev_notRecorded')}</div>
      {change}
    </div>
  );
}

const cost = (m: EvalPeriod): string | null => (m.cost.usd != null ? usd(m.cost.usd) : m.cost.tokens != null ? tokenM(m.cost.tokens) : null);

/** The cost of the two halves in one unit: dollars when both have them, else tokens when
 *  both have them, else what the current half has (its earlier value then unknown). */
function costPair(cur: EvalPeriod, prev: EvalPeriod): { value: string | null; now: number | null; was: number | null; fmt: (v: number) => string } {
  const byTok = (v: number) => tokenM(v);
  if (cur.cost.usd != null && prev.cost.usd != null) return { value: usd(cur.cost.usd), now: cur.cost.usd, was: prev.cost.usd, fmt: usd };
  if (cur.cost.tokens != null && prev.cost.tokens != null) return { value: tokenM(cur.cost.tokens), now: cur.cost.tokens, was: prev.cost.tokens, fmt: byTok };
  return cur.cost.usd != null ? { value: usd(cur.cost.usd), now: cur.cost.usd, was: null, fmt: usd }
    : { value: cur.cost.tokens == null ? null : tokenM(cur.cost.tokens), now: cur.cost.tokens, was: null, fmt: byTok };
}

export function PipelineEval({ rows, now, onOpen, compact = false }: {
  rows: PipelineRow[]; now: number; onOpen: (key: string) => void; compact?: boolean;
}) {
  const { t } = useTranslation();
  const { cur, prev } = useMemo(() => evalMeasures(rows, now), [rows, now]);
  const brains = useMemo(() => evalByBrain(rows), [rows]);
  const days = useMemo(() => evalDays(rows, now), [rows, now]);
  const stepped = useMemo(() => steppedIn(rows, now), [rows, now]);
  // the bars grow again when the runs change, not on the overlay's 30-second clock
  const fingerprint = useMemo(() => rows.map((r) => `${r.key}:${r.stage}`).join('|'), [rows]);
  const grown = useGrown(fingerprint);
  if (!rows.length) return <div className={`py-10 text-center ${muted}`} data-testid="pipeline-eval-empty">{t('ui.PipelineOverlay.empty')}</div>;
  const a = cur.autonomous;
  const rate = pct(a.rate), was = pct(prev.autonomous.rate);
  const max = Math.max(1, ...days.map((d) => d.auto + d.stepped + d.other));
  const c = costPair(cur, prev);
  const k = cur.knowledge;

  return (
    <div className="flex flex-col" data-testid="pipeline-eval">
      <section className={`${card} px-[18px] py-4`} data-testid="pipeline-eval-verdict">
        <div className="flex items-baseline flex-wrap gap-x-3.5 gap-y-1">
          <span className={`font-semibold tabular-nums ${rate == null ? `text-title ${muted}` : 'text-[44px] leading-[48px] tracking-tight'}`}
            data-testid="pipeline-eval-rate" title={a.range ? t('ui.PipelineOverlay.ev2_range', { lo: pct(a.range.lo), hi: pct(a.range.hi) }) : undefined}>
            {rate == null ? t('ui.PipelineOverlay.ev2_rateNone') : <>{rate}<span className={`text-[22px] font-medium ml-0.5 ${muted}`}>%</span></>}
          </span>
          {rate != null && <span className="text-heading">{t('ui.PipelineOverlay.ev2_rateWhat')}</span>}
          {rate != null && <span className={`text-body tabular-nums ${muted}`} data-testid="pipeline-eval-rate-of">{t('ui.PipelineOverlay.ev2_of', { k: a.k, n: a.n })}
            {a.range && <span className="sr-only"> · {t('ui.PipelineOverlay.ev2_range', { lo: pct(a.range.lo), hi: pct(a.range.hi) })}</span>}</span>}
          {rate != null && (
            <span className="ml-auto text-small px-2.5 py-0.5 rounded-full bg-[var(--color-bg-chrome)] whitespace-nowrap tabular-nums" data-testid="pipeline-eval-rate-was">
              {was == null ? <span className={muted}>{t('ui.PipelineOverlay.ev2_prevNone', { days: EVAL_HALF_DAYS })}</span> : <>
                {rate !== was && <b className="font-semibold" style={{ color: rate > was ? TONE_VAR.ok : TONE_VAR.attention }}>
                  {rate > was ? '▲' : '▼'} {t('ui.PipelineOverlay.ev2_pts', { count: Math.abs(rate - was) })}</b>}
                <span className={muted}>{rate !== was ? ' · ' : ''}{t('ui.PipelineOverlay.ev2_was', { days: EVAL_HALF_DAYS, value: `${was}%` })}</span>
              </>}
            </span>
          )}
        </div>

        <div className="relative flex items-end gap-[3px] h-[76px] mt-[18px] border-b border-[var(--color-border)]" data-testid="pipeline-eval-days"
          role="group" aria-label={t('ui.PipelineOverlay.ev2_daysLabel', { days: 2 * EVAL_HALF_DAYS })}>
          {days.map((d, i) => {
            const n = d.auto + d.stepped + d.other;
            const tip = [t('ui.PipelineOverlay.ev2_dayTip', { day: d.label, count: n }),
              d.stepped ? t('ui.PipelineOverlay.ev2_daySteppedIn', { count: d.stepped }) : null,
              d.other ? t('ui.PipelineOverlay.ev2_dayOther', { count: d.other }) : null].filter(Boolean).join(' · ');
            return (
              <div key={d.label} className="flex-1 h-full flex flex-col-reverse min-w-0" title={tip} aria-label={tip} role="img"
                style={{ opacity: i < EVAL_HALF_DAYS ? 0.45 : 1 }} data-testid="pipeline-eval-day" data-auto={d.auto} data-stepped={d.stepped} data-other={d.other}>
                {(['auto', 'stepped', 'other'] as const).map((k) => d[k] > 0 && (
                  <span key={k} className="so-grow block" style={{ height: `${(d[k] / max) * 100}%`,
                    background: k === 'auto' ? AUTO : k === 'stepped' ? TONE_VAR.attention : OTHER,
                    transform: grown ? 'scaleY(1)' : 'scaleY(0)', transitionDelay: `${i * 12}ms` }} />
                ))}
              </div>
            );
          })}
          <span aria-hidden className="absolute top-0 -bottom-px left-1/2 border-l border-dashed"
            style={{ borderColor: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }} />
        </div>
        <div className={`flex justify-between mt-1 text-meta ${muted}`} aria-hidden>
          <span>{days[0]?.label}</span>
          <span className={muted}>{t('ui.PipelineOverlay.ev2_halves', { days: EVAL_HALF_DAYS })}</span>
          <span>{days[days.length - 1]?.label}</span>
        </div>

        <div className={`grid mt-[18px] border-t border-[var(--color-border)] ${compact ? 'grid-cols-2 gap-y-3' : 'grid-cols-4'}`} data-testid="pipeline-eval-kpis">
          <Kpi first testId="pipeline-eval-cost" label={t('ui.PipelineOverlay.ev2_cost')} value={c.value}
            change={<Change testId="pipeline-eval-cost-change" cur={c.now} prev={c.was} fmt={c.fmt} upGood={false} />} />
          <Kpi testId="pipeline-eval-cycle" label={t('ui.PipelineOverlay.ev2_cycle')}
            value={cur.cycle.median == null ? null : t('ui.PipelineOverlay.minutes', { n: cur.cycle.median })}
            change={<Change testId="pipeline-eval-cycle-change" cur={cur.cycle.median} prev={prev.cycle.median} fmt={(v) => t('ui.PipelineOverlay.minutes', { n: Math.round(v) })} upGood={false} />} />
          <Kpi first={compact} testId="pipeline-eval-started" label={t('ui.PipelineOverlay.ev2_started')} value={String(cur.started)}
            change={<Change testId="pipeline-eval-started-change" cur={cur.started} prev={prev.started || null} fmt={(v) => String(Math.round(v))} upGood />} />
          <Kpi testId="pipeline-eval-knowledge" label={t('ui.PipelineOverlay.ev2_knowledge')}
            value={k.used == null && k.new == null ? null : <>{k.used == null ? '—' : k.used.toFixed(1)}<span className={`text-small font-medium mx-1 ${muted}`}>/</span>{k.new == null ? '—' : k.new.toFixed(1)}</>}
            change={<Change testId="pipeline-eval-knowledge-change" what={t('ui.PipelineOverlay.ev2_writtenBack')} cur={k.new} prev={prev.knowledge.new} fmt={(v) => v.toFixed(1)} upGood />} />
        </div>
      </section>

      <section className="mt-8" data-testid="pipeline-eval-stepped">
        <div className="flex items-baseline gap-2 mb-2.5 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]">
          <span>{t('ui.PipelineOverlay.ev2_stepped')}</span>
          <span className="inline-block min-w-[18px] px-1.5 rounded-full bg-[var(--color-bg-chrome)] text-center tabular-nums text-[var(--color-text-secondary)]">{stepped.length}</span>
          <span className={`ml-auto font-normal tracking-normal ${dim}`}>{t('ui.PipelineOverlay.ev2_lastDays', { days: EVAL_HALF_DAYS })}</span>
        </div>
        {stepped.length === 0 ? (
          <div className={`text-body ${muted}`} data-testid="pipeline-eval-stepped-none">{t('ui.PipelineOverlay.ev2_steppedNone', { days: EVAL_HALF_DAYS })}</div>
        ) : (
          <div className="flex flex-col divide-y divide-[var(--color-border)]">
            {stepped.map((r) => {
              const iv = r.interventions!;
              const how = [iv.answers ? t('ui.PipelineOverlay.ev2_answers', { count: iv.answers }) : null,
                iv.corrections ? t('ui.PipelineOverlay.ev2_corrections', { count: iv.corrections }) : null,
                iv.rescues ? t('ui.PipelineOverlay.ev2_rescues', { count: iv.rescues }) : null].filter((x): x is string => !!x);
              if (!how.length) how.push(t('swarmOut.review.autonomy.steppedIn', { count: iv.total ?? 0 }));
              return (
                <div key={r.key} role="button" tabIndex={0} aria-haspopup="dialog" onClick={() => onOpen(r.key)}
                  onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(r.key); } }}
                  className="grid grid-cols-[18px_minmax(0,1fr)_auto] gap-x-3 items-center px-2.5 py-2 -mx-2.5 rounded-lg cursor-pointer hover:bg-[var(--color-hover)]"
                  data-testid={`pipeline-eval-stepped-${r.key}`}>
                  <span aria-hidden className="w-[18px] h-[18px] rounded-full grid place-items-center text-meta font-semibold"
                    style={{ color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 12%, transparent)` }}>!</span>
                  <div className="min-w-0">
                    <div className="text-body truncate" title={r.title}>{r.title}</div>
                    <div className="mt-px flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                      <Meta r={r} />
                      <span className="inline-flex flex-wrap gap-1.5" data-testid="pipeline-eval-how">
                        {how.map((h) => (
                          <span key={h} className="px-1.5 rounded text-meta font-semibold whitespace-nowrap"
                            style={{ color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 12%, transparent)` }}>{h}</span>
                        ))}
                      </span>
                    </div>
                  </div>
                  <When r={r} now={now} stacked={compact} />
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className={`${card} px-[18px] py-4 mt-8`} data-testid="pipeline-eval-brains">
        <div className="flex items-baseline gap-2 mb-2">
          <span className="text-body font-semibold">{t('ui.PipelineOverlay.ev_byBrain')}</span>
          <span className={`ml-auto text-small ${muted}`}>{t('ui.PipelineOverlay.ev2_lastDays', { days: 2 * EVAL_HALF_DAYS })}</span>
        </div>
        <div className={`grid gap-x-3.5 items-center text-meta pb-1 ${muted}`}
          style={{ gridTemplateColumns: compact ? 'minmax(0,1fr) 40px minmax(90px,1.2fr) 64px' : 'minmax(0,1.4fr) 50px minmax(120px,1.6fr) 70px 80px' }}>
          <span>{t('ui.PipelineOverlay.brainFilter')}</span><span className="text-right">{t('ui.PipelineOverlay.ev2_runs')}</span>
          <span>{t('ui.PipelineOverlay.ev2_rateWhat')}</span><span className="text-right">{t('ui.PipelineOverlay.ev2_cycle')}</span>
          {!compact && <span className="text-right">{t('ui.PipelineOverlay.ev2_cost')}</span>}
        </div>
        {brains.map(({ brain, m }) => {
          const r = pct(m.autonomous.rate);
          return (
            <div key={brain} className="grid gap-x-3.5 items-center py-2 border-t border-[var(--color-border)] text-body tabular-nums"
              style={{ gridTemplateColumns: compact ? 'minmax(0,1fr) 40px minmax(90px,1.2fr) 64px' : 'minmax(0,1.4fr) 50px minmax(120px,1.6fr) 70px 80px' }}
              data-testid="pipeline-eval-brain">
              <span className="font-semibold truncate" title={brain}>{brain}</span>
              <span className="text-right text-[var(--color-text-secondary)]">{m.started}</span>
              <span className="flex items-center gap-2 min-w-0">
                {r == null ? <span className={muted}>{t('ui.PipelineOverlay.ev_notRecorded')}</span> : <>
                  <span className="flex-1 h-1.5 rounded-sm overflow-hidden" style={{ background: `color-mix(in srgb, ${TONE_VAR.attention} 35%, transparent)` }}>
                    <span className="so-width block h-full" style={{ width: grown ? `${r}%` : '0%', background: TONE_VAR.ok }} />
                  </span>
                  <b className="font-semibold min-w-[36px] text-right">{r}%</b>
                </>}
              </span>
              <span className="text-right text-[var(--color-text-secondary)]">{m.cycle.median == null ? '—' : t('ui.PipelineOverlay.minutes', { n: m.cycle.median })}</span>
              {!compact && <span className="text-right text-[var(--color-text-secondary)]">{cost(m) ?? '—'}</span>}
            </div>
          );
        })}
      </section>
    </div>
  );
}
