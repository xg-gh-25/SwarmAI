/**
 * BrainHomeView.tsx — the "Deliver-first" Welcome landing (Variant A, run_fc7078c4).
 *
 * REPLACES the brain-data-dump hero (run_9ada46ae Top-3 / big DddCard) with a
 * 2-tier information hierarchy that answers the real question on open — "what needs
 * me" — and demotes brain health to a single pulse strip:
 *
 *   TIER 1  Needs your decision — brains with health.pending>0 (amber rows).
 *   TIER 2  Brain pulse         — a demoted one-line strip: brain count + the 3-layer
 *                                ontology proportion bar (from summary.typeCounts,
 *                                FIRST-PAINT, no getBrainDetail) + the primary brain's
 *                                last change + a Brain Hub button.
 *
 * Brain Home is the DDD-brain home — pipeline in-flight runs are NOT its concern and
 * live in the Jobs & Runs overlay. A prior "In-flight" tier (run_fc7078c4) was removed
 * (run_2568c3fb): it coupled a heavy cross-project run.json scan (fetchActivePipelines)
 * into the Welcome first-paint path for data that does not belong to this subsystem.
 *
 * DURABILITY INVARIANT: the brains read (getBrainsWithPinned) never throws to the
 * caller (it catches to empty). The view hides itself only once the read resolves
 * empty (during the brief initial load it renders nothing, then fills — the same
 * progressive paint as the rest of the Welcome landing).
 */
import { useEffect, useState } from 'react';
import { getBrainsWithPinned } from '../../../services/ddd';
import type { BrainSummary, EntryType } from '../../../services/ddd';
import { LAYERS, layerTotals } from '../../../components/layout/dddLayers';

export interface BrainHomeViewProps {
  onOpenHub?: () => void;
  onOpenBrain?: (name: string) => void;
}

export function BrainHomeView({ onOpenHub, onOpenBrain }: BrainHomeViewProps) {
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [pinned, setPinned] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    getBrainsWithPinned().then(
      ({ brains: b, pinned: p }) => { if (alive) { setBrains(b); setPinned(p); } },
      () => { if (alive) setBrains([]); },   // brains read fails → tier 1/2 hide
    );
    return () => { alive = false; };
  }, []);

  // ── Tier 1 data: brains with a pending decision ──
  const decisionBrains = (brains ?? []).filter((b) => b.health.pending > 0);

  // ── Tier 2 data: the brain pulse (aggregate ontology + primary's last change) ──
  const allBrains = brains ?? [];
  // pinned[0] is the primary; fall back to the first brain if the pin is stale
  // (names a deleted brain) so the "edited X" signal doesn't silently vanish.
  const primary = (pinned[0] ? allBrains.find((b) => b.name === pinned[0]) : undefined) ?? allBrains[0];
  const aggTypeCounts = aggregateTypeCounts(allBrains);
  const hasPulse = allBrains.length > 0;

  const hasTier1 = decisionBrains.length > 0;

  // Whole view hides only when EVERY tier is empty (never throws / blanks siblings).
  if (!hasTier1 && !hasPulse) return null;

  return (
    <div className="w-full mt-2 flex flex-col gap-3" data-testid="brain-home">
      {hasTier1 && <DecisionBlock brains={decisionBrains} onOpenBrain={onOpenBrain} />}
      {hasPulse && (
        <BrainPulse
          count={allBrains.length}
          typeCounts={aggTypeCounts}
          lastChange={primary?.health.lastChangeRelative}
          onOpenHub={onOpenHub}
        />
      )}
    </div>
  );
}

/** Sum every brain's 7-type histogram into one workspace-wide histogram (for the
 *  pulse bar). Brains without typeCounts (daemon skew) contribute nothing. */
function aggregateTypeCounts(brains: BrainSummary[]): Record<EntryType, number> | undefined {
  const acc: Record<EntryType, number> = {
    principle: 0, correction: 0, decision: 0, model: 0, guideline: 0, pitfall: 0, process: 0,
  };
  let any = false;
  for (const b of brains) {
    if (!b.typeCounts) continue;
    any = true;
    for (const [k, n] of Object.entries(b.typeCounts) as [EntryType, number][]) acc[k] += n;
  }
  return any ? acc : undefined;
}

// ── TIER 1 ───────────────────────────────────────────────────────────────────
function DecisionBlock({ brains, onOpenBrain }: { brains: BrainSummary[]; onOpenBrain?: (n: string) => void }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))] overflow-hidden" data-testid="tier-decision">
      <div className="flex items-center gap-2 px-3.5 pt-3 pb-1.5">
        <span className="w-2 h-2 rounded-full" style={{ background: '#f0a500' }} />
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[#f0a500]">Needs your decision</span>
        <span className="ml-auto text-[10px] text-[var(--color-text-faint)]">{brains.length} brain{brains.length > 1 ? 's' : ''}</span>
      </div>
      <div className="pb-1.5">
        {brains.map((b) => (
          <button
            key={b.name}
            data-testid={`decision-${b.name}`}
            onClick={() => onOpenBrain?.(b.name)}
            className="w-full flex items-center gap-2.5 px-3.5 py-1.5 hover:bg-[var(--color-bg-hover)] transition-colors text-left"
          >
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: '#f0a500' }} />
            <span className="text-sm text-[var(--color-text)] flex-1 min-w-0 truncate">{b.name}</span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0" style={{ background: 'rgba(240,165,0,.14)', color: '#f0a500' }}>
              {b.health.pending} pending
            </span>
            {/* `sinking` removed from the first screen (run: welcome-declutter): it is an
                internal aging signal with no clear user action — lives in Brain Hub detail only. */}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── TIER 2 ───────────────────────────────────────────────────────────────────
function BrainPulse({
  count, typeCounts, lastChange, onOpenHub,
}: {
  count: number;
  typeCounts?: Record<EntryType, number>;
  lastChange?: string;
  onOpenHub?: () => void;
}) {
  const totals = typeCounts ? layerTotals(typeCounts) : null;
  const grand = totals ? totals.meta + totals.cognitive + totals.operational : 0;
  return (
    <div data-testid="tier-pulse" className="flex items-center gap-3.5 px-4 py-2.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))]">
      <span className="material-symbols-outlined text-[16px] text-[#f0a500]">psychology</span>
      <span className="text-[13px] font-semibold text-[var(--color-text)]">{count}</span>
      <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">brain{count > 1 ? 's' : ''}</span>
      {totals && grand > 0 && (
        <div className="flex h-1.5 rounded-sm overflow-hidden flex-1 min-w-[80px] max-w-[220px]"
          title={LAYERS.map((l) => `${l.label}: ${totals[l.key]}`).join(' · ')}
          data-testid="pulse-layerbar">
          {LAYERS.map((l) => {
            const w = (totals[l.key] / grand) * 100;
            return w === 0 ? null : <span key={l.key} style={{ width: `${w}%`, background: l.color }} />;
          })}
        </div>
      )}
      {lastChange && (
        <span className="text-[10px] text-[var(--color-text-faint)] shrink-0">edited {lastChange}</span>
      )}
      <button
        data-testid="brain-home-batch-review"
        onClick={() => onOpenHub?.()}
        className="ml-auto text-[11px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-0.5 hover:bg-[#12233a] shrink-0"
        title="Open Brain Hub — view all brains"
      >
        Brain Hub →
      </button>
    </div>
  );
}
