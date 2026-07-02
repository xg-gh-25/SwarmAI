/**
 * Tests for JobsRunsSection (⚡ Jobs & Runs inventory).
 *
 * Focus: the presentational contract — mock useJobsRuns to drive rows, assert
 * jobs + runs render, empty hides, and the See-more fold works per group.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { JobsRunsSection } from '../JobsRunsSection';
import type { JobsRunsResult, JobRow, RunRow } from '../../../../../hooks/useJobsRuns';

// Mock the data hook — this component is presentational; aggregateJobsRuns is
// tested separately in useJobsRuns.test.ts.
const mockUseJobsRuns = vi.fn<() => JobsRunsResult>();
vi.mock('../../../../../hooks/useJobsRuns', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../../../hooks/useJobsRuns')>();
  return { ...actual, useJobsRuns: () => mockUseJobsRuns() };
});

function jobRow(over: Partial<JobRow> = {}): JobRow {
  return { id: 'j', name: 'Morning Inbox', schedule: '0 2 * * *', health: 'healthy', lastRun: null, failures: 0, ...over };
}
function runRow(over: Partial<RunRow> = {}): RunRow {
  return { id: 'run_a', title: 'do a thing', project: 'SwarmAI', status: 'running', progress: '5/8', updatedAt: '2026-07-02T10:00:00Z', ...over };
}

describe('JobsRunsSection', () => {
  beforeEach(() => mockUseJobsRuns.mockReset());

  it('empty roster → renders null (section hidden)', () => {
    mockUseJobsRuns.mockReturnValue({ jobs: [], runs: [] });
    const { container } = render(<JobsRunsSection />);
    expect(container.firstChild).toBeNull();
  });

  it('renders scheduled jobs (name + schedule) and pipeline runs (title + progress)', () => {
    mockUseJobsRuns.mockReturnValue({
      jobs: [jobRow({ id: 'j1', name: 'Morning Inbox', schedule: '0 2 * * *' })],
      runs: [runRow({ id: 'r1', title: 'restore radar', status: 'running', progress: '5/8' })],
    });
    render(<JobsRunsSection />);
    expect(screen.getByText('Morning Inbox')).toBeInTheDocument();
    expect(screen.getByText('0 2 * * *')).toBeInTheDocument();
    expect(screen.getByText('restore radar')).toBeInTheDocument();
    expect(screen.getByText('5/8')).toBeInTheDocument();
    // group headers show counts
    expect(screen.getByText('Scheduled jobs (1)')).toBeInTheDocument();
    expect(screen.getByText('Pipeline runs (1)')).toBeInTheDocument();
  });

  it('shows a completed run (the status the attention queue drops)', () => {
    mockUseJobsRuns.mockReturnValue({
      jobs: [],
      runs: [runRow({ id: 'done', title: 'finished feature', status: 'completed', progress: '8/8' })],
    });
    render(<JobsRunsSection />);
    expect(screen.getByText('finished feature')).toBeInTheDocument();
    expect(screen.getByText('DONE')).toBeInTheDocument();
  });

  it('See-more fold: >5 jobs shows top 5 + toggle; expand reveals the rest', () => {
    const jobs = Array.from({ length: 7 }, (_, i) => jobRow({ id: `j${i}`, name: `Job ${i}` }));
    mockUseJobsRuns.mockReturnValue({ jobs, runs: [] });
    render(<JobsRunsSection />);
    expect(screen.getByText('Job 0')).toBeInTheDocument();
    expect(screen.getByText('Job 4')).toBeInTheDocument();
    expect(screen.queryByText('Job 5')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('See 2 more'));
    expect(screen.getByText('Job 5')).toBeInTheDocument();
    expect(screen.getByText('Job 6')).toBeInTheDocument();
    fireEvent.click(screen.getByText('See less'));
    expect(screen.queryByText('Job 5')).not.toBeInTheDocument();
  });

  it('header count is the TOTAL of jobs + runs', () => {
    mockUseJobsRuns.mockReturnValue({
      jobs: [jobRow({ id: 'j1' }), jobRow({ id: 'j2' })],
      runs: [runRow({ id: 'r1' })],
    });
    render(<JobsRunsSection />);
    expect(screen.getByText('3')).toBeInTheDocument(); // CollapsibleSection count badge
  });
});
