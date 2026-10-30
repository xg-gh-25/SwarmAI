/**
 * Cognitive Distillation Flow overlay tests.
 *
 * Uses mocked service responses to verify tabs, active-only fetching, the list
 * and detail audit views, plus shared loading, empty, and error states.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

const attention = vi.fn();
const flow = vi.fn();
const items = vi.fn();
const detail = vi.fn();
const health = vi.fn();
const stats = vi.fn();

vi.mock('../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: (...args: unknown[]) => attention(...args) },
}));
vi.mock('../../services/ingestion', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  ingestionService: {
    flow: (...args: unknown[]) => flow(...args), items: (...args: unknown[]) => items(...args),
    detail: (...args: unknown[]) => detail(...args), health: (...args: unknown[]) => health(...args),
    stats: (...args: unknown[]) => stats(...args),
  },
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  t: (key: string, values?: Record<string, string>) => values ? `${key}:${Object.values(values).join('/')}` : key,
}) }));

import { NeedYouContent } from './NeedYouOverlay';

const overview = { window: '24h', since: '', totalUnits: 1, denominatorUnits: 1,
  writtenRate: 1, rejectedRate: 0, stages: { inbox: 1, card_0: 1, write: 1 }, lanes: { immediate: 1 },
  sources: { remember: 1 }, states: { written: 1 }, reasons: {} };
const itemPage = { items: [{ id: 'c1', createdAt: '2026-10-27T00:00:00Z', source: 'remember', lane: 'immediate',
  state: 'done', unitStates: ['written'], stores: ['MEMORY'], reasons: [], preview: 'Keep this lesson' }],
  page: 1, pageSize: 25, total: 1, hasNext: false, windowDays: 30 };
const healthData = { lanes: { immediate: { backlog: 0, oldestWaitSeconds: null } }, events: [], breakers: [],
  lastCleanupAt: null, inboxCount: 1, inboxBytes: 100, memoryBytes: 20, evolutionBytes: 30,
  llmBySource: [{ utcDay: '2026-10-27', source: 'S2', calls: 3, inputTokens: 2400, outputTokens: 310 }],
  review: { agentSelfWritten: 2, agentSelfWithdrawn: 1, agentSelfWithdrawnRate: 0.5, byType: {
    code: { reviews: 4, units: 9, invalid: 1 },
    files: { reviews: 2, units: 3, invalid: 0 },
    conversation: { reviews: 5, units: 7, invalid: 2 } } } };
const detailData = { candidate: { payload: { text: 'raw lesson' } }, envelope: { tainted: false },
  units: [{ unitId: 'c1.lesson', claimGroup: { text: 'lesson' }, decision: { card0: { passed: true } },
    destination: { store: 'MEMORY' }, action: 'append', reason: 'pass' }],
  writes: [{ step: 'done' }], relations: [] };

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function defaults() {
  attention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
  flow.mockResolvedValue(overview); items.mockResolvedValue(itemPage); detail.mockResolvedValue(detailData);
  health.mockResolvedValue(healthData); stats.mockResolvedValue({ days: 365, rows: [] });
}

beforeEach(() => {
  for (const mock of [attention, flow, items, detail, health, stats]) mock.mockReset();
  defaults();
});

describe('Need You Flow tabs', () => {
  it('renders role tabs and fetches only the active attention tab initially', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrapper() });
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    await waitFor(() => expect(attention).toHaveBeenCalledOnce());
    expect(flow).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('cdf-tab'));
    await waitFor(() => expect(flow).toHaveBeenCalledOnce());
    expect(screen.getByTestId('cdf-flow')).toBeInTheDocument();
    expect(await screen.findByText('2400')).toBeInTheDocument();
    // after-session review metrics, per session type (session review §12)
    expect(screen.getByText('写过非代码文件')).toBeInTheDocument();
    expect(screen.queryByText('抽样漏记率')).not.toBeInTheDocument();   // no second extraction to sample
    expect(screen.getByText(/Agent 自身认知写入 EVOLUTION \/ MEMORY：2，被撤回：1（50%）/)).toBeInTheDocument();
  });

  it('renders the candidate and its full detail after selection', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByTestId('cdf-tab'));
    fireEvent.click(await screen.findByText('Keep this lesson'));
    expect(await screen.findByText(/raw lesson/)).toBeInTheDocument();
    expect(screen.getByTestId('cdf-detail')).toHaveTextContent('card0');
    expect(screen.getByTestId('cdf-detail')).toHaveTextContent('done');
  });

  it('uses the shared empty state', async () => {
    items.mockResolvedValue({ ...itemPage, items: [], total: 0 });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByTestId('cdf-tab'));
    expect(await screen.findByTestId('cdf-items-empty')).toBeInTheDocument();
  });

  it('uses shared loading and error states', async () => {
    let reject!: (error: Error) => void;
    items.mockReturnValue(new Promise((_, no) => { reject = no; }));
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByTestId('cdf-tab'));
    expect(await screen.findByTestId('cdf-items-loading')).toBeInTheDocument();
    reject(new Error('down'));
    expect(await screen.findByTestId('cdf-items-error')).toBeInTheDocument();
  });
});
