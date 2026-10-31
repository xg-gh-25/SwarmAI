/**
 * Swarm's cognition level in the first Needs You tab (docs/design/2026-10-31-cognition-level.md §6):
 * - it sits under the queue in the first tab;
 * - a trend is claimed only between weeks measured the same way;
 * - the chain shows where mistakes come from; without an audit it says when the first runs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { TFunction } from 'i18next';
import en from '../../i18n/locales/en.json';
import type { CognitionLevelData, LevelWeek } from '../../services/ingestion';

function lookup(key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);
}
const t = ((key: string, values?: Record<string, unknown>) => {
  const found = lookup(key);
  const text = typeof found === 'string' ? found : key;
  return text.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ''));
}) as unknown as TFunction;
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t, i18n: { language: 'en' } }) }));

const cognitionLevel = vi.fn();
vi.mock('../../services/ingestion', async (orig) => {
  const real = await orig<Record<string, unknown>>();
  return { ...real, ingestionService: { ...(real.ingestionService as Record<string, unknown>),
    cognitionLevel: (...a: unknown[]) => cognitionLevel(...a) } };
});
vi.mock('../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: vi.fn(async () => ({ items: [], counts: { blocking: 0, review: 0 } })) },
}));

import { NeedYouContent } from './NeedYouOverlay';
import { levelLine } from './CognitionLevel';

const W = (week: string, rate: number | null, source: LevelWeek['source'], extra: Partial<LevelWeek> = {}): LevelWeek => ({
  week, start: `2026-10-${week.slice(-2)}`, sessions: 10, known: 10, corrected: 0, notCorrectedRate: rate, source,
  corrections: 0, repeats: 0, repeatRate: null, ...extra,
});

const DATA: CognitionLevelData = {
  weeks: [W('W40', null, 'none', { known: 0 }), W('W41', 0.4, 'backfilled'), W('W42', 0.5, 'backfilled'),
    W('W44', 0.6, 'recorded', { corrections: 12, repeats: 2, repeatRate: 0.17 })],
  marks: [], coveredFrom: null, latest: W('W44', 0.6, 'recorded'), earliest: null,
  audit: { month: '2026-10', baseline: false, total: 10,
    outcomes: { 事先没有: 6, 有但没送到: 3, 送到仍犯: 1, 'DDD 内容过时': 0 }, previous: null },
  used: { usedRate: null, matureWritten: 0, matureUsed: 0, windowDays: 14 }, backfillWeeks: 4,
};

function wrap({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}

describe('cognition level', () => {
  beforeEach(() => cognitionLevel.mockReset().mockResolvedValue(DATA));

  it('a trend only between weeks measured the same way', () => {
    expect(levelLine(t, DATA.weeks)).toBe(en.cogLevel.line.notComparable);
    expect(levelLine(t, [W('a', 0.4, 'backfilled'), W('b', 0.5, 'backfilled')])).toBe(en.cogLevel.line.better);
    expect(levelLine(t, [W('a', 0.5, 'recorded'), W('b', 0.4, 'mixed')])).toBe(en.cogLevel.line.notComparable);
    expect(levelLine(t, [W('a', 0.5, 'recorded'), W('b', 0.4, 'recorded')])).toBe(en.cogLevel.line.worse);
    expect(levelLine(t, [W('a', 0.5, 'recorded'), W('b', 0.52, 'recorded')])).toBe(en.cogLevel.line.flat);
  });

  it('sits under the queue in the first tab, with this week and the chain', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const section = await screen.findByTestId('cognition-level');
    expect(screen.getByTestId('needs-you-attention').compareDocumentPosition(section)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(section).toHaveTextContent('60%');
    expect(section).toHaveTextContent('2 / 12');
    const missing = within(screen.getByTestId('level-chain')).getByTestId('level-stage-missing');
    expect(missing).toHaveTextContent('6');
    expect(missing).toHaveTextContent('60%');
  });

  it('says when the first audit runs when there is none', async () => {
    cognitionLevel.mockResolvedValue({ ...DATA, audit: null });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    expect(await screen.findByText(en.cogLevel.chain.none)).toBeInTheDocument();
  });
});
