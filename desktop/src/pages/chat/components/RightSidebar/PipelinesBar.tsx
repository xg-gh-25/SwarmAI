/**
 * PipelinesBar — bottom FYI bar in the Radar sidebar showing RUNNING pipelines
 * (Run 1 redesign). Read-only, NOT clickable: a running pipeline needs no user
 * action, so it must not present a click target (zero mis-operation surface).
 * Paused pipelines do NOT appear here — they live in the 🔔 AttentionSection.
 *
 * Empty running list → renders null (bar disappears entirely).
 */
import type { RunningPipeline } from './types';

interface PipelinesBarProps {
  running: RunningPipeline[];
}

export function PipelinesBar({ running }: PipelinesBarProps) {
  if (running.length === 0) return null;

  return (
    <div className="border-t border-[var(--color-border)] bg-[var(--color-bg-card)]">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
        <span className="text-[11px] font-medium text-[var(--color-text-muted)]">
          {running.length} running
        </span>
      </div>
      <div className="px-3 pb-2 space-y-0.5">
        {running.map((p) => (
          // Intentionally a <div>, not a <button> — running pipelines are
          // display-only (see file docstring).
          <div key={p.id} className="flex items-center gap-2 px-1 py-0.5">
            <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--color-text)]">
              {p.title}
              {p.project !== 'SwarmAI' && (
                <span className="ml-1 text-[var(--color-text-muted)]">{p.project}</span>
              )}
            </span>
            <span className="shrink-0 rounded bg-[var(--color-bg)] px-1.5 py-0.5 text-[10.5px] tabular-nums text-[var(--color-text-muted)]">
              {p.stage}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
