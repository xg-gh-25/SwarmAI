/** The overlay host: six tabs, counts on Online / Offline from the data, "ask Swarm"
 *  closes the overlay only when a chat tab took the prompt (else it says so), and
 *  the Guide's twelve sections and its flywheel links. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { routes, reset, fakeApi, show } from './harness';

vi.mock('../../../services/api', () => fakeApi());
vi.mock('../../../services/chat', () => ({ chatService: { getSession: vi.fn(async (id: string) => (id === 'gone' ? null : { id })) } }));
import EvalOverlay from '../EvalOverlay';
import { GuideTab } from '../GuideTab';

const mount = (props: Record<string, unknown>) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <EvalOverlay {...props} />
  </QueryClientProvider>,
);

beforeEach(() => {
  reset();
  routes['GET /eval/golden-set'] = { total_cases: 2, cases: [
    { id: 'A', category: 'recall', dimension: 'cognition', tier: 'active', affected_by: [], evaluators: [], last_result: { status: 'failed', run_id: 'r', triggered_at: 'x' } },
    { id: 'B', category: 'recall', dimension: 'cognition', tier: 'archived', affected_by: [], evaluators: [], last_result: { status: 'failed', run_id: 'r', triggered_at: 'x' } },
  ] };
  routes['GET /eval/session-quality'] = {
    trend: [], checks: {}, sessions_checked: 3, calibration: { accepting: true, reason: null, labels: {} },
    low_details: [{ session_id: 'gone', title: 'T', goal_score: 0.2, tool_score: 0.2, reason: 'r' }],
  };
  routes['GET /eval/health'] = { release: null, dimension_summary: {}, last_run: null, total_cases: 2, follow_ups: [], evolution: [] };
  routes['GET /eval/history'] = [];
});

describe('Eval overlay', () => {
  it('has the six tabs, with the failing (non-archived) and low-session counts', async () => {
    mount({});
    const names = screen.getAllByRole('tab').map((b) => b.textContent?.replace(/\d+$/, ''));
    expect(names).toEqual(['Overview', 'Golden Cases', 'Online Eval', 'Offline Eval', 'LLM Judges', 'Guide']);
    await waitFor(() => expect(screen.getByTestId('eval-tab-offline').textContent).toBe('Offline Eval1'));
    expect(screen.getByTestId('eval-tab-online').textContent).toBe('Online Eval1');
    fireEvent.click(screen.getByTestId('eval-tab-guide'));
    expect(screen.getByTestId('eval-guide')).toBeTruthy();
  });

  it('closes when a chat tab takes the prompt, and stays open with a notice when none can', async () => {
    const close = vi.fn();
    const dispatchPrompt = vi.fn(() => false);
    mount({ close, dispatchPrompt, resumeSession: vi.fn(() => true), initialTab: 'online' });
    fireEvent.click(await screen.findByText('Ask Swarm'));
    expect(dispatchPrompt).toHaveBeenCalledWith(expect.stringContaining('Review session gone'));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toContain('No chat tab could take it');
    dispatchPrompt.mockReturnValue(true);
    fireEvent.click(screen.getByText('Ask Swarm'));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('says so when a session cannot be opened, instead of closing', async () => {
    const close = vi.fn();
    mount({ close, resumeSession: vi.fn(() => true), initialTab: 'online' });
    fireEvent.click(await screen.findByText('Open session'));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Session gone could not be opened'));
    expect(close).not.toHaveBeenCalled();
  });
});

describe('Guide', () => {
  it('teaches in twelve sections, and the flywheel steps open their tabs', () => {
    const { go } = show(<GuideTab />);
    expect(screen.getAllByTestId(/^guide-g-/)).toHaveLength(12);
    fireEvent.click(screen.getByTestId('wheel-3'));
    expect(go).toHaveBeenCalledWith('offline', 'release-record');
    fireEvent.click(screen.getByTestId('wheel-1'));   // Build has no tab
    expect(go).toHaveBeenCalledTimes(1);
  });
});
