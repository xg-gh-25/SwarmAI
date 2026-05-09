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
import api from '../../../../services/api';

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
    if (Array.isArray(value)) {
      result[camelKey] = value.map((item) =>
        item && typeof item === 'object' ? camelizeKeys(item as Record<string, unknown>) : item
      );
    } else if (value && typeof value === 'object') {
      result[camelKey] = camelizeKeys(value as Record<string, unknown>);
    } else {
      result[camelKey] = value;
    }
  }
  return result;
}

async function fetchPipelines(): Promise<PipelineDashboard> {
  const { data: raw } = await api.get('/pipelines', { params: { active: true } });
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
  /** Injects text into the ChatInput and sends it as a message. */
  onSendMessage?: (text: string) => void;
}

async function cancelPipeline(runId: string): Promise<void> {
  await api.patch(`/pipelines/${runId}/cancel`);
}

export function PipelineSection({ onCountChange, onSendMessage }: PipelineSectionProps) {
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

  // Only poll when there are active pipelines; re-fetch on tab visibility change
  const hasActive = data && data.pipelines.some((p) => p.status === 'running' || p.status === 'paused');

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!hasActive) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    pollRef.current = setInterval(load, POLL_INTERVAL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [hasActive, load]);

  useEffect(() => {
    const onVisibility = () => { if (document.visibilityState === 'visible') load(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [load]);

  const handleResume = useCallback((run: PipelineRun) => {
    onSendMessage?.(`resume pipeline ${run.id} for ${run.project}`);
  }, [onSendMessage]);

  const handleCancel = useCallback(async (run: PipelineRun) => {
    try {
      await cancelPipeline(run.id);
      // Optimistic: update local state immediately
      setData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          pipelines: prev.pipelines.map((p) =>
            p.id === run.id ? { ...p, status: 'cancelled' as const } : p
          ),
        };
      });
    } catch {
      // Cancel failed — re-fetch true state from backend
      load();
    }
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
        const isPaused = run.status === 'paused';
        const isActive = run.status === 'running' || isPaused;

        return (
          <div
            key={run.id}
            className="group px-2 py-1.5 rounded-md hover:bg-[var(--color-hover)] transition-colors"
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
            {isPaused && run.checkpoint?.reason && (
              <p className="mt-1 ml-5 text-[10px] text-yellow-400/80 truncate">
                {run.checkpoint.reason}
              </p>
            )}

            {/* Action buttons — visible on hover for active runs */}
            {isActive && (
              <div className="flex items-center gap-1 mt-1.5 ml-5 opacity-0 group-hover:opacity-100 transition-opacity">
                {isPaused && onSendMessage && (
                  <button
                    type="button"
                    onClick={() => handleResume(run)}
                    className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-green-400 hover:bg-green-500/10 transition-colors"
                    title="Resume this pipeline"
                  >
                    <span className="material-symbols-outlined text-xs">play_arrow</span>
                    Resume
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleCancel(run)}
                  className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-red-400 hover:bg-red-500/10 transition-colors"
                  title="Cancel this pipeline"
                >
                  <span className="material-symbols-outlined text-xs">close</span>
                  Cancel
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
