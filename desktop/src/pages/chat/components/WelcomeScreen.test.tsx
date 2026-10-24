/**
 * WelcomeScreen.test.tsx — the v2 Welcome screen (design welcome-screen-v2.md).
 *
 * What is tested (acceptance tests, written before the rewrite):
 *   - Brand: logo, the SwarmAI wordmark and the slogan render.
 *   - One queue: the "Needs you" count is the attention query's blocking+review,
 *     at most 3 rows, and the card reads the shared ['overlay','needs-you'] key.
 *   - No send: clicking a row or "Open all" opens the Needs You overlay
 *     (dispatchUiCommand('show-needs-you')) and never calls a send callback.
 *   - No dead reads: rendering issues no /system/briefing or /ddd/brains request.
 *   - Empty queue shows one calm line; a failed read hides the section.
 * Methodology: render the real component in a real QueryClientProvider with the
 * attention service and the api client mocked.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const fetchAttention = vi.fn();
vi.mock('../../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: () => fetchAttention() },
}));

const apiGet = vi.fn(async () => ({ data: {} }));
vi.mock('../../../services/api', () => ({ default: { get: (...a: unknown[]) => apiGet(...a), post: vi.fn() } }));

const dispatchUiCommand = vi.fn(() => true);
vi.mock('../../../utils/uiCommands', () => ({ dispatchUiCommand: (...a: unknown[]) => dispatchUiCommand(...a) }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { WelcomeScreen as WelcomeScreenImpl } from './WelcomeScreen';
import { useAttentionQueue } from '../../../hooks/useAttentionQueue';

// Extra props (onFocusClick / onItemClick) are passed on purpose: the old screen
// took them and sent on click; the new one must ignore them.
const WelcomeScreen = WelcomeScreenImpl as React.ComponentType<Record<string, unknown>>;

const item = (i: number, brain: string | null = 'SwarmAI') => ({
  id: `a${i}`, source: 'paused_run' as const, tier: 'blocking' as const, brain,
  title: `Item ${i}`, detail: '', dispatch: { message: `Item ${i}` },
});

function renderWelcome(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(
    <QueryClientProvider client={qc}><WelcomeScreen {...props} /></QueryClientProvider>,
  ) };
}

beforeEach(() => { fetchAttention.mockReset(); apiGet.mockClear(); dispatchUiCommand.mockClear(); });
afterEach(cleanup);

describe('WelcomeScreen v2', () => {
  it('shows the logo, the wordmark and the slogan', () => {
    fetchAttention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
    renderWelcome();
    expect(screen.getByAltText('SwarmAI')).toBeTruthy();
    expect(screen.getByTestId('welcome-wordmark').textContent).toBe('SwarmAI');
    expect(screen.getByTestId('welcome-slogan').textContent).toBe('Grow your Brain. Deliver Anything.');
  });

  it('count = blocking + review from the attention query; at most 3 rows', async () => {
    fetchAttention.mockResolvedValue({
      items: [1, 2, 3, 4, 5].map((i) => item(i)),
      counts: { blocking: 5, review: 3 },
    });
    renderWelcome();
    await waitFor(() => expect(screen.getByTestId('welcome-needs-count').textContent).toBe('8'));
    expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(3);
    expect(screen.getByTestId('welcome-needs-more').textContent).toContain('5');
  });

  it('reads the shared needs-you query key (one cache with the pill and overlay)', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    const { qc } = renderWelcome();
    await waitFor(() => expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(1));
    expect(qc.getQueryData(['overlay', 'needs-you'])).toBeTruthy();
  });

  it('the pill hook and the Welcome card share one request and one number', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 4, review: 4 } });
    let pillCount = -1;
    function Pill() {
      const { counts } = useAttentionQueue();
      pillCount = counts.blocking + counts.review;
      return null;
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Pill /><WelcomeScreen /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByTestId('welcome-needs-count').textContent).toBe('8'));
    expect(pillCount).toBe(8);
    expect(fetchAttention).toHaveBeenCalledTimes(1);
  });

  it('clicking a row or Open all opens Needs You and never sends', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    const onFocusClick = vi.fn();
    const onItemClick = vi.fn();
    renderWelcome({ onFocusClick, onItemClick });
    fireEvent.click(await screen.findByTestId('welcome-needs-row'));
    fireEvent.click(screen.getByTestId('welcome-needs-open'));
    expect(dispatchUiCommand).toHaveBeenCalledTimes(2);
    expect(dispatchUiCommand).toHaveBeenCalledWith('show-needs-you');
    expect(onFocusClick).not.toHaveBeenCalled();
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('issues no /system/briefing or /ddd/brains request', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    renderWelcome();
    await screen.findByTestId('welcome-needs-row');
    const urls = apiGet.mock.calls.map((c) => String(c[0]));
    expect(urls.filter((u) => /briefing|ddd\/brains/.test(u))).toEqual([]);
  });

  it('an empty queue shows one calm line', async () => {
    fetchAttention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
    renderWelcome();
    expect(await screen.findByTestId('welcome-needs-empty')).toBeTruthy();
    expect(screen.queryByTestId('welcome-needs-row')).toBeNull();
  });

  it('a failed read hides the section (the screen still works as a chat start)', async () => {
    fetchAttention.mockRejectedValue(new Error('down'));
    renderWelcome();
    await waitFor(() => expect(fetchAttention).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('welcome-needs')).toBeNull();
    expect(screen.getByTestId('welcome-wordmark')).toBeTruthy();
  });
});
