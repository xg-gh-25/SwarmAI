/**
 * The usage view (design docs/design/2026-10-29-usage-accounting.md §5, §11c).
 *
 * Questions: (1) does the top line show the window's spend and its change against the
 * window before, and a group with tokens but no price read "cost unknown", never $0;
 * (2) is the pipeline row shown apart as a split of chat, not as a surface of its own;
 * (3) does the budget line show this month's platform spend against the budget, say
 * what stops when it is reached, save a new budget and refuse one that is not above 0;
 * (4) does each of the largest rows open what it names (a session, a run's report, the
 * jobs); (5) does the view say what it cannot see (no Hive reporting, unpriced calls);
 * (6) does a new window ask for that window.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../../i18n';
import { usageService, type UsageGroup, type UsageReport, type UsageTotal } from '../../services/usage';
import { chatService } from '../../services/chat';
import { UsageView } from './UsageView';
import { change, rowCost } from './format';

const openOverlay = vi.fn();
vi.mock('../../contexts/OverlayContext', () => ({ useOverlay: () => ({ openOverlay }) }));
vi.mock('../../services/chat', () => ({ chatService: { getSession: vi.fn() } }));
vi.mock('../../services/usage', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  const usageService = { report: vi.fn(), setBudget: vi.fn() };
  return {
    usageService,
    useUsageReport: (w: string) => useQuery({ queryKey: ['usage-report', w], queryFn: () => usageService.report(w) }),
  };
});

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

const tot = (o: Partial<UsageTotal> = {}): UsageTotal => ({
  cost_usd: 0, billed: 0, input: 0, output: 0, cache_read: 0, cache_write: 0, cache_hit_rate: null,
  rows: 0, calls: 0, unpriced_calls: 0, unpriced_tokens: 0, ...o,
});
const grp = (key: string, o: Partial<UsageGroup> = {}): UsageGroup => ({ key, ...tot(o), ...o });

function report(o: Partial<UsageReport> = {}): UsageReport {
  const chat = grp('chat', { cost_usd: 2481.26, billed: 5_858_671_499, cache_hit_rate: 0.9828, rows: 638, calls: 638 });
  const cdf = grp('cdf', { billed: 10_405_034, rows: 3, calls: 3, unpriced_calls: 3, unpriced_tokens: 10_405_034, cache_hit_rate: 0 });
  return {
    window: '7d', since: '2026-10-23T16:00:00+00:00', today: '2026-10-30',
    total: tot({ cost_usd: 2481.26, billed: 5_869_076_533, cache_hit_rate: 0.98, rows: 641, calls: 641 }),
    previous: tot({ cost_usd: 2693.19, rows: 900 }), days: 7,
    daily: [
      { day: '2026-10-23', cost_usd: 100, by_surface: { chat: 100 }, in_window: false },
      { day: '2026-10-30', cost_usd: 872.6, by_surface: { chat: 800, pipeline: 72.6 }, in_window: true },
    ],
    breakdown: { surface: [chat, cdf], feature: [grp('cdf.s2', { billed: 5, unpriced_tokens: 5 })], model: [chat], machine: [chat] },
    attribution: { ...tot({ cost_usd: 40, billed: 9_000_000 }), runs: 2 },
    turns: { turns: 638, p50: 1.75, p90: 8.86, max: 50.1, buckets: [
      { from: 0, to: 1, turns: 197, cost_usd: 86.87, share: 0.035 }, { from: 1, to: 5, turns: 304, cost_usd: 710.58, share: 0.2864 },
      { from: 5, to: 15, turns: 103, cost_usd: 811.91, share: 0.3272 }, { from: 15, to: null, turns: 34, cost_usd: 871.89, share: 0.3514 }] },
    top: {
      sessions: [grp('sess-1', { cost_usd: 559.06, calls: 119, title: '整理 SwarmAI 的 TECH' })],
      runs: [grp('run_1', { cost_usd: 30, calls: 4, title: '走 pipeline 实现 Hive tab', report_path: 'Projects/SwarmAI/.artifacts/runs/run_1/REPORT.md' })],
      jobs: [grp('morning-inbox', { cost_usd: 3.5, calls: 2 })],
      hive: [],
    },
    concentration: { top_sessions: 8, top_sessions_share: 0.846, big_turn_usd: 15, big_turns: 34, big_turns_turn_share: 0.0533, big_turns_cost_share: 0.3514 },
    gaps: { unpriced: [{ surface: 'cdf', calls: 3, tokens: 10_405_034 }], imported_cost_usd: 0, imported_calls: 0, live_since: null, hives_reporting: [] },
    budget: { month_spend_usd: 8907.65, monthly_usd: 12000, share: 0.7423 },
    ...o,
  };
}

const fn = <T,>(f: T) => f as unknown as ReturnType<typeof vi.fn>;

function view(props: Parameters<typeof UsageView>[0] = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><UsageView {...props} /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  fn(usageService.report).mockResolvedValue(report());
});

describe('usage view', () => {
  it('(1) shows the window spend and its change, and an unpriced group as cost unknown', async () => {
    view();
    expect((await screen.findByTestId('usage-total')).textContent).toBe('$2,481');
    expect(screen.getByTestId('usage-change').textContent).toBe('↓ 8% 比上一段（$2,693）');
    const rows = screen.getAllByTestId('usage-where-row');
    expect(within(rows[1]).getByText('花费未知')).toBeTruthy();
    expect(rows[1].textContent).not.toContain('$0');
    expect(change(110, 100)).toEqual({ dir: 'up', pct: 10 });
    expect(change(5, 0)).toBeNull();
    expect(rowCost({ cost_usd: 0, unpriced_tokens: 3, billed: 3 })).toBeNull();
    expect(rowCost({ cost_usd: 0, unpriced_tokens: 0, billed: 0 })).toBe('$0.00');
  });

  it('(1) shows a window whose tokens have no price as cost unknown, not $0', async () => {
    fn(usageService.report).mockResolvedValue(report({ total: tot({ billed: 9, rows: 1, calls: 1, unpriced_calls: 1, unpriced_tokens: 9 }) }));
    view();
    expect((await screen.findByTestId('usage-total')).textContent).toBe('花费未知');
  });

  it('(3) keeps the budget in reach when the window has no usage', async () => {
    fn(usageService.report).mockResolvedValue(report({ total: tot() }));
    view();
    await screen.findByTestId('usage-empty');
    expect(screen.getByTestId('usage-budget-edit')).toBeTruthy();
  });

  it('(2) shows the pipeline as a split of chat, apart from the surfaces', async () => {
    view();
    const a = await screen.findByTestId('usage-attribution');
    expect(a.textContent).toContain('不进总数');
    expect(screen.getAllByTestId('usage-where-row').map((r) => r.firstChild?.textContent)).toEqual(['chat', 'cdf']);
    fireEvent.click(screen.getByRole('tab', { name: '按功能' }));
    expect(screen.queryByTestId('usage-attribution')).toBeNull();
  });

  it('(3) holds this month\'s platform spend to the budget and saves a new one', async () => {
    fn(usageService.setBudget).mockResolvedValue(15000);
    view();
    const b = await screen.findByTestId('usage-budget');
    expect(b.textContent).toContain('本月全平台 $8,908 / 预算 $12,000（74%）');
    expect(b.textContent).toContain('只停新的 agent job');
    fireEvent.click(screen.getByTestId('usage-budget-edit'));
    fireEvent.change(screen.getByTestId('usage-budget-input'), { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('usage-budget-save'));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(usageService.setBudget).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId('usage-budget-input'), { target: { value: '15000' } });
    fireEvent.click(screen.getByTestId('usage-budget-save'));
    await waitFor(() => expect(usageService.setBudget).toHaveBeenCalledWith(15000));
  });

  it('(3) says the budget is reached when it is', async () => {
    fn(usageService.report).mockResolvedValue(report({ budget: { month_spend_usd: 13000, monthly_usd: 12000, share: 1.0833 } }));
    view();
    expect((await screen.findByTestId('usage-budget')).textContent).toContain('已到预算');
  });

  it('(4) opens what each of the largest rows names', async () => {
    const session = { id: 'sess-1' };
    fn(chatService.getSession).mockResolvedValue(session);
    const resume = vi.fn(() => true);
    const close = vi.fn();
    const opened: string[] = [];
    const onFile = (e: Event) => opened.push((e as CustomEvent).detail.path);
    document.addEventListener('swarm:open-file', onFile);
    view({ resumeSession: resume, close });
    fireEvent.click((await screen.findAllByTestId('usage-top-open'))[0]);
    await waitFor(() => expect(resume).toHaveBeenCalledWith(session));
    expect(close).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'pipeline run' }));
    expect(screen.getByText('走 pipeline 实现 Hive tab')).toBeTruthy();
    fireEvent.click(screen.getByTestId('usage-top-open'));
    expect(opened).toEqual(['Projects/SwarmAI/.artifacts/runs/run_1/REPORT.md']);
    fireEvent.click(screen.getByRole('tab', { name: 'job' }));
    fireEvent.click(screen.getByTestId('usage-top-open'));
    expect(openOverlay).toHaveBeenCalledWith('jobs');
    document.removeEventListener('swarm:open-file', onFile);
  });

  it('(5) says what it cannot see', async () => {
    view();
    const g = await screen.findByTestId('usage-gaps');
    expect(g.textContent).toContain('cdf：3 次调用、10.4M token 没有花费');
    expect(g.textContent).toContain('Hive 还没有上报');
    expect(screen.getByTestId('usage-scope').textContent).toContain('Hive 还没上报');
  });

  it('(6) asks for the window chosen', async () => {
    view();
    await screen.findByTestId('usage-total');
    fireEvent.click(screen.getByRole('tab', { name: '近 30 天' }));
    await waitFor(() => expect(usageService.report).toHaveBeenLastCalledWith('30d'));
  });
});
