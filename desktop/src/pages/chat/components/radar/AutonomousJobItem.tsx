/**
 * Single autonomous job item with status indicator, timestamps, and
 * "Coming soon" tooltip on click.
 *
 * Exports:
 * - AutonomousJobItem — Renders one RadarAutonomousJob with status and tooltip
 */

import { useState, useEffect, useRef } from 'react';
import clsx from 'clsx';
import type { RadarAutonomousJob } from '../../../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_INDICATORS: Record<string, string> = {
  running: '✅ Running',
  paused: '⏸️ Paused',
  error: '❌ Error',
  completed: '✔️ Completed',
};

/** Format an ISO timestamp as a relative time string. */
function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'Never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface AutonomousJobItemProps {
  job: RadarAutonomousJob;
  onClick: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AutonomousJobItem({ job, onClick }: AutonomousJobItemProps) {
  const [showTooltip, setShowTooltip] = useState(false);
  const tooltipTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleClick = () => {
    if (tooltipTimerRef.current) clearTimeout(tooltipTimerRef.current);
    setShowTooltip(true);
    tooltipTimerRef.current = setTimeout(() => setShowTooltip(false), 2000);
    onClick();
  };

  // Dismiss on any subsequent click when tooltip is visible
  useEffect(() => {
    if (!showTooltip) return;
    const dismiss = () => setShowTooltip(false);
    document.addEventListener('click', dismiss, { once: true, capture: true });
    return () => document.removeEventListener('click', dismiss, { capture: true });
  }, [showTooltip]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (tooltipTimerRef.current) clearTimeout(tooltipTimerRef.current);
    };
  }, []);

  const statusText = STATUS_INDICATORS[job.status] ?? job.status;
  const ariaLabel = `${job.name}, ${statusText.replace(/^[^\w]*/, '')}`;

  return (
    <li
      role="listitem"
      className={clsx('radar-job-item', job.status === 'error' && 'radar-job-item--error')}
      aria-label={ariaLabel}
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleClick();
        }
      }}
    >
      <div className="radar-job-item-content">
        <span className="radar-job-name">{job.name}</span>
        <span className="radar-job-status">{statusText}</span>
        <span className="radar-job-time">{formatRelativeTime(job.lastRunAt)}</span>
        {job.category === 'user_defined' && job.schedule && (
          <span className="radar-job-schedule">{job.schedule}</span>
        )}
      </div>
      {showTooltip && (
        <div className="radar-job-tooltip" role="status" aria-live="polite">
          Coming soon
        </div>
      )}
    </li>
  );
}
