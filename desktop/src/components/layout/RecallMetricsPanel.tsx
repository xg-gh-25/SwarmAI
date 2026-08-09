/**
 * RecallMetricsPanel — a small recall-latency read-out for one recall context.
 *
 * Unified-recall Run 3 (design §3.5): each overlay gets a small metrics panel; a
 * unified dashboard (RecallDashboard) reuses this same component per context. Shows
 * count + p50/p95 latency per (context, domain) group from GET /api/recall/metrics.
 *
 * Quiet by design: no data yet → a faint "no recall samples yet" line, never an empty
 * void or an error shout (mirrors LibraryHealth). Read-only visibility; the backend
 * degrades to an empty list on error so this never renders a crash.
 *
 * @exports RecallMetricsPanel
 */
import { useQuery } from '@tanstack/react-query';
import { getRecallMetrics, type RecallMetricsResponse } from '../../services/recall';

/** A human label per context (fallback = the raw key). */
const CONTEXT_LABEL: Record<string, string> = {
  session_prompt: 'Session prompt',
  session_ddd: 'Session DDD',
  library_overlay: 'Library',
  brainhub_overlay: 'Brain Hub',
};

export function RecallMetricsPanel({ context }: { context: string }) {
  const { data, isLoading, isError } = useQuery<RecallMetricsResponse>({
    queryKey: ['recall-metrics', context],
    queryFn: () => getRecallMetrics(context),
    staleTime: 60_000,
  });

  // Serialization-boundary guard (O023): a partial/missing payload is treated as
  // "no rows", never a render crash.
  const rows = Array.isArray(data?.contexts) ? data!.contexts : [];

  if (isLoading || isError || !data) {
    return isError ? (
      <div data-testid="recall-metrics-error" className="text-[10px] text-[var(--color-text-faint)]">
        recall metrics unavailable
      </div>
    ) : null;
  }

  if (rows.length === 0) {
    return (
      <div data-testid="recall-metrics-empty" className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-faint)]">
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#8a8f99' }} aria-hidden />
        No recall samples yet
      </div>
    );
  }

  return (
    <div data-testid="recall-metrics-panel" className="flex flex-col gap-1.5">
      <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
        ⏱ Recall latency
      </div>
      {rows.map((r) => (
        <div
          key={`${r.context}:${r.domain}`}
          data-testid="recall-metrics-row"
          className="flex items-center justify-between gap-2 text-[11px]"
        >
          <span className="min-w-0 truncate text-[var(--color-text-muted)]" title={`${r.context} · ${r.domain}`}>
            {r.domain}
          </span>
          <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-faint)]">
            n={r.count} · p50 {r.p50_ms}ms · p95 {r.p95_ms}ms
          </span>
        </div>
      ))}
    </div>
  );
}

export { CONTEXT_LABEL };
