/** The overlay host: four tabs, counts on Cases / Live sessions from the data, "ask
 *  Swarm" closes the overlay only when a chat tab took the prompt (else it says so),
 *  Method holds the Guide's twelve sections and the judges, and the flywheel links. */
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
  routes['GET /eval/judges'] = { judge_model: 'm', agent_model: null, cases_total: 0, cases_judged: 0, gating_total: 0, gating_judged: 0, judges: [] };
});

describe('Eval overlay', () => {
  it('has the four tabs, with the failing (non-archived) and low-session counts; Method holds the guide and the judges', async () => {
    mount({});
    const names = screen.getAllByRole('tab').map((b) => b.textContent?.replace(/\d+$/, ''));
    expect(names).toEqual(['Overview', 'Cases', 'Live sessions', 'Method']);
    await waitFor(() => expect(screen.getByTestId('eval-tab-cases').textContent).toBe('Cases1'));
    expect(screen.getByTestId('eval-tab-online').textContent).toBe('Live sessions1');
    fireEvent.click(screen.getByTestId('eval-tab-method'));
    expect(screen.getByTestId('eval-guide')).toBeTruthy();
    expect(await screen.findByTestId('eval-judges')).toBeTruthy();
  });

  it('the tab bar moves with the arrow keys; only the chosen tab is in the Tab order', async () => {
    mount({});
    const overview = screen.getByTestId('eval-tab-overview');
    expect(overview.tabIndex).toBe(0);
    expect(screen.getByTestId('eval-tab-cases').tabIndex).toBe(-1);
    fireEvent.keyDown(overview, { key: 'ArrowRight' });
    expect(screen.getByTestId('eval-tab-cases').getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(screen.getByTestId('eval-tab-cases'));
    fireEvent.keyDown(screen.getByTestId('eval-tab-cases'), { key: 'End' });
    expect(screen.getByTestId('eval-tab-method').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('eval-tab-method');
  });

  it('closes when a chat tab takes the prompt, and stays open with a notice when none can', async () => {
    const close = vi.fn();
    const dispatchPrompt = vi.fn(() => false);
    mount({ close, dispatchPrompt, resumeSession: vi.fn(() => true), initialTab: 'online' });
    fireEvent.click(await screen.findByText('T'));   // open the low-scored session in place
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
    expect(go).toHaveBeenCalledWith('cases', 'history');
    fireEvent.click(screen.getByTestId('wheel-1'));   // Build has no tab
    expect(go).toHaveBeenCalledTimes(1);
    // the steps with a tab are links the keyboard reaches; Build is not one
    expect(screen.getByTestId('wheel-4').getAttribute('tabindex')).toBe('0');
    expect(screen.getByTestId('wheel-1').getAttribute('tabindex')).toBeNull();
    fireEvent.keyDown(screen.getByTestId('wheel-4'), { key: 'Enter' });
    expect(go).toHaveBeenLastCalledWith('online', undefined);
  });
});
