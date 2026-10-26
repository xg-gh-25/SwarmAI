/**
 * The Online tab (design docs/design/2026-10-26-eval-as-product.md §3.5): one row
 * per check with its rate against last week and the example sessions; "not run",
 * "did not run" and "nothing to check" are each said, never shown as 0%.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OnlineScoring } from './EvalDashboard';

let payload: OnlineScoring;
vi.mock('../services/api', () => ({ default: { get: vi.fn(async () => ({ data: payload })) } }));

import { SessionQualityTab } from './EvalDashboard';

const show = () => render(<QueryClientProvider client={new QueryClient()}><SessionQualityTab /></QueryClientProvider>);

describe('Online tab', () => {
  beforeEach(() => {
    payload = { last_run: null };
  });

  it('says it has not run yet', async () => {
    show();
    expect((await screen.findByTestId('online-status')).textContent).toMatch(/Not run yet/);
  });

  it('says this week did not run, with the reason', async () => {
    payload = { last_run: '2026-10-24T10:00:00+00:00', sampler_error: 'OperationalError', sessions_checked: 0 };
    show();
    expect((await screen.findByTestId('online-status')).textContent).toMatch(/did not run: OperationalError/);
  });

  it('says there was nothing to check', async () => {
    payload = { last_run: '2026-10-24T10:00:00+00:00', sessions_checked: 0, checks: {} };
    show();
    expect((await screen.findByTestId('online-status')).textContent).toMatch(/No sessions to check/);
  });

  it('shows each check with its rate, last week and the example sessions; a worse one stands out', async () => {
    payload = {
      last_run: '2026-10-24T10:00:00+00:00', sessions_checked: 12,
      checks: {
        O1_LANGUAGE: { checked: 40, failed: 4, rate: 0.1, examples: [{ session_id: 's1', title: 'Fix the parser', snippet: '这个函数是做什么的', reason: 'asked in Chinese, answered otherwise' }] },
        O3_CORRECTIONS: { checked: 12, failed: 1, rate: 0.083, examples: [] },
      },
      trend: [{ week: '2026-W42', rates: { O1_LANGUAGE: 0.05, O3_CORRECTIONS: 0.2 } }, { week: '2026-W43', rates: {} }],
    };
    show();
    const o1 = await screen.findByTestId('online-check-O1_LANGUAGE');
    expect(o1.textContent).toMatch(/10%/);
    expect(o1.textContent).toMatch(/4\/40 · last 5%/);
    expect(o1.textContent).toMatch(/Fix the parser/);
    expect(screen.getByText('Replies in the language you asked in').className).toContain('status-error');
    expect(screen.getByText('Sessions where you corrected it').className).not.toContain('status-error');
    expect(screen.getByTestId('online-check-O4_GOAL').textContent).toMatch(/no data/);
  });
});
