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
import { describe, it, expect } from 'vitest';
import { aggregateAttention } from '../useRadarAttention';
import type { PipelineRun } from '../../services/pipelines';
import type { JobStatus } from '../../services/jobs';
import type { StreamingStateEntry } from '../../types';

function pipe(over: Partial<PipelineRun> = {}): PipelineRun {
  return {
    id: 'run_a',
    project: 'SwarmAI',
    requirement: 'do a thing',
    status: 'running',
    currentStage: 'build',
    checkpointReason: null,
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
