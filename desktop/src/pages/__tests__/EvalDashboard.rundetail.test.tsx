/**
 * RunDetail visualization (run_1f588e53 C4/C5) — verifies the TrendsTab "Recent
 * Runs" → per-run detail flow.
 *
 * Invariants under test:
 *  - TrendsTab surfaces the 3 most-recent runs as clickable cards
 *  - clicking a run opens a detail panel that calls GET /eval/runs/{run_id}
 *  - per-case results are grouped BY STATUS (run cases have no dimension field —
 *    Gate-1 BLOCK#1); the dimension label is joined from the golden set by id
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TrendsTab } from '../EvalDashboard';

const mockGet = vi.fn();
vi.mock('../../services/api', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

// history: newest-first list of runs (matches backend get_history shape)
const HISTORY = [
  { run_id: 'run_3', triggered_by: 'manual', triggered_at: '2026-06-26T10:00:00Z', overall_score: 92, dimensions: { capability: 90 }, cases_passed: 9, cases_failed: 1, cases_skipped: 0 },
  { run_id: 'run_2', triggered_by: 'weekly', triggered_at: '2026-06-25T10:00:00Z', overall_score: 85, dimensions: { capability: 84 }, cases_passed: 8, cases_failed: 2, cases_skipped: 0 },
  { run_id: 'run_1', triggered_by: 'manual', triggered_at: '2026-06-24T10:00:00Z', overall_score: 80, dimensions: { capability: 80 }, cases_passed: 8, cases_failed: 1, cases_skipped: 1 },
  { run_id: 'run_0', triggered_by: 'manual', triggered_at: '2026-06-23T10:00:00Z', overall_score: 78, dimensions: { capability: 78 }, cases_passed: 7, cases_failed: 2, cases_skipped: 1 },
];

// a single run detail: per-case results — NOTE: no `dimension` field per case
const RUN_3_DETAIL = {
  run_id: 'run_3',
  triggered_by: 'manual',
  triggered_at: '2026-06-26T10:00:00Z',
  overall_score: 92,
  cases_passed: 9,
  cases_failed: 1,
  cases_skipped: 0,
  cases: [
    { id: 'a', status: 'passed', evaluator: 'file_contains', duration_ms: 10 },
    { id: 'b', status: 'failed', evaluator: 'canary_pass', duration_ms: 20, notes: 'boom' },
  ],
};

const GS_RESPONSE = {
  total_cases: 2,
  categories: ['compliance'],
  dimensions: ['capability'],
  cases: [
    { id: 'a', category: 'compliance', dimension: 'capability', level: 'session', title: 'A', tier: 'active', evaluators: [], affected_by: [], last_result: null },
    { id: 'b', category: 'compliance', dimension: 'compliance', level: 'session', title: 'B', tier: 'active', evaluators: [], affected_by: [], last_result: null },
  ],
};

function renderTrends() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TrendsTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith('/eval/history')) return Promise.resolve({ data: HISTORY });
    if (url === '/eval/runs/run_3') return Promise.resolve({ data: RUN_3_DETAIL });
    if (url.startsWith('/eval/golden-set')) return Promise.resolve({ data: GS_RESPONSE });
    return Promise.resolve({ data: {} });
  });
});

describe('TrendsTab recent runs → RunDetailPanel', () => {
  it('surfaces exactly 3 recent runs', async () => {
    renderTrends();
    const recent = await screen.findByTestId('recent-runs');
    // 3 cards (run_3, run_2, run_1) — run_0 excluded
    const buttons = within(recent).getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });

  it('clicking a recent run opens detail and groups cases by status', async () => {
    renderTrends();
    const recent = await screen.findByTestId('recent-runs');
    // click the first (newest = run_3)
    fireEvent.click(within(recent).getAllByRole('button')[0]);
    // detail calls /eval/runs/run_3 and groups by status
    await waitFor(() => expect(screen.getByTestId('run-status-failed')).toBeInTheDocument());
    expect(screen.getByTestId('run-status-passed')).toBeInTheDocument();
    // the failed case 'b' is shown, with its dimension joined from golden set.
    // case 'b' has dimension 'compliance' in GS_RESPONSE → must render that label
    // (proves the dimById join is live, not vacuous — Gate-2 test-gap fix).
    const failedGroup = screen.getByTestId('run-status-failed');
    expect(within(failedGroup).getByText('b')).toBeInTheDocument();
    expect(within(failedGroup).getByText('compliance')).toBeInTheDocument();
  });

  it('calls GET /eval/runs/{run_id} (the previously-unused endpoint)', async () => {
    renderTrends();
    const recent = await screen.findByTestId('recent-runs');
    fireEvent.click(within(recent).getAllByRole('button')[0]);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/eval/runs/run_3'));
  });
});
