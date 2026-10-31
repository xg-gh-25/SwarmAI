/**
 * The Health tab (docs/design/2026-10-31-cdf-health.md §9.9), against a fixture of
 * GET /ingestion/cognition-health:
 * - it sits between Cognition flow and How it works;
 * - the three numbers read against something, a check with nothing to measure says
 *   "no data yet" and is not counted as passing;
 * - a sampled entry's verdict buttons send the person's verdict;
 * - the reading shows in the user's language with its basis.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import en from '../../i18n/locales/en.json';
import type { CognitionHealth } from '../../services/ingestion';

function lookup(key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);
}
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en' },
    t: (key: string, values?: Record<string, unknown>) => {
      const found = lookup(key);
      const text = typeof found === 'string' ? found : String(values?.defaultValue ?? key);
      return text.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ''));
    },
  }),
}));

const cognitionHealth = vi.fn();
const setHealthVerdict = vi.fn();
vi.mock('../../services/ingestion', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return {
    ...real,
    ingestionService: {
      ...(real.ingestionService as Record<string, unknown>),
      cognitionHealth: (...a: unknown[]) => cognitionHealth(...a),
      setHealthVerdict: (...a: unknown[]) => setHealthVerdict(...a),
      recheckHealth: vi.fn(async () => ({ started: true })),
    },
  };
});
vi.mock('../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: vi.fn(async () => ({ items: [], counts: { blocking: 0, review: 0 } })) },
}));

import { NeedYouContent } from './NeedYouOverlay';

const DEST = (key: string, group: 'every_turn' | 'on_demand' | 'other', extra: Partial<CognitionHealth['value']['destinations'][0]> = {}) => ({
  key, group, target: group === 'every_turn' ? 0.95 : 0.9, scored: 0, useful: 0, weak: 0, withdraw: 0,
  rate: null, written: 0, trend: [], samples: [], ...extra,
});

const FIXTURE: CognitionHealth = {
  window: '7d', generatedAt: '2026-10-31T09:00:00+00:00', running: false,
  value: {
    rate: 0.85, target: 0.9, scored: 20, trend: [],
    destinations: [
      DEST('MEMORY', 'every_turn'), DEST('EVOLUTION', 'every_turn'), DEST('USER_FILES', 'every_turn'),
      DEST('TIMELINE', 'every_turn'),
      DEST('DDD', 'on_demand', {
        scored: 20, useful: 17, weak: 1, withdraw: 2, rate: 0.85, written: 40,
        samples: [{ unitId: 'cand-1.unit', title: 'A status note', project: 'P', verdict: 'withdraw',
          klass: 'status', reasonEn: 'Only progress.', reasonZh: '只是进度。', userVerdict: null,
          writtenAt: '2026-10-31T08:00:00+00:00' }],
      }),
      DEST('GENERAL', 'on_demand'), DEST('GAP', 'other'), DEST('REFERENCE', 'other'),
    ],
  },
  used: { usedRate: null, matureWritten: 0, matureUsed: 0, windowDays: 14 },
  checks: [
    { id: 'C1', status: 'pass', value: { missing: 0, sessions: 5 }, requirement: '0' },
    { id: 'C3', status: 'fail', value: { invalid: 7, units: 100 }, requirement: '<= 5%' },
    { id: 'C10', status: 'no_data', value: { used: 0, written: 0 }, requirement: '> 0' },
  ],
  checksTrend: [],
  cost: { cdfUsd: 120, systemUsd: 1000, share: 0.12, unpricedCalls: 0, perUsefulUsd: 1.5,
    features: [{ feature: 'cdf.s7', usd: 118, calls: 40, avgUsd: 2.95 }],
    days: [{ day: '2026-10-30', systemUsd: 600, cdfUsd: 70 }, { day: '2026-10-31', systemUsd: 400, cdfUsd: 50 }] },
  reading: { en: { seen: ['Most entries hold.'], why: ['Written at review time.'], suggest: ['Ask if it still holds.'] },
    zh: { seen: ['大多数有用。'], why: ['复盘时写入。'], suggest: ['多问一句。'] },
    at: '2026-10-31T09:00:00+00:00', inputHash: 'h', model: 'm', basis: { scored: 20, checks: 3, window: '7d' } },
  readingFailed: false,
  agreement: { judgedByPerson: 0, agreed: 0, rate: null },
};

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('Health tab', () => {
  beforeEach(() => {
    cognitionHealth.mockReset().mockResolvedValue(FIXTURE);
    setHealthVerdict.mockReset().mockResolvedValue({ unitId: 'cand-1.unit', userVerdict: 'useful' });
  });

  it('sits between Cognition flow and How it works', () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const tabs = screen.getAllByRole('tab').map((tab) => tab.getAttribute('data-testid'));
    expect(tabs).toEqual(['need-you-tab', 'cdf-tab', 'cdf-health-tab', 'cdf-guide-tab']);
  });

  it('reads each number against something and never counts no data as a pass', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    fireEvent.click(screen.getByTestId('cdf-health-tab'));
    const line = await screen.findByTestId('health-line');
    expect(line).toHaveTextContent('1 check(s) are failing');
    expect(line).toHaveTextContent('15% of what was kept is not useful');
    const page = screen.getByTestId('cdf-health');
    expect(page).toHaveTextContent('1 / 2');                      // passed of measured, not of 3
    expect(page).toHaveTextContent('1 with no data yet');
    expect(screen.getByTestId('health-check-C10')).toHaveTextContent('no data yet');
    expect(screen.getByTestId('health-check-C3')).toHaveTextContent('7 of 100 units refused as invalid');
    expect(page).toHaveTextContent('12% of the system');
    expect(page).toHaveTextContent('≈ $1.5 per useful entry');
  });

  it("sends the person's verdict on a sampled entry", async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    fireEvent.click(screen.getByTestId('cdf-health-tab'));
    fireEvent.click(await screen.findByTestId('health-dest-DDD'));
    const samples = screen.getByTestId('health-samples-DDD');
    expect(within(samples).getByText('A status note')).toBeInTheDocument();
    expect(samples).toHaveTextContent('Only progress.');
    fireEvent.click(screen.getByTestId('health-verdict-cand-1.unit-useful'));
    await waitFor(() => expect(setHealthVerdict).toHaveBeenCalledWith('cand-1.unit', 'useful'));
  });

  it('shows the reading in the user language with its basis', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    fireEvent.click(screen.getByTestId('cdf-health-tab'));
    const reading = await screen.findByTestId('health-reading');
    expect(reading).toHaveTextContent('Most entries hold.');
    expect(reading).toHaveTextContent('Ask if it still holds.');
    expect(reading).toHaveTextContent('Based on 20 scored entries and 3 checks.');
    expect(reading).not.toHaveTextContent('大多数有用');
  });
});
