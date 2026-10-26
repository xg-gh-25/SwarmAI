/**
 * Overview's "Suggested follow-up" renders the backend's list as-is, and the
 * Reports tab lists scheduled reports with hand-run ones behind "show all"
 * (design docs/design/2026-10-26-eval-as-product.md §3.4).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/api', () => ({
  default: {
    get: vi.fn(async () => ({
      data: [
        { filename: '2026-10-24_scheduled.html', sizeBytes: 2048, modified: 1, trigger: 'scheduled', routine: true },
        { filename: '2026-07-07___lockprobe__.html', sizeBytes: 1024, modified: 0, trigger: '__lockprobe__', routine: false },
      ],
    })),
  },
}));

import { FollowUps, ReportsTab, type FollowUp } from './EvalDashboard';

const item = (over: Partial<FollowUp> = {}): FollowUp => ({
  severity: 'high', rule: 1, what: { en: 'A product check failed: Recall finds memory', zh: '产品功能坏了：能找到记忆' },
  why: "Expected 'X_OK' not found", action: { en: 'Fix the behaviour', zh: '修好这个功能' }, case_ids: ['P1'], ...over,
});

describe('FollowUps', () => {
  it('says nothing needs handling for an empty list', () => {
    render(<FollowUps items={[]} />);
    expect(screen.getByText('Nothing needs handling')).toBeTruthy();
  });

  it('shows each item with its reason and action; a high one stands out', () => {
    render(<FollowUps items={[item(), item({ severity: 'low', rule: 5, what: { en: 'The full run has never run', zh: '全量运行从没跑过' }, why: '' })]} />);
    const high = screen.getByText(/A product check failed/);
    expect(high.className).toContain('status-error');
    expect(screen.getByText(/X_OK' not found · Fix the behaviour/)).toBeTruthy();
    expect(screen.getByText('The full run has never run').className).not.toContain('status-error');
  });

  it('renders nothing while the backend has not sent a list', () => {
    const { container } = render(<FollowUps items={undefined} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('ReportsTab', () => {
  it('lists scheduled reports and keeps hand-run ones behind show all', async () => {
    render(<QueryClientProvider client={new QueryClient()}><ReportsTab /></QueryClientProvider>);
    expect(await screen.findByText('2026-10-24 scheduled')).toBeTruthy();
    expect(screen.queryByText(/lockprobe/)).toBeNull();
    fireEvent.click(screen.getByTestId('reports-show-all'));
    expect(screen.getByText(/lockprobe/)).toBeTruthy();
  });
});
