/**
 * Tests for CMBrainOverlay — the C&M Global Brain overlay (Run 1).
 *
 * Run-1 scope: opens on swarm:show-context, renders a 3-tab shell (Context /
 * Memory / Guideline) with ONLY the Context tab implemented (Memory + Guideline
 * are labeled placeholders), plus a fixed overview rail (live total tokens +
 * composition, Needs-you counts). The Context tab renders the assembled
 * context-file stack from the /eval/context-health token_block payload.
 *
 * The backend fetch is mocked at the api boundary (services/api). We assert the
 * overlay CONSUMES the payload (rows, ownership, lock, %) — not that it invents
 * numbers (the whole point of the backend-primary design).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the api client BEFORE importing the component.
vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
import api from '../../services/api';
import { CMBrainContent } from './CMBrainOverlay';

const TOKEN_BLOCK = {
  total_tokens: 100000,
  budget: 91000,
  warning_threshold: 91000,
  emergency_threshold: 130000,
  over_budget: true,
  per_file: [
    { name: 'SWARMAI.md', tokens: 2000, pct: 2.0, owner: 'system', priority: 0, locked: true, health: 'fresh' },
    { name: 'USER.md', tokens: 3000, pct: 3.0, owner: 'user', priority: 4, locked: false, health: 'idle' },
    { name: 'MEMORY.md', tokens: 48000, pct: 48.0, owner: 'agent', priority: 7, locked: false, health: 'oversized' },
    { name: 'KNOWLEDGE.md', tokens: 47000, pct: 47.0, owner: 'auto', priority: 9, locked: false, health: 'growing' },
  ],
};

// Real-shaped Review proposals (DDD cultivation) — the fields the card renders.
const REVIEW_PROPS = [
  { id: 'proposal_a8e14d', target_doc: 'IMPROVEMENT.md', target_section: 'What Failed', content: 'Trace the decisive line before asserting a root cause.', confidence: 0.82 },
  { id: 'proposal_1d7c2e', target_doc: 'TECH.md', target_section: 'Architecture', content: 'Record the lite-endpoint split as a TECH decision.', confidence: 0.71 },
  { id: 'proposal_e65e4b', target_doc: 'PRODUCT.md', target_section: 'C&M overlay', content: 'Locked context files open read-only in Canvas.', confidence: 0.64 },
];
// Real-shaped governance (Approve) proposals — source_class/occurrence/proposed_rule.
const GOV_PROPS = [
  { id: 'CLASS_B:rule', source_class: 'CLASS_B', proposal_kind: 'rule', occurrence_count: 6, proposed_rule: 'Any runtime/deploy-state claim must cite a same-turn observation.', confidence: 0.9 },
  { id: 'CLASS_A:gate', source_class: 'CLASS_A', proposal_kind: 'gate', occurrence_count: 5, proposed_rule: 'Block commit when a self-authored test patches the symbol-under-change.', confidence: 0.85 },
];

// lite endpoint shape: exactly { token_block, pending_proposals, governance_pending_count }
function mockHealth(overrides: Record<string, unknown> = {}, opts: { governancePending?: number; trendPoints?: unknown[]; reviewProps?: unknown[] } = {}) {
  const reviewProps = opts.reviewProps ?? REVIEW_PROPS;
  const govN = opts.governancePending ?? 0;
  const lite = {
    token_block: TOKEN_BLOCK,
    pending_proposals: reviewProps,
    governance_pending_count: govN,
    ...overrides,
  };
  const points = opts.trendPoints ?? [];
  (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes('context-health/lite')) {
      return Promise.resolve({ data: lite });
    }
    if (url.includes('governance/pending')) {
      return Promise.resolve({ data: { proposals: GOV_PROPS.slice(0, govN), total: govN } });
    }
    if (url.includes('brain-trend')) {
      return Promise.resolve({ data: { points, count: points.length, launch_date: (points[0] as { date?: string } | undefined)?.date ?? null } });
    }
    if (url.includes('brain-graph')) {
      return Promise.resolve({ data: { nodes: [], drill: {}, total: 0 } });
    }
    if (url.includes('archive-list')) {
      return Promise.resolve({ data: { entries: [], total: 0, shards: [], source: url.includes('evolution') ? 'evolution' : 'memory' } });
    }
    if (url.includes('archive-search')) {
      return Promise.resolve({ data: { results: [], q: '', source: url.includes('evolution') ? 'evolution' : 'memory' } });
    }
    return Promise.resolve({ data: lite }); // fallback
  });
  (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { status: 'ok' } });
}

// M3: CMBrainOverlay → CMBrainContent (OverlayHost registry). The content component
// ALWAYS renders (the host owns open/close + mount lifecycle), so tests render it
// directly; `openOverlay()` is a no-op kept so the content-assertion tests read
// unchanged. The former "does not render until event" test moved to OverlayHost.test.
function renderOverlay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CMBrainContent />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockHealth();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function openOverlay() { /* no-op: CMBrainContent renders immediately (host-owned open) */ }

describe('CMBrainContent — tabs + content', () => {
  it('renders the Context tab active by default', async () => {
    renderOverlay();
    openOverlay();
    expect(await screen.findByTestId('cm-brain-overlay')).toBeInTheDocument();
    // three tabs present
    expect(screen.getByTestId('cm-tab-context')).toBeInTheDocument();
    expect(screen.getByTestId('cm-tab-memory')).toBeInTheDocument();
    expect(screen.getByTestId('cm-tab-guideline')).toBeInTheDocument();
    // context panel is the active one
    expect(await screen.findByTestId('cm-panel-context')).toBeInTheDocument();
  });

});

describe('CMBrainOverlay — Memory tab (7-type graph + drill, DoD2)', () => {
  const GRAPH = {
    nodes: [
      { type: 'principle', count: 10, active: 10, dormant: 0 },
      { type: 'correction', count: 5, active: 5, dormant: 0 },
      { type: 'decision', count: 7, active: 7, dormant: 0 },
      { type: 'guideline', count: 101, active: 90, dormant: 11 },
      { type: 'pitfall', count: 69, active: 69, dormant: 0 },
      { type: 'process', count: 1, active: 1, dormant: 0 },
      { type: 'model', count: 4, active: 4, dormant: 0 },
    ],
    drill: {
      guideline: [{ title: 'G-latest', status: 'active', ref_count: 0, meta: '2026-08-02' }],
      principle: [], correction: [], decision: [], pitfall: [], process: [], model: [],
    },
    total: 197,
  };

  function mockGraph() {
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('brain-graph')) return Promise.resolve({ data: GRAPH });
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      if (url.includes('archive-list')) return Promise.resolve({ data: { entries: [], total: 0, shards: [], source: url.includes('evolution') ? 'evolution' : 'memory' } });
      if (url.includes('archive-search')) return Promise.resolve({ data: { results: [], q: '', source: url.includes('evolution') ? 'evolution' : 'memory' } });
      // lite (default)
      return Promise.resolve({ data: { pending_proposals: [], token_block: TOKEN_BLOCK, governance_pending_count: 0 } });
    });
  }

  async function openMemory() {
    mockGraph();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-memory').click(); });
    return screen.findByTestId('cm-panel-memory');
  }

  it('renders 7 graph nodes from the backend endpoint (backend-primary)', async () => {
    await openMemory();
    const nodes = await screen.findAllByTestId(/^cm-graph-node-/);
    expect(nodes).toHaveLength(7);
    // node count is served, not invented
    expect(screen.getByTestId('cm-graph-node-guideline').textContent).toMatch(/101/);
  });

  it('clicking a node drills the latest entries of that type', async () => {
    await openMemory();
    const node = await screen.findByTestId('cm-graph-node-guideline'); // wait for the query
    act(() => { node.click(); });
    const drill = await screen.findByTestId('cm-drill-list');
    expect(drill.textContent).toMatch(/G-latest/);
  });

  it('renders a by-type distribution bar per type', async () => {
    await openMemory();
    const bars = await screen.findAllByTestId(/^cm-bar-/);
    expect(bars).toHaveLength(7);
  });

  it('shows a "collecting since launch" placeholder when the size-trend has <2 points', async () => {
    const panel = await openMemory();
    // trend endpoint returned 0 points → NEVER a fabricated line, an explicit placeholder
    expect(panel.textContent).toMatch(/collecting/i);
  });

  it('renders an empty-but-valid graph when all node counts are zero', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('brain-graph')) return Promise.resolve({ data: {
        nodes: [
          { type: 'principle', count: 0, active: 0, dormant: 0 },
          { type: 'correction', count: 0, active: 0, dormant: 0 },
          { type: 'decision', count: 0, active: 0, dormant: 0 },
          { type: 'guideline', count: 0, active: 0, dormant: 0 },
          { type: 'pitfall', count: 0, active: 0, dormant: 0 },
          { type: 'process', count: 0, active: 0, dormant: 0 },
          { type: 'model', count: 0, active: 0, dormant: 0 },
        ], drill: {}, total: 0 } });
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      return Promise.resolve({ data: { pending_proposals: [], token_block: TOKEN_BLOCK, governance_pending_count: 0 } });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-memory').click(); });
    const panel = await screen.findByTestId('cm-panel-memory');
    expect(panel).toBeInTheDocument();
    expect((await screen.findAllByTestId(/^cm-graph-node-/))).toHaveLength(7);
  });
});

describe('CMBrainOverlay — Guideline tab (static teaching content, DoD3)', () => {
  async function openGuideline() {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-guideline').click(); });
    return screen.findByTestId('cm-panel-guideline');
  }

  it('renders the 5 lifecycle cards in order (Assemble→Recall→Judge→Sediment→Decay)', async () => {
    await openGuideline();
    const flow = screen.getByTestId('cm-guideline-lifecycle');
    const stages = within(flow).getAllByTestId(/^cm-lc-/);
    expect(stages.map((s) => s.getAttribute('data-testid'))).toEqual([
      'cm-lc-assemble', 'cm-lc-recall', 'cm-lc-judge', 'cm-lc-sediment', 'cm-lc-decay',
    ]);
  });

  it('renders Automatic vs Manual as two columns with tagged items', async () => {
    await openGuideline();
    expect(screen.getByTestId('cm-guideline-automatic')).toBeInTheDocument();
    expect(screen.getByTestId('cm-guideline-manual')).toBeInTheDocument();
    // automatic column names real mechanisms (Recall/Cultivation/Decay) — not counts
    expect(screen.getByTestId('cm-guideline-automatic').textContent).toMatch(/Recall|Cultivation|Decay/);
    // manual column names user-steered surfaces (STEERING/USER/skill)
    expect(screen.getByTestId('cm-guideline-manual').textContent).toMatch(/STEERING|USER|[Ss]kill/);
  });

  it('describes the machinery as WHAT-IT-DOES, not as raw source-symbol identifiers', async () => {
    await openGuideline();
    const machinery = screen.getByTestId('cm-guideline-chips');
    // Redesign: the raw source symbols (context_health, s_persist, ...) are noise to
    // a non-technical user (R20). The section now names what each mechanism DOES.
    const txt = machinery.textContent ?? '';
    // no raw hook/skill identifiers surfaced as content
    expect(txt).not.toMatch(/context_health|memory_edit_guard|ddd_cultivation|knowledge_backflow|correction_capture|high_signal_capture/);
    expect(txt).not.toMatch(/s_persist|s_memory-distill|s_self-evolution|s_project-manager|s_golden-case/);
    // it still teaches the machinery — in plain language
    expect(txt.length).toBeGreaterThan(20);
  });

  it('R30: bakes NO drifty numeric counts into the static content', async () => {
    const panel = await openGuideline();
    // Static teaching content must describe MECHANISMS, not counts. Guard against a
    // baked "214 entries" / "86 skills" style number sneaking in (R30 no-drift).
    // Allow structural digits inside testids/hex; check only visible text tokens.
    const visible = panel.textContent ?? '';
    // no standalone integer >=3 digits (e.g. 214, 108) that would be a drifty count
    expect(visible).not.toMatch(/\b\d{3,}\b/);
  });
});

describe('CMBrainOverlay — Context tab consumes the token block', () => {
  it('renders one row per per_file entry, driven by the payload (not hardcoded)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    await waitFor(() => {
      expect(screen.getByTestId('cm-file-row-SWARMAI.md')).toBeInTheDocument();
    });
    expect(screen.getByTestId('cm-file-row-MEMORY.md')).toBeInTheDocument();
    expect(screen.getByTestId('cm-file-row-KNOWLEDGE.md')).toBeInTheDocument();
    // exactly 4 rows — driven by the 4-entry payload
    expect(screen.getAllByTestId(/^cm-file-row-/)).toHaveLength(4);
  });

  it('row is width-capped and the filename keeps its truncation bound (no dead-space void)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    const row = await screen.findByTestId('cm-file-row-SWARMAI.md');
    // §4 group+cap: the row content is bounded (max-w-*), so metadata sits next to
    // the name instead of a screen away on a wide window.
    expect(row.className).toMatch(/max-w-/);
    // run_5f040023: the row is now a fixed-column GRID (AC4 alignment) with the
    // name in the `1fr` column, so `min-w-0 truncate` (NOT flex-1) is the
    // truncation bound — the grid `1fr` provides the flex the old flex-1 did.
    // run_2816ab1c: the name cell is now a flex-col wrapper (name + optional
    // health-counts line); `min-w-0` moved to the wrapper, `truncate` stays on the
    // inner name span. The truncation contract is unchanged — just re-nested.
    expect(row.className).toContain('grid');
    // The inner name span carries `truncate`; its wrapper (grid 1fr cell) carries
    // `min-w-0`. Select the inner one explicitly (the wrapper also contains the text).
    const nameSpan = Array.from(row.querySelectorAll('span')).find(
      (s) => s.textContent?.startsWith('SWARMAI.md') && s.className.includes('truncate'),
    ) as HTMLElement;
    expect(nameSpan).toBeTruthy();
    expect(nameSpan.className).toContain('truncate');
    expect((nameSpan.parentElement as HTMLElement).className).toContain('min-w-0');
  });

  it('shows ownership + lock from the payload', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    const swarmaiRow = await screen.findByTestId('cm-file-row-SWARMAI.md');
    // locked P0 system file shows a lock affordance
    expect(swarmaiRow.querySelector('[data-testid="cm-lock"]')).not.toBeNull();
    expect(swarmaiRow.getAttribute('data-owner')).toBe('system');
    const memRow = screen.getByTestId('cm-file-row-MEMORY.md');
    expect(memRow.getAttribute('data-owner')).toBe('agent');
    expect(memRow.querySelector('[data-testid="cm-lock"]')).toBeNull(); // not locked
  });

  it('renders the per-file Health tag from the payload (DoD5, backend-derived)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    const memRow = await screen.findByTestId('cm-file-row-MEMORY.md');
    const memTag = memRow.querySelector('[data-testid="cm-health"]');
    expect(memTag).not.toBeNull();
    expect(memTag!.textContent).toBe('oversized'); // from payload, not invented
    const userRow = screen.getByTestId('cm-file-row-USER.md');
    expect(userRow.querySelector('[data-testid="cm-health"]')!.textContent).toBe('idle');
    const swRow = screen.getByTestId('cm-file-row-SWARMAI.md');
    expect(swRow.querySelector('[data-testid="cm-health"]')!.textContent).toBe('fresh');
  });

  it('shows the truncation legend explaining the health/lock contract', async () => {
    renderOverlay();
    openOverlay();
    const panel = await screen.findByTestId('cm-panel-context');
    // legend teaches the assembly/truncation contract + the health vocab
    expect(panel.textContent).toMatch(/never truncated/i);
    expect(panel.textContent).toMatch(/fresh.*idle.*growing.*oversized|Health/i);
  });

  it('overview rail shows the live total tokens from the payload', async () => {
    renderOverlay();
    openOverlay();
    const rail = await screen.findByTestId('cm-overview-rail');
    // 100000 tokens → rendered as a compact "100K" (or contains the number).
    // Wait for the async query to resolve into the rail.
    await waitFor(() => expect(rail.textContent).toMatch(/100[,.]?0?K?/));
  });

  it('Needs-you Review = pending_proposals; Approve/Action wired to governance/pending (DoD4)', async () => {
    mockHealth({}, { governancePending: 2 }); // 2 governance proposals awaiting decision
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    expect((await screen.findByTestId('cm-needs-review')).textContent).toContain('3'); // pending_proposals
    // Approve reads governance/pending .total (real wiring, not hardcoded 0)
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
  });
});

describe('CMBrainOverlay — overview rail growth-trend + Needs-you filter (DoD4)', () => {
  it('shows a "collecting since launch" growth-trend placeholder when <2 points', async () => {
    mockHealth({}, { trendPoints: [] });
    renderOverlay();
    openOverlay();
    const rail = await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(rail.textContent).toMatch(/collecting/i));
  });

  it('draws the growth-trend line when >=2 points exist (never fabricated)', async () => {
    mockHealth({}, { trendPoints: [
      { date: '2026-08-01', prompt_tokens: 90000, memory_bytes: 47000 },
      { date: '2026-08-02', prompt_tokens: 91000, memory_bytes: 48000 },
    ] });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    // a real 2-point series → an SVG trend path in the rail
    expect(await screen.findByTestId('cm-rail-trend-svg')).toBeInTheDocument();
  });

  it('clicking a Needs-you button filters the main area to that list + back returns + rail active', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    // click Review → main area swaps to the filtered list
    act(() => { screen.getByTestId('cm-needs-review').click(); });
    const list = await screen.findByTestId('cm-needs-list');
    expect(list).toBeInTheDocument();
    expect(screen.queryByTestId('cm-panel-context')).toBeNull(); // tab content hidden
    // AC6: the rail Review button shows an ACTIVE state while its list is open
    expect(screen.getByTestId('cm-needs-review').getAttribute('data-active')).toBe('true');
    // AC6: an explicit Back header (breadcrumb) exists, labeled with the return target
    const back = await screen.findByTestId('cm-needs-back');
    expect(back.textContent).toMatch(/back/i);
    // back returns to the Context tab + clears rail active
    act(() => { back.click(); });
    expect(await screen.findByTestId('cm-panel-context')).toBeInTheDocument();
    expect(screen.queryByTestId('cm-needs-list')).toBeNull();
    expect(screen.getByTestId('cm-needs-review').getAttribute('data-active')).toBe('false');
  });
});

describe('CMBrainOverlay — AC1 lite endpoint wiring', () => {
  it('first paint queries /eval/context-health/lite, never the heavy /eval/context-health', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    await waitFor(() => expect(screen.getByTestId('cm-file-row-SWARMAI.md')).toBeInTheDocument());
    const urls = (api.get as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes('/eval/context-health/lite'))).toBe(true);
    // the heavy endpoint (exact, without /lite) must NOT be fetched on first paint
    expect(urls.some((u) => /\/eval\/context-health(?!\/lite)/.test(u))).toBe(false);
  });

  it('Approve count comes from lite governance_pending_count (no first-paint governance/pending fetch)', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    // the governance LIST is lazy — not fetched until Approve is opened
    const urls = (api.get as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes('governance/pending'))).toBe(false);
  });
});

describe('CMBrainOverlay — AC2 rows open in Canvas', () => {
  it('clicking a file row dispatches swarm:open-file for that .context file', async () => {
    mockHealth();
    const seen: Array<{ path?: string }> = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener('swarm:open-file', handler);
    try {
      renderOverlay();
      openOverlay();
      await screen.findByTestId('cm-panel-context');
      const row = await screen.findByTestId('cm-file-row-MEMORY.md');
      act(() => { row.click(); });
      expect(seen.some((d) => d.path === '.context/MEMORY.md')).toBe(true);
    } finally {
      document.removeEventListener('swarm:open-file', handler);
    }
  });

  it('LOCKED files are ALSO openable (read-only is server-driven, not a dead lock)', async () => {
    mockHealth();
    const seen: Array<{ path?: string }> = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener('swarm:open-file', handler);
    try {
      renderOverlay();
      openOverlay();
      await screen.findByTestId('cm-panel-context');
      const locked = await screen.findByTestId('cm-file-row-SWARMAI.md');
      act(() => { locked.click(); });
      expect(seen.some((d) => d.path === '.context/SWARMAI.md')).toBe(true);
    } finally {
      document.removeEventListener('swarm:open-file', handler);
    }
  });
});

describe('CMBrainOverlay — AC3/4/5 proposal cards + dual-route actions', () => {
  it('Review cards render real fields (target_doc/section/content), not a raw id subject', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    act(() => { screen.getByTestId('cm-needs-review').click(); });
    const list = await screen.findByTestId('cm-needs-list');
    // the human-readable target + content appear
    expect(list.textContent).toMatch(/IMPROVEMENT\.md/);
    expect(list.textContent).toMatch(/What Failed/);
    expect(list.textContent).toMatch(/decisive line/);
    // the id is NOT the card's headline (it may appear as a demoted footnote only)
    const card = await screen.findByTestId('cm-proposal-proposal_a8e14d');
    const head = card.querySelector('[data-testid="cm-card-what"]');
    expect(head).not.toBeNull();
    expect(head!.textContent).not.toContain('proposal_a8e14d');
  });

  it('Review Accept POSTs the cultivation route (not governance)', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    act(() => { screen.getByTestId('cm-needs-review').click(); });
    const card = await screen.findByTestId('cm-proposal-proposal_a8e14d');
    act(() => { (card.querySelector('[data-testid="cm-card-accept"]') as HTMLElement).click(); });
    await waitFor(() => {
      const posts = (api.post as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
      // bare path — the axios interceptor (api.ts) prepends /api. A hard-coded
      // '/api/cultivation/...' here would become '/api/api/...' → 404 (the bug fixed
      // 2026-08-09). Assert the exact bare path so the double-prefix can't return.
      expect(posts.some((u) => u.startsWith('/cultivation/proposals/proposal_a8e14d/approve'))).toBe(true);
      expect(posts.some((u) => u.startsWith('/api/cultivation/'))).toBe(false);
    });
  });

  it('Approve cards render governance fields + Defer POSTs the governance route', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    await screen.findByTestId('cm-needs-list');
    // the governance LIST is lazy (fetched only when Approve opens) — wait for the card
    const card = await screen.findByTestId('cm-proposal-CLASS_B:rule');
    const list = screen.getByTestId('cm-needs-list');
    expect(list.textContent).toMatch(/CLASS_B/);
    expect(list.textContent).toMatch(/same-turn observation/);
    // governance card has a Defer action (cultivation does not)
    act(() => { (card.querySelector('[data-testid="cm-card-defer"]') as HTMLElement).click(); });
    await waitFor(() => {
      const calls = (api.post as ReturnType<typeof vi.fn>).mock.calls;
      const gov = calls.find((c) => (c[0] as string).includes('/eval/governance/decision'));
      expect(gov).toBeTruthy();
      expect((gov![1] as { proposal_id: string; decision: string }).decision).toBe('defer');
      expect((gov![1] as { proposal_id: string }).proposal_id).toBe('CLASS_B:rule');
    });
  });
});

describe('CMBrainOverlay — Gate-2: lazy Approve list shows loading, not a false empty', () => {
  it('shows a loading state (never "nothing to approve") while the governance list is in flight', async () => {
    // governance/pending resolves on a deferred promise so we can observe the in-flight state
    let resolveGov: (v: unknown) => void = () => {};
    const govPromise = new Promise((r) => { resolveGov = r; });
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('context-health/lite')) return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: REVIEW_PROPS, governance_pending_count: 2 } });
      if (url.includes('governance/pending')) return govPromise.then(() => ({ data: { proposals: GOV_PROPS, total: 2 } }));
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: [], governance_pending_count: 2 } });
    });
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { status: 'ok' } });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    // while in flight: a loading indicator, NOT the false "nothing to approve"
    const loading = await screen.findByTestId('cm-needs-loading');
    expect(loading).toBeInTheDocument();
    expect(screen.getByTestId('cm-needs-list').textContent).not.toMatch(/nothing/i);
    // resolve → cards appear
    await act(async () => { resolveGov(null); });
    await screen.findByTestId('cm-proposal-CLASS_B:rule');
  });
});

describe('CMBrainOverlay — AC7 honest over-budget alert', () => {
  it('shows over-by amount + names the top oversized files + an open action', async () => {
    mockHealth(); // TOKEN_BLOCK is over_budget (100K vs 91K), MEMORY.md oversized 48K
    renderOverlay();
    openOverlay();
    const rail = await screen.findByTestId('cm-overview-rail');
    const alert = await screen.findByTestId('cm-budget-alert');
    // over-by amount (100K-91K = 9K) surfaced, not just "over budget"
    expect(alert.textContent).toMatch(/9[,.]?0?K|9000/);
    // names the biggest offender file
    expect(alert.textContent).toMatch(/MEMORY\.md/);
    void rail;
  });

  it('slims Context rows — no redundant percent column alongside the token count', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    const row = await screen.findByTestId('cm-file-row-MEMORY.md');
    // AC7: the redundant composition-% cell is dropped (token count is the one fact)
    expect(row.querySelector('[data-testid="cm-file-pct"]')).toBeNull();
  });
});

describe('CMBrainOverlay — redesign: queue semantics + humanization + demotion + pct tint', () => {
  // AC1: each opened queue carries an intent-bearing heading + a one-line explainer
  // that states WHAT it governs and WHAT its conf means (knowledge vs governance).
  it('Review (knowledge) list shows an intent-bearing heading + a conf-meaning explainer', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    act(() => { screen.getByTestId('cm-needs-review').click(); });
    await screen.findByTestId('cm-needs-list');
    const explainer = await screen.findByTestId('cm-needs-explainer');
    // states what this queue governs (knowledge sedimentation → DDD docs) …
    expect(explainer.textContent).toMatch(/knowledge|sediment|DDD|doc/i);
    // … and what conf means HERE (extraction quality, not should-sediment)
    expect(explainer.textContent).toMatch(/extract/i);
  });

  it('Approve (governance) list shows an intent-bearing heading + a recurrence-conf explainer', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    await screen.findByTestId('cm-needs-list');
    const explainer = await screen.findByTestId('cm-needs-explainer');
    // governs rules/gates …
    expect(explainer.textContent).toMatch(/rule|gate|govern/i);
    // … conf here = recurrence confidence
    expect(explainer.textContent).toMatch(/recurr/i);
  });

  // AC2: source_class rendered as a human phrase, not the raw CLASS_x token as the subject.
  it('Approve card renders a human phrase for source_class (not raw CLASS_B as the subject)', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    const card = await screen.findByTestId('cm-proposal-CLASS_B:rule');
    // a human phrase for CLASS_B (infer-without-verify / verification family) appears
    const cls = card.querySelector('[data-testid="cm-class-phrase"]');
    expect(cls).not.toBeNull();
    expect(cls!.textContent!.toLowerCase()).toMatch(/verif|observ|infer/);
    // the phrase text is not merely the raw token
    expect(cls!.textContent).not.toBe('CLASS_B');
  });

  it('source_class resolver falls back gracefully for an unmapped class code', async () => {
    mockHealth({}, { governancePending: 1 });
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('context-health/lite')) return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: REVIEW_PROPS, governance_pending_count: 1 } });
      if (url.includes('governance/pending')) return Promise.resolve({ data: { proposals: [{ id: 'CLASS_Z:rule', source_class: 'CLASS_Z', proposal_kind: 'rule', occurrence_count: 3, proposed_rule: 'Some unmapped-class rule.', confidence: 0.9 }], total: 1 } });
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: [], governance_pending_count: 1 } });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    const card = await screen.findByTestId('cm-proposal-CLASS_Z:rule');
    // does not crash; the class-phrase slot renders SOMETHING readable (the raw code as graceful fallback is allowed)
    const cls = card.querySelector('[data-testid="cm-class-phrase"]');
    expect(cls).not.toBeNull();
    expect(cls!.textContent!.length).toBeGreaterThan(0);
  });

  // AC3: low-confidence governance items are visually demoted (Approve branch only).
  it('demotes a low-confidence governance item (conf < 0.7) as not-yet-actionable', async () => {
    mockHealth({}, { governancePending: 1 });
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('context-health/lite')) return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: REVIEW_PROPS, governance_pending_count: 1 } });
      if (url.includes('governance/pending')) return Promise.resolve({ data: { proposals: [{ id: 'CLASS_C:rule', source_class: 'CLASS_C', proposal_kind: 'rule', occurrence_count: 3, proposed_rule: 'A low-confidence emerging rule.', confidence: 0.5 }], total: 1 } });
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: [], governance_pending_count: 1 } });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    const card = await screen.findByTestId('cm-proposal-CLASS_C:rule');
    // demoted marker present
    expect(card.querySelector('[data-testid="cm-demoted"]')).not.toBeNull();
  });

  it('does NOT demote a high-confidence governance item (conf >= 0.7)', async () => {
    mockHealth({}, { governancePending: 2 });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    await waitFor(() => expect(screen.getByTestId('cm-needs-approve').textContent).toContain('2'));
    act(() => { screen.getByTestId('cm-needs-approve').click(); });
    const card = await screen.findByTestId('cm-proposal-CLASS_B:rule'); // conf 0.9
    expect(card.querySelector('[data-testid="cm-demoted"]')).toBeNull();
  });

  it('does NOT apply the demotion cut to Review items (Review conf = extraction quality)', async () => {
    mockHealth(); // REVIEW_PROPS includes a 0.64 item
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    act(() => { screen.getByTestId('cm-needs-review').click(); });
    // the 0.64 Review proposal must NOT be demoted (0.7 cut is Approve-only)
    const card = await screen.findByTestId('cm-proposal-proposal_e65e4b');
    expect(card.querySelector('[data-testid="cm-demoted"]')).toBeNull();
  });

  // AC4 (run_5f040023, user override of the prior subtle-tint): explicit visible
  // SHARE bar + % number per file. The user asked for "占比数字或条形" — both.
  it('renders an explicit pct SHARE bar + percent number per file', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    const row = await screen.findByTestId('cm-file-row-MEMORY.md');
    // visible bar whose width encodes pct (48%) — sourced from payload, not invented
    const bar = row.querySelector('[data-testid="cm-pct-bar"]') as HTMLElement;
    expect(bar).not.toBeNull();
    expect(bar.style.width).toMatch(/48/);
    // explicit % number is shown (the user asked for the number, not just a cue)
    const num = row.querySelector('[data-testid="cm-pct-num"]') as HTMLElement;
    expect(num).not.toBeNull();
    expect(num.textContent).toContain('48');
  });

  // NEW ARCHITECTURE (2026-08-14): selective injection was DELETED — live memory is
  // ALWAYS full-injected (disk == prompt load). The ✂ cue + the rail's "actually
  // injected (selective)" line are GONE. Teeth: re-adding a selective branch fails this.
  it('shows NO selective cue and NO injected-estimate line (full-injection architecture)', async () => {
    mockHealth();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-panel-context');
    // No ✂ selective cue on any row (MEMORY was the selective one under the old arch).
    const memRow = await screen.findByTestId('cm-file-row-MEMORY.md');
    expect(memRow.querySelector('[data-testid="cm-selective"]')).toBeNull();
    // No "actually injected (selective)" rail line — disk size IS the prompt load.
    expect(screen.queryByTestId('cm-injected')).toBeNull();
  });
});

describe('CMBrainOverlay — Evolution tab + ArchivePanel (Run C)', () => {
  const MEM_ENTRIES = [
    { title: 'stale subprocess reused without liveness check', type: 'pitfall', date: '2026-03-15', status: 'archived', archived_from: '', shard: 'MEMORY-archive-2026-03.md' },
    { title: 'prevention over recovery', type: 'principle', date: null, status: 'archived', archived_from: '', shard: 'MEMORY-archive-2026-03.md' },
  ];
  const EVO_ENTRIES = [
    { title: 'CLASS A: Confidence → Skip Process', type: 'class', date: null, status: 'archived', archived_from: 'Corrections', shard: 'EVOLUTION-archive-2026-08.md' },
    { title: 'C037 | 2026-06-17', type: 'correction', date: '2026-06-17', status: 'archived', archived_from: '', shard: 'EVOLUTION-archive-2026-08.md' },
  ];

  // Mock that answers archive endpoints per-family + query.
  function mockArchive(opts: { evoEntries?: unknown[]; memEntries?: unknown[]; evoHits?: unknown[] } = {}) {
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('archive-list')) {
        const isEvo = url.includes('evolution');
        const entries = isEvo ? (opts.evoEntries ?? EVO_ENTRIES) : (opts.memEntries ?? MEM_ENTRIES);
        return Promise.resolve({ data: { entries, total: (entries as unknown[]).length, shards: ['shard-1.md'], source: isEvo ? 'evolution' : 'memory' } });
      }
      if (url.includes('archive-search')) {
        const isEvo = url.includes('evolution');
        return Promise.resolve({ data: { results: isEvo ? (opts.evoHits ?? []) : [], q: 'x', source: isEvo ? 'evolution' : 'memory' } });
      }
      if (url.includes('brain-trend')) return Promise.resolve({ data: { points: [], count: 0, launch_date: null } });
      if (url.includes('brain-graph')) return Promise.resolve({ data: { nodes: [], drill: {}, total: 0 } });
      return Promise.resolve({ data: { token_block: TOKEN_BLOCK, pending_proposals: [], governance_pending_count: 0 } });
    });
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { status: 'ok' } });
  }

  async function openEvolution() {
    mockArchive();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-evolution').click(); });
    return screen.findByTestId('cm-panel-evolution');
  }

  it('adds an Evolution tab that renders its archive panel (source=evolution)', async () => {
    await openEvolution();
    expect(screen.getByTestId('cm-tab-evolution')).toBeInTheDocument();
    // the evolution archive panel is present + fetched its list
    expect(await screen.findByTestId('cm-archive-evolution')).toBeInTheDocument();
    await screen.findByTestId('cm-archive-list-evolution');
  });

  it('renders archived evolution entries from the endpoint, graceful on null date + empty provenance', async () => {
    await openEvolution();
    const list = await screen.findByTestId('cm-archive-list-evolution');
    expect(list.textContent).toMatch(/CLASS A/);
    expect(list.textContent).toMatch(/Corrections/); // archived_from shown when present
    // null date renders as '—', never the literal "null"
    expect(list.textContent).not.toMatch(/null|undefined/);
    expect(list.textContent).toMatch(/—/);
  });

  it('Memory tab now also carries an archive panel (source=memory)', async () => {
    mockArchive();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-memory').click(); });
    await screen.findByTestId('cm-panel-memory');
    expect(await screen.findByTestId('cm-archive-memory')).toBeInTheDocument();
    const list = await screen.findByTestId('cm-archive-list-memory');
    // memory entries carry NO provenance ('') → nothing renders for archived_from, no "null"
    expect(list.textContent).toMatch(/stale subprocess/);
    expect(list.textContent).not.toMatch(/null|undefined/);
  });

  it('typing a query swaps the list for recall results (archive-only search)', async () => {
    const { getByTestId } = { getByTestId: screen.getByTestId };
    void getByTestId;
    mockArchive({ evoHits: [{ title: 'CLASS A skip', snippet: 'confidence → skip process, the loudest voice…', source_file: '.context/Archives/EVOLUTION-archive-2026-08.md', shard: 'EVOLUTION-archive-2026-08.md' }] });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-evolution').click(); });
    await screen.findByTestId('cm-panel-evolution');
    const input = await screen.findByTestId('cm-archive-search-input-evolution') as HTMLInputElement;
    const { fireEvent } = await import('@testing-library/react');
    await act(async () => { fireEvent.change(input, { target: { value: 'skip' } }); });
    // list is replaced by recall results
    const results = await screen.findByTestId('cm-archive-results-evolution');
    expect(results.textContent).toMatch(/CLASS A skip/);
    expect(results.textContent).toMatch(/confidence/);
    // clearing returns to the LIST with its CONTENT (not a stuck "Loading…"). Teeth
    // against a search→clear deadlock (Gate-2 #6): the list query fetched on mount
    // (searching=false initially) and react-query retains its cache across the
    // enabled-toggle, so the cached entries render immediately on clear — assert the
    // real content is back, and the loading state is NOT shown.
    act(() => { screen.getByTestId('cm-archive-search-clear-evolution').click(); });
    const backToList = await screen.findByTestId('cm-archive-list-evolution');
    expect(backToList.textContent).toMatch(/CLASS A/);           // content restored
    expect(screen.queryByTestId('cm-archive-loading-evolution')).toBeNull(); // not stuck loading
    expect(screen.queryByTestId('cm-archive-results-evolution')).toBeNull(); // results gone
  });

  it('shows an empty-but-valid state when nothing is archived (never a crash / false content)', async () => {
    mockArchive({ evoEntries: [] });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-evolution').click(); });
    await screen.findByTestId('cm-panel-evolution');
    expect(await screen.findByTestId('cm-archive-empty-evolution')).toBeInTheDocument();
  });
});

describe('CMBrainOverlay — degradation', () => {
  it('renders without crashing when token_block is null (backend swallow)', async () => {
    mockHealth({ token_block: null });
    renderOverlay();
    openOverlay();
    // overlay still opens; context panel shows an empty-but-valid state
    expect(await screen.findByTestId('cm-panel-context')).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^cm-file-row-/)).toHaveLength(0);
  });
});
