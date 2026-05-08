/**
 * Pipeline section for the Radar sidebar.
 *
 * Displays active and recently completed pipeline runs fetched from
 * ``GET /api/pipelines?active=true``. Each run shows: project, requirement
 * snippet, status badge, and a visual progress indicator (stages completed
 * out of total).
 *
 * Polls every 30s to keep the display current during active pipeline runs.
 *
 * @exports PipelineSection
 */

import { useState, useEffect, useCallback, useRef } from 'react';

// ---------------------------------------------------------------------------
// Types (mirrors backend PipelineRunResponse schema)
// ---------------------------------------------------------------------------

interface PipelineRun {
  id: string;
  project: string;
  requirement: string;
  status: 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  profile: string;
  progress: string;
  stagesCompleted: number;
  stagesTotal: number;
  tokensConsumed: number;
  tasteDecisions: number;
  checkpoint?: {
    reason: string;
    stage: string;
    checkpointedAt: string;
  } | null;
  createdAt: string;
  updatedAt: string;
}

interface PipelineDashboard {
  pipelines: PipelineRun[];
  count: number;
  summary: {
    running: number;
    paused: number;
    completed: number;
    totalTokens: number;
  };
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 30_000;

const STATUS_STYLES: Record<string, { icon: string; color: string }> = {
  running: { icon: 'play_circle', color: 'text-green-400' },
  paused: { icon: 'pause_circle', color: 'text-yellow-400' },
  completed: { icon: 'check_circle', color: 'text-[var(--color-text-muted)]' },
  failed: { icon: 'error', color: 'text-red-400' },
  cancelled: { icon: 'cancel', color: 'text-[var(--color-text-muted)]' },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function camelizeKeys(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    const camelKey = key.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    result[camelKey] = value && typeof value === 'object' && !Array.isArray(value)
      ? camelizeKeys(value as Record<string, unknown>)
      : value;
  }
  return result;
}

async function fetchPipelines(): Promise<PipelineDashboard> {
  const resp = await fetch('/api/pipelines?active=true');
  if (!resp.ok) throw new Error(`Pipeline fetch failed: ${resp.status}`);
  const raw = await resp.json();
  // Backend uses snake_case — convert to camelCase
  const pipelines = (raw.pipelines || []).map((p: Record<string, unknown>) => camelizeKeys(p));
  const summary = raw.summary ? camelizeKeys(raw.summary) : { running: 0, paused: 0, completed: 0, totalTokens: 0 };
  return { pipelines: pipelines as unknown as PipelineRun[], count: raw.count || 0, summary } as PipelineDashboard;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface PipelineSectionProps {
  onCountChange?: (count: number) => void;
}

export function PipelineSection({ onCountChange }: PipelineSectionProps) {
  const [data, setData] = useState<PipelineDashboard | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const dashboard = await fetchPipelines();
      setData(dashboard);
      onCountChange?.(dashboard.count);
    } catch {
      // Silent failure — diagnostic panel, not critical
    }
  }, [onCountChange]);

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, POLL_INTERVAL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [load]);

  if (!data || data.pipelines.length === 0) {
    return (
      <div className="px-3 py-2 text-xs text-[var(--color-text-muted)] italic">
        No active pipelines
      </div>
    );
  }

  return (
    <div className="space-y-1 px-1 py-1">
      {data.pipelines.map((run) => {
        const statusStyle = STATUS_STYLES[run.status] || STATUS_STYLES.running;
        const progressPct = run.stagesTotal > 0 ? (run.stagesCompleted / run.stagesTotal) * 100 : 0;

        return (
          <div
            key={run.id}
            className="group px-2 py-1.5 rounded-md hover:bg-[var(--color-hover)] transition-colors cursor-default"
          >
            {/* Top row: status icon + requirement */}
            <div className="flex items-start gap-1.5">
              <span className={`material-symbols-outlined text-sm mt-0.5 shrink-0 ${statusStyle.color}`}>
                {statusStyle.icon}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-[var(--color-text)] truncate leading-tight">
                  {run.requirement || run.id}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  {/* Progress bar */}
                  <div className="flex-1 h-1 rounded-full bg-[var(--color-border)] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-[var(--color-primary)] transition-all duration-300"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                  <span className="text-[10px] text-[var(--color-text-muted)] shrink-0">
                    {run.progress}
                  </span>
                </div>
              </div>
            </div>

            {/* Checkpoint reason (if paused) */}
            {run.status === 'paused' && run.checkpoint?.reason && (
              <p className="mt-1 ml-5 text-[10px] text-yellow-400/80 truncate">
                {run.checkpoint.reason}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
