/**
 * The Guide describes mechanisms only (design docs/design/2026-10-26-eval-as-product.md
 * §3.6). Times, weekdays and counts are not written in it: the next full and online
 * runs come from /api/eval/health. These assertions are what keeps it from going stale.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

let health: unknown = {};
vi.mock('../../services/api', () => ({ default: { get: vi.fn(async () => ({ data: health })) } }));

import { GuideTab } from '../EvalDashboard';

const show = () => render(<QueryClientProvider client={new QueryClient()}><GuideTab /></QueryClientProvider>);
const WEEKDAYS = /Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|周[一二三四五六日]|星期/;

describe('GuideTab', () => {
  it('writes no time, weekday or count, in either language', () => {
    for (const lang of ['EN', '中文']) {
      const { container, unmount } = show();
      fireEvent.click(screen.getByRole('radio', { name: lang }));
      const text = container.textContent || '';
      expect(text).not.toMatch(/\d/);          // no time, date, number of cases or days
      expect(text).not.toMatch(WEEKDAYS);
      unmount();
    }
  });

  it('explains the three lines and how cases are added and retired', () => {
    const { container } = show();
    const text = container.textContent || '';
    for (const line of ['quick', 'full', 'online']) expect(screen.getByTestId(`guide-line-${line}`)).toBeTruthy();
    expect(text).toMatch(/purpose/);
    expect(text).toMatch(/templates\/eval\/golden_set\.yaml/);
    expect(text).toMatch(/Nothing creates cases automatically/);
    expect(text).not.toMatch(/harvest|Layer|layer ②/i);
  });

  it('shows the next runs from the health endpoint', async () => {
    health = { lines: { quick: { last_run: null, passed: null, total: null },
      full: { last_run: null, score: null, next_run: '2026-11-09T10:30:00+00:00' },
      online: { last_run: null, sessions_checked: null, next_run: '2026-10-30T08:00:00+00:00' },
      golden_set: { product: 1, private: 0, total: 1, product_cap: 1, total_cap: 1 } } };
    show();
    await screen.findByText(/Next: 2026-11-09 10:30/);
    expect(screen.getByTestId('guide-line-online').textContent).toMatch(/Next: 2026-10-30 08:00/);
  });

  it('has no architecture images (they were hand-drawn copies that went stale)', () => {
    const { container } = show();
    expect(container.querySelectorAll('img').length).toBe(0);
  });
});
