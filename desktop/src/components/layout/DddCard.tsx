/**
 * DddCard.tsx — the unified, density-driven DDD card.
 *
 * run_9ada46ae (final mockup design): a brain card answers "what is this brain,
 * and does it need me?" — verdict-first, ontology as the hero visual, only the
 * actionable surfaced, and NO diagnostics dump.
 *
 *   • compact — clickable gallery / calm card: name·kind + six-section presence +
 *     lifecycle + 4 cheap health signals + a SLIM 3-layer ontology proportion bar
 *     (from summary.typeCounts — NO detail fetch; the gallery already parsed once).
 *   • full    — verdict dot (pending>0 = "needs decision", else "nothing queued" —
 *     NEVER "healthy/unhealthy": that would be the trust rollup the backend Gate-1
 *     refused) + the FULL 3-layer × 7-type ontology (each layer count AND each type
 *     count — the hero visual) + a "Needs you" block (non-zero actionable only;
 *     clean brain → "Nothing needs you") + two fact lines (trust distribution /
 *     activity). Summary decorations (header/presence/lifecycle/cheap) render iff
 *     provided (detail view has none; Home hero has all).
 *
 * DELETED vs the prior design: the 4-question tiles and the per-section diagnostics
 * WALL (a 40-line score dump nobody reads — drill into per-section scores via
 * BrainView's section nav instead). DELIBERATELY ABSENT (Principle-1 + dead-input):
 * entry-count / "size", last-referenced / ref-count.
 *
 * GATE-1 invariant: density-scoped guard — compact ALWAYS renders (no metrics);
 * full guards the judgment body on `metrics?.noise` (O023 daemon-skew) so a partial
 * payload degrades the body to nothing WITHOUT blanking the card.
 */
import type { BrainHealth, DetailHealth, SectionKey, EntryType } from '../../services/ddd';
import { LAYERS, layerTotals } from './dddLayers';

// ── Shared constants ─────────────────────────────────────────────────────────
const SECTION_ORDER: SectionKey[] = ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];
const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;
export type LifecycleStage = (typeof LIFECYCLE_STEPS)[number];

const _TRUST_ORDER = ['low', 'moderate', 'high', 'full'] as const;

// The 3-layer ontology model now lives in ./dddLayers.ts (LAYERS + layerTotals,
// imported above) — shared with the Welcome BrainPulse strip (R25).

/** Count sections whose trust is BELOW `high`. A DISTRIBUTION count, NOT a
 *  collapsed rollup verdict (backend Gate-1 refused a project trust rollup). */
function _trustBelowHigh(trust: DetailHealth['trust']): { below: number; total: number } {
  if (!trust) return { below: 0, total: 0 };
  let below = 0, total = 0;
  for (const sections of Object.values(trust)) {
    for (const level of Object.values(sections)) {
      total += 1;
      const idx = level ? _TRUST_ORDER.indexOf(level as (typeof _TRUST_ORDER)[number]) : -1;
      if (idx < _TRUST_ORDER.indexOf('high')) below += 1;
    }
  }
  return { below, total };
}

function _ageOf(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'unknown';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// ── Props ──────────────────────────────────────────────────────────────────
interface CommonProps { name: string; kind: string }
interface CompactProps extends CommonProps {
  density: 'compact';
  sectionsPresent: Record<SectionKey, boolean>;
  lifecycleStage: LifecycleStage;
  health: BrainHealth;
  /** 3-layer proportion bar source — from BrainSummary (cheap, one gallery parse).
   *  Optional for daemon-skew: an old daemon omits it → no bar. */
  typeCounts?: Record<EntryType, number>;
  onOpen: (name: string) => void;
}
interface FullProps extends CommonProps {
  density: 'full';
  sectionsPresent?: Record<SectionKey, boolean>;
  lifecycleStage?: LifecycleStage;
  health?: BrainHealth;
  metrics?: DetailHealth;
  typeCounts?: Record<EntryType, number>;
}
type DddCardProps = CompactProps | FullProps;

// ── Shared sub-components ────────────────────────────────────────────────────
function CardHeader({ name, kind, verdict }: { name: string; kind: string; verdict?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="material-symbols-outlined text-[16px] text-[#f0a500]">psychology</span>
      <span className="text-[13px] font-semibold">{name}</span>
      <span className="text-[10px] font-mono text-[var(--color-text-faint)] px-1.5 py-0.5 rounded bg-[var(--color-bg)]">{kind}</span>
      {verdict != null && <span className="ml-auto">{verdict}</span>}
    </div>
  );
}

function PresenceBar({ name, sectionsPresent }: { name: string; sectionsPresent: Record<SectionKey, boolean> }) {
  return (
    <div className="flex gap-0.5 mb-2" title="six-section presence">
      {SECTION_ORDER.map((k) => (
        <span key={k} data-testid={`presence-${name}-${k}`}
          className={`flex-1 h-1.5 rounded-sm ${sectionsPresent[k] ? 'bg-[#3fb950]' : 'bg-[var(--color-hover)]'}`} />
      ))}
    </div>
  );
}

function LifecycleBar({ lifecycleStage }: { lifecycleStage: LifecycleStage }) {
  const active = LIFECYCLE_STEPS.indexOf(lifecycleStage);
  return (
    <div className="flex items-center gap-1 mb-2 text-[9px] font-mono">
      {LIFECYCLE_STEPS.map((s, i) => (
        <span key={s} className={i <= active ? 'text-[#3fb950]' : 'text-[#3b4552]'}>
          {s}{i < LIFECYCLE_STEPS.length - 1 ? ' ›' : ''}
        </span>
      ))}
    </div>
  );
}

/** Verdict dot — reads ONLY pending (a decision queue), never trust or sinking.
 *  So it can't reintroduce the refused trust rollup: pending>0 = needs a human
 *  decision; =0 = nothing queued. NOT "healthy/unhealthy". */
function VerdictDot({ pending }: { pending: number }) {
  const needs = pending > 0;
  return (
    <span data-testid="ddd-verdict" className="flex items-center gap-1.5 text-[11px] font-medium"
      style={{ color: needs ? '#f0a500' : '#3fb950' }}>
      <span className="w-2 h-2 rounded-full" style={{ background: needs ? '#f0a500' : '#3fb950' }} />
      {needs ? 'Needs decision' : 'Nothing queued'}
    </span>
  );
}

function CheapHealth({ health }: { health: BrainHealth }) {
  return (
    <div className="grid grid-cols-2 gap-1 text-[10px]">
      <Cheap testid="dddcard-cheap-sinking" label="Sinking" value={String(health.sinking)} warn={health.sinking > 0} />
      <Cheap testid="dddcard-cheap-pending" label="Pending" value={String(health.pending)} warn={health.pending > 0} />
      <Cheap testid="dddcard-cheap-uncommitted" label="Uncommitted" value={health.uncommitted ? 'yes' : 'no'} warn={health.uncommitted} />
      <Cheap testid="dddcard-cheap-lastchange" label="Last change" value={health.lastChangeRelative} />
    </div>
  );
}
function Cheap({ testid, label, value, warn }: { testid: string; label: string; value: string; warn?: boolean }) {
  return (
    <div data-testid={testid} className="flex items-center justify-between px-1.5 py-0.5 rounded bg-[var(--color-bg)]">
      <span className="text-[var(--color-text-faint)]">{label}</span>
      <span className={warn ? 'text-[#f0a500]' : 'text-[var(--color-text-muted)]'}>{value}</span>
    </div>
  );
}

/** Slim 3-layer proportion bar for the COMPACT card — proportion only, no per-type
 *  breakdown (that's the full card). From summary.typeCounts, no fetch. */
function CompactLayerBar({ typeCounts }: { typeCounts?: Record<EntryType, number> }) {
  if (!typeCounts) return null;
  const t = layerTotals(typeCounts);
  const total = t.meta + t.cognitive + t.operational;
  if (total === 0) return null;
  const tip = LAYERS.map((l) => `${l.label}: ${t[l.key]}`).join(' · ');
  return (
    <div data-testid="ddd-compact-layerbar" className="flex h-1 rounded-sm overflow-hidden mt-1.5" title={tip}>
      {LAYERS.map((l) => {
        const w = (t[l.key] / total) * 100;
        return w === 0 ? null : <span key={l.key} style={{ width: `${w}%`, background: l.color }} />;
      })}
    </div>
  );
}

export function DddCard(props: DddCardProps) {
  const { name, kind } = props;
  const { sectionsPresent, lifecycleStage, health } = props;

  if (props.density === 'compact') {
    const onOpen = props.onOpen;
    return (
      <button onClick={() => onOpen(name)} data-testid={`dddcard-${name}`}
        className="text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 hover:border-[#3b4552] transition-colors w-full h-full">
        <CardHeader name={name} kind={kind} verdict={<VerdictDot pending={props.health.pending} />} />
        <PresenceBar name={name} sectionsPresent={props.sectionsPresent} />
        <LifecycleBar lifecycleStage={props.lifecycleStage} />
        <CheapHealth health={props.health} />
        <CompactLayerBar typeCounts={props.typeCounts} />
      </button>
    );
  }

  // full — verdict in the header when we have pending (from metrics OR cheap health)
  const pending = props.metrics?.escalationPending ?? health?.pending;
  return (
    <div data-testid={`dddcard-${name}`} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
      <CardHeader name={name} kind={kind} verdict={pending != null ? <VerdictDot pending={pending} /> : undefined} />
      {sectionsPresent != null && <PresenceBar name={name} sectionsPresent={sectionsPresent} />}
      {lifecycleStage != null && <LifecycleBar lifecycleStage={lifecycleStage} />}
      {health != null && <CheapHealth health={health} />}
      <FullBody metrics={props.metrics} health={props.health} typeCounts={props.typeCounts} />
    </div>
  );
}

/**
 * The full-density judgment body: ontology (hero) + needs-you + 2 facts.
 *
 * run_b4d3eeeb — SPLIT to kill the on-load height jump. Two independent visibility
 * sources with different arrival times:
 *   • Ontology ← `typeCounts` (from the summary, available on FIRST PAINT). Renders
 *     immediately, does NOT wait for the metrics fetch.
 *   • needs-you + facts ← `metrics.noise` (from the 2nd getBrainDetail fetch, arrives
 *     late). While metrics is pending BUT ontology is showing, a skeleton reserves the
 *     metrics-block height so its later arrival causes NO layout shift.
 * Density-scoped guard (O023 daemon-skew): the metrics-block still renders only on
 * `metrics.noise`. With NEITHER typeCounts NOR metrics → render nothing (card survives).
 */
function FullBody(
  { metrics, health, typeCounts }:
  { metrics?: DetailHealth; health?: BrainHealth; typeCounts?: Record<EntryType, number> },
) {
  const hasMetrics = !!(metrics && metrics.noise);
  const hasOntology = !!typeCounts;
  // Nothing to show at all (no summary ontology AND no metrics) → body renders nothing.
  if (!hasOntology && !hasMetrics) return null;

  // Ontology-only first paint: show the hero ontology now + reserve the metrics-block
  // height with a skeleton so the real block's later arrival doesn't jump the layout.
  if (!hasMetrics) {
    return (
      <div className="mt-2 flex flex-col gap-2.5">
        {typeCounts && <Ontology typeCounts={typeCounts} />}
        <MetricsSkeleton />
      </div>
    );
  }

  const { below, total } = _trustBelowHigh(metrics.trust);
  const trustStale = metrics.computedAt === null;
  const pct = total === 0 ? null : Math.round(((total - below) / total) * 100);

  // needs-you: only non-zero actionable items
  const needs: { n: number; label: string }[] = [];
  if (metrics.escalationPending > 0) needs.push({ n: metrics.escalationPending, label: 'proposals awaiting review' });
  if (metrics.noise.reclaimable > 0) needs.push({ n: metrics.noise.reclaimable, label: 'reclaimable (run reclaim)' });
  if (health && health.sinking > 0) needs.push({ n: health.sinking, label: 'entries sinking (decaying)' });

  const freshText = health?.lastChangeRelative ?? _ageOf(metrics.computedAt);

  return (
    <div className="mt-2 flex flex-col gap-2.5">
      {/* ── Ontology — the hero visual: 3 layers × 7 types with counts ── */}
      {typeCounts && <Ontology typeCounts={typeCounts} />}

      {/* ── Needs you ── */}
      <div data-testid="ddd-needs-you"
        className={`rounded-md border px-2.5 py-2 ${needs.length ? 'bg-[#1e1a0e] border-[#5a4a20]' : 'bg-[#0f1a10] border-[#1f3d24]'}`}>
        <div className={`text-[9px] uppercase tracking-wide font-semibold mb-1 ${needs.length ? 'text-[#f0a500]' : 'text-[#3fb950]'}`}>
          {needs.length ? 'Needs you' : '✓ Nothing needs you'}
        </div>
        {needs.map((it) => (
          <div key={it.label} className="flex items-center gap-2 text-[11px] py-0.5">
            <span className="font-semibold text-[#f0a500] min-w-[28px]">{it.n}</span>
            <span className="text-[var(--color-text-muted)]">{it.label}</span>
          </div>
        ))}
      </div>

      {/* ── Two fact lines: trust distribution + activity ── */}
      <div className="flex flex-col gap-1 text-[11px]">
        <div data-testid="ddd-fact-trust" className="flex items-baseline gap-2">
          <span className="text-[var(--color-text-faint)] w-[52px]">Trust</span>
          <span className={below > 0 ? 'text-[#f0a500]' : 'text-[var(--color-text)]'}>
            {trustStale ? 'not computed' : `${pct}% sections ≥ high`}
          </span>
          <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">
            {trustStale ? 'no scheduled score' : `scored ${_ageOf(metrics.computedAt)}`}
          </span>
        </div>
        <div data-testid="ddd-fact-activity" className="flex items-baseline gap-2">
          <span className="text-[var(--color-text-faint)] w-[52px]">Activity</span>
          <span className="text-[var(--color-text)]">edited {freshText}</span>
          <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">
            {metrics.recentActivity === undefined ? '—' : metrics.recentActivity} sediments / 30d
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Placeholder that reserves the height of the metrics-block (needs-you + 2 facts)
 * while `getBrainDetail` is still in flight. STRUCTURAL, not a magic pixel height:
 * it mirrors the real block's layout — a needs-you card box + two fact lines — so
 * the swap to real content on arrival is close to zero-shift. Muted, non-interactive.
 */
function MetricsSkeleton() {
  return (
    <div data-testid="ddd-metrics-skeleton" aria-hidden className="flex flex-col gap-2.5 animate-pulse">
      {/* needs-you box placeholder */}
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2">
        <div className="h-2 w-24 rounded bg-[var(--color-hover)] mb-2" />
        <div className="h-2.5 w-40 rounded bg-[var(--color-hover)]" />
      </div>
      {/* two fact-line placeholders */}
      <div className="flex flex-col gap-1">
        <div className="h-2.5 w-full rounded bg-[var(--color-hover)]" />
        <div className="h-2.5 w-3/4 rounded bg-[var(--color-hover)]" />
      </div>
    </div>
  );
}

/**
 * The FULL 3-layer × 7-type ontology — the hero visual. Each layer: name + count +
 * proportion bar; under it, each type's count as a chip. This is what makes the
 * brain's cognitive "shape" legible (the whole reason the redesign exists). Honest
 * label: covers the ② canonical docs, not "the whole brain". Omitted if empty.
 */
function Ontology({ typeCounts }: { typeCounts: Record<EntryType, number> }) {
  const t = layerTotals(typeCounts);
  const total = t.meta + t.cognitive + t.operational;
  if (total === 0) return null;
  return (
    <div data-testid="ddd-ontology" className="flex flex-col gap-2">
      {/* NO total-entry-count header — Principle-1: a bigger brain is not a better
          one. The per-layer + per-type counts convey the cognitive SHAPE without a
          headline "size" number (the shape is the signal, not the volume). */}
      <div className="text-[9px] uppercase tracking-wide text-[var(--color-text-faint)]">
        Knowledge ontology · 3 layers × 7 types
      </div>
      {LAYERS.map((l) => (
        <div key={l.key} data-testid={`ddd-layer-${l.key}`} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: l.color }} />
            <span className="text-[11px] font-semibold" style={{ color: l.color }}>{l.label}</span>
            <span className="text-[10px] text-[var(--color-text-muted)]">{t[l.key]}</span>
            <span className="flex-1 h-1.5 rounded-sm bg-[var(--color-bg)] overflow-hidden ml-1">
              <span className="block h-full" style={{ width: `${(t[l.key] / total) * 100}%`, background: l.color }} />
            </span>
          </div>
          <div className="flex flex-wrap gap-1 pl-[17px]">
            {l.types.map((ty) => (
              <span key={ty} data-testid={`ddd-type-${ty}`}
                className={`flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-bg)] ${typeCounts[ty] ? 'text-[var(--color-text-muted)]' : 'opacity-40 text-[var(--color-text-faint)]'}`}>
                <span className="font-semibold text-[var(--color-text)]">{typeCounts[ty] ?? 0}</span> {ty}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
