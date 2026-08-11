/**
 * BrainHomeView.test.tsx — the Deliver-first Welcome landing (Variant A, run_fc7078c4).
 *
 * Covers the 2-tier hierarchy (run_2568c3fb — the pipeline In-flight tier was
 * removed as a wrong-subsystem coupling; runs live in the Jobs & Runs overlay):
 *   TIER 1  Needs your decision — brains with health.pending>0.
 *   TIER 2  Brain pulse         — count + layer bar + primary lastChange + hub button.
 * BrainHomeView now has a SINGLE read (getBrainsWithPinned); it hides only when the
 * brains read resolves empty and never throws to the caller.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BrainHomeView } from './BrainHomeView';
import type { BrainSummary, SectionKey, EntryType } from '../../../services/ddd';

const SECTIONS: Record<SectionKey, boolean> = {
  identity: true, knowledge: true, gates: false, capabilities: true, delivery: false, refresher: true,
};
const TC: Record<EntryType, number> = {
  principle: 2, correction: 1, decision: 4, model: 1, guideline: 10, pitfall: 8, process: 2,
};
const mk = (name: string, over: Partial<BrainSummary['health']> = {}, typeCounts?: Record<EntryType, number>): BrainSummary => ({
  name, kind: 'knowledge', sectionsPresent: SECTIONS, lifecycleStage: 'GROW',
  health: { sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '1d ago', ...over },
  typeCounts,
});

const mockGetBrainsWithPinned = vi.fn();
vi.mock('../../../services/ddd', async (orig) => ({
  ...(await orig<typeof import('../../../services/ddd')>()),
  getBrainsWithPinned: () => mockGetBrainsWithPinned(),
}));

beforeEach(() => {
  mockGetBrainsWithPinned.mockReset();
  mockGetBrainsWithPinned.mockResolvedValue({ brains: [], pinned: [] });
});

describe('BrainHomeView — TIER 1: needs decision', () => {
  it('lists ONLY brains with health.pending>0 (amber rows show pending, NOT sinking)', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({
      brains: [mk('SwarmAI', { pending: 78, sinking: 185 }, TC), mk('AIDLC', { pending: 0 }, TC), mk('CMHK', { pending: 4, sinking: 21 }, TC)],
      pinned: ['SwarmAI', 'AIDLC', 'CMHK'],
    });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-decision')).toBeTruthy());
    expect(screen.getByTestId('decision-SwarmAI')).toBeTruthy();
    expect(screen.getByTestId('decision-CMHK')).toBeTruthy();
    // pending==0 brain excluded
    expect(screen.queryByTestId('decision-AIDLC')).toBeNull();
    expect(screen.getByTestId('decision-SwarmAI').textContent).toContain('78 pending');
    // NEGATIVE guard (run: welcome-declutter, 16525126): `sinking` was deliberately
    // removed from the first screen — it is an internal aging signal with no clear
    // user action, and lives in Brain Hub detail only. Asserting its ABSENCE (rather
    // than just deleting the old positive assertion) is what keeps the product
    // decision enforced: re-adding the badge turns this RED instead of passing silently.
    expect(screen.getByTestId('decision-SwarmAI').textContent).not.toContain('sinking');
    expect(screen.getByTestId('decision-SwarmAI').textContent).not.toContain('185');
  });

  it('decision block absent when no brain has pending>0', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', { pending: 0 }, TC)], pinned: ['SwarmAI'] });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-decision')).toBeNull();
  });
});

describe('BrainHomeView — no pipeline coupling (run_2568c3fb)', () => {
  it('never renders an in-flight tier and never reads pipelines', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', { pending: 3 }, TC)], pinned: ['SwarmAI'] });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-inflight')).toBeNull();
  });
});

describe('BrainHomeView — TIER 2: brain pulse (first-paint, no detail fetch)', () => {
  it('renders count + layer bar from summary.typeCounts + primary lastChange + hub button', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({
      brains: [mk('SwarmAI', { lastChangeRelative: '5m ago' }, TC), mk('AIDLC', {}, TC)],
      pinned: ['SwarmAI', 'AIDLC'],
    });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.getByTestId('pulse-layerbar')).toBeTruthy();  // from typeCounts, NO getBrainDetail
    expect(screen.getByTestId('tier-pulse').textContent).toContain('5m ago');  // primary's lastChange
    expect(screen.getByTestId('brain-home-batch-review')).toBeTruthy();
  });

  it('hub button calls onOpenHub; a decision row calls onOpenBrain', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', { pending: 3 }, TC)], pinned: ['SwarmAI'] });
    const onOpenHub = vi.fn(); const onOpenBrain = vi.fn();
    render(<BrainHomeView onOpenHub={onOpenHub} onOpenBrain={onOpenBrain} />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-home-batch-review'));
    expect(onOpenHub).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('decision-SwarmAI'));
    expect(onOpenBrain).toHaveBeenCalledWith('SwarmAI');
  });
});

describe('BrainHomeView — durability (single brains read)', () => {
  it('brains read REJECTS → renders nothing (never throws, never a blank box)', async () => {
    mockGetBrainsWithPinned.mockRejectedValue(new Error('brains down'));
    const { container } = render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('brains load with pending=0 → pulse shows, decision hidden', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', {}, TC)], pinned: ['SwarmAI'] });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-decision')).toBeNull();
    expect(screen.queryByTestId('tier-inflight')).toBeNull();
  });

  it('brains EMPTY → renders nothing (never a blank box)', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [], pinned: [] });
    const { container } = render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
    expect(container.textContent).toBe('');
  });
});
