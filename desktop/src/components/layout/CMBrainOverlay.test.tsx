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
  default: { get: vi.fn() },
}));
import api from '../../services/api';
import { CMBrainOverlay } from './CMBrainOverlay';
import { __resetActiveOverlayEvent } from './useExclusiveOverlay';

const TOKEN_BLOCK = {
  total_tokens: 100000,
  budget: 91000,
  warning_threshold: 91000,
  emergency_threshold: 130000,
  over_budget: true,
  per_file: [
    { name: 'SWARMAI.md', tokens: 2000, pct: 2.0, owner: 'system', priority: 0, locked: true },
    { name: 'USER.md', tokens: 3000, pct: 3.0, owner: 'user', priority: 4, locked: false },
    { name: 'MEMORY.md', tokens: 48000, pct: 48.0, owner: 'agent', priority: 7, locked: false },
    { name: 'KNOWLEDGE.md', tokens: 47000, pct: 47.0, owner: 'auto', priority: 9, locked: false },
  ],
};

function mockHealth(overrides: Record<string, unknown> = {}) {
  (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: {
      refresh_log: [],
      staleness: [],
      pending_proposals: [{ id: 'p1' }, { id: 'p2' }, { id: 'p3' }],
      weeks_available: 0,
      semantic_drift: { report_date: null, findings: [], drift_count: 0, at_risk_cases: [] },
      token_block: TOKEN_BLOCK,
      ...overrides,
    },
  });
}

function renderOverlay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CMBrainOverlay />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  __resetActiveOverlayEvent();
  mockHealth();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function openOverlay() {
  act(() => { window.dispatchEvent(new CustomEvent('swarm:show-context')); });
}

describe('CMBrainOverlay — open/close + tabs', () => {
  it('does not render until swarm:show-context fires', () => {
    renderOverlay();
    expect(screen.queryByTestId('cm-brain-overlay')).toBeNull();
  });

  it('opens on swarm:show-context with the Context tab active', async () => {
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

  it('Memory tab still renders a COMPACT roadmap teaser (not a full-height empty void)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');

    act(() => { screen.getByTestId('cm-tab-memory').click(); });
    const mem = await screen.findByTestId('cm-placeholder-memory');
    // §4: NOT a full-height centered void — no py-16, no justify-center/items-center
    expect(mem.className).not.toContain('py-16');
    expect(mem.className).not.toContain('justify-center');
    expect(mem.className).not.toContain('items-center');
    // teaser communicates value compactly — names the real stores it will surface
    expect(mem.textContent).toMatch(/MEMORY\.md|EVOLUTION\.md/);
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

  it('overview rail shows the live total tokens from the payload', async () => {
    renderOverlay();
    openOverlay();
    const rail = await screen.findByTestId('cm-overview-rail');
    // 100000 tokens → rendered as a compact "100K" (or contains the number).
    // Wait for the async query to resolve into the rail.
    await waitFor(() => expect(rail.textContent).toMatch(/100[,.]?0?K?/));
  });

  it('Needs-you Review count = pending_proposals length; Approve/Action = 0 (never faked)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-overview-rail');
    expect((await screen.findByTestId('cm-needs-review')).textContent).toContain('3');
    expect(screen.getByTestId('cm-needs-approve').textContent).toContain('0');
    expect(screen.getByTestId('cm-needs-action').textContent).toContain('0');
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
