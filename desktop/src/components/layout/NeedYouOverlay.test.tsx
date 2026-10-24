/**
 * Tests for NeedYouOverlay's data path (docs/design/left-nav-overlay-finish.md §1.3).
 *
 * Need You opens from the AlertsPill, not a nav card, so the nav contract test does
 * not cover its fetching. Verified here, against a mocked attentionService:
 * - the first load failing shows the shared error state with a Retry, and Retry
 *   fetches again and shows the items;
 * - reopening inside the freshness window renders from the cache (no new GET);
 * - prefetchNav(qc, 'needs-you') fetches the queue, so an open after hover is instant.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

const fetchAttention = vi.fn();
vi.mock('../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: (...a: unknown[]) => fetchAttention(...a) },
}));

import { NeedYouContent } from './NeedYouOverlay';
import { prefetchNav } from './overlayQueries';

const ITEM = {
  id: 'a1', tier: 'blocking', source: 'job', brain: null, title: 'Job X failed',
  detail: '', dispatch: { message: 'look at job X' },
};
const ok = { items: [ITEM], counts: { blocking: 1, review: 0 } };

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
const freshQc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('NeedYouOverlay — data', () => {
  beforeEach(() => fetchAttention.mockReset());

  it('first load fails → error with Retry; Retry fetches again and shows items', async () => {
    fetchAttention.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(ok);
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    const retry = await screen.findByTestId('needyou-load-retry');
    expect(screen.getByTestId('needyou-load-error')).toHaveTextContent(/attention queue/i);
    fireEvent.click(retry);
    expect(await screen.findByText('Job X failed')).toBeInTheDocument();
    expect(fetchAttention).toHaveBeenCalledTimes(2);
  });

  it('reopening inside the freshness window renders from cache (no new GET)', async () => {
    fetchAttention.mockResolvedValue(ok);
    const qc = freshQc();
    const first = render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(qc) });
    await screen.findByText('Job X failed');
    first.unmount();
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(qc) });
    expect(screen.getByText('Job X failed')).toBeInTheDocument();
    expect(fetchAttention).toHaveBeenCalledTimes(1);
  });

  it("prefetchNav(qc, 'needs-you') fetches the queue before the open", async () => {
    fetchAttention.mockResolvedValue(ok);
    const qc = freshQc();
    prefetchNav(qc, 'needs-you');
    await waitFor(() => expect(fetchAttention).toHaveBeenCalledTimes(1));
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(qc) });
    expect(await screen.findByText('Job X failed')).toBeInTheDocument();
    expect(fetchAttention).toHaveBeenCalledTimes(1);
  });
});

describe('NeedYouOverlay — row actions (same as Next steps)', () => {
  beforeEach(() => fetchAttention.mockReset());

  it('row text fills the chat input with the prompt (not sent) and closes', async () => {
    fetchAttention.mockResolvedValue(ok);
    const onDispatch = vi.fn(() => true);
    const close = vi.fn();
    render(<NeedYouContent onDispatch={onDispatch} close={close} />, { wrapper: wrap(freshQc()) });
    fireEvent.click(await screen.findByTestId('needs-you-row-fill'));
    expect(onDispatch).toHaveBeenCalledWith('look at job X', { autoSend: false });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('↑ sends the prompt now and closes', async () => {
    fetchAttention.mockResolvedValue(ok);
    const onDispatch = vi.fn(() => true);
    const close = vi.fn();
    render(<NeedYouContent onDispatch={onDispatch} close={close} />, { wrapper: wrap(freshQc()) });
    fireEvent.click(await screen.findByTestId('needs-you-row-send'));
    expect(onDispatch).toHaveBeenCalledWith('look at job X', { autoSend: true });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('stays open when the prompt could not land (all tabs busy)', async () => {
    fetchAttention.mockResolvedValue(ok);
    const close = vi.fn();
    render(<NeedYouContent onDispatch={() => false} close={close} />, { wrapper: wrap(freshQc()) });
    fireEvent.click(await screen.findByTestId('needs-you-row-send'));
    expect(close).not.toHaveBeenCalled();
  });

  it('shows the summary under the title', async () => {
    fetchAttention.mockResolvedValue({ ...ok, items: [{ ...ITEM, summary: 'X · failed 7 runs in a row' }] });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    expect(await screen.findByText('X · failed 7 runs in a row')).toBeInTheDocument();
  });
});
