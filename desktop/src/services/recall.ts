/**
 * recall.ts — client for the recall-metrics visibility endpoint (unified-recall Run 3).
 *
 * Wraps GET /api/recall/metrics — count/p50/p95 recall latency by (context, domain),
 * computed read-side by the backend from the recall_metrics table. Read-only; the
 * backend degrades to an empty contexts list on error, so callers never see a 500.
 *
 * @exports getRecallMetrics, RecallMetricRow, RecallMetricsResponse, RECALL_CONTEXTS
 */
import api from './api';

/** One aggregated row: a (context, domain) group with count + latency percentiles. */
export interface RecallMetricRow {
  context: string;
  domain: string;
  count: number;
  p50_ms: number;
  p95_ms: number;
}

export interface RecallMetricsResponse {
  generated_at: string;
  contexts: RecallMetricRow[];
}

/** The recall contexts the daemon records today (Run 2 + Run 3). Drives the dashboard's
 *  per-context sections; a context with no rows yet simply renders empty. */
export const RECALL_CONTEXTS = [
  'session_prompt',
  'session_ddd',
  'library_overlay',
  'brainhub_overlay',
] as const;

/** GET /api/recall/metrics — optionally filtered to one context / a lookback window. */
export async function getRecallMetrics(
  context?: string,
  windowHours?: number,
): Promise<RecallMetricsResponse> {
  const params = new URLSearchParams();
  if (context) params.set('context', context);
  if (windowHours != null) params.set('window', String(windowHours));
  const qs = params.toString();
  const resp = await api.get<RecallMetricsResponse>(
    `/recall/metrics${qs ? `?${qs}` : ''}`,
  );
  return resp.data;
}
