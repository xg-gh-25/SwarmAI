/**
 * EvalDashboard GoldenSetTab integration — summary chips, chip-click→filter,
 * and tier filter dropdown.
 *
 * Invariants under test:
 *  - the summary chip row renders counts derived from the fetched cases[]
 *  - clicking a Category chip narrows the table to that category
 *  - the Tier filter dropdown narrows the table to the chosen tier
 *  - chip counts reflect the FULL set (not the filtered view) so the user can
 *    always see the whole distribution to drill into
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GoldenSetTab } from '../EvalDashboard';

const mockGet = vi.fn();
vi.mock('../../services/api', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const CASES = [
  { id: 'a', category: 'compliance', dimension: 'compliance', level: 'session', title: 'Comp A', tier: 'active', eval_method: 'llm', _origin: 'public', evaluators: [], affected_by: [], last_result: { status: 'passed', run_id: 'r', triggered_at: 't' } },
  { id: 'b', category: 'compliance', dimension: 'capability', level: 'session', title: 'Comp B', tier: 'stable', eval_method: 'programmatic', _origin: 'private', evaluators: [], affected_by: [], last_result: { status: 'failed', run_id: 'r', triggered_at: 't' } },
  { id: 'c', category: 'decision', dimension: 'judgment_quality', level: 'session', title: 'Dec C', tier: 'active', eval_method: 'llm', _origin: 'private', evaluators: [], affected_by: [], last_result: null },
  { id: 'd', category: 'recall', dimension: 'utility', level: 'trace', title: 'Rec D', tier: 'draft', _origin: 'public', evaluators: [], affected_by: [], last_result: null },
];

const GS_RESPONSE = {
  total_cases: 4,
  filtered_count: 4,
  categories: ['compliance', 'decision', 'recall'],
  dimensions: ['compliance', 'capability', 'judgment_quality', 'utility'],
  cases: CASES,
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GoldenSetTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith('/eval/golden-set')) return Promise.resolve({ data: GS_RESPONSE });
    return Promise.resolve({ data: {} });
  });
});

describe('GoldenSetTab summary + filters', () => {
  it('renders a summary section with category counts from the data', async () => {
    renderTab();
    const summary = await screen.findByTestId('golden-summary');
    // compliance appears twice → chip shows "2"
    const compChip = within(summary).getByTestId('chip-category-compliance');
    expect(compChip).toHaveTextContent('compliance');
    // exact count assertion (substring '2' would also pass on 12/20 — guard against that)
    expect(within(summary).getByTestId('chip-category-compliance-count')).toHaveTextContent(/^2$/);
    // surfaces the data-driven (non-enum) dimension value "utility"
    expect(within(summary).getByTestId('chip-dimension-utility')).toBeInTheDocument();
  });

  it('clicking a Category chip narrows the table to that category', async () => {
    renderTab();
    const summary = await screen.findByTestId('golden-summary');
    // before: all 4 rows present
    expect(screen.getByText('Dec C')).toBeInTheDocument();
    fireEvent.click(within(summary).getByTestId('chip-category-compliance'));
    // after: only compliance rows (a, b) — decision/recall gone
    expect(screen.getByText('Comp A')).toBeInTheDocument();
    expect(screen.getByText('Comp B')).toBeInTheDocument();
    expect(screen.queryByText('Dec C')).not.toBeInTheDocument();
    expect(screen.queryByText('Rec D')).not.toBeInTheDocument();
  });

  it('tier filter dropdown narrows the table to the chosen tier', async () => {
    renderTab();
    await screen.findByTestId('golden-summary');
    const tierSelect = screen.getByTestId('filter-tier');
    fireEvent.change(tierSelect, { target: { value: 'draft' } });
    // only the draft case (d) remains
    expect(screen.getByText('Rec D')).toBeInTheDocument();
    expect(screen.queryByText('Comp A')).not.toBeInTheDocument();
    expect(screen.queryByText('Dec C')).not.toBeInTheDocument();
  });

  it('chip counts reflect the full set even after a filter is applied', async () => {
    renderTab();
    const summary = await screen.findByTestId('golden-summary');
    fireEvent.click(within(summary).getByTestId('chip-category-decision'));
    // table now shows only decision, but the compliance chip still shows 2
    expect(within(summary).getByTestId('chip-category-compliance-count')).toHaveTextContent(/^2$/);
  });
});

describe('GoldenSetTab grouping + origin badge (run_1f588e53)', () => {
  it('groups cases into collapsible category sections', async () => {
    renderTab();
    await screen.findByTestId('golden-set-groups');
    // 3 category groups present (compliance, decision, recall)
    expect(screen.getByTestId('cat-group-compliance')).toBeInTheDocument();
    expect(screen.getByTestId('cat-group-decision')).toBeInTheDocument();
    expect(screen.getByTestId('cat-group-recall')).toBeInTheDocument();
  });

  it('collapsing a category group hides its rows but keeps the header', async () => {
    renderTab();
    await screen.findByTestId('golden-set-groups');
    expect(screen.getByText('Comp A')).toBeInTheDocument();
    // click the compliance group header button (first button in the group = header;
    // subsequent buttons are per-row archive actions)
    const group = screen.getByTestId('cat-group-compliance');
    fireEvent.click(within(group).getAllByRole('button')[0]);
    // rows gone, header button (with count) stays
    expect(screen.queryByText('Comp A')).not.toBeInTheDocument();
    expect(within(group).getAllByRole('button')[0]).toBeInTheDocument();
    // other groups unaffected
    expect(screen.getByText('Dec C')).toBeInTheDocument();
  });

  it('renders public/private origin badges distinguishing curated vs instance', async () => {
    renderTab();
    await screen.findByTestId('golden-set-groups');
    // public + private both appear as badge text (Comp A=public, Comp B=private)
    expect(screen.getAllByText('public').length).toBeGreaterThan(0);
    expect(screen.getAllByText('private').length).toBeGreaterThan(0);
  });

  it('filtering to a category shows only that group (grouping agrees with filter)', async () => {
    renderTab();
    const summary = await screen.findByTestId('golden-summary');
    fireEvent.click(within(summary).getByTestId('chip-category-decision'));
    // only the decision group remains — compliance/recall groups absent (zero-count hidden)
    expect(screen.getByTestId('cat-group-decision')).toBeInTheDocument();
    expect(screen.queryByTestId('cat-group-compliance')).not.toBeInTheDocument();
    expect(screen.queryByTestId('cat-group-recall')).not.toBeInTheDocument();
  });
});
