/**
 * The Cognition flow's dashboard: four numbers a person checks first — captured, learned
 * (new or added to an entry), reinforced, not kept — each with its change against the
 * window before and its own trend over the window (one bar per hour for 24h, per day for
 * 7d / 30d, small multiples on a shared time axis). "Learned" is the one that stands out.
 *
 * Data: `LandedOverview.series` / `.previous` (core/ingestion/flow_view.landed_overview).
 * `previous` is null when the store does not reach a full window back: no change is
 * claimed then.
 *
 * Key export: `CdfDashboard`.
 */
import { useTranslation } from 'react-i18next';
import type { IngestionWindow, LandedOverview, TrendPoint } from '../../services/ingestion';
import { fmt } from './cdfText';

type Metric = 'captured' | 'learned' | 'reinforced' | 'notKept';
const METRICS: Metric[] = ['captured', 'learned', 'reinforced', 'notKept'];
const ACCENT = 'var(--color-zone-cognitive)';
const QUIET = 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';
const CARD_BG = 'color-mix(in srgb, var(--color-text) 3%, transparent)';

function bucketLabel(bucket: string, window: IngestionWindow): string {
  if (window === '24h') {
    const date = new Date(`${bucket}:00:00Z`);
    return Number.isNaN(date.getTime()) ? bucket.slice(11) : `${String(date.getHours()).padStart(2, '0')}:00`;
  }
  return bucket.slice(5);
}

function Trend({ points, metric, window, accent }: {
  points: TrendPoint[]; metric: Metric; window: IngestionWindow; accent: boolean;
}) {
  const values = points.map((point) => point[metric]);
  const max = Math.max(1, ...values);
  const gap = points.length > 24 ? 1 : 2;
  const width = 100 / Math.max(1, points.length);
  return (
    <div className="mt-3">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="block h-12 w-full" role="img"
        aria-label={values.join(', ')}>
        <line x1={0} y1={39.5} x2={100} y2={39.5} stroke="var(--color-border)" strokeWidth={0.5} vectorEffect="non-scaling-stroke" />
        {points.map((point, index) => {
          const height = (values[index] / max) * 38;
          return (
            <rect key={point.bucket} x={index * width + gap * 0.1} width={Math.max(0.4, width - gap * 0.2)}
              y={40 - height} height={height} fill={accent ? ACCENT : QUIET}>
              <title>{`${bucketLabel(point.bucket, window)} · ${fmt(values[index])}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between text-meta text-[var(--color-text-dim)]">
        <span>{points.length ? bucketLabel(points[0].bucket, window) : ''}</span>
        <span>{points.length ? bucketLabel(points[points.length - 1].bucket, window) : ''}</span>
      </div>
    </div>
  );
}

export function CdfDashboard({ data, window, notKeptHint }: {
  data: LandedOverview; window: IngestionWindow; notKeptHint?: string;
}) {
  const { t } = useTranslation();
  const now = { captured: data.total, learned: data.totals.new + data.totals.adds,
    reinforced: data.totals.reinforced, notKept: data.totals.notKept };
  const change = (metric: Metric): string => {
    if (!data.previous) return '';   // said once, under the cards
    const before = data.previous[metric];
    if (before === 0) return now[metric] === 0 ? t('cdf.dash.same', { window: t(`cdf.w${window}`) })
      : t('cdf.dash.fromZero', { window: t(`cdf.w${window}`) });
    const pct = Math.round(((now[metric] - before) / before) * 100);
    if (pct === 0) return t('cdf.dash.same', { window: t(`cdf.w${window}`) });
    return t(pct > 0 ? 'cdf.dash.up' : 'cdf.dash.down', { pct: Math.abs(pct), window: t(`cdf.w${window}`) });
  };
  return (
    <section data-testid="cdf-dashboard">
      <div className="grid grid-cols-4 gap-3">
      {METRICS.map((metric) => {
        const accent = metric === 'learned';
        return (
          <div key={metric} className="min-w-0 rounded-lg px-4 py-3.5" style={{ background: CARD_BG }} data-testid={`cdf-kpi-${metric}`}>
            <div className="text-small text-[var(--color-text-muted)]">{t(`cdf.dash.${metric}`)}</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-display font-semibold tabular-nums"
                style={{ color: accent ? ACCENT : 'var(--color-text)' }}>{fmt(now[metric])}</span>
              <span className="truncate text-small text-[var(--color-text-dim)]">{change(metric)}</span>
            </div>
            <p className="mt-0.5 line-clamp-2 h-9 text-small text-[var(--color-text-muted)]">
              {metric === 'notKept' ? (notKeptHint ?? '') : t(`cdf.dash.${metric}Hint`)}
            </p>
            <Trend points={data.series} metric={metric} window={window} accent={accent} />
          </div>
        );
      })}
      </div>
      {!data.previous && <p className="mt-1.5 text-small text-[var(--color-text-dim)]">{t('cdf.dash.noPrevious')}</p>}
    </section>
  );
}
