/**
 * JobsRunsOverlay interaction tests — lock the data-rendering + write-routing
 * behaviors that the service unit layer can't reach:
 *   AC2  roster renders with real health dots + overview stats (no fabrication)
 *   AC3  clicking a job card opens the drawer showing the REAL last-output body
 *        + a recent-runs list with real per-run status/tokens/duration
 *   AC6  Run now calls jobsService.runJob; create/pause/edit/delete route through
 *        onDispatch (chat) — the overlay never writes yaml directly (Gate-1 #7)
 *
 * The overlay renders inside a Modal gated on `swarm:show-jobs`; we fire that
 * event to open it. jobsService + pipelinesService are mocked so we assert on
 * rendered data and the dispatched prompt.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { JobsRunsOverlay } from '../JobsRunsOverlay';
import { jobsService, type JobRosterRow, type JobsOverview, type JobRunsResult } from '../../../services/jobs';
import { pipelinesService } from '../../../services/pipelines';

vi.mock('../../../services/jobs', () => ({
  jobsService: {
    fetchRoster: vi.fn(),
    fetchOverview: vi.fn(),
    runJob: vi.fn(),
    fetchJobRuns: vi.fn(),
  },
}));
vi.mock('../../../services/pipelines', () => ({
  pipelinesService: { fetchAllPipelines: vi.fn() },
}));

const fn = (f: unknown) => f as ReturnType<typeof vi.fn>;

function mkRoster(over: Partial<JobRosterRow> = {}): JobRosterRow {
  return {
    id: 'stock-analysis', name: 'Stock Analysis', consecutiveFailures: 0, enabled: true,
    lastRun: '2026-07-31T06:46:00+00:00', lastError: null, schedule: '0 6 * * 1-5',
    lastStatus: 'success', type: 'agent_task', category: 'user', source: 'user', totalRuns: 42,
    ...over,
  };
}

const OVERVIEW: JobsOverview = { total: 10, enabled: 8, healthy: 6, failing: 1, neverRun: 1, monthlySpendUsd: 3.5 };

const RUNS: JobRunsResult = {
  jobId: 'stock-analysis', lastOutput: 'FULL STOCK REPORT BODY\nline2',
  lastOutputDate: '2026-07-31',
  recent: [
    { date: '2026-07-31', status: 'success', tokens: 200, duration: 6.0, hasOutput: true },
    { date: '2026-07-30', status: 'failed', tokens: 10, duration: 2.0, hasOutput: false },
  ],
};

function openOverlay() {
  window.dispatchEvent(new CustomEvent('swarm:show-jobs'));
}

describe('JobsRunsOverlay interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fn(jobsService.fetchRoster).mockResolvedValue([mkRoster(), mkRoster({ id: 'brain-push', name: 'Brain Push', enabled: false, lastStatus: 'never', source: 'user' })]);
    fn(jobsService.fetchOverview).mockResolvedValue(OVERVIEW);
    fn(jobsService.fetchJobRuns).mockResolvedValue(RUNS);
    fn(jobsService.runJob).mockResolvedValue({ status: 'success', summary: 'ran' });
    fn(pipelinesService.fetchAllPipelines).mockResolvedValue([]);
  });

  it('AC2: renders roster cards with health dots + real overview stats', async () => {
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    // Overview strip shows the real failing count (not fabricated)
    const overview = await screen.findByTestId('jobs-overview');
    expect(within(overview).getByText('$3.50')).toBeTruthy();

    // Two roster cards, each with a health dot
    const cards = await screen.findAllByTestId('job-card');
    expect(cards.length).toBe(2);
    expect(screen.getAllByTestId('job-health-dot').length).toBe(2);
    expect(screen.getByText('Stock Analysis')).toBeTruthy();
  });

  it('AC3: clicking a job card opens the drawer with real last-output + recent runs', async () => {
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);

    const drawer = await screen.findByTestId('job-detail-drawer');
    // Real last-output body (not a truncated summary)
    await waitFor(() => expect(within(drawer).getByText(/FULL STOCK REPORT BODY/)).toBeTruthy());
    // Recent-runs list carries real per-run status/tokens
    expect(within(drawer).getByText('200 tok')).toBeTruthy();
    expect(fn(jobsService.fetchJobRuns)).toHaveBeenCalledWith('stock-analysis');
  });

  it('AC6: Run now calls runJob; Edit routes through chat dispatch', async () => {
    const onDispatch = vi.fn(() => true);
    render(<JobsRunsOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    await screen.findByTestId('job-detail-drawer');

    fireEvent.click(screen.getByTestId('job-action-run-now'));
    await waitFor(() => expect(fn(jobsService.runJob)).toHaveBeenCalledWith('stock-analysis'));

    // Edit must NOT write yaml — it hands a prompt to chat (s_job-manager owns it)
    fireEvent.click(screen.getByTestId('job-action-edit'));
    expect(onDispatch).toHaveBeenCalledTimes(1);
    expect(onDispatch.mock.calls[0][0]).toContain('stock-analysis');
  });

  it('AC6: New Job form routes creation through chat dispatch (never writes yaml)', async () => {
    const onDispatch = vi.fn(() => true);
    render(<JobsRunsOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click(screen.getByTestId('jobs-new-btn'));
    fireEvent.change(await screen.findByTestId('jobs-new-name'), { target: { value: 'Nightly audit' } });
    fireEvent.change(screen.getByTestId('jobs-new-schedule'), { target: { value: '0 2 * * *' } });
    fireEvent.change(screen.getByTestId('jobs-new-prompt'), { target: { value: 'audit deps' } });
    fireEvent.click(screen.getByTestId('jobs-new-submit'));

    expect(onDispatch).toHaveBeenCalledTimes(1);
    const prompt = onDispatch.mock.calls[0][0];
    expect(prompt).toContain('Nightly audit');
    expect(prompt).toContain('0 2 * * *');
  });

  it('AC6: system jobs hide pause/edit/delete (yaml-read-only)', async () => {
    fn(jobsService.fetchRoster).mockResolvedValue([mkRoster({ id: 'signal-fetch', name: 'Signal Fetch', source: 'system', category: 'system' })]);
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    await screen.findByTestId('job-detail-drawer');
    // Run-now always available; edit/delete absent for a system job
    expect(screen.getByTestId('job-action-run-now')).toBeTruthy();
    expect(screen.queryByTestId('job-action-edit')).toBeNull();
    expect(screen.queryByTestId('job-action-delete')).toBeNull();
  });
});
