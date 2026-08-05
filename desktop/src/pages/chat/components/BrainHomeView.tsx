/**
 * BrainHomeView.tsx — the durable "Brain Home" layer on the empty-state landing
 * (run_6924b463, cycle 3, design 2026-08-04).
 *
 * A bento of DddCards mounted ABOVE the session-briefing on WelcomeScreen:
 *   • hero  — the ONE brain that most needs attention (max cheap-signal weight),
 *             rendered as a full DddCard with its metric tiles. Metrics come from a
 *             SINGLE lazy getBrainDetail(heroName) fetch (design: "1 hero detail-fetch").
 *   • calm  — every other brain as a compact DddCard (from the getBrains summary list;
 *             no per-brain detail fetch — cheap).
 *   • hub-root batch-review strip — a single affordance to open Brain Hub.
 *
 * DURABILITY INVARIANT (AC5): this layer reads getBrains INDEPENDENTLY of the
 * session-briefing. An empty/failed briefing must NOT blank the brain layer, and an
 * empty/failed brain read must NOT blank the briefing. Two reads, two owners. On its
 * own read failure / zero brains, BrainHomeView renders nothing (never throws).
 *
 * "needs attention" weight (cheap signals only — no detail fetch to rank): uncommitted
 * (2) + sinking + pending. Ties broken by name for stable ordering (no Math.random).
 */
import { useEffect, useState } from 'react';
import { getBrains, getBrainDetail, aggregateTypeCounts } from '../../../services/ddd';
import type { BrainSummary, DetailHealth, EntryType } from '../../../services/ddd';
import { DddCard } from '../../../components/layout/DddCard';

export function attentionScore(h: BrainSummary['health']): number {
  return (h.uncommitted ? 2 : 0) + h.sinking + h.pending;
}

/** Pick the hero: highest attention score, ties broken by name (stable, no RNG). */
export function pickHero(brains: BrainSummary[]): BrainSummary | null {
  if (brains.length === 0) return null;
  return [...brains].sort((a, b) => {
    const d = attentionScore(b.health) - attentionScore(a.health);
    return d !== 0 ? d : a.name.localeCompare(b.name);
  })[0];
}

export interface BrainHomeViewProps {
  onOpenHub?: () => void;
  onOpenBrain?: (name: string) => void;
}

export function BrainHomeView({ onOpenHub, onOpenBrain }: BrainHomeViewProps) {
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [heroMetrics, setHeroMetrics] = useState<DetailHealth | undefined>(undefined);
  const [heroTypeCounts, setHeroTypeCounts] = useState<Record<EntryType, number> | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    getBrains().then(
      (b) => { if (alive) setBrains(b); },
      () => { if (alive) setBrains([]); },   // own-read failure → render nothing, never blank
    );
    return () => { alive = false; };
  }, []);

  const hero = brains ? pickHero(brains) : null;

  // ONE lazy detail fetch, only for the hero, only after we know who it is.
  useEffect(() => {
    if (!hero) return;
    let alive = true;
    getBrainDetail(hero.name).then(
      (d) => {
        if (!alive) return;
        setHeroMetrics(d.health);
        setHeroTypeCounts(aggregateTypeCounts(d.sections));  // type-mix bar (Gate-1 data path)
      },
      () => { if (alive) { setHeroMetrics(undefined); setHeroTypeCounts(undefined); } },  // tiles just won't render
    );
    return () => { alive = false; };
  }, [hero?.name]);

  // Still loading, or genuinely zero brains → render nothing (durability: never a blank box).
  if (brains === null || brains.length === 0 || !hero) return null;

  const calm = brains.filter((b) => b.name !== hero.name);

  const openBrain = (name: string) => onOpenBrain?.(name);

  return (
    <div className="w-full max-w-2xl mt-2" data-testid="brain-home">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)]">Your Brains</span>
        <button
          data-testid="brain-home-batch-review"
          onClick={() => onOpenHub?.()}
          className="text-[11px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-0.5 hover:bg-[#12233a]"
          title="Open Brain Hub — review all brains"
        >
          Review all
        </button>
      </div>

      {/* bento: hero spans full width (rich full card), calm brains in a compact grid */}
      <div data-testid="brain-home-hero" className="mb-3">
        <DddCard
          density="full"
          name={hero.name}
          kind={hero.kind}
          sectionsPresent={hero.sectionsPresent}
          lifecycleStage={hero.lifecycleStage}
          health={hero.health}
          metrics={heroMetrics}
          typeCounts={heroTypeCounts}
        />
      </div>

      {calm.length > 0 && (
        <div
          data-testid="brain-home-calm"
          className="grid gap-2"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
        >
          {calm.map((b) => (
            <DddCard
              key={b.name}
              density="compact"
              name={b.name}
              kind={b.kind}
              sectionsPresent={b.sectionsPresent}
              lifecycleStage={b.lifecycleStage}
              health={b.health}
              onOpen={openBrain}
            />
          ))}
        </div>
      )}
    </div>
  );
}
