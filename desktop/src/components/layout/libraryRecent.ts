/**
 * The Library Recent tab's pure helpers (LibraryOverlay): a file's readable name, the
 * feed's source, and grouping by local day.
 *
 * Exports: RecentSource, RecentItem, readableName, sourceOf, sourceCounts, groupByDay, pad
 */

export type RecentSource = 'cdf' | 'chat' | 'job';
/** One GET /library/recent item (snake_case as served). */
export interface RecentItem { path: string; category: string; mtime: number; size: number; source: RecentSource; cdf_units?: number; }

export const pad = (n: number) => String(n).padStart(2, '0');

/** A file's readable name: the basename without its extension, a leading date, a CDF
 *  unit suffix (`-cand-<hex>-unit`), with dashes and underscores as spaces. */
export function readableName(path: string): string {
  const base = path.split('/').pop() ?? path;
  const stem = base.includes('.') && !base.startsWith('.') ? base.slice(0, base.lastIndexOf('.')) : base;
  if (/^\d{4}-\d{2}-\d{2}$/.test(stem)) return stem;   // a day's log is named by its date
  const name = stem
    .replace(/^\d{4}-\d{2}-\d{2}-(?=.)/, '')
    .replace(/-cand-[0-9a-f]+-unit$/i, '')
    .replace(/[-_]+/g, ' ')
    .trim();
  return name || stem;
}

/** Old feeds (before the CDF journal attribution) said session / you: neither is CDF. */
export const sourceOf = (s: string): RecentSource => (s === 'cdf' || s === 'job' ? s : 'chat');

export interface DayGroup { key: string; label: string; items: RecentItem[] }

/** Items grouped by local day, newest first: today, yesterday, then MM-DD. */
export function groupByDay(items: RecentItem[], labels: { today: string; yesterday: string }, now = new Date()): DayGroup[] {
  const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = dayKey(now);
  const y = new Date(now); y.setDate(y.getDate() - 1);
  const yesterday = dayKey(y);
  const groups: DayGroup[] = [];
  for (const it of [...items].sort((a, b) => b.mtime - a.mtime)) {
    const d = new Date(it.mtime * 1000);
    const key = dayKey(d);
    let g = groups[groups.length - 1];
    if (!g || g.key !== key) {
      g = { key, label: key === today ? labels.today : key === yesterday ? labels.yesterday : `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, items: [] };
      groups.push(g);
    }
    g.items.push(it);
  }
  return groups;
}

/** Files this week by source: the feed's own counts (taken before its item cap), else
 *  counted from the items an older feed carries. */
export function sourceCounts(feed: { items: Array<{ source: string }>; by_source?: Partial<Record<RecentSource, number>> } | undefined): Record<RecentSource, number> {
  const out: Record<RecentSource, number> = { cdf: 0, chat: 0, job: 0 };
  if (!feed) return out;
  if (feed.by_source) {
    (Object.keys(out) as RecentSource[]).forEach((k) => { out[k] = feed.by_source?.[k] ?? 0; });
  } else {
    feed.items.forEach((it) => { out[sourceOf(it.source)] += 1; });
  }
  return out;
}
