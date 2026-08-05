/**
 * DddCard.test.tsx — the unified density-driven DDD card.
 *
 * run_6924b463 c2-3: created the SSOT card (compact gallery + full detail/hero).
 * run_d1e933aa c2:   restructured full-density metrics into USER JUDGMENT LANGUAGE
 *                    — 4 questions + a 7-type×3-layer "type mix" bar.
 *
 * The 4 questions (a user opening a brain actually asks these, not "what are the
 * 7 raw fields"):
 *   Q1 healthy? → trust badge (distribution, NOT a rollup verdict) + computedAt age
 *   Q2 fresh?   → lastChangeRelative (hero) / computedAt age (detail)
 *   Q3 growing? → recentActivity (30d changelog) + escalationPending
 *   Q4 prune?   → noise.reclaimable + sinking
 * Type-mix bar: aggregates entries[].entryType → 3 layers (meta/cognitive/
 * operational), detail-only, labeled honestly as "知识文档类型分布".
 *
 * Load-bearing invariants preserved from c2-3:
 *   • density-scoped guard (compact ALWAYS renders; full guards only the tiles block)
 *   • trust is a DISTRIBUTION count (below/total), never a collapsed rollup verdict
 *   • Principle-1: NO size / entry-count / ref-count anywhere on the card
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DddCard } from './DddCard';
import type { BrainHealth, DetailHealth, SectionKey, EntryType } from '../../services/ddd';

const SECTIONS: Record<SectionKey, boolean> = {
  identity: true, knowledge: true, gates: false,
  capabilities: true, delivery: false, refresher: true,
};

const CHEAP: BrainHealth = {
  sinking: 2,
  pending: 1,
  uncommitted: true,
  lastChangeRelative: '3h ago',
};

const METRICS: DetailHealth = {
  noise: { reclaimable: 5, rate: 0.1 },
  trust: { 'PRODUCT.md': { identity: 'high', knowledge: 'moderate' } },
  escalationPending: 3,
  recall: { value: null, experimental: true },
  recentActivity: 12,
  diagnostics: { 'PRODUCT.md': { sections: { identity: { composite: 88, trust: 'high' } } } },
  computedAt: '2026-08-04T00:00:00Z',
};

const TYPE_COUNTS: Record<EntryType, number> = {
  principle: 2, correction: 1,           // meta-cognitive = 3
  decision: 4, model: 1,                 // cognitive = 5
  guideline: 10, pitfall: 8, process: 2, // operational = 20
};

describe('DddCard — compact density (gallery)', () => {
  it('renders presence bar + lifecycle + 4 cheap health, is a clickable button', () => {
    const onOpen = vi.fn();
    render(
      <DddCard density="compact" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="GROW" health={CHEAP} onOpen={onOpen} />,
    );
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-sinking')).toBeTruthy();
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    expect(onOpen).toHaveBeenCalledWith('SwarmAI');
    // compact NEVER shows the expensive judgment blocks or the type bar
    expect(screen.queryByTestId('ddd-q4-prune')).toBeNull();
    expect(screen.queryByTestId('ddd-typebar')).toBeNull();
  });

  it('GATE-1 INVARIANT: compact card renders fully even though it carries NO metrics', () => {
    render(
      <DddCard density="compact" name="IVTHub" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="CREATE" health={CHEAP} onOpen={vi.fn()} />,
    );
    expect(screen.getByTestId('dddcard-IVTHub')).toBeTruthy();
    expect(screen.getByTestId('presence-IVTHub-identity')).toBeTruthy();
  });
});

describe('DddCard — full density: the 4 judgment questions', () => {
  it('renders all 4 question blocks (healthy/fresh/growing/prune) with real signals', () => {
    render(<DddCard density="full" name="SwarmAI" kind="knowledge" metrics={METRICS} />);
    // Q1 healthy — trust badge (distribution 1/2 below-high, NOT a rollup verdict)
    expect(screen.getByTestId('ddd-q1-healthy').textContent).toContain('1/2');
    // Q3 growing — recentActivity surfaced
    expect(screen.getByTestId('ddd-q3-growing').textContent).toContain('12');
    // Q4 prune — noise reclaimable
    expect(screen.getByTestId('ddd-q4-prune').textContent).toContain('5');
    // recall experimental chip preserved
    expect(screen.getByTestId('recall-experimental-chip')).toBeTruthy();
  });

  it('Q1 trust badge shows computedAt AGE (honest staleness), not a bare pass', () => {
    render(<DddCard density="full" name="SwarmAI" kind="knowledge" metrics={METRICS} />);
    const q1 = screen.getByTestId('ddd-q1-healthy');
    expect(q1.textContent && q1.textContent.length > 0).toBe(true);
    expect(screen.getByTestId('ddd-trust-computedat')).toBeTruthy();
  });

  it('Q3 recentActivity undefined (old daemon) → honest "—", not a confident "0"', () => {
    render(<DddCard density="full" name="X" kind="knowledge"
      metrics={{ ...METRICS, recentActivity: undefined }} />);
    expect(screen.getByTestId('ddd-q3-growing').textContent).toContain('—');
  });

  it('Q1 trust null (no scheduled score) → honest "not computed", never fabricated', () => {
    render(<DddCard density="full" name="X" kind="knowledge"
      metrics={{ ...METRICS, trust: null, computedAt: null }} />);
    const q1 = screen.getByTestId('ddd-q1-healthy');
    expect(q1.textContent?.toLowerCase()).toMatch(/not computed|—|no scheduled/);
  });

  it('diagnostics row still available under the questions, omitted when null', () => {
    const { rerender } = render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} />);
    expect(screen.getByTestId('health-diagnostics').textContent).toContain('PRODUCT.md·identity');
    rerender(<DddCard density="full" name="S" kind="knowledge" metrics={{ ...METRICS, diagnostics: null }} />);
    expect(screen.queryByTestId('health-diagnostics')).toBeNull();
  });

  it('PRINCIPLE-1: no size / entry-count / ref-count anywhere on the card', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={TYPE_COUNTS} />);
    const txt = screen.getByTestId('dddcard-S').textContent ?? '';
    expect(txt.toLowerCase()).not.toContain('last referenced');
    expect(txt.toLowerCase()).not.toContain('ref count');
    expect(txt).not.toMatch(/\b\d+\s+entries\b/i);
  });

  it('GATE-1 GUARD: metrics undefined → question blocks render nothing, container still renders', () => {
    render(<DddCard density="full" name="SwarmAI" kind="knowledge" />);
    expect(screen.queryByTestId('ddd-q1-healthy')).toBeNull();
    expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy();
  });

  it('GATE-1 GUARD: metrics present but noise missing (partial daemon) → no blocks, no crash', () => {
    const partial = { ...METRICS, noise: undefined } as unknown as DetailHealth;
    render(<DddCard density="full" name="SwarmAI" kind="knowledge" metrics={partial} />);
    expect(screen.queryByTestId('ddd-q4-prune')).toBeNull();
    expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy();
  });
});

describe('DddCard — 7-type × 3-layer type-mix bar', () => {
  it('renders the 3 layers with correct aggregated counts from typeCounts', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={TYPE_COUNTS} />);
    expect(screen.getByTestId('ddd-typebar')).toBeTruthy();
    expect(screen.getByTestId('ddd-typelayer-meta').textContent).toContain('3');
    expect(screen.getByTestId('ddd-typelayer-cognitive').textContent).toContain('5');
    expect(screen.getByTestId('ddd-typelayer-operational').textContent).toContain('20');
  });

  it('type bar omitted when typeCounts not provided (detail consumer without entries)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} />);
    expect(screen.queryByTestId('ddd-typebar')).toBeNull();
  });

  it('type bar omitted on an all-zero distribution (no vanity empty bar)', () => {
    const zero = { principle: 0, correction: 0, decision: 0, model: 0, guideline: 0, pitfall: 0, process: 0 } as Record<EntryType, number>;
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={zero} />);
    expect(screen.queryByTestId('ddd-typebar')).toBeNull();
  });
});

describe('DddCard — full density, HOME-HERO consumer (summary + metrics + types)', () => {
  it('renders header + presence + lifecycle + cheap health + 4 questions + type bar together', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" health={CHEAP} metrics={METRICS} typeCounts={TYPE_COUNTS} />,
    );
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-sinking')).toBeTruthy();
    expect(screen.getByTestId('ddd-q1-healthy')).toBeTruthy();
    expect(screen.getByTestId('ddd-typebar')).toBeTruthy();
    expect(screen.queryByTestId('dddcard-SwarmAI')?.tagName).not.toBe('BUTTON');
  });

  it('Q2 fresh uses lastChangeRelative when hero cheap-health is present', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" health={CHEAP} metrics={METRICS} />,
    );
    expect(screen.getByTestId('ddd-q2-fresh').textContent).toContain('3h ago');
  });
});
