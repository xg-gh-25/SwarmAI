/**
 * Jobs section for the Radar sidebar.
 *
 * Displays autonomous job status fetched via
 * ``radarService.fetchAutonomousJobs`` (no workspace_id param — the API
 * returns all jobs globally).  Each row shows the job name, a colored
 * status indicator (running=green pulsing, paused=gray, error=red,
 * completed=blue), and a category label (system / user_defined).
 *
 * Jobs are NOT draggable — no ``DragHandle`` is rendered.
 *
 * Key exports:
 * - ``JobsSection``           — The section component
 * - ``JOB_STATUS_CONFIG``     — Status-to-color/label mapping
 * - ``JOB_CATEGORY_LABELS``   — Category-to-display-label mapping
 * - ``countActiveJobs``       — Returns count of non-completed jobs
 */

import { useState, useEffect } from 'react';
import type { RadarAutonomousJob } from '../../../../types';
import { radarService } from '../../../../services/radar';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Visual configuration per job status.
 * ``color`` is a CSS color value; ``pulse`` enables a CSS animation.
 */
export const JOB_STATUS_CONFIG: Record<
  RadarAutonomousJob['status'],
  { color: string; label: string; pulse: boolean }
> = {
  running:   { color: 'var(--color-success, #22c55e)', label: 'Running',   pulse: true },
  paused:    { color: 'var(--color-text-muted, #9ca3af)', label: 'Paused',  pulse: false },
  error:     { color: 'var(--color-error, #ef4444)',   label: 'Error',     pulse: false },
  completed: { color: 'var(--color-info, #3b82f6)',    label: 'Completed', pulse: false },
};

/** Human-friendly labels for job categories. */
export const JOB_CATEGORY_LABELS: Record<RadarAutonomousJob['category'], string> = {
  system: 'System',
  user_defined: 'User',
};

// ---------------------------------------------------------------------------
// Pure helpers (exported for testing)
// ---------------------------------------------------------------------------

/** Count active (non-completed) jobs for the badge. */
export function countActiveJobs(jobs: RadarAutonomousJob[]): number {
  return jobs.filter((j) => j.status !== 'completed').length;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function JobsSection() {
  const [jobs, setJobs] = useState<RadarAutonomousJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch autonomous jobs on mount
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    radarService
      .fetchAutonomousJobs()
      .then((data) => {
        if (!cancelled) {
          setJobs(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load jobs');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // --- Loading state ---
  if (loading) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        Loading jobs…
      </p>
    );
  }

  // --- Error state ---
  if (error) {
    return (
      <p className="text-xs text-[var(--color-error)] py-2">
        {error}
      </p>
    );
  }

  // --- Empty state ---
  if (jobs.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        No autonomous jobs
      </p>
    );
  }

  // --- Item list ---
  return (
    <ul className="space-y-1">
      {jobs.map((job) => {
        const cfg = JOB_STATUS_CONFIG[job.status] ?? JOB_STATUS_CONFIG.paused;
        const categoryLabel =
          JOB_CATEGORY_LABELS[job.category] ?? JOB_CATEGORY_LABELS.system;

        return (
          <li
            key={job.id}
            className="flex items-center gap-2 px-1 py-1 rounded hover:bg-[var(--color-hover)] transition-colors"
          >
            {/* Status indicator dot */}
            <span
              className={`shrink-0 w-2 h-2 rounded-full${cfg.pulse ? ' animate-pulse' : ''}`}
              style={{ backgroundColor: cfg.color }}
              title={cfg.label}
            />

            {/* Job name */}
            <span className="text-xs text-[var(--color-text)] truncate flex-1">
              {job.name}
            </span>

            {/* Category label */}
            <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] px-1 py-0.5 rounded bg-[var(--color-surface, transparent)]">
              {categoryLabel}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
