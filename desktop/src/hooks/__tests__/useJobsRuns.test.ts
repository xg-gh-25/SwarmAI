/**
 * Tests for the Jobs & Runs aggregation (Option B, run_06b89c00).
 *
 * The pure `aggregateJobsRuns` function is the testable core: it maps + sorts
 * the two sources (scheduled jobs, pipeline runs) into the roster the section
 * renders. Drives the real function (no mock of the function under test) —
 * only the inputs are constructed inline.
 */
import { describe, it, expect } from 'vitest';
import { aggregateJobsRuns, jobHealth } from '../useJobsRuns';
import type { JobStatus } from '../../services/jobs';
import type { PipelineRun } from '../../services/pipelines';

function job(over: Partial<JobStatus> = {}): JobStatus {
  return {
    id: 'j',
    name: 'Job',
    consecutiveFailures: 0,
    enabled: true,
    lastRun: null,
    lastError: null,
    schedule: '0 2 * * *',
    lastStatus: 'success',
    ...over,
  };
}

function pipe(over: Partial<PipelineRun> = {}): PipelineRun {
  return {
    id: 'run_a',
    project: 'SwarmAI',
    requirement: 'do a thing',
    status: 'completed',
    currentStage: 'reflect',
    checkpointReason: null,
    pauseKind: null,
    progress: '8/8',
    updatedAt: '2026-07-02T10:00:00Z',
    ...over,
  };
}

describe('jobHealth', () => {
  it('disabled job → disabled (regardless of failures)', () => {
    expect(jobHealth(job({ enabled: false, consecutiveFailures: 3 }))).toBe('disabled');
  });
  it('enabled job with failures → failed', () => {
    expect(jobHealth(job({ consecutiveFailures: 2 }))).toBe('failed');
  });
  it('enabled job whose last run failed (no streak yet) → failed', () => {
    expect(jobHealth(job({ consecutiveFailures: 0, lastStatus: 'failed' }))).toBe('failed');
  });
  it('enabled, no failures, last success → healthy', () => {
    expect(jobHealth(job({ lastStatus: 'success' }))).toBe('healthy');
  });
});

describe('aggregateJobsRuns', () => {
  it('shows ALL jobs (not just failing) — healthy + disabled + failing all present', () => {
    const jobs = [
      job({ id: 'healthy', consecutiveFailures: 0 }),
      job({ id: 'failing', consecutiveFailures: 2 }),
      job({ id: 'off', enabled: false }),
    ];
    const { jobs: rows } = aggregateJobsRuns(jobs, []);
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.id).sort()).toEqual(['failing', 'healthy', 'off']);
  });

  it('sorts jobs failed → healthy → disabled', () => {
    const jobs = [
      job({ id: 'off', enabled: false }),
      job({ id: 'healthy' }),
      job({ id: 'failing', consecutiveFailures: 1 }),
    ];
    const { jobs: rows } = aggregateJobsRuns(jobs, []);
    expect(rows.map((r) => r.id)).toEqual(['failing', 'healthy', 'off']);
  });

  it('carries schedule + lastRun + failures onto the row', () => {
    const { jobs: rows } = aggregateJobsRuns(
      [job({ id: 'x', schedule: '0 9 * * 1-5', lastRun: '2026-07-01T09:00:00Z', consecutiveFailures: 3 })],
      [],
    );
    expect(rows[0].schedule).toBe('0 9 * * 1-5');
    expect(rows[0].lastRun).toBe('2026-07-01T09:00:00Z');
    expect(rows[0].failures).toBe(3);
  });

  it('shows ONLY active runs (running + paused); drops completed/failed/cancelled/abandoned', () => {
    const runs = [
      pipe({ id: 'run_done', status: 'completed' }),
      pipe({ id: 'run_fail', status: 'failed' }),
      pipe({ id: 'run_cancel', status: 'cancelled' }),
      pipe({ id: 'run_abandon', status: 'abandoned' }),
      pipe({ id: 'run_run', status: 'running' }),
      pipe({ id: 'run_pause', status: 'paused' }),
    ];
    const { runs: rows } = aggregateJobsRuns([], runs);
    // only the 2 active survive
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.id).sort()).toEqual(['run_pause', 'run_run']);
  });

  it('all-inactive runs → empty runs list (whole group hides)', () => {
    const runs = [
      pipe({ id: 'd', status: 'completed' }),
      pipe({ id: 'f', status: 'failed' }),
      pipe({ id: 'a', status: 'abandoned' }),
    ];
    const { runs: rows } = aggregateJobsRuns([], runs);
    expect(rows).toEqual([]);
  });

  it('sorts active runs running → paused; newest-first within a bucket', () => {
    const runs = [
      pipe({ id: 'old_pause', status: 'paused', updatedAt: '2026-06-01T00:00:00Z' }),
      pipe({ id: 'new_pause', status: 'paused', updatedAt: '2026-07-02T00:00:00Z' }),
      pipe({ id: 'running', status: 'running', updatedAt: '2026-05-01T00:00:00Z' }),
      pipe({ id: 'done', status: 'completed', updatedAt: '2026-07-03T00:00:00Z' }), // dropped despite newest
    ];
    const { runs: rows } = aggregateJobsRuns([], runs);
    // completed dropped; running first, then paused newest-first
    expect(rows.map((r) => r.id)).toEqual(['running', 'new_pause', 'old_pause']);
  });

  it('AC1: a crash-residue paused run is DROPPED from the run roster (mirrors NEEDS YOU)', () => {
    const runs = [
      pipe({ id: 'run_crash', status: 'paused', pauseKind: 'crash_residue',
             checkpointReason: 'session_crash_auto_detected' }),
    ];
    const { runs: rows } = aggregateJobsRuns([], runs);
    expect(rows).toEqual([]);
  });

  it('AC2: decision pause + running are KEPT; only crash-residue is dropped', () => {
    const runs = [
      pipe({ id: 'run_crash', status: 'paused', pauseKind: 'crash_residue' }),
      pipe({ id: 'run_decision', status: 'paused', pauseKind: 'decision',
             updatedAt: '2026-07-02T00:00:00Z' }),
      pipe({ id: 'run_running', status: 'running', pauseKind: null,
             updatedAt: '2026-07-01T00:00:00Z' }),
    ];
    const { runs: rows } = aggregateJobsRuns([], runs);
    expect(rows.map((r) => r.id)).toEqual(['run_running', 'run_decision']);
  });

  it('AC-failopen: a paused run with pauseKind=null (old backend) STILL shows — fail SAFE', () => {
    const runs = [pipe({ id: 'run_legacy', status: 'paused', pauseKind: null })];
    const { runs: rows } = aggregateJobsRuns([], runs);
    expect(rows.map((r) => r.id)).toEqual(['run_legacy']);
  });

  it('empty sources → empty rows (section will hide)', () => {
    const { jobs, runs } = aggregateJobsRuns([], []);
    expect(jobs).toEqual([]);
    expect(runs).toEqual([]);
  });

  it('carries run progress + status onto the row', () => {
    const { runs } = aggregateJobsRuns([], [pipe({ id: 'r', status: 'running', progress: '5/8' })]);
    expect(runs[0].progress).toBe('5/8');
    expect(runs[0].status).toBe('running');
  });
});
