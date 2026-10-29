/**
 * format — the usage view's number text and the pure choices behind it (design
 * docs/design/2026-10-29-usage-accounting.md §5).
 *
 * @exports money, tokens, pct, change, rowCost, SURFACE_COLOR
 */
import type { UsageTotal } from '../../services/usage';

/** $2,511 at a hundred and above, $8.74 below it. */
export function money(v: number): string {
  const abs = Math.abs(v);
  return `$${v.toLocaleString('en-US', abs >= 100 ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function tokens(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

export function pct(share: number | null | undefined): string {
  return share == null ? '—' : `${Math.round(share * 100)}%`;
}

/** The change from the window before: direction and whole percent; null when the
 *  window before spent nothing (no base to compare with). */
export function change(cur: number, prev: number | null | undefined): { dir: 'up' | 'down' | 'flat'; pct: number } | null {
  if (prev == null || prev <= 0) return null;
  const p = Math.round(((cur - prev) / prev) * 100);
  return { dir: p > 0 ? 'up' : p < 0 ? 'down' : 'flat', pct: Math.abs(p) };
}

/** A group's cost as shown: unknown when it has tokens and none of them is priced
 *  (never a $0 that reads as free). */
export function rowCost(g: Pick<UsageTotal, 'cost_usd' | 'unpriced_tokens' | 'billed'>): string | null {
  if (g.cost_usd === 0 && g.unpriced_tokens > 0) return null;
  return money(g.cost_usd);
}

export const SURFACE_COLOR: Record<string, string> = {
  chat: '#6b7fa3',
  channel: '#5b8db8',
  pipeline: '#8f6fd6',
  cdf: '#3aa39b',
  job: '#c98b3a',
  background: '#7d8590',
  eval: '#a07d90',
  hive: '#4a8fb0',
};
