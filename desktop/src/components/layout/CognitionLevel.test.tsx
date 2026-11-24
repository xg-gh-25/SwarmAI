/**
 * Swarm's cognition level in the first Needs You tab (docs/design/2026-10-31-cognition-level.md §6):
 * - it sits under the queue in the first tab, one number against the four weeks before;
 * - a change is coloured only when the backend's trend says better or worse;
 * - a week whose rules changed carries a mark; repeated corrections are amber;
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
  const plural = typeof values?.count === 'number' ? lookup(`${key}_${values.count === 1 ? 'one' : 'other'}`) : undefined;
  const found = plural ?? lookup(key);
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

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { NeedYouContent } from './NeedYouOverlay';
import { levelLine, trendVerdict } from './cognitionLevelText';
import { mapIngestionValue, type LevelTrend } from '../../services/ingestion';

/** The cases the backend's headline test reads too (backend/tests/test_north_star.py). */
const WORDING = JSON.parse(readFileSync(join(__dirname, '../../../../backend/tests/fixtures/north_star_trend_wording.json'),
  'utf8')) as { cases: Array<{ name: string; trend: unknown; verdict: string }> };
const trendOf = (now: number, before: number, direction: LevelTrend['direction'], margin: number | null = 11): LevelTrend => ({
  weeks: 4, now: { from: 'W15', to: 'W18', judged: 40, corrected: Math.round(40 * now), rate: now },
  before: { from: 'W11', to: 'W14', judged: 40, corrected: Math.round(40 * before), rate: before },
  difference: now - before, low: null, high: null, margin, direction, minJudged: 10,
});

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
  trend: trendOf(0.45, 0.6, 'better'),
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

  it.each(WORDING.cases.map((c) => [c.name, c] as const))('says the verdict the prompt headline says: %s', (_name, c) => {
    expect(trendVerdict(t, mapIngestionValue<LevelTrend>(c.trend))).toBe(c.verdict);
  });

  it('words the backend trend and claims no direction of its own', () => {
    expect(levelLine(t, null)).toBe(t('cogLevel.line.tooFew', { n: 8 }));
    expect(levelLine(t, trendOf(0.4, 0.6, 'better'))).toBe(
      "60% of the last 4 weeks' conversations needed no correction, 40% the 4 weeks before: better");
    expect(levelLine(t, trendOf(0.6, 0.4, 'worse'))).toBe(
      "40% of the last 4 weeks' conversations needed no correction, 60% the 4 weeks before: worse");
    // a 20-point move the backend's interval holds is no visible change, not "better"
    expect(levelLine(t, trendOf(0.4, 0.6, 'same', 21))).toBe(
      "60% of the last 4 weeks' conversations needed no correction, 40% the 4 weeks before: no visible change (±21 points)");
    expect(levelLine(t, trendOf(0.4, 0.6, null, null))).toContain(': too few sessions for a trend');
  });

  it('sits under the queue in the first tab: the trend window\'s share against the 4 weeks before', async () => {
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const section = await screen.findByTestId('cognition-level');
    expect(screen.getByTestId('needs-you-attention').compareDocumentPosition(section)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // the big number is the backend trend's own window (1 - 0.45), not this week
    expect(screen.getByTestId('level-big-value')).toHaveTextContent('55%');
    const cmp = screen.getByTestId('level-compare');
    expect(cmp).toHaveTextContent('▲ 15 points');
    expect(cmp).toHaveTextContent('4 weeks before 40%');
    expect(cmp.querySelector('b')?.getAttribute('style')).toContain('--color-status-success');
    // the prompt's wording, for a screen reader and on hover
    expect(screen.getByTestId('level-line')).toHaveTextContent("55% of the last 4 weeks' conversations needed no correction, 40% the 4 weeks before: better");
  });

  it('claims no change when the backend sees none, even across a gap', async () => {
    cognitionLevel.mockResolvedValue({ ...DATA, trend: trendOf(0.45, 0.6, 'same', 21) });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const cmp = await screen.findByTestId('level-compare');
    expect(cmp).not.toHaveTextContent('▲');
    expect(cmp.querySelector('b')).toBeNull();
    expect(cmp).toHaveTextContent('4 weeks before 40%');
  });

  it('this week, repeated corrections in amber, and a mark on the week whose rules changed', async () => {
    cognitionLevel.mockResolvedValue({ ...DATA, marks: [{ at: '2026-10-18T12:00:00', kind: 'governance', label: 'SOUL' }] });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    const kpis = await screen.findByTestId('level-kpis');
    expect(within(kpis).getByTestId('level-kpis-week')).toHaveTextContent('60%');
    const repeats = within(kpis).getByTestId('level-kpis-repeats');
    expect(repeats).toHaveTextContent('2 / 12');
    expect(repeats.querySelector('div:last-child')?.getAttribute('style')).toContain('--color-status-warning');
    const weeks = screen.getAllByTestId('level-week');
    const marked = weeks.filter((w) => w.querySelector('[data-testid="level-mark"]'));
    expect(marked).toHaveLength(1);
    expect(marked[0].getAttribute('title')).toContain('rules changed: SOUL');
    const missing = within(screen.getByTestId('level-chain')).getByTestId('level-stage-missing');
    expect(missing).toHaveTextContent('6');
    expect(screen.getByTestId('level-chain')).toHaveTextContent('mostly “not known beforehand”, 60%');
  });

  it('says when the first audit runs when there is none', async () => {
    cognitionLevel.mockResolvedValue({ ...DATA, audit: null });
    render(<NeedYouContent onDispatch={() => true} close={() => {}} />, { wrapper: wrap });
    expect(await screen.findByText(en.cogLevel.chain.none)).toBeInTheDocument();
  });
});
