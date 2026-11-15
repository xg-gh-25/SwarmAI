/**
 * The cognition level's text and curve arithmetic, shared by the tab, the C&M rail and
 * their tests. The trend itself is computed once, in the backend
 * (core/ingestion/cognition_level.trend: the last four weeks pooled against the four
 * before, with a 95% interval of the difference); this file only words it, in the same
 * verdicts the prompt headline uses (core/north_star.trend_verdict; both held to
 * backend/tests/fixtures/north_star_trend_wording.json). The curve's line pools four weeks
 * too, weighted by the conversations judged (one week swings by 20-40 points).
 *
 * Key exports: `rolling`, `pooled`, `levelLine`, `trendVerdict`, `WINDOW`.
 */
import type { TFunction } from 'i18next';
import type { LevelTrend, LevelWeek } from '../../services/ingestion';

export const WINDOW = 4;
const MIN_POOLED = 10;   // fewer judged conversations in a window give no share

/** The share not corrected over *weeks*, pooled; null with too few conversations. */
export function pooled(weeks: LevelWeek[]): number | null {
  const judged = weeks.reduce((n, w) => n + w.judged, 0);
  if (judged < MIN_POOLED) return null;
  return 1 - weeks.reduce((n, w) => n + w.corrected, 0) / judged;
}

/** For each week, the pooled share over it and the WINDOW - 1 weeks before it. */
export function rolling(weeks: LevelWeek[]): Array<number | null> {
  return weeks.map((_, i) => (i < WINDOW - 1 ? null : pooled(weeks.slice(i - WINDOW + 1, i + 1))));
}

/** The verdict on the backend's trend: better / worse only when its 95% interval excludes
 *  zero, otherwise no visible change within ±the interval; too few without a direction. */
export function trendVerdict(t: TFunction, trend: { direction?: LevelTrend['direction']; margin?: number | null } | null | undefined): string {
  const direction = trend?.direction;
  if (direction === 'better' || direction === 'worse') return t(`cogLevel.verdict.${direction}`);
  if (direction === 'same') return t('cogLevel.verdict.same', { margin: trend?.margin ?? '' });
  return t('cogLevel.verdict.tooFew');
}

/** The headline: the backend's trend, as shares not corrected (the tab's measure). */
export function levelLine(t: TFunction, trend: LevelTrend | null | undefined): string {
  if (!trend || trend.now.rate == null || trend.before.rate == null) {
    return t('cogLevel.line.tooFew', { n: 2 * WINDOW });
  }
  return t('cogLevel.line.trend', {
    now: `${Math.round((1 - trend.now.rate) * 100)}%`,
    before: `${Math.round((1 - trend.before.rate) * 100)}%`,
    verdict: trendVerdict(t, trend),
  });
}
