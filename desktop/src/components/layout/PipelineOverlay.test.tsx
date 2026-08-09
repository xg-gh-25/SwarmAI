/**
 * Tests for PipelineOverlay — the WORK-zone retro-analytics dashboard (run_f8494370).
 *
 * Covers:
 * - opens on `swarm:show-pipeline`, fetches analytics ONCE, renders overall strip
 *   + by-project groups one screen (AC5).
 * - clicking a run opens the detail drawer → fetchRunDetail (AC6).
 * - Resume on a paused/aborted run routes through onDispatch (Gate-1 #7) with a
 *   run-resume command (AC6).
 * - Cancel calls PATCH /pipelines/{id}/cancel (AC6).
 * - NO polling: fetchAnalytics called once per open (not on an interval).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act, fireEvent, waitFor, within } from '@testing-library/react';

const fetchAnalytics = vi.fn();
const fetchRunDetail = vi.fn();
const fetchActivePipelines = vi.fn();
vi.mock('../../services/pipelines', () => ({
  pipelinesService: {
    fetchAnalytics: (w: string) => fetchAnalytics(w),
    fetchRunDetail: (id: string) => fetchRunDetail(id),
    fetchActivePipelines: () => fetchActivePipelines(),
  },
}));

const apiPatch = vi.fn();
vi.mock('../../services/api', () => ({
  default: { patch: (url: string) => apiPatch(url) },
}));

import { PipelineContent } from './PipelineOverlay';

const ANALYTICS = {
  window: '30d',
  overall: {
    totalRuns: 3, completed: 2, completionRate: 0.667, avgCycleMin: 12.0,
    tokensActual: 55000, tokensEst: 60000, profileMix: { full: 2, goal: 1 }, abortedCount: 1,
  },
  trend: [{ week: '2026-07-27', runs: 2, completed: 1, avgCycleMin: 10, tokens: 30000 }],
  byProject: [
    {
      project: 'SwarmAI', runCount: 2, completionRate: 0.5, avgCycleMin: 12, abortedCount: 1,
      runs: [
        { id: 'run_done1', requirement: 'Ship feature X', status: 'completed', profile: 'full',
          progress: '8/8', cycleTimeMin: 12, tokensActual: 30000, tokensEst: 28000,
          createdAt: '2026-08-01T10:00:00+00:00', updatedAt: '2026-08-01T10:12:00+00:00',
          pauseKind: null, checkpointReason: null,
          reportPath: 'Projects/SwarmAI/.artifacts/runs/run_done1/REPORT.md' },
        { id: 'run_paused1', requirement: 'Aborted thing', status: 'paused', profile: 'goal',
          progress: '3/6', cycleTimeMin: null, tokensActual: 5000, tokensEst: 40000,
          createdAt: '2026-08-01T09:00:00+00:00', updatedAt: '2026-08-01T09:30:00+00:00',
          pauseKind: 'decision', checkpointReason: 'Gate 1 BLOCK: needs decision',
          reportPath: null },
      ],
    },
    {
      project: 'CMHK_SalesIntel', runCount: 1, completionRate: 1, avgCycleMin: 8, abortedCount: 0,
      runs: [
        { id: 'run_cmhk1', requirement: 'Report gen', status: 'completed', profile: 'bugfix',
          progress: '8/8', cycleTimeMin: 8, tokensActual: 20000, tokensEst: 22000,
          createdAt: '2026-07-30T10:00:00+00:00', updatedAt: '2026-07-30T10:08:00+00:00',
          pauseKind: null, checkpointReason: null, reportPath: null },
      ],
    },
  ],
};

const DETAIL = {
  id: 'run_paused1', project: 'SwarmAI', requirement: 'Aborted thing', status: 'paused',
  profile: 'goal', cycleTimeMin: null, reportMd: '', reportPath: null, reflectLessons: [],
  stageTokens: [{ stage: 'evaluate', est: 6000, actual: 4000 }],
  commits: [], checkpointReason: 'Gate 1 BLOCK: needs decision',
  createdAt: '2026-08-01T09:00:00+00:00', updatedAt: '2026-08-01T09:30:00+00:00',
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  fetchAnalytics.mockResolvedValue(ANALYTICS);
  // status-aware: return a detail matching the requested run's status so the
  // drawer's resumable/cancel affordances reflect the real run.
  fetchRunDetail.mockImplementation((id: string) => {
    if (id === 'run_done1') {
      return Promise.resolve({ ...DETAIL, id: 'run_done1', status: 'completed',
        cycleTimeMin: 12, checkpointReason: null,
        reportPath: 'Projects/SwarmAI/.artifacts/runs/run_done1/REPORT.md' });
    }
    return Promise.resolve(DETAIL);
  });
  // Running now: two runs, one running + one paused → count = 1.
  fetchActivePipelines.mockResolvedValue([
    { id: 'run_r', status: 'running' }, { id: 'run_p', status: 'paused' },
  ]);
  apiPatch.mockResolvedValue({});
});

function renderAndOpen(onDispatch = vi.fn().mockReturnValue(true)) {
  // M4: PipelineContent renders immediately (host owns open + fresh mount per open).
  render(<PipelineContent onDispatch={onDispatch} close={() => {}} />);
  return { onDispatch };
}

describe('PipelineOverlay', () => {
  it('fetches analytics once on open and renders overall + by-project (one screen)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-overall')).toBeInTheDocument());
    // overall strip
    expect(screen.getByTestId('pipeline-overall').textContent).toContain('67%'); // completion
    // BOTH projects visible (global + by-project one screen)
    expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument();
    expect(screen.getByTestId('pipeline-project-CMHK_SalesIntel')).toBeInTheDocument();
    // fetched exactly once (no polling)
    expect(fetchAnalytics).toHaveBeenCalledTimes(1);
  });

  it('collapses all groups except the first (no 750-button wall on open)', async () => {
    renderAndOpen();
    // first group (SwarmAI) expanded → its run buttons render
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_done1')).toBeInTheDocument());
    // second group (CMHK_SalesIntel) collapsed → its run button is NOT rendered until clicked
    expect(screen.queryByTestId('pipeline-run-run_cmhk1')).toBeNull();
    // expand it → now its run appears
    fireEvent.click(screen.getByTestId('pipeline-project-CMHK_SalesIntel').querySelector('button')!);
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_cmhk1')).toBeInTheDocument());
  });

  it('renders a run-time column (updatedAt) on each run row', async () => {
    renderAndOpen();
    const row = await screen.findByTestId('pipeline-run-run_done1');
    // fmtTs('2026-08-01T10:12:00+00:00') → 'YYYY-MM-DD HH:MM' (local); assert the
    // date portion is present (the exact HH:MM is timezone-dependent in CI).
    expect(row.textContent).toContain('2026-08-01');
  });

  it('does not poll — analytics fetched once, not on an interval', async () => {
    vi.useFakeTimers();
    renderAndOpen();
    await act(async () => { await Promise.resolve(); });
    act(() => { vi.advanceTimersByTime(60000); });
    expect(fetchAnalytics).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('re-fetches when the window toggles to YTD', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-window-ytd')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-window-ytd'));
    await waitFor(() => expect(fetchAnalytics).toHaveBeenCalledWith('ytd'));
  });

  it('opens the detail drawer on run click and fetches its retro', async () => {
    renderAndOpen();
    // run_paused1 is a needs-you run → appears in BOTH the pinned Needs-you region
    // and its project group. Click the row inside the project group (scoped).
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_paused1')).getByTitle('Aborted thing'));
    await waitFor(() => expect(screen.getByTestId('pipeline-run-drawer')).toBeInTheDocument());
    expect(fetchRunDetail).toHaveBeenCalledWith('run_paused1');
    await waitFor(() => expect(screen.getByText(/Gate 1 BLOCK/)).toBeInTheDocument());
  });

  it('Resume routes a run-resume command through onDispatch (Gate-1 #7)', async () => {
    const { onDispatch } = renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_paused1')).getByTitle('Aborted thing'));
    await waitFor(() => expect(screen.getByTestId('pipeline-resume-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-resume-btn'));
    expect(onDispatch).toHaveBeenCalledTimes(1);
    const prompt = onDispatch.mock.calls[0][0];
    expect(prompt).toContain('run-resume');
    expect(prompt).toContain('run_paused1');
    expect(prompt).toContain('SwarmAI');
  });

  it('Cancel calls PATCH /pipelines/{id}/cancel', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_paused1')).getByTitle('Aborted thing'));
    await waitFor(() => expect(screen.getByTestId('pipeline-cancel-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-cancel-btn'));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/pipelines/run_paused1/cancel'));
  });

  it('a crash-residue paused run has NO Resume button in the drawer', async () => {
    // The Gate-2 HIGH: the drawer must re-derive pause_kind from checkpoint_reason,
    // not assume every paused run is resumable. A crash-residue zombie is NOT.
    fetchRunDetail.mockResolvedValueOnce({
      ...DETAIL, id: 'run_paused1', status: 'paused',
      checkpointReason: 'session_crash_auto_detected',  // canonical crash marker
    });
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_paused1')).getByTitle('Aborted thing'));
    await waitFor(() => expect(screen.getByTestId('pipeline-run-drawer')).toBeInTheDocument());
    expect(screen.queryByTestId('pipeline-resume-btn')).toBeNull();
  });

  it('a decision-paused run DOES show Resume in the drawer', async () => {
    // (regression companion: a genuine decision-pause IS resumable — DETAIL default
    //  has checkpointReason 'Gate 1 BLOCK: needs decision', not the crash marker)
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_paused1')).getByTitle('Aborted thing'));
    await waitFor(() => expect(screen.getByTestId('pipeline-resume-btn')).toBeInTheDocument());
  });

  it('a completed run has no Resume button (not resumable)', async () => {
    renderAndOpen();
    // run_done1 is completed (not needs-you) → single row in its group.
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_done1')).toBeInTheDocument());
    fireEvent.click(within(screen.getByTestId('pipeline-run-run_done1')).getByTitle('Ship feature X'));
    await waitFor(() => expect(screen.getByTestId('pipeline-run-drawer')).toBeInTheDocument());
    expect(screen.queryByTestId('pipeline-resume-btn')).toBeNull();
  });

  // ── run_929024a8 redesign ACs ──────────────────────────────────────────────

  it('AC1: states the time window explicitly (last 30 days / YTD)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByText(/Showing runs from the last 30 days/)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-window-ytd'));
    await waitFor(() => expect(screen.getByText(/Showing runs from Jan 1 \(year to date\)/)).toBeInTheDocument());
  });

  it('AC4: shows "Running now" (from active pipelines), NOT the profile ciphertext', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-overall')).toBeInTheDocument());
    const strip = screen.getByTestId('pipeline-overall').textContent!;
    expect(strip).toContain('Running now');
    expect(strip).toContain('1'); // one running in the active mock
    // the old unreadable "g2 f1"-style ciphertext must be gone
    expect(strip).not.toMatch(/[gfbdurt]\d+\s+[gfbdurt]\d+/);
    expect(strip).not.toContain('Profiles');
  });

  it('AC2: a run with a report shows a report button that opens Canvas (close + swarm:open-file)', async () => {
    const close = vi.fn();
    render(<PipelineContent onDispatch={vi.fn().mockReturnValue(true)} close={close} />);
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const dispatchSpy = vi.spyOn(document, 'dispatchEvent');
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    // run_done1 HAS a reportPath; run_paused1 does NOT
    expect(grp.getByTestId('pipeline-run-report-run_done1')).toBeInTheDocument();
    expect(within(grp.getByTestId('pipeline-run-run_paused1')).queryByTestId('pipeline-run-report-run_paused1')).toBeNull();
    fireEvent.click(grp.getByTestId('pipeline-run-report-run_done1'));
    expect(close).toHaveBeenCalledTimes(1);
    const ev = dispatchSpy.mock.calls.map((c) => c[0]).find((e) => (e as Event).type === 'swarm:open-file') as CustomEvent;
    expect(ev).toBeTruthy();
    expect(ev.detail.path).toBe('Projects/SwarmAI/.artifacts/runs/run_done1/REPORT.md');
    dispatchSpy.mockRestore();
  });

  it('AC3: detail drawer opens report in Canvas via a button, not an inline <pre>', async () => {
    const close = vi.fn();
    render(<PipelineContent onDispatch={vi.fn().mockReturnValue(true)} close={close} />);
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    fireEvent.click(within(grp.getByTestId('pipeline-run-run_done1')).getByTitle('Ship feature X'));
    await waitFor(() => expect(screen.getByTestId('pipeline-detail-report-btn')).toBeInTheDocument());
    const dispatchSpy = vi.spyOn(document, 'dispatchEvent');
    fireEvent.click(screen.getByTestId('pipeline-detail-report-btn'));
    expect(close).toHaveBeenCalled();
    const ev = dispatchSpy.mock.calls.map((c) => c[0]).find((e) => (e as Event).type === 'swarm:open-file') as CustomEvent;
    expect(ev.detail.path).toBe('Projects/SwarmAI/.artifacts/runs/run_done1/REPORT.md');
    dispatchSpy.mockRestore();
  });

  it('AC5/AC6: pinned Needs-you region by default; clicking the stat focuses the list to needs-you', async () => {
    renderAndOpen();
    // B: pinned region present by default, listing the decision-paused run
    await waitFor(() => expect(screen.getByTestId('pipeline-needsyou-region')).toBeInTheDocument());
    const region = within(screen.getByTestId('pipeline-needsyou-region'));
    expect(region.getByTestId('pipeline-run-run_paused1')).toBeInTheDocument();
    // A: click the Needs you stat → region hides (would double-show) + groups filter
    fireEvent.click(screen.getByTestId('pipeline-needsyou-stat'));
    await waitFor(() => expect(screen.queryByTestId('pipeline-needsyou-region')).toBeNull());
    // the completed run is filtered OUT of the focused view; the needs-you run remains
    expect(screen.queryByTestId('pipeline-run-run_done1')).toBeNull();
    expect(screen.getByTestId('pipeline-run-run_paused1')).toBeInTheDocument();
    // CMHK group has no needs-you run → dropped entirely
    expect(screen.queryByTestId('pipeline-project-CMHK_SalesIntel')).toBeNull();
    // click again → restore
    fireEvent.click(screen.getByTestId('pipeline-needsyou-stat'));
    await waitFor(() => expect(screen.getByTestId('pipeline-needsyou-region')).toBeInTheDocument());
  });

  it('AC5(pill): a decision-paused run renders a "needs you" status pill, a completed run "completed"', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pipeline-project-SwarmAI')).toBeInTheDocument());
    const grp = within(screen.getByTestId('pipeline-project-SwarmAI'));
    expect(within(grp.getByTestId('pipeline-run-run_paused1')).getByText('needs you')).toBeInTheDocument();
    expect(within(grp.getByTestId('pipeline-run-run_done1')).getByText('completed')).toBeInTheDocument();
  });
});
