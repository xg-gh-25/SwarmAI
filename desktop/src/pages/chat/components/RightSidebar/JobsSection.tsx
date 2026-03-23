/**
 * Jobs section for the Radar sidebar.
 *
 * Displays autonomous job status fetched from the backend, which reads
 * real job definitions (jobs.yaml) and runtime state (state.json) from
 * the SwarmWS scheduler directory.
 *
 * Each row shows: status indicator (color-coded), job name, schedule,
 * last run time (relative), run count, and failure state.
 *
 * Jobs are NOT draggable — no DragHandle is rendered.
 *
 * Key exports:
 * - ``JobsSection``           — The section component
 * - ``JOB_STATUS_CONFIG``     — Status-to-color/label mapping
 * - ``JOB_CATEGORY_LABELS``   — Category-to-display-label mapping
 * - ``countActiveJobs``       — Returns count of non-completed jobs
 */

import { useState, useEffect, useRef } from 'react';
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
  running:   { color: 'var(--color-success, #22c55e)', label: 'Healthy',   pulse: true },
  paused:    { color: 'var(--color-text-muted, #9ca3af)', label: 'Disabled', pulse: false },
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

/** Count active (non-completed, non-paused) jobs for the badge. */
export function countActiveJobs(jobs: RadarAutonomousJob[]): number {
  return jobs.filter((j) => j.status !== 'completed' && j.status !== 'paused').length;
}

/** Format a relative time string from an ISO timestamp. */
function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return 'never';
  try {
    const date = new Date(isoString);
    const now = Date.now();
    const diffMs = now - date.getTime();
    if (diffMs < 0) return 'just now';

    const minutes = Math.floor(diffMs / 60_000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;

    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return 'unknown';
  }
}

/** Format a cron expression into a human-readable description. */
function formatSchedule(schedule: string | null): string {
  if (!schedule) return '';
  if (schedule.startsWith('after:')) return `after ${schedule.slice(6)}`;
  // Basic cron → human mapping for common patterns
  if (schedule === '0 0 * * 1-5') return 'Weekdays 8am';
  if (schedule.match(/^0 \d+(,\d+)* \* \* \*$/)) {
    const hours = schedule.split(' ')[1].split(',');
    return `${hours.length}x daily`;
  }
  if (schedule.match(/^0 \d+ \* \* 0$/)) return 'Weekly';
  return schedule;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface JobsSectionProps {
  /** Report item count to parent for badge display. */
  onCountChange?: (count: number) => void;
}

export function JobsSection({ onCountChange }: JobsSectionProps = {}) {
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

  // Report count to parent
  const prevCountRef = useRef(-1);
  useEffect(() => {
    const active = countActiveJobs(jobs);
    if (onCountChange && active !== prevCountRef.current) {
      prevCountRef.current = active;
      onCountChange(active);
    }
  }, [jobs, onCountChange]);

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
        No scheduled jobs configured
      </p>
    );
  }

  // --- Item list ---
  return (
    <ul className="space-y-0.5">
      {jobs.map((job) => {
        const cfg = JOB_STATUS_CONFIG[job.status] ?? JOB_STATUS_CONFIG.paused;
        const categoryLabel =
          JOB_CATEGORY_LABELS[job.category] ?? JOB_CATEGORY_LABELS.system;
        const relTime = formatRelativeTime(job.lastRunAt);
        const schedule = formatSchedule(job.schedule);
        const hasFailures = job.consecutiveFailures > 0;

        return (
          <li
            key={job.id}
            className="px-1.5 py-1.5 rounded hover:bg-[var(--color-hover)] transition-colors"
          >
            {/* Row 1: status dot + name + category */}
            <div className="flex items-center gap-2">
              <span
                className={`shrink-0 w-2 h-2 rounded-full${cfg.pulse ? ' animate-pulse' : ''}`}
                style={{ backgroundColor: cfg.color }}
                title={cfg.label}
              />
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {job.name}
              </span>
              <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] px-1 py-0.5 rounded bg-[var(--color-surface, transparent)]">
                {categoryLabel}
              </span>
            </div>

            {/* Row 2: schedule + last run + runs count */}
            <div className="flex items-center gap-2 ml-4 mt-0.5">
              {schedule && (
                <span className="text-[10px] text-[var(--color-text-muted)]">
                  {schedule}
                </span>
              )}
              <span className="text-[10px] text-[var(--color-text-muted)]">
                · {relTime}
              </span>
              {job.totalRuns > 0 && (
                <span className="text-[10px] text-[var(--color-text-muted)]">
                  · {job.totalRuns} runs
                </span>
              )}
              {hasFailures && (
                <span className="text-[10px] text-[var(--color-error)]" title={`${job.consecutiveFailures} consecutive failures`}>
                  · {job.consecutiveFailures}× fail
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
