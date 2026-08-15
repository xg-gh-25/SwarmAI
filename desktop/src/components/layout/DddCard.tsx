/**
 * DddCard.tsx — the unified, density-driven DDD card.
 *
 * run_9ada46ae + run_ee179ca1 (verdict-first two-zone gallery): a brain card
 * answers ONE decision — "does this brain need me?" — and NOTHING else. Both
 * densities are verdict-first and shed the redundant-ink widgets (six-section
 * presence bar, lifecycle chain, 2×2 cheap-health grid) that carried no signal on
 * a mature brain and produced the "密集恐惧症" data-dump.
 *
 *   • compact (gallery) — SELF-SELECTS by health.pending:
 *       NEEDS (pending>0) → amber-accented; header verdict + slim 3-layer ontology
 *         bar + NeedsActionable (the few actionable counts spelled out: N proposals
 *         / N sinking / uncommitted). NOT the boxed 2×2 grid.
 *       CALM (pending==0) → header verdict + ontology bar + ONE muted CalmMeta line
 *         (lifecycle · N sinking · last-change). No presence/lifecycle/cheap widgets.
 *     Both from the cheap BrainSummary — NO detail fetch — and stay click-to-open.
 *   • full (Gallery primary hero + detail health-strip) — verdict dot (pending>0 =
 *     "needs decision", else "nothing queued" — NEVER "healthy/unhealthy": the trust
 *     rollup backend Gate-1 refused) + FULL 3-layer × 7-type ontology (hero visual) +
 *     a "Needs you" block (non-zero actionable only; clean → "Nothing needs you") +
 *     two fact lines (trust distribution / activity). The hero ALSO drops the
 *     redundant presence/lifecycle/cheap widgets — its FullBody IS the signal.
 *
 * DELETED vs the prior design: the 4-question tiles + per-section diagnostics WALL
 * (drill into per-section scores via BrainView's section nav instead); AND the
 * PresenceBar / LifecycleBar / CheapHealth widgets (run_ee179ca1 — redundant ink).
 * DELIBERATELY ABSENT (Principle-1 + dead-input): entry-count / "size",
 * last-referenced / ref-count.
 *
 * GATE-1 invariant: density-scoped guard — compact ALWAYS renders (needs only the
 * cheap BrainSummary.health); full guards the judgment body on `metrics?.noise`
 * (O023 daemon-skew) so a partial payload degrades the body to nothing WITHOUT
 * blanking the card.
 */
import type { BrainHealth, DetailHealth, EntryType } from '../../services/ddd';
import { LAYERS, layerTotals } from './dddLayers';

// ── Shared constants ─────────────────────────────────────────────────────────
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
  // NOTE (run_ee179ca1): sectionsPresent was dropped — the presence bar it fed is
  // gone (redundant ink). health + lifecycleStage still drive the verdict/CalmMeta.
  lifecycleStage: LifecycleStage;
  health: BrainHealth;
  /** 3-layer proportion bar source — from BrainSummary (cheap, one gallery parse).
   *  Optional for daemon-skew: an old daemon omits it → no bar.
   *  NOTE (run_3d371424): NO LONGER rendered on the compact gallery card — the layer
   *  bar was pure decoration that answered no gallery decision, and with operational
   *  ~80% on a mature brain it always collapsed into one dominant green line that stole
   *  the eye (Von Restorff). Kept in the prop for the full-card ontology; the gallery
   *  card is now name + kind + verdict signals + a bottom briefing. */
  typeCounts?: Record<EntryType, number>;
  /** One-line "what this DDD is" briefing (item 2, run_3d371424) — rendered at the
   *  BOTTOM of the card, below the decision signals, so "what needs me" stays primary
   *  and "what is this brain" is the secondary orient. OPTIONAL (daemon-skew / no
   *  aim.json description → no briefing line). */
  description?: string;
  onOpen: (name: string) => void;
}
interface FullProps extends CommonProps {
  density: 'full';
  // health carries pending for the verdict dot; metrics/typeCounts drive FullBody.
  // (sectionsPresent/lifecycleStage dropped — the hero no longer renders those widgets.)
  health?: BrainHealth;
  metrics?: DetailHealth;
  typeCounts?: Record<EntryType, number>;
  /** AC6 (run_a607f2b0): when supplied, the hero is CLICKABLE (opens the brain,
   *  same as a compact card). OMITTED for the in-BrainView health-strip use — it's
   *  already inside the brain, so navigating to itself would be wrong. Presence of
   *  onOpen is the discriminator: button vs plain div. */
  onOpen?: (name: string) => void;
  /** ontologyOnly (run_115aa182): suppress FullBody's needs-you sub-block (keep
   *  ontology + facts). Set by the Brain Hub OVERVIEW §① where a dedicated §②
   *  NeedYouBlock owns needs-you; DEFAULT false keeps the Gallery hero's verdict. */
  ontologyOnly?: boolean;
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

/** The actionable list for a NEEDS card — the 2×2 boxed cheap grid is REPLACED by
 *  the few things that actually need a human, spelled out. Only non-zero items
 *  render (pending is the verdict; sinking/uncommitted are supporting facts).
 *  All from the cheap BrainSummary — no fetch. */
function NeedsActionable({ health }: { health: BrainHealth }) {
  const items: { key: string; n: string; label: string }[] = [];
  if (health.pending > 0) items.push({ key: 'pending', n: String(health.pending), label: 'proposals awaiting review' });
  if (health.sinking > 0) items.push({ key: 'sinking', n: String(health.sinking), label: 'entries sinking (decaying)' });
  if (health.uncommitted) items.push({ key: 'uncommitted', n: '•', label: 'uncommitted changes' });
  return (
    <div data-testid="dddcard-needs-actionable" className="flex flex-col gap-1 mt-1.5">
      {items.map((it) => (
        <div key={it.key} className="flex items-baseline gap-2 text-[11px]">
          <span className="font-semibold text-[#f0a500] min-w-[22px]">{it.n}</span>
          <span className="text-[var(--color-text-muted)]">{it.label}</span>
        </div>
      ))}
    </div>
  );
}

/** The single muted meta line for a CALM card — lifecycle stage · last change.
 *  Replaces the whole presence/lifecycle/2×2-grid stack (all no-signal on a calm
 *  brain). Sinking>0 is surfaced quietly here as a fact, never promoted to a zone. */
function CalmMeta({ lifecycleStage, health }: { lifecycleStage: LifecycleStage; health: BrainHealth }) {
  const bits = [lifecycleStage, `${health.sinking} sinking`, health.lastChangeRelative];
  return (
    <div data-testid="dddcard-calm-meta" className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-faint)] mt-1.5 font-mono">
      {bits.map((b, i) => (
        <span key={i}>{i > 0 ? '· ' : ''}{b}</span>
      ))}
    </div>
  );
}

/** Bottom-of-card briefing: one muted line answering "what IS this DDD" (item 2,
 *  run_3d371424). Rendered LAST (below the decision signals) so the card's primary
 *  read stays "does this need me?" and the briefing is the secondary orient — the
 *  layout XG confirmed on the wireframe. A thin top divider separates it from the
 *  signals above. Omitted entirely when there's no description (daemon-skew /
 *  no aim.json `description`) — never an empty divider. line-clamp-2 caps the height
 *  so a long description can't blow out the card. */
function CardBriefing({ description }: { description?: string }) {
  if (!description) return null;
  return (
    <div
      data-testid="dddcard-briefing"
      className="mt-2 pt-2 border-t border-[var(--color-border)] text-[10px] leading-snug text-[var(--color-text-muted)] line-clamp-2"
    >
      {description}
    </div>
  );
}

export function DddCard(props: DddCardProps) {
  const { name, kind } = props;

  if (props.density === 'compact') {
    const onOpen = props.onOpen;
    const h = props.health;
    // Verdict-first: the card SELF-SELECTS by health.pending. Both variants shed the
    // redundant-ink widgets (presence bar / lifecycle chain / 2×2 cheap grid) — they
    // carry no signal on a mature brain and were the "密集恐惧症" data-dump. A NEEDS
    // card spells out the few actionable counts; a CALM card is a quiet meta line.
    const needs = h.pending > 0;
    // NO per-card verdict dot: the ZONE ("▲ Needs you" / "Calm · nothing queued")
    // carries the verdict for every card inside it, so a per-card dot is redundant
    // ink (Tufte) — and a green "nothing queued" dot on every calm card was the
    // "全凭绿" wall the user rejected. Freeing green from a status role leaves it to
    // mean exactly ONE thing in the gallery: the operational ontology layer. A needs
    // card still signals via the amber left accent + amber actionable counts.
    return (
      <button onClick={() => onOpen(name)} data-testid={`dddcard-${name}`}
        className={`text-left rounded-lg bg-[var(--color-card)] p-3 hover:border-[#3b4552] transition-colors w-full h-full ${
          needs ? 'border-l-[3px] border-l-[#f0a500] border-y border-r border-[#4a3a12]' : 'border border-[var(--color-border)]'
        }`}>
        <CardHeader name={name} kind={kind} />
        {needs ? <NeedsActionable health={h} /> : <CalmMeta lifecycleStage={props.lifecycleStage} health={h} />}
        <CardBriefing description={props.description} />
      </button>
    );
  }

  // full (hero / detail health-strip) — verdict-first. Like the compact card, the
  // hero drops the redundant presence/lifecycle/cheap widgets: its FullBody
  // (ontology + needs-you + facts) IS the signal. pending comes from metrics OR
  // cheap health, whichever the caller supplied.
  const pending = props.metrics?.escalationPending ?? props.health?.pending;
  const body = (
    <>
      <CardHeader name={name} kind={kind} verdict={pending != null ? <VerdictDot pending={pending} /> : undefined} />
      <FullBody metrics={props.metrics} health={props.health} typeCounts={props.typeCounts}
        ontologyOnly={props.ontologyOnly} />
    </>
  );
  // AC6: clickable hero when onOpen is supplied (gallery), plain div otherwise
  // (in-BrainView health-strip — navigating to the brain it's already inside is wrong).
  if (props.onOpen) {
    const onOpen = props.onOpen;
    return (
      <button onClick={() => onOpen(name)} data-testid={`dddcard-${name}`}
        className="text-left w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 hover:border-[#3b4552] transition-colors">
        {body}
      </button>
    );
  }
  return (
    <div data-testid={`dddcard-${name}`} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
      {body}
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
  { metrics, health, typeCounts, ontologyOnly = false }:
  { metrics?: DetailHealth; health?: BrainHealth; typeCounts?: Record<EntryType, number>;
    // ontologyOnly (run_115aa182): suppress the needs-you sub-block, keeping ontology +
    // Trust/Activity facts. Used by the Brain Hub OVERVIEW §①, where a dedicated §②
    // NeedYouBlock already owns needs-you (proposals/reclaimable/sinking) — rendering
    // FullBody's own needs-you there duplicated it. DEFAULT false so the Gallery hero
    // (no separate §②) keeps its needs-you verdict — its FullBody IS the only signal. */
    ontologyOnly?: boolean },
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

      {/* ── Needs you ── (suppressed when ontologyOnly: a dedicated §② owns it) */}
      {!ontologyOnly && (
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
      )}

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
export function Ontology({ typeCounts }: { typeCounts: Record<EntryType, number> }) {
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
