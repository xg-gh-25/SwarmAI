/**
 * BrainHomeView.tsx — the durable "Brain Home" layer on the empty-state landing.
 *
 * run_9ada46ae (Top-3 layout): the Welcome screen shows ONLY the pinned Top-3 —
 * the primary (SwarmAI) as a full ontology card on the left + up to 2 pinned focus
 * brains as small cards stacked on the right. Everything else lives in Brain Hub
 * (a "view all" link). Pinned order is backend-driven (getBrainsWithPinned →
 * project_registry.get_pinned_projects: SwarmAI first, existence-guarded).
 *
 * REPLACES the old attention-picked hero (pickHero/attentionScore, retired) — the
 * pinned set is a deliberate product choice (the focus projects), not "the noisiest
 * brain". A non-pinned brain that needs attention is surfaced in Brain Hub, not here.
 *
 * DURABILITY INVARIANT: this layer reads brains INDEPENDENTLY of the session
 * briefing. On its own read failure / zero brains, it renders nothing (never throws,
 * never blanks the briefing). ONE lazy detail fetch for the primary full card only.
 */
import { useEffect, useState } from 'react';
import { getBrainsWithPinned, getBrainDetail, aggregateTypeCounts } from '../../../services/ddd';
import type { BrainSummary, DetailHealth, EntryType } from '../../../services/ddd';
import { DddCard } from '../../../components/layout/DddCard';

export interface BrainHomeViewProps {
  onOpenHub?: () => void;
  onOpenBrain?: (name: string) => void;
}

export function BrainHomeView({ onOpenHub, onOpenBrain }: BrainHomeViewProps) {
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [pinned, setPinned] = useState<string[]>([]);
  const [primaryMetrics, setPrimaryMetrics] = useState<DetailHealth | undefined>(undefined);
  const [primaryTypeCounts, setPrimaryTypeCounts] = useState<Record<EntryType, number> | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    getBrainsWithPinned().then(
      ({ brains: b, pinned: p }) => {
        if (!alive) return;
        setBrains(b);
        setPinned(p);
        const primary = p[0];
        if (primary) {
          getBrainDetail(primary).then(
            (d) => { if (alive) { setPrimaryMetrics(d.health); setPrimaryTypeCounts(aggregateTypeCounts(d.sections)); } },
            () => { if (alive) { setPrimaryMetrics(undefined); setPrimaryTypeCounts(undefined); } },
          );
        }
      },
      () => { if (alive) setBrains([]); },   // own-read failure → render nothing, never blank
    );
    return () => { alive = false; };
  }, []);

  // loading / zero brains → render nothing (never a blank box)
  if (brains === null || brains.length === 0) return null;

  const byName = new Map(brains.map((b) => [b.name, b]));
  const primary = pinned[0] ? byName.get(pinned[0]) : undefined;
  const rightPins = pinned.slice(1).map((n) => byName.get(n)).filter((b): b is BrainSummary => !!b);
  if (!primary) return null;  // no resolvable primary → nothing (rest is in Brain Hub)

  const openBrain = (name: string) => onOpenBrain?.(name);

  return (
    <div className="w-full mt-2" data-testid="brain-home">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)]">Your Brains · Top 3</span>
        <button
          data-testid="brain-home-batch-review"
          onClick={() => onOpenHub?.()}
          className="text-[11px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-0.5 hover:bg-[#12233a]"
          title="Open Brain Hub — view all brains"
        >
          View all in Brain Hub →
        </button>
      </div>

      {/* Top-3 bento: primary full card (left) + up to 2 pinned small stacked (right).
          run_b4d3eeeb:
          • ≥2 pins → 2-col grid, items-stretch so the right cell matches the hero
            height and the pins fill it (flex-1 + h-full button), no bottom-right gap.
          • <2 pins → collapse to a single 1fr column (no reserved 260px empty gap),
            and items-start so a lone pin sits at natural height (no over-stretch). */}
      <div
        className={`grid gap-3 ${rightPins.length >= 2 ? 'items-stretch' : 'items-start'}`}
        style={{ gridTemplateColumns: rightPins.length > 0 ? 'minmax(0, 1fr) 260px' : 'minmax(0, 1fr)' }}
        data-testid="brain-home-top3"
      >
        <div data-testid="brain-home-hero">
          <DddCard
            density="full"
            name={primary.name}
            kind={primary.kind}
            sectionsPresent={primary.sectionsPresent}
            lifecycleStage={primary.lifecycleStage}
            health={primary.health}
            metrics={primaryMetrics}
            typeCounts={primaryTypeCounts ?? primary.typeCounts}
          />
        </div>
        {rightPins.length > 0 && (
          <div data-testid="brain-home-pins" className="flex flex-col gap-3 h-full">
            {rightPins.map((b) => (
              <div key={b.name} className="flex-1 min-h-0">
                <DddCard
                  density="compact"
                  name={b.name}
                  kind={b.kind}
                  sectionsPresent={b.sectionsPresent}
                  lifecycleStage={b.lifecycleStage}
                  health={b.health}
                  typeCounts={b.typeCounts}
                  onOpen={openBrain}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
