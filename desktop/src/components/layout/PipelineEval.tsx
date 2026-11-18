/**
 * PipelineEval — the Pipelines overlay's Eval tab: how SwarmAI's own pipeline runs did,
 * read with the measures the CNTech & IVT Autonomous standard asks every team to read
 * (its S7, in its order), so the standard SwarmAI hands others holds for itself.
 *
 * The last 15 days against the 15 before (the analytics window is 30 days), over the rows
 * the shared filter left:
 * - ① Autonomous Rate leads, the largest number: k of n finished runs nobody stepped
 *   into, and a 95% Wilson range from those two counts;
 * - ② Human on the loop (answers per run, unplanned corrections and rescues per run),
 *   ③ Knowledge Usage, ④ Cost (per run, and how many runs it covers), ⑥ Throughput
 *   (runs started), ⑤ Cycle time (median, P90), and where runs waited on you, by station;
 * - By Brain: the same measures over the whole window.
 * Which runs count as autonomous, and every count behind ② and ④, are the summary's
 * (core.run_interventions, run_protocol.usage): read as they are, never recomputed here.
 * A measure nothing records says so; it is never shown as 0.
 *
 * Exports: PipelineEval
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { TONE_VAR } from '../swarm-out/tone';
import { EVAL_HALF_DAYS, evalByBrain, evalMeasures, tokenM, type EvalPeriod, type PipelineRow } from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const pct = (x: number | null) => (x == null ? null : `${Math.round(x * 100)}%`);
const f1 = (x: number | null) => (x == null ? null : x.toFixed(1));
const usd = (x: number | null) => (x == null ? null : `$${x.toFixed(2)}`);

function Delta({ cur, prev, unit, upGood }: { cur: number | null; prev: number | null; unit: (d: number) => string; upGood: boolean }) {
  const { t } = useTranslation();
  if (cur == null || prev == null) return null;
  const d = cur - prev;
  if (Math.abs(d) < 1e-9) return <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.ev_noChange', { days: EVAL_HALF_DAYS })}</div>;
  return (
    <div className="text-small">
      <span className="font-semibold" style={{ color: (d > 0) === upGood ? 'var(--color-text)' : TONE_VAR.attention }}>{d > 0 ? '▲' : '▼'} {unit(Math.abs(d))}</span>
      <span className={muted}> {t('ui.PipelineOverlay.ev_vsBefore', { days: EVAL_HALF_DAYS })}</span>
    </div>
  );
}

function Tile({ label, value, sub, children, testId, wrap }: { label: string; value: string | null; sub?: React.ReactNode; children?: React.ReactNode; testId: string; wrap?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 min-w-0" data-testid={testId}>
      <div className="text-small font-semibold text-[var(--color-text)]">{label}</div>
      <div className={`mt-1 font-semibold tabular-nums ${wrap ? 'break-words' : 'truncate'} ${value == null ? `text-body ${muted}` : 'text-title'}`} data-testid={`${testId}-value`}>
        {value ?? t('ui.PipelineOverlay.ev_notRecorded')}</div>
      {sub && <div className={`text-small mt-0.5 ${muted}`}>{sub}</div>}
      {children}
    </div>
  );
}

export function PipelineEval({ rows, now }: { rows: PipelineRow[]; now: number }) {
  const { t } = useTranslation();
  const { cur, prev } = useMemo(() => evalMeasures(rows, now), [rows, now]);
  const brains = useMemo(() => evalByBrain(rows), [rows]);
  if (!rows.length) return <div className={`py-10 text-center ${muted}`} data-testid="pipeline-eval-empty">{t('ui.PipelineOverlay.empty')}</div>;
  const a = cur.autonomous;
  const cells = (m: EvalPeriod): (string | null)[] => [
    pct(m.autonomous.rate) ? `${pct(m.autonomous.rate)} · ${m.autonomous.k}/${m.autonomous.n}` : null,
    m.human.answers == null ? null : `${f1(m.human.answers)} · ${f1(m.human.unplanned) ?? '—'}`,
    m.cost.usd == null && m.cost.tokens == null ? null : [usd(m.cost.usd), m.cost.tokens == null ? null : tokenM(m.cost.tokens)].filter(Boolean).join(' · '),
    String(m.started),
    m.cycle.median == null ? null : t('ui.PipelineOverlay.minutes', { n: m.cycle.median }),
  ];
  const heads = [t('ui.PipelineOverlay.ev_m1'), t('ui.PipelineOverlay.ev_m2'), t('ui.PipelineOverlay.ev_m4'),
    t('ui.PipelineOverlay.ev_m6'), t('ui.PipelineOverlay.ev_m5')];

  return (
    <div className="flex flex-col gap-5" data-testid="pipeline-eval">
      <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.ev_since', { days: EVAL_HALF_DAYS })}</div>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(260px, 2fr) repeat(3, minmax(0, 1fr))' }}>
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4 min-w-0" style={{ gridRow: 'span 2' }} data-testid="pipeline-eval-m1">
          <div className="text-body font-semibold text-[var(--color-text)]">{t('ui.PipelineOverlay.ev_m1')}</div>
          <div className="flex items-end gap-4 mt-1">
            <div className={`font-bold leading-none tabular-nums ${a.rate == null ? `text-title ${muted}` : 'text-[56px] text-[var(--color-text)]'}`} data-testid="pipeline-eval-m1-value">
              {pct(a.rate) ?? t('ui.PipelineOverlay.ev_notRecorded')}</div>
            <div className="pb-1"><Delta cur={a.rate} prev={prev.autonomous.rate} unit={(d) => t('ui.PipelineOverlay.ev_pts', { n: Math.round(d * 100) })} upGood /></div>
          </div>
          <div className={`text-small mt-2 ${muted}`} data-testid="pipeline-eval-m1-sub">
            {a.n ? t('ui.PipelineOverlay.ev_m1Sub', { k: a.k, n: a.n, lo: pct(a.range!.lo), hi: pct(a.range!.hi) }) : t('ui.PipelineOverlay.ev_m1None')}</div>
        </div>
        <Tile testId="pipeline-eval-m2" label={t('ui.PipelineOverlay.ev_m2')} value={f1(cur.human.answers)}
          sub={cur.human.answers == null ? undefined : t('ui.PipelineOverlay.ev_m2Sub', { unplanned: f1(cur.human.unplanned) ?? '—', n: cur.human.n })} />
        <Tile testId="pipeline-eval-m3" label={t('ui.PipelineOverlay.ev_m3')} value={null} sub={t('ui.PipelineOverlay.ev_m3None')} />
        <Tile testId="pipeline-eval-m4" label={t('ui.PipelineOverlay.ev_m4')}
          value={cur.cost.usd == null && cur.cost.tokens == null ? null : [usd(cur.cost.usd), cur.cost.tokens == null ? null : tokenM(cur.cost.tokens)].filter(Boolean).join(' · ')}
          sub={t('ui.PipelineOverlay.ev_m4Sub', { usdRuns: cur.cost.usdRuns, tokenRuns: cur.cost.tokenRuns, runs: cur.cost.runs })} />
        <Tile testId="pipeline-eval-m6" label={t('ui.PipelineOverlay.ev_m6')} value={String(cur.started)} sub={t('ui.PipelineOverlay.ev_m6Sub')}>
          <Delta cur={cur.started} prev={prev.started} unit={(d) => String(d)} upGood />
        </Tile>
        <Tile testId="pipeline-eval-m5" label={t('ui.PipelineOverlay.ev_m5')} value={cur.cycle.median == null ? null : t('ui.PipelineOverlay.minutes', { n: cur.cycle.median })}
          sub={cur.cycle.median == null ? undefined : t('ui.PipelineOverlay.ev_m5Sub', { p90: cur.cycle.p90, n: cur.cycle.n })} />
        <Tile wrap testId="pipeline-eval-waited" label={t('ui.PipelineOverlay.ev_waitedAt')}
          value={cur.waitedAt.length ? cur.waitedAt.slice(0, 3).map((x) => `${x.station.toUpperCase()} ${x.runs}`).join(' · ') : null}
          sub={cur.waitedAt.length ? t('ui.PipelineOverlay.ev_waitedSub') : t('ui.PipelineOverlay.ev_waitedNone')} />
      </div>

      <section data-testid="pipeline-eval-brains">
        <h3 className="m-0 mb-2 text-body font-semibold">{t('ui.PipelineOverlay.ev_byBrain')}</h3>
        <table className="w-full text-small tabular-nums border-collapse">
          <thead><tr className={muted}>
            <th className="text-left font-normal py-1.5 pr-3">{t('ui.PipelineOverlay.brainFilter')}</th>
            {heads.map((h) => <th key={h} className="text-right font-normal py-1.5 px-3">{h}</th>)}
          </tr></thead>
          <tbody>{brains.map(({ brain, m }) => (
            <tr key={brain} className="border-t border-[var(--color-border)]" data-testid="pipeline-eval-brain">
              <td className="py-1.5 pr-3 font-semibold text-[var(--color-text)]">{brain}</td>
              {cells(m).map((c, i) => <td key={i} className={`py-1.5 px-3 text-right ${c == null ? muted : i === 0 ? 'font-semibold text-[var(--color-text)]' : ''}`}>{c ?? t('ui.PipelineOverlay.ev_notRecorded')}</td>)}
            </tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  );
}
