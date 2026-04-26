/**
 * Jobs status bar — bottom bar showing job health summary.
 *
 * Click → expand/collapse job list.
 * Used by RadarSidebar (always visible at bottom).
 */

import { useState } from 'react';
import type { JobsSummary } from '../../../../services/system';

interface JobsBarProps {
  summary: JobsSummary;
}

export function JobsBar({ summary }: JobsBarProps) {
  const [expanded, setExpanded] = useState(false);

  if (summary.total === 0) return null;

  const hasFailed = summary.failed > 0;

  return (
    <div className="border-t border-[var(--color-border)]">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer"
      >
        <span className="text-[11px]">⚡</span>
        <span className={`text-[11px] font-medium ${hasFailed ? 'text-red-400' : 'text-[var(--color-text-muted)]'}`}>
          {summary.healthy} healthy
          {hasFailed && ` · ${summary.failed} failed`}
          {summary.disabled > 0 && ` · ${summary.disabled} off`}
        </span>
        <span className="ml-auto text-[var(--color-text-muted)]">
          <span
            className="material-symbols-outlined text-sm transition-transform duration-200"
            style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)' }}
          >
            expand_more
          </span>
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-0.5">
          {summary.jobs.map((job) => {
            const dotColor = {
              healthy: 'bg-green-400',
              running: 'bg-green-400 animate-pulse',
              failed: 'bg-red-400',
              disabled: 'bg-[var(--color-text-muted)]',
            }[job.status] ?? 'bg-[var(--color-text-muted)]';

            return (
              <div
                key={job.id}
                className="flex items-center gap-2 px-1 py-0.5"
              >
                <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${dotColor}`} />
                <span className="text-[11px] text-[var(--color-text)] truncate flex-1">
                  {job.name}
                </span>
                <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
                  {job.schedule}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
