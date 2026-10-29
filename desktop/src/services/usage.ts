/**
 * usage — the usage view's data (GET /api/usage/report, PUT /api/usage/budget;
 * backend core/usage_report.py, design docs/design/2026-10-29-usage-accounting.md §5).
 *
 * Every figure is the usage ledger's: spend rows of every machine (this host and the
 * Hives) and every surface. A cost is the CLI's book cost, not the AWS bill; a row
 * with tokens and no known cost counts in `unpriced_*`, never as $0. Attribution (a
 * pipeline run's share of its session) splits chat spend and is never added to a total.
 *
 * @exports UsageWindow, UsageTotal, UsageGroup, UsageReport, usageService, useUsageReport
 */
import { useQuery } from '@tanstack/react-query';
import api from './api';

export type UsageWindow = '7d' | '30d' | 'all';
export type UsageDimension = 'surface' | 'feature' | 'model' | 'machine';
export type UsageTop = 'sessions' | 'runs' | 'jobs' | 'hive';

export interface UsageTotal {
  cost_usd: number;
  /** every billed token: input + output + cache reads + cache writes */
  billed: number;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  cache_hit_rate: number | null;
  rows: number;
  /** model calls: a CLI result writes one row per model, one call */
  calls: number;
  unpriced_calls: number;
  unpriced_tokens: number;
}

export interface UsageGroup extends UsageTotal {
  key: string | null;
  title?: string | null;
  report_path?: string | null;
  machine?: string;
  dispatch_id?: string | null;
  session_id?: string | null;
}

export interface UsageDay {
  day: string;
  cost_usd: number;
  by_surface: Record<string, number>;
  in_window: boolean;
}

export interface UsageBucket { from: number; to: number | null; turns: number; cost_usd: number; share: number | null }

export interface UsageReport {
  window: UsageWindow;
  since: string | null;
  today: string;
  total: UsageTotal;
  previous: UsageTotal | null;
  days: number;
  daily: UsageDay[];
  breakdown: Record<UsageDimension, UsageGroup[]>;
  attribution: UsageTotal & { runs: number };
  turns: { turns: number; p50: number | null; p90: number | null; max: number | null; buckets: UsageBucket[] };
  top: Record<UsageTop, UsageGroup[]>;
  concentration: {
    top_sessions: number;
    top_sessions_share: number | null;
    big_turn_usd: number;
    big_turns: number;
    big_turns_turn_share: number | null;
    big_turns_cost_share: number | null;
  };
  gaps: {
    unpriced: { surface: string; calls: number; tokens: number }[];
    imported_cost_usd: number;
    imported_calls: number;
    live_since: string | null;
    hives_reporting: string[];
  };
  budget: { month_spend_usd: number | null; monthly_usd: number | null; share: number | null };
}

export const usageService = {
  async report(window: UsageWindow): Promise<UsageReport> {
    const r = await api.get<UsageReport>('/usage/report', { params: { window } });
    return r.data;
  },
  async setBudget(monthlyUsd: number): Promise<number> {
    const r = await api.put<{ monthly_usd: number }>('/usage/budget', { monthly_usd: monthlyUsd });
    return r.data.monthly_usd;
  },
};

export function useUsageReport(window: UsageWindow) {
  return useQuery({
    queryKey: ['usage-report', window],
    queryFn: () => usageService.report(window),
    refetchInterval: 60_000,
    staleTime: 20_000,
  });
}
