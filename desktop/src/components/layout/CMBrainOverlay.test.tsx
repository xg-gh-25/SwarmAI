/**
 * Tests for CMBrainOverlay — the Context & Memory overlay (design Knowledge/Mockups/
 * 2026-11-24-cm-library-redesign.html): four tabs 上下文 · 记忆 · 知识 · 工作原理.
 *
 * The backend is mocked at the api boundary (services/api); the tests assert the overlay
 * CONSUMES the payloads (files, entries, recall counts) and invents no numbers, and that a
 * tab whose first load is pending shows the shared loading state, never a blank panel.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor, within, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
import api from '../../services/api';
import { CMBrainContent } from './CMBrainOverlay';

const get = api.get as ReturnType<typeof vi.fn>;
const post = api.post as ReturnType<typeof vi.fn>;

// per_file in priority order, as /eval/context-health/lite serves it
const PER_FILE = [
  { name: 'SWARMAI.md', tokens: 6287, disk_tokens: 6459, pct: 8.7, owner: 'system', priority: 0, locked: true, health: 'fresh', health_counts: null },
  { name: 'IDENTITY.md', tokens: 358, disk_tokens: 481, pct: 0.5, owner: 'system', priority: 1, locked: true, health: 'idle', health_counts: null },
  { name: 'SELF.md', tokens: 4991, disk_tokens: 5440, pct: 6.9, owner: 'system', priority: 2, locked: true, health: 'fresh', health_counts: null },
  { name: 'SOUL.md', tokens: 6404, disk_tokens: 6483, pct: 8.9, owner: 'system', priority: 2, locked: true, health: 'fresh', health_counts: null },
  { name: 'AGENT.md', tokens: 15488, disk_tokens: 15761, pct: 21.5, owner: 'system', priority: 3, locked: false, health: 'fresh', health_counts: null },
  { name: 'USER.md', tokens: 1133, disk_tokens: 1139, pct: 1.6, owner: 'user', priority: 4, locked: false, health: 'idle', health_counts: null },
  { name: 'STEERING.md', tokens: 5015, disk_tokens: 5052, pct: 7.0, owner: 'user', priority: 5, locked: false, health: 'fresh', health_counts: null },
  { name: 'TOOLS.md', tokens: 7470, disk_tokens: 7501, pct: 10.4, owner: 'user', priority: 6, locked: false, health: 'fresh', health_counts: null },
  { name: 'MEMORY.md', tokens: 15269, disk_tokens: 15999, pct: 21.2, owner: 'agent', priority: 7, locked: false, health: 'fresh', health_counts: { entries: 63, duplicate: 0 } },
  { name: 'EVOLUTION.md', tokens: 8190, disk_tokens: 8337, pct: 11.4, owner: 'agent', priority: 8, locked: false, health: 'fresh', health_counts: { entries: 18, duplicate: 0 } },
  { name: 'TIMELINE.md', tokens: 587, disk_tokens: 71064, pct: 0.8, owner: 'user', priority: 10, locked: false, health: 'fresh', health_counts: null },
];
const TOTAL = PER_FILE.reduce((n, f) => n + f.tokens, 0); // 71192
const BLOCK = { total_tokens: TOTAL, disk_tokens: 144415, budget: 133000, warning_threshold: 133000,
  emergency_threshold: 172000, over_budget: false, per_file: PER_FILE };
const NORTH_STAR = { at: '2026-11-24T08:43:06+00:00', measures: { cdf: {
  every_turn: { MEMORY: [15269, 15018], EVOLUTION: [8190, 7673] }, every_turn_days: { MEMORY: 7, EVOLUTION: 7 } } } };
const withReadings = (cdf: Record<string, unknown>) => ({ north_star: { ...NORTH_STAR, measures: { cdf } } });

const GOV = [
  { id: 'GC14', gc_id: 'GC14', occurrence_count: 3, proposed_rule: 'Give a concurrent plan before dispatching', checked: 'checked against: R16b' },
];

const today = new Date();
const iso = (daysAgo: number) => new Date(today.getTime() - daysAgo * 86400_000).toISOString().slice(0, 10);
const MEM_ENTRIES = [
  { title: 'Newest guideline', meta: '', type: 'guideline', date: iso(2) },
  { title: 'A pitfall one', meta: '', type: 'pitfall', date: iso(20) },
  { title: 'A pitfall two', meta: '', type: 'pitfall', date: iso(21) },
  { title: 'A pitfall three', meta: '', type: 'pitfall', date: iso(22) },
  { title: 'A principle', meta: '', type: 'principle', date: iso(30) },
  { title: 'A pitfall four', meta: '', type: 'pitfall', date: iso(40) },
  { title: 'A pitfall five', meta: '', type: 'pitfall', date: iso(50) },
  { title: 'Oldest pitfall', meta: '', type: 'pitfall', date: iso(60) },
];
const MEM_GRAPH = { nodes: [{ type: 'principle', count: 1 }, { type: 'guideline', count: 1 }, { type: 'pitfall', count: 6 }],
  entries: MEM_ENTRIES, total: 8, drill: {} };
const EVO_GRAPH = {
  nodes: [{ type: 'correction', count: 2 }, { type: 'competence', count: 1 }],
  entries: [
    { title: 'C103 | x', meta: '', type: 'correction', date: iso(0), id: 'C103', label: 'Next-step proposals stay one-by-one' },
    { title: 'K007 | x', meta: '', type: 'competence', date: iso(22), id: 'K007', label: 'Knowledge quality comes from extraction' },
    { title: 'C098 | x', meta: '', type: 'correction', date: iso(22), id: 'C098', label: 'Whoever dispatched judges' },
  ],
  total: 3, drill: {},
};
const KNOWLEDGE = {
  window_days: 7,
  contexts: {
    session_prompt: { runs: 102, hits: 90, prev_runs: 65, prev_hits: 59 },
    session_followup: { runs: 247, hits: 213, prev_runs: 158, prev_hits: 135 },
    session_ddd: { runs: 77, hits: 52, prev_runs: 38, prev_hits: 30 },
  },
  stores: { projects: { brains: 9, entries: 5727 }, general: { files: 104 }, sessions: 289, archives: { files: 13, entries: 902 } },
};
const LANDED = {
  window: '7d', since: '', total: 1921, series: [], previous: null,
  totals: { new: 0, adds: 0, reinforced: 0, not_kept: 0, waiting: 0 }, groups: {}, reasons: {}, sources: {},
  steps: { captured: 1921, basic_dropped: 0, place_dropped: 0, moved: 0, reinforced: 0, known: 0, withdrawn: 0, older: 0, written: 1828 },
  landed: [
    { store: 'DDD', project: 'SwarmAI', path: 'a', written: 1617, reinforced: 0 },
    { store: 'MEMORY', project: null, path: 'b', written: 4, reinforced: 0 },
    { store: 'EVOLUTION', project: null, path: 'c', written: 2, reinforced: 0 },
    // only reinforced this week: not a file written into
    { store: 'GENERAL', project: null, path: 'd', written: 0, reinforced: 5 },
  ],
  moved: [], attention: [], store_bytes: 0, oldest_at: null,
};

interface Opts { lite?: Record<string, unknown>; gov?: unknown[]; memHits?: unknown[]; govPromise?: Promise<unknown> }
function mockAll(opts: Opts = {}) {
  const lite = { token_block: BLOCK, governance_pending_count: (opts.gov ?? []).length, north_star: NORTH_STAR, ...opts.lite };
  get.mockImplementation((url: string) => {
    if (url.includes('context-health/lite')) return Promise.resolve({ data: lite });
    if (url.includes('governance/pending')) {
      const data = { proposals: opts.gov ?? [], total: (opts.gov ?? []).length };
      return opts.govPromise ? opts.govPromise.then(() => ({ data })) : Promise.resolve({ data });
    }
    if (url.includes('brain-graph')) return Promise.resolve({ data: url.includes('evolution') ? EVO_GRAPH : MEM_GRAPH });
    if (url.includes('archive-list')) {
      return Promise.resolve({ data: url.includes('evolution')
        ? { files: [{ name: 'EVOLUTION-archive-2026-11.md', entry_count: 100 }], total_files: 1 }
        : { files: [{ name: 'MEMORY-archive-2026-10.md', entry_count: 700 }, { name: 'MEMORY-archive-2026-09.md', entry_count: 102 }], total_files: 2 } });
    }
    if (url.includes('archive-search')) {
      return Promise.resolve({ data: { results: url.includes('source=memory') ? (opts.memHits ?? []) : [] } });
    }
    if (url.includes('/recall/knowledge')) return Promise.resolve({ data: KNOWLEDGE });
    if (url.includes('/ingestion/landed')) return Promise.resolve({ data: LANDED });
    return Promise.reject(new Error(`unmocked ${url}`));
  });
  post.mockResolvedValue({ data: { status: 'ok' } });
}

function renderOverlay(onOpenOverlay?: (id: 'library' | 'brain-hub') => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><CMBrainContent onOpenOverlay={onOpenOverlay} /></QueryClientProvider>);
}
function openTab(id: string) { act(() => { screen.getByTestId(`cm-tab-${id}`).click(); }); }
function captureOpenFile() {
  const seen: string[] = [];
  const h = (e: Event) => seen.push((e as CustomEvent).detail.path);
  document.addEventListener('swarm:open-file', h);
  return { seen, stop: () => document.removeEventListener('swarm:open-file', h) };
}

beforeEach(() => mockAll());
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('shell', () => {
  it('has four tabs and no left rail; Context is open first', async () => {
    renderOverlay();
    for (const id of ['context', 'memory', 'knowledge', 'how']) expect(screen.getByTestId(`cm-tab-${id}`)).toBeInTheDocument();
    expect(screen.queryByTestId('cm-tab-evolution')).toBeNull();
    expect(screen.queryByTestId('cm-tab-guideline')).toBeNull();
    expect(screen.queryByTestId('cm-overview-rail')).toBeNull();
    expect(await screen.findByTestId('cm-panel-context')).toBeInTheDocument();
  });

  it('first paint reads the lite endpoint only (no governance list, no heavy context-health)', async () => {
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    const urls = get.mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes('/eval/context-health/lite'))).toBe(true);
    expect(urls.some((u) => /\/eval\/context-health(?!\/lite)/.test(u))).toBe(false);
    expect(urls.some((u) => u.includes('governance/pending'))).toBe(false);
  });
});

describe('上下文 — the load', () => {
  it('one number against the budget, a bar split by owner, a legend with each group total', async () => {
    renderOverlay();
    expect((await screen.findByTestId('cm-load-big-value')).textContent).toBe('71K');
    expect(screen.getByTestId('cm-load-budget').textContent).toContain('54% of the 133K budget');
    expect(screen.getAllByTestId(/^cm-budget-seg-/).map((s) => s.dataset.testid)).toEqual([
      'cm-budget-seg-system', 'cm-budget-seg-user', 'cm-budget-seg-agent']);
    const legend = screen.getByTestId('cm-load-legend').textContent ?? '';
    expect(legend).toContain('Built in33.5K');
    expect(legend).toContain('Yours14.2K');
    expect(legend).toContain('Swarm learned23.5K');
  });

  it('no amber card when nothing waits on the person and the load is under budget', async () => {
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(screen.queryByTestId('cm-cards')).toBeNull();
    expect(screen.queryByTestId('cm-budget-alert')).toBeNull();
  });

  it('a load error says so with Retry, never a zero', async () => {
    get.mockRejectedValue(new Error('down'));
    renderOverlay();
    expect((await screen.findByTestId('cm-needs-error')).textContent).toMatch(/nothing to do/);
    expect(screen.queryByTestId('cm-load-big')).toBeNull();
  });
});

describe('上下文 — the files grouped by owner', () => {
  it('groups agent · user · system, each with its total; the agent group with its 7-days-ago total', async () => {
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    const groups = screen.getAllByTestId(/^cm-group-(agent|user|system|auto)$/).map((g) => g.dataset.testid);
    expect(groups).toEqual(['cm-group-agent', 'cm-group-user', 'cm-group-system']);
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K · 7 days ago 22.7K');
    expect(screen.getByTestId('cm-group-total-user').textContent).toBe('14.2K');
  });

  it('rows are sorted by tokens within a group, rendered from per_file (no hardcoded list)', async () => {
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    const user = within(screen.getByTestId('cm-group-user')).getAllByTestId(/^cm-file-row-/).map((r) => r.dataset.testid);
    expect(user).toEqual(['cm-file-row-TOOLS.md', 'cm-file-row-STEERING.md', 'cm-file-row-USER.md', 'cm-file-row-TIMELINE.md']);
    expect(screen.getAllByTestId(/^cm-file-row-/)).toHaveLength(PER_FILE.length);
    expect(screen.queryByTestId('cm-file-row-KNOWLEDGE.md')).toBeNull();
  });

  it('a row shows its role, tokens, its 7-day change, the file size only when 10x, a lock when locked', async () => {
    renderOverlay();
    const mem = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem).getByTestId('cm-file-role').textContent).toBe('63 entries · principles, decisions, lessons');
    expect(within(mem).getByTestId('cm-file-tokens').textContent).toBe('15.3K');
    expect(within(mem).getByTestId('cm-file-delta').textContent).toBe('+0.3K');
    expect(within(mem).queryByTestId('cm-file-disk')).toBeNull();
    const tl = screen.getByTestId('cm-file-row-TIMELINE.md');
    expect(within(tl).getByTestId('cm-file-disk').textContent).toBe('file 71K');
    expect(within(tl).queryByTestId('cm-file-delta')).toBeNull();
    expect(within(screen.getByTestId('cm-file-row-SWARMAI.md')).getByTestId('cm-lock')).toBeInTheDocument();
    expect(within(screen.getByTestId('cm-file-row-AGENT.md')).queryByTestId('cm-lock')).toBeNull();
    // dropped: priority column, share bar + %, health badges
    expect(mem.textContent).not.toMatch(/P7|21\.2%|fresh/);
  });

  it('an unknown file has no role label; another owner is its own group', async () => {
    mockAll({ lite: { token_block: { ...BLOCK, per_file: [...PER_FILE,
      { name: 'EXTRA.md', tokens: 900, disk_tokens: 900, pct: 1, owner: 'auto', priority: 11, locked: false, health_counts: null }] } } });
    renderOverlay();
    const row = await screen.findByTestId('cm-file-row-EXTRA.md');
    expect(within(row).queryByTestId('cm-file-role')).toBeNull();
    expect(within(screen.getByTestId('cm-group-auto')).getAllByTestId(/^cm-file-row-/)).toHaveLength(1);
  });

  it('repeated entries show only when there are some', async () => {
    mockAll({ lite: { token_block: { ...BLOCK, per_file: PER_FILE.map((f) => (f.name === 'MEMORY.md'
      ? { ...f, health_counts: { entries: 63, duplicate: 2 } } : f)) } } });
    renderOverlay();
    const dup = await screen.findByTestId('cm-health-counts-MEMORY.md');
    expect(dup.textContent).toBe('2 repeated');
    // a health signal, nothing to decide: not amber
    expect(dup.className).not.toContain('status-warning');
    expect(screen.queryByTestId('cm-health-counts-EVOLUTION.md')).toBeNull();
  });

  it('the whole row opens the file, locked ones too', async () => {
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    const cap = captureOpenFile();
    try {
      act(() => { screen.getByTestId('cm-file-row-MEMORY.md').click(); });
      act(() => { screen.getByTestId('cm-file-row-SWARMAI.md').click(); });
    } finally { cap.stop(); }
    expect(cap.seen).toEqual(['.context/MEMORY.md', '.context/SWARMAI.md']);
  });

  it('a group shows its 7-days-ago total only when every file in it has one', async () => {
    mockAll({ lite: withReadings({ every_turn: { MEMORY: [15269, 15018] }, every_turn_days: { MEMORY: 7 } }) });
    renderOverlay();
    const mem = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem).getByTestId('cm-file-delta').textContent).toBe('+0.3K');
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K');
  });

  it('the earlier reading is called by its real age; one day on record, or no age served, shows none', async () => {
    // 3 days on record: "3 days ago", never "7 days ago"
    mockAll({ lite: withReadings({ every_turn: NORTH_STAR.measures.cdf.every_turn, every_turn_days: { MEMORY: 3, EVOLUTION: 3 } }) });
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K · 3 days ago 22.7K');
    cleanup();
    // one day on record (span 0): the older value is today's, no delta and no "days ago"
    mockAll({ lite: withReadings({ every_turn: { MEMORY: [15269, 15269], EVOLUTION: [8190, 8190] }, every_turn_days: { MEMORY: 0, EVOLUTION: 0 } }) });
    renderOverlay();
    const mem = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem).queryByTestId('cm-file-delta')).toBeNull();
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K');
    cleanup();
    // a snapshot from before the ages were served: no 7-day numbers at all
    mockAll({ lite: withReadings({ every_turn: { MEMORY: [15269, 15018], EVOLUTION: [8190, 7673] } }) });
    renderOverlay();
    const mem2 = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem2).queryByTestId('cm-file-delta')).toBeNull();
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K');
    cleanup();
    // two ages in one group: no group total "N days ago" (which N?), each row keeps its delta
    mockAll({ lite: withReadings({ every_turn: NORTH_STAR.measures.cdf.every_turn, every_turn_days: { MEMORY: 7, EVOLUTION: 2 } }) });
    renderOverlay();
    const mem3 = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem3).getByTestId('cm-file-delta').textContent).toBe('+0.3K');
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K');
  });

  it('a generated file has its own colour, not a second grey beside the built-in files', async () => {
    mockAll({ lite: { token_block: { ...BLOCK, per_file: [...PER_FILE,
      { name: 'EXTRA.md', tokens: 900, disk_tokens: 900, pct: 1, owner: 'auto', priority: 11, locked: false, health_counts: null }] } } });
    renderOverlay();
    const auto = await screen.findByTestId('cm-budget-seg-auto');
    const system = screen.getByTestId('cm-budget-seg-system');
    expect(auto.style.background).toContain('--line-judge');
    expect(auto.style.background).not.toBe(system.style.background);
  });
  it('without the north star readings no 7-day numbers are shown', async () => {
    mockAll({ lite: { north_star: null } });
    renderOverlay();
    const mem = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(within(mem).queryByTestId('cm-file-delta')).toBeNull();
    expect(screen.getByTestId('cm-group-total-agent').textContent).toBe('23.5K');
  });
});

describe('上下文 — amber cards', () => {
  it('a governance card appears only when one is pending, fetched lazily, with its decisions', async () => {
    mockAll({ gov: GOV });
    renderOverlay();
    const card = await screen.findByTestId('cm-proposal-GC14');
    expect(card.textContent).toContain('A new rule: Give a concurrent plan before dispatching');
    expect(card.textContent).toContain('needs your approval');
    expect(card.textContent).toContain('checked against: R16b · seen 3×');
    act(() => { within(card).getByTestId('cm-card-defer').click(); });
    await waitFor(() => expect(post).toHaveBeenCalledWith('/eval/governance/decision', { proposal_id: 'GC14', decision: 'defer' }));
  });

  it('a pending count whose list comes back empty leaves no blank band', async () => {
    mockAll({ lite: { governance_pending_count: 1 } });
    renderOverlay();
    await screen.findByTestId('cm-file-row-MEMORY.md');
    await waitFor(() => expect(get.mock.calls.some((c) => String(c[0]).includes('governance/pending'))).toBe(true));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('cm-cards')).toBeNull();
  });

  it('a refused decision shows the backend reason and keeps its buttons', async () => {
    mockAll({ gov: GOV });
    post.mockRejectedValue({ response: { status: 400, data: { detail: 'GC14 holds a phrase the memory guard blocks' } } });
    renderOverlay();
    const card = await screen.findByTestId('cm-proposal-GC14');
    act(() => { within(card).getByTestId('cm-card-accept').click(); });
    expect((await screen.findByTestId('cm-card-refused')).textContent).toContain('memory guard blocks');
    expect(within(card).getByTestId('cm-card-accept')).toBeInTheDocument();
  });

  it('over the budget: an amber card naming the biggest injected files, each one click open', async () => {
    mockAll({ lite: { token_block: { ...BLOCK, budget: 60000, over_budget: true } } });
    renderOverlay();
    const card = await screen.findByTestId('cm-budget-alert');
    expect(card.textContent).toContain('Over the budget by 11.2K');
    expect(within(card).getByTestId('cm-budget-open-AGENT.md')).toBeInTheDocument();
    expect(within(card).getByTestId('cm-budget-open-MEMORY.md')).toBeInTheDocument();
    expect(within(card).queryByTestId('cm-budget-open-TIMELINE.md')).toBeNull();
    // nothing pending: no governance list is fetched and no rule card shows
    expect(get.mock.calls.some((c) => String(c[0]).includes('governance/pending'))).toBe(false);
    expect(screen.queryByTestId(/^cm-proposal-/)).toBeNull();
  });
});

describe('记忆', () => {
  async function openMemory() {
    renderOverlay();
    openTab('memory');
    return screen.findByTestId('cm-panel-memory');
  }

  it('MEMORY: count, size against 7 days ago, a type bar, the newest six first, then all', async () => {
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-memory');
    expect((await within(zone).findByTestId('cm-memory-big-value')).textContent).toBe('8entries');
    expect(within(zone).getByTestId('cm-memory-size').textContent).toBe('15.3K tokens · 7 days ago 15.0K');
    const rows = within(zone).getAllByTestId('cm-entry-memory');
    expect(rows).toHaveLength(6);
    expect(rows[0].textContent).toContain('Newest guideline');
    expect(rows[5].textContent).toContain('A pitfall four');
    act(() => { within(zone).getByTestId('cm-more-memory').click(); });
    expect(within(zone).getAllByTestId('cm-entry-memory')).toHaveLength(8);
  });

  it('a type chip filters the list to that type; clicking it again clears', async () => {
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-memory');
    const chip = await within(zone).findByTestId('cm-chip-memory-principle');
    act(() => { chip.click(); });
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(within(zone).getAllByTestId('cm-entry-memory').map((r) => r.textContent)).toEqual([expect.stringContaining('A principle')]);
    act(() => { chip.click(); });
    expect(within(zone).getAllByTestId('cm-entry-memory')).toHaveLength(6);
  });

  it('EVOLUTION: corrections and competences, newest first, ids shown, +n this week', async () => {
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-evolution');
    expect((await within(zone).findByTestId('cm-evolution-big-value')).textContent).toBe('3entries');
    expect(within(zone).getByTestId('cm-evolution-fresh').textContent).toBe('+1 this week');
    expect(within(zone).getByTestId('cm-chip-evolution-competence').textContent).toContain('competence');
    const rows = within(zone).getAllByTestId('cm-entry-evolution');
    expect(rows[0].textContent).toContain('C103 · Next-step proposals stay one-by-one');
    // MEMORY shows no weekly chip
    expect(screen.queryByTestId('cm-memory-fresh')).toBeNull();
  });

  it('an entry opens its file', async () => {
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-evolution');
    const row = (await within(zone).findAllByTestId('cm-entry-evolution'))[0];
    const cap = captureOpenFile();
    try { act(() => { row.click(); }); } finally { cap.stop(); }
    expect(cap.seen).toEqual(['.context/EVOLUTION.md']);
  });

  it('the search box says how much archive it covers; a query searches both files and both archives', async () => {
    mockAll({ memHits: [{ title: 'Old pitfall in an archive', snippet: 'body', source_file: '.context/Archives/MEMORY-archive-2026-10.md', shard: 'MEMORY-archive-2026-10.md' }] });
    await openMemory();
    expect((await screen.findByTestId('cm-memory-search-hint')).textContent).toBe('incl. 3 archives · 902 entries');
    await screen.findAllByTestId('cm-entry-memory');
    await act(async () => { fireEvent.change(screen.getByTestId('cm-memory-search'), { target: { value: 'pitfall' } }); });
    // results replace the two zones
    expect(screen.queryByTestId('cm-zone-memory')).toBeNull();
    expect((await screen.findAllByTestId('cm-hit-main')).length).toBe(6);
    const arch = await screen.findByTestId('cm-hit-archive', undefined, { timeout: 2000 });
    expect(arch.textContent).toContain('Old pitfall in an archive');
    const urls = get.mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes('archive-search?source=memory&q=pitfall'))).toBe(true);
    expect(urls.some((u) => u.includes('archive-search?source=evolution&q=pitfall'))).toBe(true);
    const cap = captureOpenFile();
    try { act(() => { arch.click(); }); } finally { cap.stop(); }
    expect(cap.seen).toEqual(['.context/MEMORY-archive-2026-10.md']);
    act(() => { screen.getByTestId('cm-memory-search-clear').click(); });
    expect(await screen.findByTestId('cm-zone-memory')).toBeInTheDocument();
  });

  it('Esc in the search box clears the query and stays in the overlay', async () => {
    await openMemory();
    await screen.findAllByTestId('cm-entry-memory');
    const box = screen.getByTestId('cm-memory-search') as HTMLInputElement;
    await act(async () => { fireEvent.change(box, { target: { value: 'pitfall' } }); });
    expect(screen.queryByTestId('cm-zone-memory')).toBeNull();
    // handled (default prevented), so the overlay's Escape does not close it
    expect(fireEvent.keyDown(box, { key: 'Escape' })).toBe(false);
    expect(box.value).toBe('');
    expect(await screen.findByTestId('cm-zone-memory')).toBeInTheDocument();
    // an empty box lets Escape through
    expect(fireEvent.keyDown(box, { key: 'Escape' })).toBe(true);
  });

  it('while a new query waits for its debounce, the old query\'s archive hits are gone', async () => {
    mockAll({ memHits: [{ title: 'Old pitfall in an archive', snippet: 'body', source_file: '', shard: 'MEMORY-archive-2026-10.md' }] });
    await openMemory();
    await screen.findAllByTestId('cm-entry-memory');
    await act(async () => { fireEvent.change(screen.getByTestId('cm-memory-search'), { target: { value: 'pitfall' } }); });
    await screen.findByTestId('cm-hit-archive', undefined, { timeout: 2000 });
    await act(async () => { fireEvent.change(screen.getByTestId('cm-memory-search'), { target: { value: 'principle' } }); });
    expect(screen.queryByTestId('cm-hit-archive')).toBeNull();
  });

  it('an entry with an id and no title of its own shows the id once', async () => {
    const base = get.getMockImplementation() as (url: string) => Promise<unknown>;
    get.mockImplementation((url: string) => (url.includes('brain-graph?source=evolution')
      ? Promise.resolve({ data: { nodes: [{ type: 'competence', count: 1 }], total: 1, drill: {},
        entries: [{ title: 'K009 | 2026-11-20', meta: '', type: 'competence', date: '2026-11-20', id: 'K009', label: '' }] } })
      : base(url)));
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-evolution');
    const row = (await within(zone).findAllByTestId('cm-entry-evolution'))[0];
    expect(row.textContent).toContain('K009');
    expect(row.textContent).not.toContain('K009 ·');
    expect(row.textContent).not.toContain('|');
  });

  it('a pitfall is not a second blue beside a guideline in the type bar', async () => {
    await openMemory();
    const zone = await screen.findByTestId('cm-zone-memory');
    const tint = (type: string) => (within(zone).getByTestId(`cm-chip-memory-${type}`).querySelector('i') as HTMLElement).style.background;
    await within(zone).findByTestId('cm-chip-memory-pitfall');
    expect(tint('pitfall')).not.toBe(tint('guideline'));
    expect(tint('pitfall')).not.toMatch(/primary|zone-work|status-warning/);
  });

  it('no match says so', async () => {
    await openMemory();
    await screen.findAllByTestId('cm-entry-memory');
    await act(async () => { fireEvent.change(screen.getByTestId('cm-memory-search'), { target: { value: 'zzqx' } }); });
    expect((await screen.findByTestId('cm-search-empty', undefined, { timeout: 2000 })).textContent).toContain('zzqx');
  });

  it('dropped: the bubble graph, the by-type bars, the size trend, the archive file list', async () => {
    const panel = await openMemory();
    await screen.findAllByTestId('cm-entry-memory');
    expect(screen.queryAllByTestId(/^cm-graph-node-|^cm-bar-|^cm-trend|^cm-archive-list-/)).toHaveLength(0);
    expect(panel.textContent).not.toMatch(/How it works:|collecting since/i);
  });
});

describe('知识', () => {
  it('the first-recall hit rate against last week, the three recalls, the stores with their links', async () => {
    const onOpen = vi.fn();
    renderOverlay(onOpen);
    openTab('knowledge');
    expect((await screen.findByTestId('cm-recall-big-value')).textContent).toBe('88%');
    expect(screen.getByTestId('cm-recall-compare').textContent).toBe('90 / 102 this week · last week 91%');
    expect(screen.getByTestId('cm-leg-value-session_prompt').textContent).toBe('90 / 102');
    expect(screen.getByTestId('cm-leg-value-session_followup').textContent).toBe('213 / 247');
    expect(screen.getByTestId('cm-leg-value-session_ddd').textContent).toBe('52 / 77');
    expect(screen.getByTestId('cm-store-projects').textContent).toContain('Project Brains · 9');
    expect(screen.getByTestId('cm-store-projects').textContent).toContain('5727 entries');
    expect(screen.getByTestId('cm-store-general').textContent).toContain('104 files');
    expect(screen.getByTestId('cm-store-sessions').textContent).toContain('289 sessions');
    expect(screen.getByTestId('cm-store-archives').textContent).toContain('13 files · 902 entries');
    act(() => { screen.getByTestId('cm-store-projects').click(); });
    act(() => { screen.getByTestId('cm-store-general').click(); });
    expect(onOpen.mock.calls).toEqual([['brain-hub'], ['library']]);
    act(() => { screen.getByTestId('cm-store-archives').click(); });
    expect(await screen.findByTestId('cm-panel-memory')).toBeInTheDocument();
  });

  it('a store that could not be read shows a dash, not 0; no recall yet is said in words', async () => {
    get.mockImplementation((url: string) => url.includes('/recall/knowledge')
      ? Promise.resolve({ data: { ...KNOWLEDGE, contexts: {}, stores: { ...KNOWLEDGE.stores, sessions: null } } })
      : Promise.resolve({ data: { token_block: BLOCK, governance_pending_count: 0 } }));
    renderOverlay();
    openTab('knowledge');
    expect(await screen.findByTestId('cm-recall-none')).toBeInTheDocument();
    expect(screen.getByTestId('cm-store-sessions').textContent).toContain('—');
    expect(screen.getByTestId('cm-store-sessions').textContent).not.toMatch(/\b0\b/);
  });
});

describe('工作原理', () => {
  it('five layers in order; ① lists the files in per_file priority order with live size', async () => {
    renderOverlay();
    openTab('how');
    await screen.findByTestId('cm-panel-how');
    const layers = within(screen.getByTestId('cm-how-layers')).getAllByTestId(/^cm-layer-/).map((l) => l.dataset.testid);
    expect(layers).toEqual(['cm-layer-1', 'cm-layer-2', 'cm-layer-3', 'cm-layer-4', 'cm-layer-5']);
    const order = await screen.findByTestId('cm-how-order');
    expect(order.textContent).toBe('In order: SWARMAI → IDENTITY → SELF · SOUL → AGENT → USER → STEERING → TOOLS → MEMORY → EVOLUTION → TIMELINE');
    expect(screen.getByTestId('cm-layer-1').textContent).toContain('71K / 133K');
    expect(screen.getByTestId('cm-layer-1').textContent).toContain('TIMELINE only its summaries');
  });

  it('the order follows per_file, not a fixed list', async () => {
    mockAll({ lite: { token_block: { ...BLOCK, per_file: [PER_FILE[8], PER_FILE[0]].map((f, i) => ({ ...f, priority: i })) } } });
    renderOverlay();
    openTab('how');
    expect((await screen.findByTestId('cm-how-order')).textContent).toBe('In order: MEMORY → SWARMAI');
  });

  it('live numbers: recalls brought back this week, CDF writes and every-turn writes', async () => {
    renderOverlay();
    openTab('how');
    expect((await screen.findByTestId('cm-how-recalled')).textContent).toBe('355 brought back this week');
    await waitFor(() => expect(screen.getByTestId('cm-how-loop').textContent).toContain('1,828 entries into 3 files'));
    expect(screen.getByTestId('cm-how-loop').textContent).toContain('+6 this week');
  });

  it('without the CDF numbers the loop line says what happens, with no numbers', async () => {
    get.mockImplementation((url: string) => (url.includes('/ingestion/landed') ? Promise.reject(new Error('x'))
      : url.includes('/recall/knowledge') ? Promise.resolve({ data: KNOWLEDGE })
        : Promise.resolve({ data: { token_block: BLOCK, governance_pending_count: 0 } })));
    renderOverlay();
    openTab('how');
    const loop = await screen.findByTestId('cm-how-loop');
    await waitFor(() => expect(get.mock.calls.some((c) => String(c[0]).includes('/ingestion/landed'))).toBe(true));
    expect(loop.textContent).not.toMatch(/\d/);
  });

  it('dropped: the lifecycle boxes and the automatic / manual columns', async () => {
    renderOverlay();
    openTab('how');
    await screen.findByTestId('cm-panel-how');
    expect(screen.queryAllByTestId(/^cm-lc-|^cm-guideline-/)).toHaveLength(0);
  });
});

describe('first load pending', () => {
  it('each tab shows the shared loading state while its query is pending, then its data', async () => {
    const waits: Array<() => void> = [];
    const answer = get.getMockImplementation()!;
    // hold every GET until released, then answer as mockAll does
    get.mockImplementation((url: string) => new Promise((resolve, reject) => {
      waits.push(() => { (answer(url) as Promise<unknown>).then(resolve, reject); });
    }));
    renderOverlay();
    const panel = await screen.findByTestId('cm-panel-context');
    expect(within(panel).getByTestId('cm-context-loading')).toHaveTextContent('Loading');
    openTab('memory');
    expect(within(screen.getByTestId('cm-zone-memory')).getByTestId('cm-memory-loading')).toHaveTextContent('Loading');
    expect(within(screen.getByTestId('cm-zone-evolution')).getByTestId('cm-evolution-loading')).toHaveTextContent('Loading');
    openTab('knowledge');
    expect(within(screen.getByTestId('cm-panel-knowledge')).getByTestId('cm-knowledge-loading')).toHaveTextContent('Loading');
    await act(async () => { while (waits.length) waits.shift()!(); });
    expect(await screen.findByTestId('cm-recall-big-value')).toHaveTextContent('88%');
    expect(screen.queryByTestId('cm-knowledge-loading')).toBeNull();
    openTab('context');
    await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(screen.queryByTestId('cm-context-loading')).toBeNull();
    openTab('memory');
    await screen.findByTestId('cm-memory-big-value');
    expect(screen.queryByTestId('cm-memory-loading')).toBeNull();
  });
});
