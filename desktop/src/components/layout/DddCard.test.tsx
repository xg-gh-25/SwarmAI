/**
 * DddCard.test.tsx — the unified density-driven DDD card (run_6924b463 cycle 2).
 *
 * Tests the SSOT card that replaces the old BrainCard (gallery) + HealthStrip
 * (detail) split. Two densities:
 *   • compact — clickable, cheap health only (sinking/pending/uncommitted/lastChange)
 *   • full    — static, expensive metric tiles (noise/trust/escalation/recall)
 *
 * Load-bearing invariant (Gate-1 correction, run_6924b463): the density-aware
 * guard. compact NEVER guards on `noise` (a compact card carries no metrics and
 * must ALWAYS render — a blank gallery card is the bug the skeptic caught). full
 * guards ONLY the metric-tiles block on `metrics.noise`, so a daemon-skew partial
 * payload degrades the tiles to render-nothing WITHOUT blanking the whole card.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DddCard } from './DddCard';
import type { BrainHealth, DetailHealth, SectionKey } from '../../services/ddd';

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
  diagnostics: { 'PRODUCT.md': { sections: { identity: { composite: 88, trust: 'high' } } } },
  computedAt: '2026-08-04T00:00:00Z',
};

describe('DddCard — compact density (gallery)', () => {
  it('renders presence bar + lifecycle + 4 cheap health, is a clickable button', () => {
    const onOpen = vi.fn();
    render(
      <DddCard density="compact" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="GROW" health={CHEAP} onOpen={onOpen} />,
    );
    // six-section presence bar
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
    expect(screen.getByTestId('presence-SwarmAI-gates')).toBeTruthy();
    // cheap health tiles
    expect(screen.getByTestId('dddcard-cheap-sinking')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-pending')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-uncommitted')).toBeTruthy();
    expect(screen.getByTestId('dddcard-cheap-lastchange')).toBeTruthy();
    // clickable
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    expect(onOpen).toHaveBeenCalledWith('SwarmAI');
    // compact NEVER shows expensive metric tiles
    expect(screen.queryByTestId('health-tile-noise')).toBeNull();
  });

  it('GATE-1 INVARIANT: compact card renders fully even though it carries NO metrics', () => {
    // The skeptic caught: a unified `health?.noise` whole-card guard would blank
    // every gallery card (BrainSummary has no noise). compact must always render.
    render(
      <DddCard density="compact" name="IVTHub" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="CREATE" health={CHEAP} onOpen={vi.fn()} />,
    );
    expect(screen.getByTestId('dddcard-IVTHub')).toBeTruthy();
    expect(screen.getByTestId('presence-IVTHub-identity')).toBeTruthy();
  });
});

describe('DddCard — full density (detail)', () => {
  it('renders the 4 expensive metric tiles when metrics.noise is present', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" metrics={METRICS} />,
    );
    expect(screen.getByTestId('health-tile-noise')).toBeTruthy();
    expect(screen.getByTestId('health-tile-trust')).toBeTruthy();
    expect(screen.getByTestId('health-tile-escalation')).toBeTruthy();
    expect(screen.getByTestId('health-tile-recall')).toBeTruthy();
    // recall is experimental → chip present
    expect(screen.getByTestId('recall-experimental-chip')).toBeTruthy();
    // full density is NOT a clickable open-button
    expect(screen.queryByTestId('dddcard-SwarmAI')?.tagName).not.toBe('BUTTON');
    // HealthStrip parity: the demoted diagnostics row is NOT dropped (finding #4)
    const diag = screen.getByTestId('health-diagnostics');
    expect(diag.textContent).toContain('PRODUCT.md·identity');
    expect(diag.textContent).toContain('88');
  });

  it('diagnostics row is omitted when diagnostics is null (no scheduled score)', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW"
        metrics={{ ...METRICS, diagnostics: null }} />,
    );
    expect(screen.getByTestId('health-tile-noise')).toBeTruthy();
    expect(screen.queryByTestId('health-diagnostics')).toBeNull();
  });

  it('GATE-1 GUARD: metrics undefined → tiles block renders nothing, card still renders', () => {
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" />,
    );
    // tiles gone (daemon-skew guard)…
    expect(screen.queryByTestId('health-tile-noise')).toBeNull();
    // …but the card body (presence) still renders — NOT blanked
    expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy();
  });

  it('GATE-1 GUARD: metrics present but noise missing (partial daemon payload) → no tiles, no crash', () => {
    const partial = { ...METRICS, noise: undefined } as unknown as DetailHealth;
    render(
      <DddCard density="full" name="SwarmAI" kind="knowledge"
        sectionsPresent={SECTIONS} lifecycleStage="REVIEW" metrics={partial} />,
    );
    expect(screen.queryByTestId('health-tile-noise')).toBeNull();
    expect(screen.getByTestId('presence-SwarmAI-identity')).toBeTruthy();
  });
});
