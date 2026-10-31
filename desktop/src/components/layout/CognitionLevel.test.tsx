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
import { levelLine, rolling } from './cognitionLevelText';

const W = (week: string, rate: number | null, judged = 10, extra: Partial<LevelWeek> = {}): LevelWeek => ({
  week, start: `2026-10-${week.slice(-2)}`, sessions: judged, judged,
  corrected: rate == null ? 0 : Math.round(judged * (1 - rate)), notCorrectedRate: rate,
  corrections: 0, repeats: 0, repeatRate: null, recordCorrected: 0, recordClean: 0, ...extra,
});
const eight = (first: number, last: number) => [
  ...[1, 2, 3, 4].map((i) => W(`W1${i}`, first)), ...[5, 6, 7, 8].map((i) => W(`W1${i}`, last))];

const DATA: CognitionLevelData = {
  weeks: [W('W40', null, 0), ...eight(0.4, 0.6).slice(0, 7),
    W('W18', 0.6, 10, { corrections: 12, repeats: 2, repeatRate: 0.17 })],
  marks: [], coveredFrom: null, latest: null, earliest: null,
  audit: { month: '2026-10', baseline: false, total: 10,
    outcomes: { 事先没有: 6, 有但没送到: 3, 送到仍犯: 1, 'DDD 内容过时': 0 }, previous: null },
  used: { usedRate: null, matureWritten: 0, matureUsed: 0, windowDays: 14 },
  agreement: { correctedTotal: 14, correctedAgreed: 13, cleanTotal: 0, cleanAgreed: 0 },
};

function wrap({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}

describe('cognition level', () => {
  beforeEach(() => cognitionLevel.mockReset().mockResolvedValue(DATA));

  it('the trend pools four weeks against the four before', () => {
    expect(levelLine(t, eight(0.4, 0.6).slice(0, 7))).toBe(t('cogLevel.line.tooFew', { n: 8 }));
    expect(levelLine(t, eight(0.4, 0.6))).toBe(t('cogLevel.line.better', { now: '60%', before: '40%' }));
    expect(levelLine(t, eight(0.6, 0.4))).toBe(t('cogLevel.line.worse', { now: '40%', before: '60%' }));
    expect(levelLine(t, eight(0.5, 0.5))).toBe(t('cogLevel.line.flat', { now: '50%', before: '50%' }));
    // weighted by conversations: a 2-conversation week cannot swing the share
    const r = rolling([W('a', 0.5, 20), W('b', 0.5, 20), W('c', 0.5, 20), W('d', 0, 2)]);
    expect(r[3]).toBeCloseTo(1 - (30 + 2) / 62, 5);
    expect(r[2]).toBeNull();
  });

  it('sits under the queue in the first tab, with this week and the chain', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const section = await screen.findByTestId('cognition-level');
    expect(screen.getByTestId('needs-you-attention').compareDocumentPosition(section)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(section).toHaveTextContent('60%');
    expect(section).toHaveTextContent('2 / 12');
    expect(screen.getByTestId('level-agreement')).toHaveTextContent('of 14 conversations it recorded a correction in, the judge found 13');
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
