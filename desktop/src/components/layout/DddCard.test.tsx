/**
 * DddCard.test.tsx — the unified density-driven DDD card.
 *
 * run_9ada46ae: final mockup-driven design.
 *   full   → verdict dot (pending>0 = "needs decision", NEVER "healthy" — no trust
 *            rollup) + the FULL 3-layer×7-type ontology (each layer count + each
 *            type count) as the hero visual + a "needs you" block (non-zero
 *            actionable only; clean brain → "nothing needs you") + 2 fact lines
 *            (trust distribution / activity). Diagnostics wall DELETED.
 *   compact→ presence + lifecycle + cheap health + a slim 3-LAYER proportion bar
 *            (from summary.typeCounts, NO detail fetch).
 *
 * Invariants preserved: density-scoped guard (compact always renders); trust is a
 * DISTRIBUTION count, never a collapsed rollup; NO size/entry-count/ref-count.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DddCard } from './DddCard';
import type { BrainHealth, DetailHealth, SectionKey, EntryType } from '../../services/ddd';

const SECTIONS: Record<SectionKey, boolean> = {
  identity: true, knowledge: true, gates: false,
  capabilities: true, delivery: false, refresher: true,
};
const CHEAP: BrainHealth = { sinking: 2, pending: 1, uncommitted: true, lastChangeRelative: '3h ago' };
const CHEAP_CLEAN: BrainHealth = { sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '5d ago' };
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
  principle: 2, correction: 1,           // meta = 3
  decision: 4, model: 1,                 // cognitive = 5
  guideline: 10, pitfall: 8, process: 2, // operational = 20
};

describe('DddCard — compact (gallery)', () => {
  it('renders presence + cheap health, clickable, and a 3-layer proportion bar from typeCounts', () => {
    const onOpen = vi.fn();
    render(
      <DddCard density="compact" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="GROW" health={CHEAP}
        typeCounts={TYPE_COUNTS} onOpen={onOpen} />,
    );
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-sinking')).toBeTruthy();
    // compact gets the slim 3-layer bar (no per-type breakdown, no fetch)
    expect(screen.getByTestId('ddd-compact-layerbar')).toBeTruthy();
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    expect(onOpen).toHaveBeenCalledWith('SwarmAI');
    // compact NEVER shows the full ontology (per-type) or the needs-you block
    expect(screen.queryByTestId('ddd-ontology')).toBeNull();
    expect(screen.queryByTestId('ddd-needs-you')).toBeNull();
  });

  it('compact with NO typeCounts (daemon skew) still renders, just no bar', () => {
    render(
      <DddCard density="compact" name="X" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="CREATE" health={CHEAP} onOpen={vi.fn()} />,
    );
    expect(screen.getByTestId('dddcard-X')).toBeTruthy();
    expect(screen.queryByTestId('ddd-compact-layerbar')).toBeNull();
  });
});

describe('DddCard — full: verdict dot', () => {
  it('pending>0 → "needs decision" verdict (amber), NOT "healthy"', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} health={CHEAP} />);
    const v = screen.getByTestId('ddd-verdict');
    expect(v.textContent?.toLowerCase()).toMatch(/needs decision|待决策|decision/i);
    // MUST NOT claim healthy (no trust rollup verdict)
    expect(v.textContent?.toLowerCase()).not.toContain('healthy');
  });

  it('pending=0 → "nothing queued" verdict, and it does NOT consult trust (0% trust ≠ unhealthy)', () => {
    const clean = { ...METRICS, escalationPending: 0, trust: null, computedAt: null };
    render(<DddCard density="full" name="S" kind="knowledge" metrics={clean} health={CHEAP_CLEAN} />);
    const v = screen.getByTestId('ddd-verdict');
    expect(v.textContent?.toLowerCase()).toMatch(/nothing|无待|queued|clear/i);
    expect(v.textContent?.toLowerCase()).not.toContain('unhealthy');
  });
});

describe('DddCard — full: FULL 3-layer × 7-type ontology (hero visual)', () => {
  it('renders every layer with its count AND every type with its count', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={TYPE_COUNTS} />);
    const onto = screen.getByTestId('ddd-ontology');
    expect(onto).toBeTruthy();
    // layer totals
    expect(screen.getByTestId('ddd-layer-meta').textContent).toContain('3');
    expect(screen.getByTestId('ddd-layer-cognitive').textContent).toContain('5');
    expect(screen.getByTestId('ddd-layer-operational').textContent).toContain('20');
    // per-type counts must be visible (the whole point — "nobody could tell the ontology")
    expect(screen.getByTestId('ddd-type-principle').textContent).toContain('2');
    expect(screen.getByTestId('ddd-type-decision').textContent).toContain('4');
    expect(screen.getByTestId('ddd-type-pitfall').textContent).toContain('8');
  });

  it('ontology omitted when no typeCounts (detail fetch pending / daemon skew)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} />);
    expect(screen.queryByTestId('ddd-ontology')).toBeNull();
  });

  // run_b4d3eeeb: kill the load-shift. Ontology comes from summary.typeCounts
  // (available first paint), so it must render BEFORE metrics arrive — not gated
  // on the 2nd fetch. The metrics-block (needs-you/facts) is what arrives late,
  // and a skeleton must reserve its height so its arrival causes no jump.
  it('ontology renders on FIRST PAINT from typeCounts WITHOUT metrics (no load shift)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" typeCounts={TYPE_COUNTS} />);
    // ontology visible before metrics resolve
    expect(screen.getByTestId('ddd-ontology')).toBeTruthy();
    expect(screen.getByTestId('ddd-layer-operational').textContent).toContain('20');
    // metrics-dependent blocks are NOT shown yet (no metrics)
    expect(screen.queryByTestId('ddd-needs-you')).toBeNull();
    expect(screen.queryByTestId('ddd-fact-trust')).toBeNull();
    // a skeleton reserves the metrics-block height (prevents the jump on arrival)
    expect(screen.getByTestId('ddd-metrics-skeleton')).toBeTruthy();
  });

  it('no skeleton once metrics have arrived (real metrics-block replaces it)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={TYPE_COUNTS} />);
    expect(screen.queryByTestId('ddd-metrics-skeleton')).toBeNull();
    expect(screen.getByTestId('ddd-needs-you')).toBeTruthy();
  });

  it('with NEITHER metrics NOR typeCounts → body renders nothing (container survives)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" />);
    expect(screen.queryByTestId('ddd-ontology')).toBeNull();
    expect(screen.queryByTestId('ddd-metrics-skeleton')).toBeNull();
    expect(screen.getByTestId('dddcard-S')).toBeTruthy();
  });
});

describe('DddCard — full: needs-you block', () => {
  it('lists non-zero actionable items (pending + sinking) when present', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} health={CHEAP} />);
    const n = screen.getByTestId('ddd-needs-you');
    expect(n.textContent).toContain('3');  // escalationPending
    expect(n.textContent?.toLowerCase()).toMatch(/review|裁决|proposal/i);
  });

  it('clean brain → "nothing needs you" (0 pending, 0 reclaimable, 0 sinking)', () => {
    const clean = { ...METRICS, escalationPending: 0, noise: { reclaimable: 0, rate: 0 } };
    render(<DddCard density="full" name="S" kind="knowledge" metrics={clean} health={CHEAP_CLEAN} />);
    const n = screen.getByTestId('ddd-needs-you');
    expect(n.textContent?.toLowerCase()).toMatch(/nothing|✓|无/i);
  });
});

describe('DddCard — full: facts + deletions', () => {
  it('trust fact is a DISTRIBUTION (below/total), never a rollup verdict word', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} />);
    const f = screen.getByTestId('ddd-fact-trust');
    expect(f.textContent).toMatch(/1\/2|50%|below|≥ high/);
    expect(f.textContent).not.toContain('moderate');  // no collapsed verdict
  });

  it('diagnostics wall is DELETED (no per-section score dump)', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} />);
    expect(screen.queryByTestId('health-diagnostics')).toBeNull();
  });

  it('PRINCIPLE-1: no size / entry-count / ref-count anywhere', () => {
    render(<DddCard density="full" name="S" kind="knowledge" metrics={METRICS} typeCounts={TYPE_COUNTS} />);
    const txt = screen.getByTestId('dddcard-S').textContent ?? '';
    expect(txt.toLowerCase()).not.toContain('last referenced');
    expect(txt.toLowerCase()).not.toContain('ref count');
    // hardened: the ontology header must NOT carry a total "entries" count (a size
    // number). Check the ontology subtree's text directly for the word "entries"
    // — the old \b\d+\s+entries pattern was silently defeated by "types28" (no
    // word-boundary between adjacent spans), a false-negative the adversary caught.
    const onto = screen.getByTestId('ddd-ontology');
    expect(onto.textContent?.toLowerCase()).not.toContain('entries');
  });

  it('GATE-1 GUARD: metrics undefined → judgment body renders nothing, container survives', () => {
    render(<DddCard density="full" name="S" kind="knowledge" />);
    expect(screen.queryByTestId('ddd-verdict')).toBeNull();
    expect(screen.getByTestId('dddcard-S')).toBeTruthy();
  });
});

describe('DddCard — full HERO (summary + metrics + types)', () => {
  it('renders header + presence + verdict + ontology + needs-you together, static (not a button)', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" health={CHEAP}
        metrics={METRICS} typeCounts={TYPE_COUNTS} />,
    );
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    expect(screen.getByTestId('ddd-verdict')).toBeTruthy();
    expect(screen.getByTestId('ddd-ontology')).toBeTruthy();
    expect(screen.getByTestId('ddd-needs-you')).toBeTruthy();
    expect(screen.queryByTestId('dddcard-SwarmAI')?.tagName).not.toBe('BUTTON');
  });
});
