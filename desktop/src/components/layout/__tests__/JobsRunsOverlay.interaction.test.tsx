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
 * event to open it. jobsService is mocked so we assert on
 * rendered data and the dispatched prompt.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { JobsRunsOverlay } from '../JobsRunsOverlay';
import { jobsService, type JobRosterRow, type JobsOverview, type JobRunsResult } from '../../../services/jobs';

vi.mock('../../../services/jobs', () => ({
  jobsService: {
    fetchRoster: vi.fn(),
    fetchOverview: vi.fn(),
    runJob: vi.fn(),
    fetchJobRuns: vi.fn(),
  },
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
    // The dispatched prompt must hand inline Swarm the FULL context (all 4 fields)
    // AND explicitly name the create path so the agent creates the job in one step
    // rather than inferring intent (mirror of ToDo dispatch handing full context).
    expect(prompt).toContain('Nightly audit');    // name
    expect(prompt).toContain('0 2 * * *');         // schedule (cron)
    expect(prompt).toMatch(/agent_task/);          // type present
    expect(prompt).toContain('audit deps');        // prompt/command body
    expect(prompt).toMatch(/s_job-manager/);       // names the create skill
    expect(prompt.toLowerCase()).toMatch(/confirm/); // confirm-then-create (HITL)
  });

  it('New Job form: script type surfaces the command as the command body + names the skill', async () => {
    const onDispatch = vi.fn(() => true);
    render(<JobsRunsOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click(screen.getByTestId('jobs-new-btn'));
    fireEvent.change(await screen.findByTestId('jobs-new-name'), { target: { value: 'Disk cleanup' } });
    fireEvent.change(screen.getByTestId('jobs-new-schedule'), { target: { value: '0 3 * * 0' } });
    fireEvent.click(screen.getByTestId('jobs-new-type-script'));
    fireEvent.change(screen.getByTestId('jobs-new-prompt'), { target: { value: 'rm -rf /tmp/cache' } });
    fireEvent.click(screen.getByTestId('jobs-new-submit'));

    const prompt = onDispatch.mock.calls[0][0];
    expect(prompt).toMatch(/script/);
    expect(prompt).toContain('rm -rf /tmp/cache');
    expect(prompt).toMatch(/s_job-manager/);
    // A script job's shell command MUST land in config.command (the field
    // executor._handle_script reads) — a script job built with the command in
    // the prompt field fails run-now with "No command configured" (proven by E2E).
    // The dispatched prompt must tell inline Swarm exactly where the command goes.
    expect(prompt).toMatch(/config\.command/);
  });

  it('New Job form: agent_task type does NOT mention config.command (only script needs it)', async () => {
    const onDispatch = vi.fn(() => true);
    render(<JobsRunsOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');

    fireEvent.click(screen.getByTestId('jobs-new-btn'));
    fireEvent.change(await screen.findByTestId('jobs-new-name'), { target: { value: 'Daily digest' } });
    fireEvent.change(screen.getByTestId('jobs-new-schedule'), { target: { value: '0 9 * * *' } });
    fireEvent.change(screen.getByTestId('jobs-new-prompt'), { target: { value: 'summarize my inbox' } });
    fireEvent.click(screen.getByTestId('jobs-new-submit'));

    const prompt = onDispatch.mock.calls[0][0];
    // agent_task reads the prompt field directly — no config.command note (avoids
    // steering the agent to the wrong field for an agent_task job).
    expect(prompt).not.toMatch(/config\.command/);
    expect(prompt).toContain('summarize my inbox');
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

  it('AC5: Runs view is PURE job-runs — no pipeline rows, newest-first', async () => {
    // Two scheduled-job runs; the newer instant must render first. The Runs view
    // must NOT contain any pipeline row (XG: pipeline 别揉进来) — it no longer
    // fetches or merges pipeline runs at all.
    fn(jobsService.fetchRoster).mockResolvedValue([
      mkRoster({ id: 'older', name: 'Older Job', lastRun: '2026-07-30T06:00:00Z', lastStatus: 'success' }),
      mkRoster({ id: 'newer', name: 'Newer Job', lastRun: '2026-07-31T06:00:00Z', lastStatus: 'failed' }),
    ]);
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    fireEvent.click(screen.getByTestId('jobs-view-runs'));

    await waitFor(() => expect(screen.getAllByTestId('run-row').length).toBe(2));
    const rows = screen.getAllByTestId('run-row');
    // Newest job first; both rows are job runs.
    expect(within(rows[0]).getByText('Newer Job')).toBeTruthy();
    expect(within(rows[1]).getByText('Older Job')).toBeTruthy();
    // Load-bearing proof of "no pipeline": the row count equals EXACTLY the number
    // of jobs with a lastRun (2). A leaked pipeline row would push the count to 3
    // and fail here — this is what would go RED if the pipeline leg were restored,
    // unlike a text-match on a badge that no longer exists (adversarial LOW).
    const jobsWithRuns = 2;
    expect(rows.length).toBe(jobsWithRuns);
  });

  it('AC6: pause/resume label reflects enabled state; run-now disabled for a paused job', async () => {
    fn(jobsService.fetchRoster).mockResolvedValue([mkRoster({ id: 'brain-push', name: 'Brain Push', enabled: false, lastStatus: 'never', source: 'user' })]);
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    await screen.findByTestId('job-detail-drawer');

    // Disabled job → the toggle reads "Resume" (not "Pause"); run-now disabled.
    expect(screen.getByTestId('job-action-resume')).toBeTruthy();
    expect(screen.queryByTestId('job-action-pause')).toBeNull();
    expect((screen.getByTestId('job-action-run-now') as HTMLButtonElement).disabled).toBe(true);
  });

  it('empty roster shows the guide empty-state, not a blank column', async () => {
    fn(jobsService.fetchRoster).mockResolvedValue([]);
    fn(jobsService.fetchOverview).mockResolvedValue({ total: 0, enabled: 0, healthy: 0, failing: 0, neverRun: 0, monthlySpendUsd: 0 });
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    expect(await screen.findByTestId('jobs-empty')).toBeTruthy();
    expect(screen.queryByTestId('job-card')).toBeNull();
  });

  it('never-run drawer shows "No output captured", never crashes on empty runs', async () => {
    fn(jobsService.fetchJobRuns).mockResolvedValue({ jobId: 'stock-analysis', lastOutput: null, lastOutputDate: null, recent: [] });
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    const drawer = await screen.findByTestId('job-detail-drawer');
    await waitFor(() => expect(within(drawer).getByText(/No output captured/)).toBeTruthy());
    expect(within(drawer).getByText(/No run history/)).toBeTruthy();
  });

  it('F1: a failed overview fetch does NOT blank a successful roster (independent settle)', async () => {
    // /jobs/status blips but /jobs/ succeeds — the roster must still render, not
    // fall to the false "no jobs" empty state.
    fn(jobsService.fetchOverview).mockRejectedValue(new Error('status 503'));
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    // Roster cards render despite the overview failure; no false empty-state.
    expect((await screen.findAllByTestId('job-card')).length).toBe(2);
    expect(screen.queryByTestId('jobs-empty')).toBeNull();
    // Overview strip is absent (its fetch failed) but the page is not blanked.
    expect(screen.queryByTestId('jobs-overview')).toBeNull();
  });

  it('F2: drawer reflects fresh data after Run now (re-derived by id, not a stale snapshot)', async () => {
    // First roster: lastStatus success. After run-now, refreshJobs returns an
    // updated row (lastStatus running) — the open drawer must show the NEW status.
    const before = mkRoster({ id: 'stock-analysis', name: 'Stock Analysis', lastStatus: 'success', totalRuns: 42 });
    const after = mkRoster({ id: 'stock-analysis', name: 'Stock Analysis', lastStatus: 'running', totalRuns: 43 });
    fn(jobsService.fetchRoster).mockResolvedValueOnce([before]).mockResolvedValue([after]);
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    const drawer = await screen.findByTestId('job-detail-drawer');
    expect(within(drawer).getByText('42')).toBeTruthy();  // totalRuns before

    fireEvent.click(screen.getByTestId('job-action-run-now'));
    // After run-now → refreshJobs → roster replaced → drawer re-derives to the new row.
    await waitFor(() => expect(within(screen.getByTestId('job-detail-drawer')).getByText('43')).toBeTruthy());
  });

  it('F3: run-now failure surfaces an error line (not silent)', async () => {
    fn(jobsService.runJob).mockRejectedValue(new Error('boom'));
    render(<JobsRunsOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('jobs-overlay');
    fireEvent.click((await screen.findAllByTestId('job-card'))[0]);
    await screen.findByTestId('job-detail-drawer');
    fireEvent.click(screen.getByTestId('job-action-run-now'));
    expect(await screen.findByTestId('job-run-error')).toBeTruthy();
  });
});
