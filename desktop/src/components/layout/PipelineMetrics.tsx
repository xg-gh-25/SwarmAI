/**
 * PipelineMetrics — the Pipelines overlay's Metrics tab (design
 * docs/design/2026-11-01-brain-eval-and-runs.md §4.0): how the runs went over the window,
 * for the rows the shared filter left.
 *
 * - Run distribution: the total, this machine's and the Hives' shares (each Hive by
 *   name), and a stacked bar per day (per week once the data spans 14 days), this
 *   machine and Hive in the dataviz categorical slots 1 and 2 (``--viz-1`` / ``--viz-2``,
 *   the same colors the list's location tags use).
 * - Five measures in one row, each a big number and what it is out of: delivered,
 *   autonomous, quality, speed (median, P90), cost (tokens per delivery, in M; $ only
 *   where it was recorded). A click on one shows its drill-down chart below: outcomes per
 *   day, interventions per run (answers / corrections / rescues), dimensions × verdicts,
 *   each run's time by profile with the median, each run's tokens with the costliest
 *   marked. A mark's tooltip names the run; a click opens its big card.
 * Nothing measured shows "—", never 0; a value covering only part of the runs says how
 * many it covers. Pure presentation over ``pipelineRows``.
 *
 * Exports: PipelineMetrics
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { autonomy, cost, delivery, distribution, quality, speed, tokenM, type PipelineRow } from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const SERIES = { local: 'var(--viz-1)', hive: 'var(--viz-2)', third: 'var(--viz-3)' };
const OUTCOME_FILL = {
  landed: 'var(--color-status-success)', manual: 'var(--viz-1)',
  not_landed: 'var(--color-status-warning)', discarded: 'var(--color-text-muted)',
} as const;

type Measure = 'delivery' | 'autonomy' | 'quality' | 'speed' | 'cost';

const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`);
const shortDay = (iso: string) => iso.slice(5, 10);

function Legend({ items }: { items: { label: string; fill: string }[] }) {
  return (
    <div className={`flex flex-wrap gap-3 text-small ${muted}`}>
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block w-2.5 h-2.5 rounded-[3px]" style={{ background: i.fill }} />{i.label}
        </span>
      ))}
    </div>
  );
}

/** Stacked bars over labelled buckets: one segment per series, a 2px gap between them. */
function StackedBars({ points, series, testId, height = 120 }: {
  points: { label: string; values: Record<string, number> }[];
  series: { key: string; label: string; fill: string }[];
  testId: string; height?: number;
}) {
  const max = Math.max(1, ...points.map((p) => series.reduce((s, x) => s + (p.values[x.key] ?? 0), 0)));
  const w = Math.max(points.length * 28, 120);
  const bw = Math.min(20, (w / Math.max(points.length, 1)) - 8);
  return (
    <svg viewBox={`0 0 ${w} ${height + 18}`} width="100%" height={height + 18} preserveAspectRatio="none" data-testid={testId} role="img">
      {points.map((p, i) => {
        const x = (i + 0.5) * (w / points.length) - bw / 2;
        let y = height;
        return (
          <g key={p.label}>
            {series.map((s) => {
              const v = p.values[s.key] ?? 0;
              if (!v) return null;
              const h = (v / max) * (height - 4);
              y -= h;
              const seg = <rect key={s.key} x={x} y={y + 1} width={bw} height={Math.max(h - 2, 1)} rx={3} style={{ fill: s.fill }}
                data-testid={`${testId}-seg`}><title>{`${shortDay(p.label)} · ${s.label} ${v}`}</title></rect>;
              return seg;
            })}
            <text x={x + bw / 2} y={height + 14} textAnchor="middle" className="text-meta" style={{ fill: 'var(--color-text-muted)' }}>{shortDay(p.label)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function PipelineMetrics({ rows, now, onOpen }: { rows: PipelineRow[]; now: number; onOpen: (key: string) => void }) {
  const { t } = useTranslation();
  const [measure, setMeasure] = useState<Measure>('delivery');
  const dist = useMemo(() => distribution(rows, now), [rows, now]);
  const del = useMemo(() => delivery(rows, now), [rows, now]);
  const aut = useMemo(() => autonomy(rows), [rows]);
  const qua = useMemo(() => quality(rows), [rows]);
  const spd = useMemo(() => speed(rows), [rows]);
  const cst = useMemo(() => cost(rows), [rows]);
  const title = (key: string) => rows.find((r) => r.key === key)?.title ?? key;

  if (!rows.length) return <div className={`py-10 text-center ${muted}`} data-testid="pipeline-metrics-empty">{t('ui.PipelineOverlay.empty')}</div>;
  const share = (n: number) => (dist.total ? Math.round((n / dist.total) * 100) : 0);
  const tiles: { key: Measure; label: string; big: string; sub: string }[] = [
    { key: 'delivery', label: t('ui.PipelineOverlay.m.delivery'), big: `${del.delivered} / ${del.total}`,
      sub: t('ui.PipelineOverlay.m.deliverySub', { pct: pct(del.rate), landed: del.counts.landed, manual: del.counts.manual,
        notLanded: del.counts.not_landed + del.counts.failed, discarded: del.counts.discarded }) },
    { key: 'autonomy', label: t('ui.PipelineOverlay.m.autonomy'), big: aut.known ? `${aut.unattended} / ${aut.known}` : '—',
      sub: aut.known ? t('ui.PipelineOverlay.m.autonomySub', { avg: (aut.avg ?? 0).toFixed(1), known: aut.known, finished: aut.finished })
        : t('ui.PipelineOverlay.m.autonomyNone') },
    { key: 'quality', label: t('ui.PipelineOverlay.m.quality'), big: qua.audited ? t('ui.PipelineOverlay.m.blocked', { count: qua.blocked }) : '—',
      sub: qua.blockedDims.length ? `⚠ ${qua.blockedDims.map((d) => `${d.dim} ${d.count}`).join(' · ')}`
        : qua.audited ? t('ui.PipelineOverlay.m.noBlock', { n: qua.audited }) : t('ui.PipelineOverlay.m.noAudit') },
    { key: 'speed', label: t('ui.PipelineOverlay.m.speed'), big: spd.median == null ? '—' : t('ui.PipelineOverlay.minutes', { n: spd.median }),
      sub: spd.n ? t('ui.PipelineOverlay.m.speedSub', { p90: spd.p90, n: spd.n }) : t('ui.PipelineOverlay.m.speedNone') },
    { key: 'cost', label: t('ui.PipelineOverlay.m.cost'), big: cst.perDelivery == null ? '—' : tokenM(cst.perDelivery),
      sub: t('ui.PipelineOverlay.m.costSub', { total: tokenM(cst.tokens), known: cst.tokensKnown, runs: cst.runs,
        usd: cst.usd == null ? t('ui.PipelineOverlay.notRecorded') : `$${cst.usd.toFixed(2)} (${cst.usdKnown} / ${cst.runs})` }) },
  ];

  return (
    <div className="flex flex-col gap-5" data-testid="pipeline-metrics">
      <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.m.since', { since: dist.since ? shortDay(dist.since) : '—', count: dist.total })}</div>

      <section className="grid gap-6 items-center" style={{ gridTemplateColumns: 'minmax(180px, 240px) minmax(0, 1fr)' }} data-testid="pipeline-distribution">
        <div>
          <div className={`text-meta font-semibold ${muted}`}>{t('ui.PipelineOverlay.m.distribution')}</div>
          <div className="text-display font-semibold tabular-nums" data-testid="pipeline-dist-total">{dist.total}</div>
          <div className="text-small flex flex-col gap-0.5">
            <span><span aria-hidden="true" className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: SERIES.local }} />
              {t('ui.PipelineOverlay.local')} <b className="tabular-nums">{dist.local}</b> <span className={muted}>{share(dist.local)}%</span></span>
            <span data-testid="pipeline-dist-hive"><span aria-hidden="true" className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: SERIES.hive }} />
              Hive <b className="tabular-nums">{dist.hive}</b> <span className={muted}>{share(dist.hive)}%{dist.byHive.length ? ` · ${dist.byHive.map((h) => `${h.hive} ${h.count}`).join(' · ')}` : ''}</span></span>
          </div>
        </div>
        <div className="min-w-0">
          <StackedBars testId="pipeline-dist-bars" points={dist.points.map((p) => ({ label: p.label, values: { local: p.local, hive: p.hive } }))}
            series={[{ key: 'local', label: t('ui.PipelineOverlay.local'), fill: SERIES.local }, { key: 'hive', label: 'Hive', fill: SERIES.hive }]} />
          <div className="flex items-center justify-between">
            <Legend items={[{ label: t('ui.PipelineOverlay.local'), fill: SERIES.local }, { label: 'Hive', fill: SERIES.hive }]} />
            <span className={`text-meta ${muted}`}>{dist.weekly ? t('ui.PipelineOverlay.m.weekly') : t('ui.PipelineOverlay.m.daily')}</span>
          </div>
        </div>
      </section>

      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(5, minmax(0, 1fr))' }} role="tablist" data-testid="pipeline-measures">
        {tiles.map((x) => (
          <button key={x.key} type="button" role="tab" aria-selected={measure === x.key} onClick={() => setMeasure(x.key)}
            data-testid={`pipeline-measure-${x.key}`}
            className={`text-left rounded-lg border px-3 py-2.5 min-w-0 ${measure === x.key ? 'border-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_8%,transparent)]' : 'border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-hover)]'}`}>
            <div className={`text-meta font-semibold ${muted}`}>{x.label}</div>
            <div className="text-title font-semibold tabular-nums mt-0.5 truncate">{x.big}</div>
            <div className={`text-small ${muted} line-clamp-2`}>{x.sub}</div>
          </button>
        ))}
      </div>

      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4" data-testid={`pipeline-drill-${measure}`}>
        {measure === 'delivery' && <>
          <StackedBars testId="pipeline-drill-delivery-bars" points={del.points.map(({ label, ...values }) => ({ label, values }))}
            series={(['landed', 'manual', 'not_landed', 'discarded'] as const).map((k) => ({ key: k, label: t(`ui.PipelineOverlay.outcome_${k}`), fill: OUTCOME_FILL[k] }))} />
          <Legend items={(['landed', 'manual', 'not_landed', 'discarded'] as const).map((k) => ({ label: t(`ui.PipelineOverlay.outcome_${k}`), fill: OUTCOME_FILL[k] }))} />
        </>}
        {measure === 'autonomy' && (aut.bars.length === 0
          ? <div className={`text-small ${muted}`}>{aut.known ? t('ui.PipelineOverlay.m.allUnattended') : t('ui.PipelineOverlay.m.autonomyNone')}</div>
          : <>
            <div className="flex flex-col gap-1.5" data-testid="pipeline-drill-autonomy-bars">
              {aut.bars.map((b) => {
                const total = b.answers + b.corrections + b.rescues;
                const max = Math.max(...aut.bars.map((x) => x.answers + x.corrections + x.rescues), 1);
                return (
                  <button key={b.key} type="button" onClick={() => onOpen(b.key)} className="grid items-center gap-3 text-left hover:bg-[var(--color-hover)] rounded px-1"
                    style={{ gridTemplateColumns: 'minmax(0, 260px) minmax(0, 1fr) 28px' }} title={`${b.title} · ${total}`}>
                    <span className="truncate text-small">{b.title}</span>
                    <span className="flex h-3 gap-[2px]" style={{ width: `${(total / max) * 100}%` }}>
                      {([['answers', SERIES.local], ['corrections', SERIES.hive], ['rescues', SERIES.third]] as const).map(([k, fill]) => b[k] > 0 && (
                        <span key={k} className="h-full rounded-[3px]" style={{ flex: b[k], background: fill }} />))}
                    </span>
                    <span className="text-small tabular-nums text-right">{total}</span>
                  </button>
                );
              })}
            </div>
            <Legend items={[{ label: t('ui.PipelineOverlay.ivAnswer'), fill: SERIES.local }, { label: t('ui.PipelineOverlay.ivCorrection'), fill: SERIES.hive },
              { label: t('ui.PipelineOverlay.ivRescue'), fill: SERIES.third }]} />
          </>)}
        {measure === 'quality' && (qua.matrix.length === 0
          ? <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.m.noAudit')}</div>
          : (
            <table className="text-small tabular-nums" data-testid="pipeline-drill-quality-matrix">
              <thead><tr className={muted}><th className="text-left font-normal pr-6">{t('ui.PipelineOverlay.m.dimension')}</th>
                <th className="font-normal px-3">{t('ui.PipelineOverlay.m.verdictPass')}</th><th className="font-normal px-3">{t('ui.PipelineOverlay.m.verdictWarn')}</th><th className="font-normal px-3">{t('ui.PipelineOverlay.m.verdictBlock')}</th></tr></thead>
              <tbody>{qua.matrix.map((m) => (
                <tr key={m.dim}><td className="pr-6 py-0.5">{m.dim}</td>
                  <td className="px-3 text-center" style={{ color: m.PASS ? 'var(--color-status-success)' : undefined }}>{m.PASS || '·'}</td>
                  <td className="px-3 text-center" style={{ color: m.WARN ? 'var(--color-status-warning)' : undefined }}>{m.WARN || '·'}</td>
                  <td className="px-3 text-center" style={{ color: m.BLOCK ? 'var(--color-status-error)' : undefined }}>{m.BLOCK ? `⚠ ${m.BLOCK}` : '·'}</td></tr>
              ))}</tbody>
            </table>
          ))}
        {measure === 'speed' && (spd.n === 0
          ? <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.m.speedNone')}</div>
          : <SpeedStrip points={spd.points} median={spd.median} onOpen={onOpen} />)}
        {measure === 'cost' && (cst.bars.length === 0
          ? <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.notRecorded')}</div>
          : (
            <div className="flex flex-col gap-1" data-testid="pipeline-drill-cost-bars">
              {cst.bars.map((b) => (
                <button key={b.key} type="button" onClick={() => onOpen(b.key)} className="grid items-center gap-3 text-left hover:bg-[var(--color-hover)] rounded px-1"
                  style={{ gridTemplateColumns: 'minmax(0, 260px) minmax(0, 1fr) 56px' }} title={`${title(b.key)} · ${tokenM(b.tokens)}`}>
                  <span className="truncate text-small">{b.title}</span>
                  <span className="h-3 rounded-[3px]" style={{ width: `${(b.tokens / cst.bars[0].tokens) * 100}%`, background: b.key === cst.top ? SERIES.hive : SERIES.local }} />
                  <span className="text-small tabular-nums text-right">{tokenM(b.tokens)}</span>
                </button>
              ))}
            </div>
          ))}
      </section>
    </div>
  );
}

/** Each run's time as a dot on its profile's row, the median as a line; a dot opens its run. */
function SpeedStrip({ points, median, onOpen }: { points: { key: string; title: string; profile: string; min: number }[]; median: number | null; onOpen: (k: string) => void }) {
  const { t } = useTranslation();
  const profiles = [...new Set(points.map((p) => p.profile))];
  const max = Math.max(...points.map((p) => p.min), 1);
  const W = 640, row = 28, left = 90;
  const x = (m: number) => left + (m / max) * (W - left - 16);
  return (
    <svg viewBox={`0 0 ${W} ${profiles.length * row + 22}`} width="100%" data-testid="pipeline-drill-speed-strip" role="img">
      {median != null && <line x1={x(median)} x2={x(median)} y1={0} y2={profiles.length * row} strokeWidth={2} strokeDasharray="4 3"
        style={{ stroke: 'var(--color-text-muted)' }} />}
      {profiles.map((p, i) => (
        <g key={p}>
          <text x={0} y={i * row + 18} className="text-meta" style={{ fill: 'var(--color-text-muted)' }}>{p} {points.filter((q) => q.profile === p).length}</text>
          {points.filter((q) => q.profile === p).map((q) => (
            <circle key={q.key} cx={x(q.min)} cy={i * row + 14} r={5} style={{ fill: 'var(--viz-1)', stroke: 'var(--color-card)', strokeWidth: 2, cursor: 'pointer' }}
              onClick={() => onOpen(q.key)} data-testid="pipeline-speed-dot"><title>{t('ui.PipelineOverlay.m.dotTitle', { title: q.title, n: q.min })}</title></circle>
          ))}
        </g>
      ))}
      <text x={x(0)} y={profiles.length * row + 16} className="text-meta" style={{ fill: 'var(--color-text-muted)' }}>0</text>
      <text x={x(max)} y={profiles.length * row + 16} textAnchor="end" className="text-meta" style={{ fill: 'var(--color-text-muted)' }}>{t('ui.PipelineOverlay.m.minutes', { n: max })}</text>
    </svg>
  );
}
