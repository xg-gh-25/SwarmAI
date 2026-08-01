/**
 * Tests for the Radar attention-queue aggregation (Run 1 redesign).
 *
 * The pure `aggregateAttention` function is the testable core: it merges the
 * three attention sources (paused pipelines, failed jobs, waiting tabs) into a
 * single ordered queue + a running-pipeline FYI list, EXCLUDING the currently
 * active session so the user isn't nagged about the tab they're already in.
 *
 * These tests drive the real function (no mock of the function under test) —
 * only the inputs are constructed inline.
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { aggregateAttention, useRadarAttention } from '../useRadarAttention';
import type { PipelineRun } from '../../services/pipelines';
import type { JobStatus } from '../../services/jobs';
import type { StreamingStateEntry } from '../../types';
import { pipelinesService } from '../../services/pipelines';
import { jobsService } from '../../services/jobs';
import { chatService } from '../../services/chat';

function pipe(over: Partial<PipelineRun> = {}): PipelineRun {
  return {
    id: 'run_a',
    project: 'SwarmAI',
    requirement: 'do a thing',
    status: 'running',
    currentStage: 'build',
    checkpointReason: null,
    pauseKind: null,
    progress: '',
    updatedAt: '',
    ...over,
  };
}

function job(over: Partial<JobStatus> = {}): JobStatus {
  return {
    id: 'some-job',
    name: 'Some Job',
    consecutiveFailures: 0,
    enabled: true,
    lastRun: null,
    ...over,
  };
}

function streaming(over: Partial<StreamingStateEntry> = {}): StreamingStateEntry {
  return {
    streaming: false,
    state: 'idle',
    waitingInput: false,
    pendingCount: 0,
    pendingQuestion: null,
    lastDrainedSeqs: [],
    postDisconnectFlushing: false,
    ...over,
  };
}

describe('aggregateAttention', () => {
  it('AC3: merges paused pipeline + failed job + waiting tab (non-current); running goes to FYI list, not attention', () => {
    const pipelines: PipelineRun[] = [
      pipe({ id: 'run_paused', status: 'paused', currentStage: 'build', checkpointReason: 'Gate-1 BLOCK: decide X?' }),
      pipe({ id: 'run_running', status: 'running', currentStage: 'test' }),
    ];
    const jobs: JobStatus[] = [
      job({ id: 'morning-inbox', name: 'Morning Inbox', consecutiveFailures: 2 }),
      job({ id: 'healthy-job', name: 'Healthy', consecutiveFailures: 0 }),
    ];
    const streamingState: Record<string, StreamingStateEntry> = {
      'sess-other': streaming({ waitingInput: true, pendingQuestion: { toolUseId: 't1', questions: [{ header: 'Pick A/B', question: '', options: [] } as never] } }),
      'sess-current': streaming({ waitingInput: true }),
    };
    const openTabs = [
      { id: 'tab-other', sessionId: 'sess-other' },
      { id: 'tab-current', sessionId: 'sess-current' },
    ];

    const { attentionItems, runningPipelines } = aggregateAttention({
      pipelines,
      jobs,
      streamingState,
      openTabs,
      currentSessionId: 'sess-current',
    });

    // paused pipeline + failed job + waiting non-current tab = 3 attention items
    expect(attentionItems).toHaveLength(3);
    const kinds = attentionItems.map((i) => i.kind).sort();
    expect(kinds).toEqual(['job', 'paused', 'waiting']);

    // the running pipeline is NOT in attention — it's a FYI-only item
    expect(attentionItems.find((i) => i.kind === 'paused' && i.id === 'run_paused')).toBeTruthy();
    expect(runningPipelines).toHaveLength(1);
    expect(runningPipelines[0].id).toBe('run_running');

    // paused item carries the decision reason (not just the stage)
    const paused = attentionItems.find((i) => i.kind === 'paused')!;
    expect(paused.kind === 'paused' && paused.reason).toBe('Gate-1 BLOCK: decide X?');

    // waiting item maps to the TAB id (for onSelectTab), not the session id
    const waiting = attentionItems.find((i) => i.kind === 'waiting')!;
    expect(waiting.id).toBe('tab-other');
  });

  it('AC3: excludes the current session even if it is waiting_input', () => {
    const streamingState: Record<string, StreamingStateEntry> = {
      'sess-current': streaming({ waitingInput: true }),
    };
    const { attentionItems } = aggregateAttention({
      pipelines: [],
      jobs: [],
      streamingState,
      openTabs: [{ id: 'tab-current', sessionId: 'sess-current' }],
      currentSessionId: 'sess-current',
    });
    expect(attentionItems).toHaveLength(0);
  });

  it('AC3-empty: all sources empty → empty attention + empty running', () => {
    const { attentionItems, runningPipelines } = aggregateAttention({
      pipelines: [],
      jobs: [],
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    expect(attentionItems).toEqual([]);
    expect(runningPipelines).toEqual([]);
  });

  it('AC-disabled: a DISABLED job with failures is EXCLUDED from the attention queue (brain-push repro)', () => {
    const jobs: JobStatus[] = [
      job({ id: 'brain-push', name: 'Brain Backup Push', consecutiveFailures: 1, enabled: false }),
      job({ id: 'os-eval', name: 'OS Eval', consecutiveFailures: 2, enabled: true }),
    ];
    const { attentionItems } = aggregateAttention({
      pipelines: [],
      jobs,
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    // Only the ENABLED failing job surfaces; the disabled one is filtered out.
    expect(attentionItems).toHaveLength(1);
    expect(attentionItems[0].kind === 'job' && attentionItems[0].id).toBe('os-eval');
  });

  it('AC-failopen: a failing job with enabled omitted (shape surprise) still shows — fail OPEN', () => {
    // jobToCamelCase defaults enabled=true when absent; assert the filter honors it.
    const jobs: JobStatus[] = [job({ id: 'mystery', consecutiveFailures: 3 })];
    const { attentionItems } = aggregateAttention({
      pipelines: [],
      jobs,
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    expect(attentionItems).toHaveLength(1);
  });

  it('AC1: a crash-residue paused run (pauseKind=crash_residue) is DROPPED from NEEDS YOU', () => {
    // The common case per Gate-1 finding C: a NON-terminal crash-residue run
    // (partial stages, session died) — must NOT nag the user as a decision.
    const pipelines: PipelineRun[] = [
      pipe({ id: 'run_crash', status: 'paused', currentStage: 'think',
             checkpointReason: 'session_crash_auto_detected', pauseKind: 'crash_residue' }),
    ];
    const { attentionItems } = aggregateAttention({
      pipelines,
      jobs: [],
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    expect(attentionItems).toHaveLength(0);
  });

  it('AC2: a real decision pause (pauseKind=decision) STILL appears in NEEDS YOU with its reason', () => {
    const pipelines: PipelineRun[] = [
      pipe({ id: 'run_decision', status: 'paused', currentStage: 'plan',
             checkpointReason: 'Gate-1 BLOCK: decide X?', pauseKind: 'decision' }),
    ];
    const { attentionItems } = aggregateAttention({
      pipelines,
      jobs: [],
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    expect(attentionItems).toHaveLength(1);
    const it0 = attentionItems[0];
    expect(it0.kind === 'paused' && it0.id).toBe('run_decision');
    expect(it0.kind === 'paused' && it0.reason).toBe('Gate-1 BLOCK: decide X?');
  });

  it('AC1-mixed: crash-residue dropped while decision + running are kept in the same batch', () => {
    const pipelines: PipelineRun[] = [
      pipe({ id: 'run_crash', status: 'paused', pauseKind: 'crash_residue',
             checkpointReason: 'session_crash_auto_detected' }),
      pipe({ id: 'run_decision', status: 'paused', pauseKind: 'decision',
             checkpointReason: 'budget checkpoint' }),
      pipe({ id: 'run_running', status: 'running' }),
    ];
    const { attentionItems, runningPipelines } = aggregateAttention({
      pipelines,
      jobs: [],
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    // only the decision pause reaches NEEDS YOU; running is FYI-only
    expect(attentionItems.map((i) => i.id)).toEqual(['run_decision']);
    expect(runningPipelines.map((p) => p.id)).toEqual(['run_running']);
  });

  it('AC1-failopen: a paused run with pauseKind=null (old backend / pre-field) STILL shows — fail SAFE', () => {
    // During a deploy window the field may be absent; an attention queue must
    // never SILENTLY hide a possible real decision. null !== crash_residue → show.
    const pipelines: PipelineRun[] = [
      pipe({ id: 'run_legacy', status: 'paused', pauseKind: null,
             checkpointReason: 'Gate-1 BLOCK: legacy' }),
    ];
    const { attentionItems } = aggregateAttention({
      pipelines,
      jobs: [],
      streamingState: {},
      openTabs: [],
      currentSessionId: undefined,
    });
    expect(attentionItems).toHaveLength(1);
  });

  it('perf-regression (run_843962a5 adversarial HIGH): a SECOND identical 30s poll yields a STABLE result reference — no re-render churn on the host', async () => {
    // Root-cause guard for the hook-lift blast-radius finding. Before the fix,
    // poll() unconditionally setState-d fresh fetch arrays every tick, so an
    // idle 30s poll churned references → ChatPage (the lifted host) re-rendered
    // every 30s. Fix: bail setState when the payload is unchanged + memoize the
    // aggregate. This test drives the REAL hook, advances the REAL 30s interval
    // with fake timers, and returns byte-identical-but-new-reference data on the
    // 2nd poll. Reverting EITHER the setState bail-out OR the useMemo → RED
    // (identity would change on the 2nd poll).
    vi.useFakeTimers();
    try {
      // Each call returns a FRESH object with equal content (mimics a real fetch).
      vi.spyOn(pipelinesService, 'fetchActivePipelines').mockImplementation(async () => [
        { id: 'run_x', project: 'SwarmAI', requirement: 'r', status: 'paused',
          currentStage: 'build', checkpointReason: 'Gate-1 BLOCK', pauseKind: 'decision',
          progress: '', updatedAt: '' } as PipelineRun,
      ]);
      vi.spyOn(jobsService, 'fetchJobs').mockImplementation(async () => []);
      vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}));

      const { result, unmount } = renderHook(() => useRadarAttention('sess-current', []));

      // Flush the initial poll (mount effect).
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.attentionItems).toHaveLength(1);
      const firstRef = result.current;
      const firstItems = result.current.attentionItems;

      // Advance one full 30s interval → the REAL poll fires again with identical data.
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });

      // Unchanged data must NOT produce a new result object (setState bail-out +
      // memo hold the reference stable → host does not re-render).
      expect(result.current).toBe(firstRef);
      expect(result.current.attentionItems).toBe(firstItems);

      unmount();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });

  it('perf-regression INVERSE (meta-review MED): a CHANGED poll (same id, different status) DOES produce a new reference — the bail-out never drops a real change', async () => {
    // Guards the inverse of the churn fix: sameJson must NOT be over-eager. If a
    // genuine change (run_x running→paused) serialized equal, the UI would go
    // stale (silent dropped update). This proves a real status change flows
    // through: poll 1 = running (→ FYI, 0 attention), poll 2 = paused (→ 1
    // attention). Reference MUST change and attentionItems MUST update.
    vi.useFakeTimers();
    try {
      let status: 'running' | 'paused' = 'running';
      vi.spyOn(pipelinesService, 'fetchActivePipelines').mockImplementation(async () => [
        { id: 'run_x', project: 'SwarmAI', requirement: 'r', status,
          currentStage: 'build', checkpointReason: 'Gate-1 BLOCK', pauseKind: 'decision',
          progress: '', updatedAt: '' } as PipelineRun,
      ]);
      vi.spyOn(jobsService, 'fetchJobs').mockImplementation(async () => []);
      vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}));

      const { result, unmount } = renderHook(() => useRadarAttention('sess-current', []));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      // poll 1: running → FYI only, zero attention items
      expect(result.current.attentionItems).toHaveLength(0);
      const firstRef = result.current;

      status = 'paused'; // real backend change
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });

      // The change MUST propagate: new reference + the paused item now in attention.
      expect(result.current).not.toBe(firstRef);
      expect(result.current.attentionItems).toHaveLength(1);
      expect(result.current.attentionItems[0].id).toBe('run_x');

      unmount();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });

  it('AC3: a waiting session with no open tab is ignored (can not switch to a tab that is not open)', () => {
    const streamingState: Record<string, StreamingStateEntry> = {
      'sess-orphan': streaming({ waitingInput: true }),
    };
    const { attentionItems } = aggregateAttention({
      pipelines: [],
      jobs: [],
      streamingState,
      openTabs: [], // no tab maps to sess-orphan
      currentSessionId: undefined,
    });
    expect(attentionItems).toHaveLength(0);
  });
});
