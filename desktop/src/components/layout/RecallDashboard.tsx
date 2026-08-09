/**
 * RecallDashboard — the unified recall-metrics view across ALL contexts.
 *
 * Unified-recall Run 3 (design §3.5): the "统一 dashboard" companion to the per-overlay
 * RecallMetricsPanel. One section per recorded recall context (session prompt / session
 * DDD / Library / Brain Hub), each rendering that context's RecallMetricsPanel. A context
 * with no samples yet renders its quiet empty line — the sections are always present so
 * the reader sees the full recall surface, not just whatever happened to fire.
 *
 * @exports RecallDashboard
 */
import { RecallMetricsPanel, CONTEXT_LABEL } from './RecallMetricsPanel';
import { RECALL_CONTEXTS } from '../../services/recall';

export function RecallDashboard() {
  return (
    <div data-testid="recall-dashboard" className="flex flex-col gap-3">
      <div className="text-[12px] font-mono uppercase tracking-wider text-[var(--color-text-muted)]">
        Recall metrics
      </div>
      {RECALL_CONTEXTS.map((ctx) => (
        <section key={ctx} data-testid={`recall-dashboard-${ctx}`} className="flex flex-col gap-1.5">
          <div className="text-[11px] font-medium text-[var(--color-text)]">
            {CONTEXT_LABEL[ctx] ?? ctx}
          </div>
          <RecallMetricsPanel context={ctx} />
        </section>
      ))}
    </div>
  );
}
