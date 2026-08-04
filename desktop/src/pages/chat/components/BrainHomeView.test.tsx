/**
 * BrainHomeView.test.tsx — the durable Brain Home layer (run_6924b463 cycle 3).
 *
 * Covers: hero selection (attention weight), bento render (hero full + calm compact),
 * the single hero detail-fetch, the batch-review affordance, and the AC5 DURABILITY
 * invariant (independent read: zero brains → renders nothing, never throws/blanks).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BrainHomeView, pickHero, attentionScore } from './BrainHomeView';
import type { BrainSummary, BrainDetail, SectionKey } from '../../../services/ddd';

const SECTIONS: Record<SectionKey, boolean> = {
  identity: true, knowledge: true, gates: false, capabilities: true, delivery: false, refresher: true,
};
const mk = (name: string, over: Partial<BrainSummary['health']> = {}): BrainSummary => ({
  name, kind: 'knowledge', sectionsPresent: SECTIONS, lifecycleStage: 'GROW',
  health: { sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '1d ago', ...over },
});

const HERO_DETAIL: BrainDetail = {
  name: 'Needy', kind: 'knowledge', sections: [],
  health: {
    noise: { reclaimable: 7, rate: 0.2 },
    trust: null, escalationPending: 2,
    recall: { value: null, experimental: true },
    diagnostics: null, computedAt: null,
  },
};

const mockGetBrains = vi.fn();
const mockGetBrainDetail = vi.fn();
vi.mock('../../../services/ddd', async (orig) => ({
  ...(await orig<typeof import('../../../services/ddd')>()),
  getBrains: () => mockGetBrains(),
  getBrainDetail: (n: string) => mockGetBrainDetail(n),
}));

beforeEach(() => {
  mockGetBrains.mockReset();
  mockGetBrainDetail.mockReset();
  mockGetBrainDetail.mockResolvedValue(HERO_DETAIL);
});

describe('BrainHomeView — hero selection (pure)', () => {
  it('attentionScore: uncommitted weighs 2, plus sinking + pending', () => {
    expect(attentionScore({ sinking: 1, pending: 2, uncommitted: true, lastChangeRelative: '' })).toBe(5);
    expect(attentionScore({ sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '' })).toBe(0);
  });
  it('pickHero picks the max-attention brain; ties broken by name (stable, no RNG)', () => {
    const hero = pickHero([mk('Calm'), mk('Needy', { sinking: 3, uncommitted: true }), mk('Mild', { pending: 1 })]);
    expect(hero?.name).toBe('Needy');
    // tie → alphabetical
    expect(pickHero([mk('Zebra'), mk('Apple')])?.name).toBe('Apple');
    expect(pickHero([])).toBeNull();
  });
});

describe('BrainHomeView — bento render', () => {
  it('renders hero (full, with metric tiles from the single detail fetch) + calm compact grid', async () => {
    mockGetBrains.mockResolvedValue([mk('Needy', { sinking: 4, uncommitted: true }), mk('Calm'), mk('Mild', { pending: 1 })]);
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('brain-home')).toBeTruthy());
    // hero is the needy one, rendered full → its metric tiles appear (after detail fetch)
    await waitFor(() => expect(screen.getByTestId('health-tile-noise')).toBeTruthy());
    expect(screen.getByTestId('brain-home-hero')).toBeTruthy();
    // exactly ONE hero detail fetch, for the hero
    expect(mockGetBrainDetail).toHaveBeenCalledTimes(1);
    expect(mockGetBrainDetail).toHaveBeenCalledWith('Needy');
    // calm brains present as compact cards (clickable buttons)
    expect(screen.getByTestId('brain-home-calm')).toBeTruthy();
    expect(screen.getByTestId('dddcard-Calm').tagName).toBe('BUTTON');
    expect(screen.getByTestId('dddcard-Mild').tagName).toBe('BUTTON');
    // hero is NOT duplicated in the calm grid
    expect(screen.queryByTestId('brain-home-calm')?.querySelector('[data-testid="dddcard-Needy"]')).toBeNull();
  });

  it('batch-review affordance calls onOpenHub; compact card calls onOpenBrain', async () => {
    mockGetBrains.mockResolvedValue([mk('Needy', { sinking: 4 }), mk('Calm')]);
    const onOpenHub = vi.fn();
    const onOpenBrain = vi.fn();
    render(<BrainHomeView onOpenHub={onOpenHub} onOpenBrain={onOpenBrain} />);
    await waitFor(() => expect(screen.getByTestId('brain-home')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-home-batch-review'));
    expect(onOpenHub).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('dddcard-Calm'));
    expect(onOpenBrain).toHaveBeenCalledWith('Calm');
  });
});

describe('BrainHomeView — AC5 durability (independent read)', () => {
  it('zero brains → renders nothing (never a blank box), no detail fetch', async () => {
    mockGetBrains.mockResolvedValue([]);
    const { container } = render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrains).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
    expect(container.textContent).toBe('');
    expect(mockGetBrainDetail).not.toHaveBeenCalled();
  });

  it('getBrains REJECTS → renders nothing, does not throw (briefing survives)', async () => {
    mockGetBrains.mockRejectedValue(new Error('backend down'));
    render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrains).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
  });

  it('hero detail fetch REJECTS → hero still renders (summary), just no metric tiles', async () => {
    mockGetBrains.mockResolvedValue([mk('Needy', { sinking: 4 }), mk('Calm')]);
    mockGetBrainDetail.mockRejectedValue(new Error('detail down'));
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('brain-home-hero')).toBeTruthy());
    // hero summary (presence bar) renders even though tiles don't
    expect(screen.getByTestId('presence-Needy-knowledge')).toBeTruthy();
    expect(screen.queryByTestId('health-tile-noise')).toBeNull();
  });
});
