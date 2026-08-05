/**
 * BrainHomeView.tsx — the "Deliver-first" Welcome landing (Variant A, run_fc7078c4).
 *
 * REPLACES the brain-data-dump hero (run_9ada46ae Top-3 / big DddCard) with a
 * 3-tier information hierarchy that answers the real question on open — "what needs
 * me / what's in flight" — and demotes brain health to a single pulse strip:
 *
 *   TIER 1  Needs your decision — brains with health.pending>0 (amber rows).
 *   TIER 2  In flight          — active pipeline runs (running / paused-decision;
 *                                crash_residue paused runs are FILTERED, they are
 *                                not decisions). Focus items stay in WelcomeScreen.
 *   TIER 3  Brain pulse        — a demoted one-line strip: brain count + the 3-layer
 *                                ontology proportion bar (from summary.typeCounts,
 *                                FIRST-PAINT, no getBrainDetail) + the primary brain's
 *                                last change + a Brain Hub button.
 *
 * DURABILITY INVARIANT (hardened run_fc7078c4): the two reads — brains
 * (getBrainsWithPinned) and runs (fetchActivePipelines) — are INDEPENDENT. One
 * failing/empty renders only its tier absent; siblings survive. Neither read throws
 * to the caller (each catches to empty). The view hides itself only once BOTH reads
 * have resolved empty (during the brief initial load it renders nothing, then fills —
 * the same progressive paint as the rest of the Welcome landing).
 */
import { useEffect, useState } from 'react';
import { getBrainsWithPinned } from '../../../services/ddd';
import type { BrainSummary, EntryType } from '../../../services/ddd';
import { pipelinesService, type PipelineRun } from '../../../services/pipelines';
import { LAYERS, layerTotals } from '../../../components/layout/dddLayers';

export interface BrainHomeViewProps {
  onOpenHub?: () => void;
  onOpenBrain?: (name: string) => void;
}

// Cap the in-flight list so a busy multi-project workspace doesn't wall the screen.
const MAX_INFLIGHT = 6;

export function BrainHomeView({ onOpenHub, onOpenBrain }: BrainHomeViewProps) {
  // Two INDEPENDENT reads — a failure in one must not blank the other (durability).
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [pinned, setPinned] = useState<string[]>([]);
  const [runs, setRuns] = useState<PipelineRun[]>([]);

  useEffect(() => {
    let alive = true;
    getBrainsWithPinned().then(
      ({ brains: b, pinned: p }) => { if (alive) { setBrains(b); setPinned(p); } },
      () => { if (alive) setBrains([]); },   // brains read fails → tier 1/3 hide, tier 2 survives
    );
    pipelinesService.fetchActivePipelines().then(
      (r) => { if (alive) setRuns(r); },
      () => { if (alive) setRuns([]); },      // runs read fails → tier 2 hides, tier 1/3 survive
    );
    return () => { alive = false; };
  }, []);

  // ── Tier 1 data: brains with a pending decision ──
  const decisionBrains = (brains ?? []).filter((b) => b.health.pending > 0);

  // ── Tier 2 data: active runs that are RUNNING or a genuine paused DECISION.
  //    crash_residue paused runs are NOT decisions (the same noise Radar drops). ──
  const activeInFlight = runs.filter(
    (r) => r.status === 'running' || (r.status === 'paused' && r.pauseKind === 'decision'),
  );
  const inFlight = activeInFlight.slice(0, MAX_INFLIGHT);
  const inFlightOverflow = activeInFlight.length - inFlight.length;  // >0 → "+N more" (no silent cap)

  // ── Tier 3 data: the brain pulse (aggregate ontology + primary's last change) ──
  const allBrains = brains ?? [];
  // pinned[0] is the primary; fall back to the first brain if the pin is stale
  // (names a deleted brain) so the "edited X" signal doesn't silently vanish.
  const primary = (pinned[0] ? allBrains.find((b) => b.name === pinned[0]) : undefined) ?? allBrains[0];
  const aggTypeCounts = aggregateTypeCounts(allBrains);
  const hasPulse = allBrains.length > 0;

  const hasTier1 = decisionBrains.length > 0;
  const hasTier2 = inFlight.length > 0;

  // Whole view hides only when EVERY tier is empty (never throws / blanks siblings).
  if (!hasTier1 && !hasTier2 && !hasPulse) return null;

  return (
    <div className="w-full mt-2 flex flex-col gap-3" data-testid="brain-home">
      {hasTier1 && <DecisionBlock brains={decisionBrains} onOpenBrain={onOpenBrain} />}
      {hasTier2 && <InFlightBlock runs={inFlight} total={activeInFlight.length} overflow={inFlightOverflow} onOpenHub={onOpenHub} />}
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
            {b.health.sinking > 0 && (
              <span className="text-[11px] text-[var(--color-text-faint)] shrink-0">{b.health.sinking} sinking</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── TIER 2 ───────────────────────────────────────────────────────────────────
function InFlightBlock({
  runs, total, overflow, onOpenHub,
}: { runs: PipelineRun[]; total: number; overflow: number; onOpenHub?: () => void }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))] overflow-hidden" data-testid="tier-inflight">
      <div className="flex items-center gap-2 px-3.5 pt-3 pb-1.5">
        <span className="text-[#58a6ff] text-[11px]">▸</span>
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[#58a6ff]">In flight</span>
        <span className="ml-auto text-[10px] text-[var(--color-text-faint)]">{total} run{total > 1 ? 's' : ''}</span>
      </div>
      <div className="pb-1.5">
        {runs.map((r) => {
          const isDecision = r.pauseKind === 'decision';
          return (
            <div key={r.id} data-testid={`inflight-${r.id}`} className="flex items-center gap-2.5 px-3.5 py-1.5">
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: isDecision ? '#f0a500' : '#3fb950' }} />
              <span className="text-[13px] text-[var(--color-text-secondary)] flex-1 min-w-0 truncate" title={r.requirement}>
                {r.requirement || r.project}
              </span>
              <span className="text-[9px] font-mono text-[var(--color-text-faint)] shrink-0">{r.project}</span>
              <span
                className="text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0"
                style={isDecision
                  ? { background: 'rgba(240,165,0,.14)', color: '#f0a500' }
                  : { background: 'rgba(88,166,255,.14)', color: '#58a6ff' }}
              >
                {isDecision ? 'needs decision' : 'running'}
              </span>
            </div>
          );
        })}
        {overflow > 0 && (
          <button
            data-testid="inflight-overflow"
            onClick={() => onOpenHub?.()}
            className="w-full text-left px-3.5 py-1.5 text-[11px] text-[#58a6ff] hover:bg-[var(--color-bg-hover)] transition-colors"
            title="Open Brain Hub / Jobs & Runs to see all"
          >
            +{overflow} more →
          </button>
        )}
      </div>
    </div>
  );
}

// ── TIER 3 ───────────────────────────────────────────────────────────────────
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
