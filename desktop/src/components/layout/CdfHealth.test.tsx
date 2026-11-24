/**
 * The Health tab (docs/design/2026-10-31-cdf-health.md §9.9), against a fixture of
 * GET /ingestion/cognition-health:
 * - it sits between Cognition flow and How it works;
 * - one number against its target, three numbers beside it; a check with nothing to
 *   measure is not counted as passing;
 * - what to fix: each failing check and each place below its target, handed to Swarm with
 *   the judge's suggestions;
 * - a sampled entry's verdict buttons send the person's verdict;
 * - the reading opens on demand, in the user's language with its basis; an old check says so.
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
      const plural = typeof values?.count === 'number' ? lookup(`${key}_${values.count === 1 ? 'one' : 'other'}`) : undefined;
      const found = plural ?? lookup(key);
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
      DEST('GENERAL', 'on_demand'), DEST('GAP', 'other', { scored: 5, useful: 2, weak: 3, rate: 0.4, written: 9 }),
      DEST('REFERENCE', 'other'),
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

  it('reads the number against its target and never counts no data as a pass', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    fireEvent.click(screen.getByTestId('cdf-health-tab'));
    expect(await screen.findByTestId('health-rate-value')).toHaveTextContent('85%');
    expect(screen.getByTestId('health-target')).toHaveTextContent('5 points below · target 90%');
    const kpis = screen.getByTestId('health-kpis');
    expect(within(kpis).getByTestId('health-kpis-checks')).toHaveTextContent('1/2');   // passed of measured, not of 3
    expect(within(kpis).getByTestId('health-kpis-cost')).toHaveTextContent('$120');
    expect(within(kpis).getByTestId('health-kpis-cost')).toHaveTextContent('12% of all');
    expect(within(kpis).getByTestId('health-kpis-perUseful')).toHaveTextContent('$1.5');
    const checks = screen.getByTestId('health-checks');
    expect(checks).toHaveTextContent('1 other check passed');
    expect(checks).toHaveTextContent('1 without data');
    expect(screen.queryByTestId('health-check-C10')).toBeNull();
    fireEvent.click(screen.getByTestId('health-checks-toggle'));
    expect(screen.getByTestId('health-check-C10')).toHaveTextContent('no data yet');
    // one pass and one without data: the line is not green
    expect(screen.getByTestId('health-checks-dot').getAttribute('style')).not.toContain('--color-status-success');
    // a check run a while ago says so
    expect(screen.getByTestId('health-stamp').getAttribute('data-stale')).toBe('true');
    // the reading waits behind its toggle
    expect(screen.queryByTestId('health-reading')).toBeNull();
  });

  it('lists what to fix (the failing check, the place below target) and hands it to Swarm', async () => {
    const onDispatch = vi.fn((_p: string, _o: { autoSend: boolean }) => true);
    render(<NeedYouContent onDispatch={onDispatch} close={() => {}} />, { wrapper: wrap });
    fireEvent.click(screen.getByTestId('cdf-health-tab'));
    const check = await screen.findByTestId('health-fix-check-C3');
    expect(check).toHaveTextContent('7 of 100 units refused as invalid');
    const place = screen.getByTestId('health-fix-dest-DDD');
    expect(place).toHaveTextContent('85%');
    expect(place).toHaveTextContent('20 scored · weak 1 · withdraw 2');
    // too few samples (here 5 scored at 40%) is never a problem to fix
    expect(screen.queryByTestId('health-fix-dest-GAP')).toBeNull();
    expect(screen.queryByTestId('health-fix-dest-MEMORY')).toBeNull();
    fireEvent.click(within(check).getByRole('button'));
    const [msg, opts] = onDispatch.mock.calls[0];
    expect(opts).toEqual({ autoSend: true });
    expect(msg).toContain('7 of 100 units refused as invalid');
    expect(msg).toContain('Ask if it still holds.');
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
    fireEvent.click(await screen.findByTestId('health-reading-toggle'));
    const reading = screen.getByTestId('health-reading');
    expect(reading).toHaveTextContent('Most entries hold.');
    expect(reading).toHaveTextContent('Ask if it still holds.');
    expect(reading).toHaveTextContent('Based on 20 scored entries and 3 checks.');
    expect(reading).not.toHaveTextContent('大多数有用');
  });
});
