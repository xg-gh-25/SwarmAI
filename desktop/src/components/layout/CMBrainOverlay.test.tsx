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
import { render, screen, act, cleanup, waitFor } from '@testing-library/react';
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

  it('Memory and Guideline tabs are placeholders (not implemented in Run 1)', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('cm-brain-overlay');
    act(() => { screen.getByTestId('cm-tab-memory').click(); });
    expect(await screen.findByTestId('cm-placeholder-memory')).toBeInTheDocument();
    act(() => { screen.getByTestId('cm-tab-guideline').click(); });
    expect(await screen.findByTestId('cm-placeholder-guideline')).toBeInTheDocument();
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
