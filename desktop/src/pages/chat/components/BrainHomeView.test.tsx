/**
 * BrainHomeView.test.tsx — the durable Brain Home layer (run_9ada46ae Top-3).
 *
 * Covers: Top-3 locked render (primary full + pinned smalls stacked), the single
 * primary detail-fetch, the "view all" affordance, and the durability invariant
 * (independent read: zero brains / reject → renders nothing, never throws/blanks).
 * pickHero/attentionScore retired (pinned is backend-driven, not attention-picked).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BrainHomeView } from './BrainHomeView';
import type { BrainSummary, BrainDetail, SectionKey } from '../../../services/ddd';

const SECTIONS: Record<SectionKey, boolean> = {
  identity: true, knowledge: true, gates: false, capabilities: true, delivery: false, refresher: true,
};
const mk = (name: string, over: Partial<BrainSummary['health']> = {}): BrainSummary => ({
  name, kind: 'knowledge', sectionsPresent: SECTIONS, lifecycleStage: 'GROW',
  health: { sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '1d ago', ...over },
});

const PRIMARY_DETAIL: BrainDetail = {
  name: 'SwarmAI', kind: 'knowledge', sections: [],
  health: {
    noise: { reclaimable: 7, rate: 0.2 },
    trust: null, escalationPending: 2,
    recall: { value: null, experimental: true },
    recentActivity: 40, diagnostics: null, computedAt: null,
  },
};

const mockGetBrainsWithPinned = vi.fn();
const mockGetBrainDetail = vi.fn();
vi.mock('../../../services/ddd', async (orig) => ({
  ...(await orig<typeof import('../../../services/ddd')>()),
  getBrainsWithPinned: () => mockGetBrainsWithPinned(),
  getBrainDetail: (n: string) => mockGetBrainDetail(n),
}));

beforeEach(() => {
  mockGetBrainsWithPinned.mockReset();
  mockGetBrainDetail.mockReset();
  mockGetBrainDetail.mockResolvedValue(PRIMARY_DETAIL);
});

describe('BrainHomeView — Top-3 locked render', () => {
  it('renders primary (full) + pinned smalls stacked; ONE detail fetch (primary only)', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({
      brains: [mk('SwarmAI', { pending: 3 }), mk('AIDLC', { sinking: 2 }), mk('CMHK_SalesIntel'), mk('Other')],
      pinned: ['SwarmAI', 'AIDLC', 'CMHK_SalesIntel'],
    });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('brain-home')).toBeTruthy());
    // primary rendered full → verdict in the hero (scope: pins also carry a verdict dot)
    const heroEl = screen.getByTestId('brain-home-hero');
    await waitFor(() => expect(heroEl.querySelector('[data-testid="ddd-verdict"]')).toBeTruthy());
    expect(screen.getByTestId('brain-home-hero')).toBeTruthy();
    // exactly ONE detail fetch, for the primary (SwarmAI)
    expect(mockGetBrainDetail).toHaveBeenCalledTimes(1);
    expect(mockGetBrainDetail).toHaveBeenCalledWith('SwarmAI');
    // the 2 right pins render as compact clickable cards
    expect(screen.getByTestId('brain-home-pins')).toBeTruthy();
    expect(screen.getByTestId('dddcard-AIDLC').tagName).toBe('BUTTON');
    expect(screen.getByTestId('dddcard-CMHK_SalesIntel').tagName).toBe('BUTTON');
    // NON-pinned brain (Other) is NOT shown here — it lives in Brain Hub
    expect(screen.queryByTestId('dddcard-Other')).toBeNull();
  });

  it('view-all affordance calls onOpenHub; a pinned small calls onOpenBrain', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({
      brains: [mk('SwarmAI'), mk('AIDLC')],
      pinned: ['SwarmAI', 'AIDLC'],
    });
    const onOpenHub = vi.fn();
    const onOpenBrain = vi.fn();
    render(<BrainHomeView onOpenHub={onOpenHub} onOpenBrain={onOpenBrain} />);
    await waitFor(() => expect(screen.getByTestId('brain-home')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-home-batch-review'));
    expect(onOpenHub).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('dddcard-AIDLC'));
    expect(onOpenBrain).toHaveBeenCalledWith('AIDLC');
  });
});

describe('BrainHomeView — durability (independent read)', () => {
  it('zero brains → renders nothing (never a blank box), no detail fetch', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [], pinned: [] });
    const { container } = render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
    expect(container.textContent).toBe('');
    expect(mockGetBrainDetail).not.toHaveBeenCalled();
  });

  it('read REJECTS → renders nothing, does not throw (briefing survives)', async () => {
    mockGetBrainsWithPinned.mockRejectedValue(new Error('backend down'));
    render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
  });

  it('no resolvable primary (pinned name absent from brains) → renders nothing', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('Other')], pinned: ['SwarmAI'] });
    render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
  });

  it('primary detail fetch REJECTS → primary still renders (summary), just no judgment body', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', { pending: 1 }), mk('AIDLC')], pinned: ['SwarmAI', 'AIDLC'] });
    mockGetBrainDetail.mockRejectedValue(new Error('detail down'));
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('brain-home-hero')).toBeTruthy());
    // primary summary (presence bar + verdict from cheap pending) renders even without detail
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    const heroEl = screen.getByTestId('brain-home-hero');
    expect(heroEl.querySelector('[data-testid="ddd-verdict"]')).toBeTruthy();
    // no judgment body (ontology/needs-you) without metrics
    expect(heroEl.querySelector('[data-testid="ddd-needs-you"]')).toBeNull();
  });
});
