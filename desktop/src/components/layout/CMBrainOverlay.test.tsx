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

  it('renders reference chips for the real hooks + skills that drive the brain', async () => {
    await openGuideline();
    const chips = screen.getByTestId('cm-guideline-chips');
    expect(chips.textContent).toMatch(/context_health|ddd_cultivation|correction_capture/);
    expect(chips.textContent).toMatch(/s_persist|s_self-evolution/);
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
    // Gate-1 constraint: the name span MUST keep `flex-1 min-w-0 truncate` — that
    // pairing IS the truncation bound; removing flex-1 breaks truncation for long
    // filenames. The void is fixed by capping the ROW, not by dropping flex-1.
    const nameSpan = Array.from(row.querySelectorAll('span')).find(
      (s) => s.textContent === 'SWARMAI.md',
    ) as HTMLElement;
    expect(nameSpan).toBeTruthy();
    expect(nameSpan.className).toContain('flex-1');
    expect(nameSpan.className).toContain('min-w-0');
    expect(nameSpan.className).toContain('truncate');
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
      expect(posts.some((u) => u.includes('/api/cultivation/proposals/proposal_a8e14d/approve'))).toBe(true);
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
