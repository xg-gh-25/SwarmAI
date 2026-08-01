/**
 * useRadarAttention — data hook for the Radar sidebar's 🔔 attention queue and
 * the bottom running-pipeline FYI bar (Run 1 redesign).
 *
 * It aggregates THREE independent, pure-read backend sources — with ZERO
 * backend changes — into one attention queue:
 *   1. paused pipelines   (GET /api/pipelines?active=true → checkpoint.reason)
 *   2. failing jobs        (GET /api/jobs/ → consecutive_failures > 0)
 *   3. waiting tabs        (GET /chat/sessions/streaming-state → waiting_input),
 *                          EXCLUDING the currently-active session.
 * Running pipelines from source (1) are split out to a separate FYI list.
 *
 * The merge is a pure function (`aggregateAttention`) so it is unit-testable
 * without React. The hook wraps it with polling (pipelines + jobs on a 30s
 * interval; streaming-state reuses the same tick — it is cheap and already
 * polled elsewhere at 15s).
 *
 * Exports:
 * - aggregateAttention  — pure merge (tested directly)
 * - useRadarAttention   — React hook returning { attentionItems, runningPipelines }
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { pipelinesService, type PipelineRun } from '../services/pipelines';
import { jobsService, type JobStatus } from '../services/jobs';
import { chatService } from '../services/chat';
import type { StreamingStateEntry } from '../types';
import type { AttentionItem, RunningPipeline } from '../pages/chat/components/RightSidebar/types';

const POLL_MS = 30_000;

/**
 * Cheap structural equality for poll payloads. The three sources are small,
 * JSON-serializable, and fetched fresh each tick — stable-key JSON compare is
 * both correct and far cheaper than the re-renders it prevents. Used to skip
 * setState when a poll returns data identical to the previous tick.
 */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

export interface AggregateInput {
  pipelines: PipelineRun[];
  jobs: JobStatus[];
  streamingState: Record<string, StreamingStateEntry>;
  openTabs: { id: string; sessionId?: string }[];
  currentSessionId: string | undefined;
}

export interface AggregateResult {
  attentionItems: AttentionItem[];
  runningPipelines: RunningPipeline[];
}

/**
 * Pure merge of the three attention sources. No I/O, no React — unit-tested.
 * Order: paused pipelines → failed jobs → waiting tabs (most-decision-heavy first).
 */
export function aggregateAttention(input: AggregateInput): AggregateResult {
  const { pipelines, jobs, streamingState, openTabs, currentSessionId } = input;

  const attentionItems: AttentionItem[] = [];
  const runningPipelines: RunningPipeline[] = [];

  // 1. Pipelines: paused → attention (with decision reason); running → FYI list.
  for (const p of pipelines) {
    if (p.status === 'paused') {
      // Drop crash-residue pauses: a run the orphan-transition paused when a
      // session died is NOT a decision the user must make — surfacing it buries
      // the real decision-pauses. The backend classifies this via pause_kind
      // (canonical _CRASH_ZOMBIE_REASON) — we consume the verdict, never re-derive
      // it here (the frontend only gets an "N/M" progress string, so it cannot
      // replicate the backend's stage-based is_terminal_run check). Fail SAFE:
      // an absent/null pauseKind (old backend, pre-field run) is NOT dropped —
      // an attention queue must never silently hide a possible real decision.
      if (p.pauseKind === 'crash_residue') continue;
      attentionItems.push({
        kind: 'paused',
        id: p.id,
        title: p.requirement,
        project: p.project,
        stage: p.currentStage,
        reason: p.checkpointReason ?? '',
      });
    } else if (p.status === 'running') {
      runningPipelines.push({
        id: p.id,
        title: p.requirement,
        project: p.project,
        stage: p.currentStage,
      });
    }
    // completed/failed/cancelled/abandoned → neither (no "watch me" value)
  }

  // 2. Jobs: enabled AND consecutive_failures > 0 → attention.
  //    A DISABLED job's failure count is stale/non-actionable (e.g. brain-push,
  //    halted 2026-06-27) — surfacing it drowns real signals in "Needs You".
  //    `enabled` fails open (only explicit false hides), so a shape surprise
  //    still shows the job rather than silently swallowing a real failure.
  for (const j of jobs) {
    if (j.enabled && j.consecutiveFailures > 0) {
      attentionItems.push({
        kind: 'job',
        id: j.id,
        title: j.name,
        failures: j.consecutiveFailures,
        lastError: j.lastError,
      });
    }
  }

  // 3. Waiting tabs: a session in waiting_input that (a) is NOT the current
  //    session and (b) maps to an OPEN tab (else onSelectTab has no target).
  //    Build session_id → tabId from openTabs (streaming-state is session-keyed).
  const sessionToTab = new Map<string, string>();
  for (const t of openTabs) {
    if (t.sessionId) sessionToTab.set(t.sessionId, t.id);
  }
  for (const [sessionId, entry] of Object.entries(streamingState)) {
    if (!entry.waitingInput) continue;
    if (sessionId === currentSessionId) continue; // already looking at it
    const tabId = sessionToTab.get(sessionId);
    if (!tabId) continue; // no open tab → can't switch to it
    const q = entry.pendingQuestion?.questions?.[0];
    const questionLabel =
      (q && (q as { header?: string }).header) || 'waiting for your answer';
    attentionItems.push({
      kind: 'waiting',
      id: tabId,
      title: `Tab · ${sessionId.slice(0, 8)}`,
      question: questionLabel,
    });
  }

  return { attentionItems, runningPipelines };
}

/**
 * React hook: polls the three sources and returns the merged attention queue +
 * running-pipeline FYI list. Fails soft — any source that errors contributes
 * nothing (the sidebar must never crash on a transient fetch failure).
 */
export function useRadarAttention(
  currentSessionId: string | undefined,
  openTabs: { id: string; sessionId?: string }[],
): AggregateResult {
  const [pipelines, setPipelines] = useState<PipelineRun[]>([]);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [streamingState, setStreamingState] = useState<Record<string, StreamingStateEntry>>({});

  // Keep openTabs/currentSessionId in refs so the poll closure always reads the
  // latest without re-subscribing the interval on every tab change.
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;

  const poll = useCallback(async () => {
    const [p, j, s] = await Promise.all([
      pipelinesService.fetchActivePipelines().catch(() => [] as PipelineRun[]),
      jobsService.fetchJobs().catch(() => [] as JobStatus[]),
      chatService.getStreamingState().catch(() => ({} as Record<string, StreamingStateEntry>)),
    ]);
    // Bail out of state churn when a poll returns data identical to the last —
    // the common IDLE case. Every 30s the fetch yields a fresh array/object
    // (new reference) even when the payload is byte-identical; blindly
    // setState-ing it re-renders this hook's host (ChatPage) every 30s for no
    // reason. Deep-equal-guard each setter so an unchanged poll produces ZERO
    // state updates → React bails the re-render at the source. (run_843962a5
    // adversarial HIGH: hook lift widened the re-render blast radius to the
    // whole ChatPage subtree.)
    setPipelines((prev) => (sameJson(prev, p) ? prev : p));
    setJobs((prev) => (sameJson(prev, j) ? prev : j));
    setStreamingState((prev) => (sameJson(prev, s) ? prev : s));
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  // Memoize the aggregate so the returned object identity is stable across
  // renders when the source data is unchanged. Combined with the setState
  // bail-out above, an idle 30s tick now causes no downstream re-render.
  return useMemo(
    () =>
      aggregateAttention({
        pipelines,
        jobs,
        streamingState,
        openTabs: openTabsRef.current,
        currentSessionId,
      }),
    [pipelines, jobs, streamingState, openTabs, currentSessionId],
  );
}
