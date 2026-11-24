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
import en from '../../i18n/locales/en.json';

// The real English strings, so the test reads what an English-locale user sees.
// Plural keys (_one / _other) and {{name}} interpolation as i18next does them.
vi.mock('react-i18next', () => {
  const look = (key: string) => key.split('.').reduce<unknown>(
    (node, part) => (node as Record<string, unknown> | undefined)?.[part], en) as string | undefined;
  return {
    useTranslation: () => ({
      t: (key: string, o?: Record<string, unknown>) => {
        const raw = (typeof o?.count === 'number' ? look(`${key}_${o.count === 1 ? 'one' : 'other'}`) : undefined)
          ?? look(key) ?? (o?.defaultValue as string | undefined) ?? key;
        return typeof raw === 'string' ? raw.replace(/\{\{(\w+)\}\}/g, (_, n: string) => String(o?.[n] ?? '')) : key;
      },
    }),
  };
});

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

describe('NeedYouOverlay — labels', () => {
  it('in the English locale both tabs are English', () => {
    fetchAttention.mockResolvedValue(ok);
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    expect(screen.getByTestId('need-you-tab')).toHaveTextContent('Needs you');
    expect(screen.getByTestId('cdf-tab')).toHaveTextContent('Cognition flow');
  });
});

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

describe('NeedYouOverlay — for you, and what Swarm handles itself', () => {
  beforeEach(() => fetchAttention.mockReset());
  const PERSON = { ...ITEM, id: 'p1', source: 'governance', title: 'A rule proposal', summary: 'One dispatch for independent work',
    detail: 'Seen 3 times', handler: 'person', since: null, dispatch: { message: 'explain the rule', context: { kind: 'governance' } } };
  const AGENT = { ...ITEM, id: 'g1', handler: 'agent', summary: 'Evening scan · failed 3 runs in a row', detail: 'timeout',
    dispatch: { message: 'fix the job', context: { kind: 'job' } } };
  const AGENT2 = { ...AGENT, id: 'g2', summary: 'Morning scan', title: 'A digest is ready', detail: '',
    dispatch: { message: 'learn the digest', context: { kind: 'job_digest' } } };
  const both = { items: [PERSON, AGENT, AGENT2], counts: { blocking: 2, review: 1, person: 1, agent: 2 } };

  it("a person's item is a card titled by the thing, with its ask; the tab counts only the person's", async () => {
    fetchAttention.mockResolvedValue(both);
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    const card = await screen.findByTestId('needs-you-card');
    expect(card).toHaveTextContent('One dispatch for independent work');
    expect(card).toHaveTextContent('Seen 3 times');
    expect(screen.getByTestId('needs-you-ask')).toHaveTextContent(en.needYou.ask.governance);
    expect(screen.getAllByTestId('needs-you-card')).toHaveLength(1);
    expect(screen.getByTestId('need-you-tab-count')).toHaveTextContent('1');
    expect(screen.getAllByTestId('needs-you-swarm-row')).toHaveLength(2);
  });

  it('the card sends its prompt now or fills the input, then closes; it stays open when nothing landed', async () => {
    fetchAttention.mockResolvedValue(both);
    const onDispatch = vi.fn(() => true);
    const close = vi.fn();
    const view = render(<NeedYouContent onDispatch={onDispatch} close={close} />, { wrapper: wrap(freshQc()) });
    fireEvent.click(await screen.findByTestId('needs-you-send'));
    expect(onDispatch).toHaveBeenLastCalledWith('explain the rule', { autoSend: true });
    fireEvent.click(screen.getByTestId('needs-you-fill'));
    expect(onDispatch).toHaveBeenLastCalledWith('explain the rule', { autoSend: false });
    expect(close).toHaveBeenCalledTimes(2);
    view.unmount();
    const stay = vi.fn();
    render(<NeedYouContent onDispatch={() => false} close={stay} />, { wrapper: wrap(freshQc()) });
    fireEvent.click(await screen.findByTestId('needs-you-send'));
    expect(stay).not.toHaveBeenCalled();
    expect(screen.getByTestId('needs-you-missed')).toHaveTextContent(en.needYou.notLanded);
  });

  it('a row of Swarm’s hands its own prompt over; "all" hands every one in one message', async () => {
    fetchAttention.mockResolvedValue(both);
    const onDispatch = vi.fn((_p: string, _o: { autoSend: boolean }) => true);
    render(<NeedYouContent onDispatch={onDispatch} close={() => {}} />, { wrapper: wrap(freshQc()) });
    fireEvent.click((await screen.findAllByTestId('needs-you-hand'))[1]);
    expect(onDispatch).toHaveBeenLastCalledWith('learn the digest', { autoSend: true });
    fireEvent.click(screen.getByTestId('needs-you-hand-all'));
    const [msg, opts] = onDispatch.mock.calls[onDispatch.mock.calls.length - 1];
    expect(opts).toEqual({ autoSend: true });
    expect(msg).toContain('fix the job');
    expect(msg).toContain('learn the digest');
    expect(msg).not.toContain('explain the rule');
  });

  it('with nothing for the person: one calm line, no count, and Swarm’s rows still listed', async () => {
    fetchAttention.mockResolvedValue({ items: [AGENT], counts: { blocking: 1, review: 0, person: 0, agent: 1 } });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    expect(await screen.findByTestId('needs-you-calm')).toHaveTextContent(en.needYou.empty);
    expect(screen.queryByTestId('needs-you-card')).toBeNull();
    expect(screen.queryByTestId('need-you-tab-count')).toBeNull();
    expect(screen.getByTestId('needs-you-swarm-row')).toHaveTextContent('Evening scan');
  });

  it('a summary that only repeats the detail gives way to the title', async () => {
    const stall = { ...AGENT, id: 's1', title: 'Cognitive Distillation has stopped moving', summary: 'a job waits 16 h', detail: 'a job waits 16 h' };
    fetchAttention.mockResolvedValue({ items: [stall], counts: { blocking: 0, review: 1, person: 0, agent: 1 } });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap(freshQc()) });
    const row = await screen.findByTestId('needs-you-swarm-row');
    expect(row.querySelector('b')?.textContent).toBe('Cognitive Distillation has stopped moving');
    expect(row.textContent?.match(/a job waits 16 h/g)).toHaveLength(1);
  });
});
