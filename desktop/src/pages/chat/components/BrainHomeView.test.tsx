/**
 * BrainHomeView.test.tsx — the Deliver-first Welcome landing (Variant A, run_fc7078c4).
 *
 * Covers the 3-tier hierarchy + the hardened durability invariant: brains
 * (getBrainsWithPinned) and runs (fetchActivePipelines) are INDEPENDENT reads —
 * one failing must not blank the other; the view hides only when BOTH are empty.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BrainHomeView } from './BrainHomeView';
import type { BrainSummary, SectionKey, EntryType } from '../../../services/ddd';
import type { PipelineRun } from '../../../services/pipelines';

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
const run = (id: string, status: PipelineRun['status'], pauseKind: PipelineRun['pauseKind'], requirement = 'do a thing'): PipelineRun => ({
  id, project: 'SwarmAI', requirement, status, currentStage: 'build', checkpointReason: null, pauseKind, progress: '4/8', updatedAt: '',
});

const mockGetBrainsWithPinned = vi.fn();
const mockFetchActive = vi.fn();
vi.mock('../../../services/ddd', async (orig) => ({
  ...(await orig<typeof import('../../../services/ddd')>()),
  getBrainsWithPinned: () => mockGetBrainsWithPinned(),
}));
vi.mock('../../../services/pipelines', async (orig) => ({
  ...(await orig<typeof import('../../../services/pipelines')>()),
  pipelinesService: { fetchActivePipelines: () => mockFetchActive() },
}));

beforeEach(() => {
  mockGetBrainsWithPinned.mockReset();
  mockFetchActive.mockReset();
  mockGetBrainsWithPinned.mockResolvedValue({ brains: [], pinned: [] });
  mockFetchActive.mockResolvedValue([]);
});

describe('BrainHomeView — TIER 1: needs decision', () => {
  it('lists ONLY brains with health.pending>0 (amber rows w/ pending + sinking)', async () => {
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
    expect(screen.getByTestId('decision-SwarmAI').textContent).toContain('185 sinking');
  });

  it('decision block absent when no brain has pending>0', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', { pending: 0 }, TC)], pinned: ['SwarmAI'] });
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-decision')).toBeNull();
  });
});

describe('BrainHomeView — TIER 2: in flight (real pills, crash_residue filtered)', () => {
  it('maps running→running pill, paused+decision→needs-decision; FILTERS crash_residue', async () => {
    mockFetchActive.mockResolvedValue([
      run('run_a', 'running', null, 'Canvas rail store'),
      run('run_b', 'paused', 'decision', 'Welcome layout'),
      run('run_c', 'paused', 'crash_residue', 'orphaned run'),   // must be filtered
    ]);
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-inflight')).toBeTruthy());
    expect(screen.getByTestId('inflight-run_a').textContent).toContain('running');
    expect(screen.getByTestId('inflight-run_b').textContent).toContain('needs decision');
    // crash_residue paused run is NOT a decision → filtered out
    expect(screen.queryByTestId('inflight-run_c')).toBeNull();
    // no fabricated push-ready pill anywhere
    expect(screen.getByTestId('tier-inflight').textContent?.toLowerCase()).not.toContain('push-ready');
  });

  it('caps at 6 rows but shows "+N more" (no silent truncation) + total in header', async () => {
    const many = Array.from({ length: 10 }, (_, i) => run(`run_${i}`, 'running', null, `task ${i}`));
    mockFetchActive.mockResolvedValue(many);
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-inflight')).toBeTruthy());
    // only 6 rows rendered
    expect(screen.getByTestId('inflight-run_5')).toBeTruthy();
    expect(screen.queryByTestId('inflight-run_6')).toBeNull();
    // overflow disclosed, header shows true total
    expect(screen.getByTestId('inflight-overflow').textContent).toContain('+4 more');
    expect(screen.getByTestId('tier-inflight').textContent).toContain('10 run');
  });

  it('in-flight block absent when no active runs', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', {}, TC)], pinned: ['SwarmAI'] });
    mockFetchActive.mockResolvedValue([run('run_x', 'completed', null)]);  // completed is not active-inflight
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-inflight')).toBeNull();
  });
});

describe('BrainHomeView — TIER 3: brain pulse (first-paint, no detail fetch)', () => {
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

describe('BrainHomeView — durability (INDEPENDENT reads)', () => {
  it('brains read REJECTS but runs load → in-flight still shows (tier1/3 hidden, tier2 survives)', async () => {
    mockGetBrainsWithPinned.mockRejectedValue(new Error('brains down'));
    mockFetchActive.mockResolvedValue([run('run_a', 'running', null)]);
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-inflight')).toBeTruthy());
    expect(screen.queryByTestId('tier-decision')).toBeNull();
    expect(screen.queryByTestId('tier-pulse')).toBeNull();
  });

  it('runs read REJECTS but brains load → pulse still shows (tier2 hidden)', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [mk('SwarmAI', {}, TC)], pinned: ['SwarmAI'] });
    mockFetchActive.mockRejectedValue(new Error('runs down'));
    render(<BrainHomeView />);
    await waitFor(() => expect(screen.getByTestId('tier-pulse')).toBeTruthy());
    expect(screen.queryByTestId('tier-inflight')).toBeNull();
  });

  it('BOTH empty → renders nothing (never a blank box)', async () => {
    mockGetBrainsWithPinned.mockResolvedValue({ brains: [], pinned: [] });
    mockFetchActive.mockResolvedValue([]);
    const { container } = render(<BrainHomeView />);
    await waitFor(() => expect(mockGetBrainsWithPinned).toHaveBeenCalled());
    await waitFor(() => expect(mockFetchActive).toHaveBeenCalled());
    expect(screen.queryByTestId('brain-home')).toBeNull();
    expect(container.textContent).toBe('');
  });
});
