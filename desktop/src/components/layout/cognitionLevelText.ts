/**
 * The cognition level's arithmetic, shared by the tab and its tests: a four-week rolling
 * share and the headline sentence. One week is too few conversations to read a trend
 * from (measured: neighbouring weeks swing by 20-40 points), so both pool four weeks,
 * weighted by the conversations judged.
 *
 * Key exports: `rolling`, `pooled`, `levelLine`, `WINDOW`.
 */
import type { TFunction } from 'i18next';
import type { LevelWeek } from '../../services/ingestion';

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

/** The headline: the last WINDOW weeks against the WINDOW before them. */
export function levelLine(t: TFunction, weeks: LevelWeek[]): string {
  if (weeks.length < 2 * WINDOW) return t('cogLevel.line.tooFew', { n: 2 * WINDOW });
  const now = pooled(weeks.slice(-WINDOW));
  const before = pooled(weeks.slice(-2 * WINDOW, -WINDOW));
  if (now == null || before == null) return t('cogLevel.line.tooFew', { n: 2 * WINDOW });
  const delta = now - before;
  const values = { now: `${Math.round(now * 100)}%`, before: `${Math.round(before * 100)}%` };
  if (delta >= 0.05) return t('cogLevel.line.better', values);
  if (delta <= -0.05) return t('cogLevel.line.worse', values);
  return t('cogLevel.line.flat', values);
}
