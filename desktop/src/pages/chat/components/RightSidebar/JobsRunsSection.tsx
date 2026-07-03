/**
 * JobsRunsSection — the ⚡ "Jobs & Runs" inventory section in the Radar sidebar.
 *
 * Restores the job + pipeline-run visibility the Run-1 redesign dropped (Option
 * B, run_06b89c00): it is the single INVENTORY surface — every scheduled job
 * (status dot + schedule + last-run) and every ACTIVE pipeline run (running /
 * paused only, with a status badge + N/M progress). Completed/failed/cancelled/
 * abandoned runs are dropped as historical noise (run_820a4732). It REPLACES the
 * old bottom PipelinesBar (deleted) and the orphaned JobsBar (deleted).
 *
 * Relationship to 🔔 Needs You: that queue owns only the ACTIONABLE copies
 * (failing jobs, paused runs). This section is the full roster — the same item
 * legitimately appears in both, with different intent ("act on this" vs "here's
 * everything + status"). Default-EXPANDED so the roster is open on load; it
 * lives INSIDE the scrollable section stack (not a pinned bottom bar), so with a
 * long ToDo/Attention/Changes stack above it, it scrolls with them.
 *
 * Jobs and runs each fold behind a "See N more" toggle past SEE_MORE_LIMIT so a
 * long roster stays scannable; the section-header count shows the TOTAL
 * (jobs + runs) — nothing hidden silently. Empty roster → renders null.
 *
 * Data + sort come from useJobsRuns (pure aggregateJobsRuns, 30s poll). This
 * component is presentational.
 *
 * @exports JobsRunsSection
 */
import { useState, useEffect } from 'react';
import { CollapsibleSection } from './shared/CollapsibleSection';
import { useJobsRuns, type JobRow, type RunRow, type JobHealth } from '../../../../hooks/useJobsRuns';
import { formatRelativeTime } from '../briefing/BriefingUtils';

/** How many rows show per group before the "See more" fold. */
const SEE_MORE_LIMIT = 5;

/** Status-dot colour per job health — mirrors the old JobsBar palette. */
const HEALTH_DOT: Record<JobHealth, string> = {
  healthy: 'bg-green-400',
  failed: 'bg-red-400',
  disabled: 'bg-[var(--color-text-muted)]',
};

/** Run-status badge — same visual family as the Changes NEW/UPD + attention pills. */
const RUN_BADGE: Record<RunRow['status'], { label: string; cls: string }> = {
  running: { label: 'RUN', cls: 'text-green-400 bg-green-400/10' },
  paused: { label: 'PAUSED', cls: 'text-amber-400 bg-amber-400/10' },
  completed: { label: 'DONE', cls: 'text-blue-400 bg-blue-400/10' },
  failed: { label: 'FAIL', cls: 'text-red-400 bg-red-400/10' },
  cancelled: { label: 'CANCEL', cls: 'text-[var(--color-text-muted)] bg-[var(--color-hover)]' },
  abandoned: { label: 'ABANDON', cls: 'text-[var(--color-text-muted)] bg-[var(--color-hover)]' },
};

const ROW_CLS =
  'group flex h-6 items-center gap-1.5 px-2 rounded text-[12px] transition-colors hover:bg-[var(--color-hover)]';

function JobRowView({ job }: { job: JobRow }) {
  const rel = job.lastRun ? formatRelativeTime(job.lastRun) : 'never';
  return (
    <div className={ROW_CLS} title={`${job.name} · ${job.schedule || 'no schedule'} · last run ${rel}`}>
      <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${HEALTH_DOT[job.health]}`} />
      <span className="min-w-0 flex-1 truncate text-[var(--color-text)]">{job.name}</span>
      {job.schedule && (
        <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-muted)]">{job.schedule}</span>
      )}
      <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] tabular-nums w-[52px] text-right">
        {rel}
      </span>
    </div>
  );
}

function RunRowView({ run }: { run: RunRow }) {
  const badge = RUN_BADGE[run.status];
  return (
    <div className={ROW_CLS} title={`${run.project} · ${run.title} · ${run.status} ${run.progress}`}>
      <span className={`shrink-0 rounded px-1 text-[9px] font-bold tracking-wide ${badge.cls}`}>
        {badge.label}
      </span>
      <span className="min-w-0 flex-1 truncate text-[var(--color-text)]">{run.title}</span>
      <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-muted)] tabular-nums">
        {run.progress}
      </span>
    </div>
  );
}

/** A group (jobs or runs) with its own "See N more" fold. */
function FoldGroup<T>({
  label,
  items,
  render,
  keyOf,
}: {
  label: string;
  items: T[];
  render: (item: T) => React.ReactNode;
  keyOf: (item: T) => string;
}) {
  const [showAll, setShowAll] = useState(false);
  // A 30s poll can shrink the group to ≤ SEE_MORE_LIMIT while expanded; without
  // this reset the toggle button (gated on length > limit) vanishes and strands
  // showAll=true with no way back to collapsed (Gate-2 MED, run_06b89c00).
  useEffect(() => {
    if (items.length <= SEE_MORE_LIMIT && showAll) setShowAll(false);
  }, [items.length, showAll]);
  if (items.length === 0) return null;
  const visible = showAll ? items : items.slice(0, SEE_MORE_LIMIT);
  const hidden = items.length - visible.length;

  return (
    <div className="py-0.5">
      <div className="px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.8px] text-[var(--color-text-muted)]">
        {label} ({items.length})
      </div>
      {visible.map((item) => (
        <div key={keyOf(item)}>{render(item)}</div>
      ))}
      {items.length > SEE_MORE_LIMIT && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="flex w-full items-center justify-center gap-0.5 rounded px-2 py-0.5 text-[10.5px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
          aria-expanded={showAll}
        >
          {showAll ? 'See less' : `See ${hidden} more`}
          <span
            className="material-symbols-outlined text-[14px] transition-transform duration-150"
            style={{ transform: showAll ? 'rotate(180deg)' : 'rotate(0deg)' }}
          >
            expand_more
          </span>
        </button>
      )}
    </div>
  );
}

export function JobsRunsSection() {
  const { jobs, runs } = useJobsRuns();
  const total = jobs.length + runs.length;

  if (total === 0) return null;

  return (
    <CollapsibleSection
      name="jobs-runs"
      icon="bolt"
      label="Jobs & Runs"
      count={total}
      defaultExpanded={true}
      accent="rgba(99,102,241,0.35)"
    >
      <FoldGroup
        label="Scheduled jobs"
        items={jobs}
        keyOf={(j) => j.id}
        render={(j) => <JobRowView job={j} />}
      />
      <FoldGroup
        label="Pipeline runs"
        items={runs}
        keyOf={(r) => r.id}
        render={(r) => <RunRowView run={r} />}
      />
    </CollapsibleSection>
  );
}
