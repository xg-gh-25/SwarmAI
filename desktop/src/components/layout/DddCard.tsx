/**
 * DddCard.tsx — the unified, density-driven DDD card.
 *
 * run_6924b463 c2-3: SSOT replacing BrainHub's BrainCard (gallery) + HealthStrip
 * (detail). Two densities:
 *   • compact — clickable gallery / Home-calm card: name·kind + six-section
 *     presence bar + lifecycle + 4 CHEAP health signals (from BrainSummary).
 *   • full    — a static, metrics-bearing card whose SUMMARY decorations are
 *     CONDITIONAL (detail view carries only DetailHealth; Home hero carries both).
 *
 * run_d1e933aa c2: the full-density metrics are organized into USER JUDGMENT
 * LANGUAGE — the 4 questions a user actually asks when opening a brain, plus a
 * 7-type×3-layer "type mix" bar. This is NOT new data; it re-groups the existing
 * DetailHealth fields (+ recentActivity) into decisions:
 *   Q1 healthy?  → trust distribution (below/total, NOT a rollup verdict) + how
 *                  fresh the score is (computedAt age — honest, never a naked pass)
 *   Q2 fresh?    → lastChangeRelative (hero) or the score's computedAt age (detail)
 *   Q3 growing?  → recentActivity (30d changelog — value≠size) + escalationPending
 *   Q4 prune?    → noise.reclaimable (+ the gallery's sinking, shown on the hero)
 * Type-mix bar → aggregates entries[].entryType into 3 layers, DETAIL-ONLY,
 *   labeled "知识文档类型分布" (honest: it covers the ② canonical docs, not "the
 *   whole brain"). The layer counts come from a `typeCounts` prop the CONSUMER
 *   aggregates from detail.sections[].entries — DddCard never touches sections.
 *
 * DELIBERATELY ABSENT (Principle-1 + dead-input): entry-count / "size", and
 * last-referenced / ref-count. A bigger brain is not a better one; ref_count is a
 * dead engine input. Neither earns a place on a judgment card.
 *
 * GATE-1 CORRECTION (load-bearing invariant): the guard is density-scoped, NOT a
 * whole-card `if(!health?.noise) return null`. compact has no `noise` and MUST
 * always render. full guards ONLY the question blocks on `metrics?.noise` (O023
 * daemon-skew), so a partial payload degrades the blocks to nothing WITHOUT
 * blanking the card body.
 */
import type { BrainHealth, DetailHealth, SectionKey, EntryType } from '../../services/ddd';

// ── Shared constants (SSOT — the old BrainHub local copies are gone; DddCard owns them) ──
const SECTION_ORDER: SectionKey[] = ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];
const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;
export type LifecycleStage = (typeof LIFECYCLE_STEPS)[number];

const _TRUST_ORDER = ['low', 'moderate', 'high', 'full'] as const;

/** The 7-type → 3-layer map (authoritative: backend MEMORY_SECTIONS[*].layer,
 *  ddd_entry_lifecycle.py:52-61). Pure classification, no rollup. */
const LAYER_OF_TYPE: Record<EntryType, 'meta' | 'cognitive' | 'operational'> = {
  principle: 'meta', correction: 'meta',
  decision: 'cognitive', model: 'cognitive',
  guideline: 'operational', pitfall: 'operational', process: 'operational',
};
const LAYER_META: { key: 'meta' | 'cognitive' | 'operational'; label: string; color: string }[] = [
  { key: 'meta', label: 'Meta-cognitive', color: '#a371f7' },
  { key: 'cognitive', label: 'Cognitive', color: '#58a6ff' },
  { key: 'operational', label: 'Operational', color: '#3fb950' },
];

/** Count sections whose trust is BELOW `high`. Factual distribution count, NOT a
 *  collapsed rollup verdict (backend Gate-1 MAJOR refused a project trust rollup). */
function _trustBelowHigh(trust: DetailHealth['trust']): { below: number; total: number } {
  if (!trust) return { below: 0, total: 0 };
  let below = 0;
  let total = 0;
  for (const sections of Object.values(trust)) {
    for (const level of Object.values(sections)) {
      total += 1;
      const idx = level ? _TRUST_ORDER.indexOf(level as (typeof _TRUST_ORDER)[number]) : -1;
      if (idx < _TRUST_ORDER.indexOf('high')) below += 1;
    }
  }
  return { below, total };
}

/** Human "N ago" from an ISO timestamp — for the trust score's freshness. */
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

// ── Props: a discriminated union on `density` ────────────────────────────────
interface CommonProps {
  name: string;
  kind: string;
}
interface CompactProps extends CommonProps {
  density: 'compact';
  sectionsPresent: Record<SectionKey, boolean>;
  lifecycleStage: LifecycleStage;
  health: BrainHealth;          // cheap signals, always present in a gallery summary
  onOpen: (name: string) => void;
}
interface FullProps extends CommonProps {
  density: 'full';
  /** summary decorations — present on the Home hero, ABSENT on the bare detail
   *  view (BrainDetail has no lifecycle/cheap-health). Rendered iff provided. */
  sectionsPresent?: Record<SectionKey, boolean>;
  lifecycleStage?: LifecycleStage;
  health?: BrainHealth;
  metrics?: DetailHealth;       // expensive question blocks; OPTIONAL (daemon-skew) — guarded
  /** 7-type → 3-layer distribution, aggregated by the CONSUMER from
   *  detail.sections[].entries. Absent on a consumer that has no entries. */
  typeCounts?: Record<EntryType, number>;
}
type DddCardProps = CompactProps | FullProps;

/** name·kind header (compact always; full only when it's a hero i.e. has summary). */
function CardHeader({ name, kind }: { name: string; kind: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="material-symbols-outlined text-[16px] text-[#f0a500]">psychology</span>
      <span className="text-[13px] font-semibold">{name}</span>
      <span className="ml-auto text-[10px] font-mono text-[var(--color-text-faint)] px-1.5 py-0.5 rounded bg-[var(--color-bg)]">{kind}</span>
    </div>
  );
}

function PresenceBar({ name, sectionsPresent }: { name: string; sectionsPresent: Record<SectionKey, boolean> }) {
  return (
    <div className="flex gap-0.5 mb-2" title="six-section presence">
      {SECTION_ORDER.map((k) => (
        <span
          key={k}
          data-testid={`presence-${name}-${k}`}
          className={`flex-1 h-1.5 rounded-sm ${sectionsPresent[k] ? 'bg-[#3fb950]' : 'bg-[var(--color-hover)]'}`}
        />
      ))}
    </div>
  );
}

function LifecycleBar({ lifecycleStage }: { lifecycleStage: LifecycleStage }) {
  const activeStep = LIFECYCLE_STEPS.indexOf(lifecycleStage);
  return (
    <div className="flex items-center gap-1 mb-2 text-[9px] font-mono">
      {LIFECYCLE_STEPS.map((s, i) => (
        <span key={s} className={i <= activeStep ? 'text-[#3fb950]' : 'text-[#3b4552]'}>
          {s}{i < LIFECYCLE_STEPS.length - 1 ? ' ›' : ''}
        </span>
      ))}
    </div>
  );
}

export function DddCard(props: DddCardProps) {
  const { name, kind } = props;

  // Summary decorations render iff their data is present: always for compact;
  // for full only when it's a hero (detail view omits lifecycle/cheap-health).
  const { sectionsPresent, lifecycleStage, health } = props;

  const body = (
    <>
      {sectionsPresent != null && <CardHeader name={name} kind={kind} />}
      {sectionsPresent != null && <PresenceBar name={name} sectionsPresent={sectionsPresent} />}
      {lifecycleStage != null && <LifecycleBar lifecycleStage={lifecycleStage} />}
      {health != null && <CheapHealth health={health} />}
      {props.density === 'full' && (
        <JudgmentBlocks metrics={props.metrics} health={props.health} typeCounts={props.typeCounts} />
      )}
    </>
  );

  // compact = clickable open-button; full = static. The guard is density-scoped in
  // the sub-blocks above — the card body ALWAYS renders (Gate-1 invariant).
  if (props.density === 'compact') {
    const onOpen = props.onOpen;
    return (
      <button
        onClick={() => onOpen(name)}
        data-testid={`dddcard-${name}`}
        className="text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 hover:border-[#3b4552] transition-colors"
      >
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

/** 4 cheap health signals — cloned from BrainHub `Health` grid. */
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

/**
 * The 4 user-judgment questions + type-mix bar + diagnostics — the full-density
 * body. Density-scoped guard: `metrics?.noise` missing → render nothing (the card
 * body is unaffected — Gate-1 correction). `health` (cheap, hero-only) feeds Q2's
 * lastChangeRelative + Q4's sinking; absent on the bare detail consumer.
 */
function JudgmentBlocks(
  { metrics, health, typeCounts }:
  { metrics?: DetailHealth; health?: BrainHealth; typeCounts?: Record<EntryType, number> },
) {
  if (!metrics || !metrics.noise) return null;
  const { below, total } = _trustBelowHigh(metrics.trust);
  const trustValue = total === 0 ? '—' : `${below}/${total}`;
  const trustStale = metrics.computedAt === null;

  // Q2 freshness: hero has lastChangeRelative (git); the bare detail view only has
  // the score's computedAt age. Prefer the git signal when present.
  const freshText = health?.lastChangeRelative ?? _ageOf(metrics.computedAt);

  const diagFlat: { key: string; composite?: number; trust?: string }[] = [];
  if (metrics.diagnostics) {
    for (const [doc, docData] of Object.entries(metrics.diagnostics)) {
      for (const [sec, s] of Object.entries(docData?.sections ?? {})) {
        diagFlat.push({ key: `${doc}·${sec}`, composite: s?.composite, trust: s?.trust });
      }
    }
  }

  return (
    <div className="mt-1.5 flex flex-col gap-2">
      {/* ── The 4 judgment questions ── */}
      <div className="grid grid-cols-2 gap-2">
        {/* Q1 — healthy? trust distribution + honest freshness of the score */}
        <Question testid="ddd-q1-healthy" q="Healthy?" >
          <span className={below > 0 ? 'text-[#f0a500]' : 'text-[var(--color-text)]'}>
            {trustStale ? 'not computed' : `${trustValue} below high`}
          </span>
          <span data-testid="ddd-trust-computedat" className="text-[9px] text-[var(--color-text-faint)]">
            {trustStale ? 'no scheduled score' : `scored ${_ageOf(metrics.computedAt)}`}
          </span>
        </Question>

        {/* Q2 — fresh? last real change */}
        <Question testid="ddd-q2-fresh" q="Fresh?">
          <span className="text-[var(--color-text)]">{freshText}</span>
          <span className="text-[9px] text-[var(--color-text-faint)]">last change</span>
        </Question>

        {/* Q3 — growing? 30d sedimentation activity + escalations awaiting review.
            recentActivity counts ALL ddd-changelog writes in 30d — dominantly
            AUTO-APPLIED cultivation (E2E audit: ~820/851 on SwarmAI carry
            action:"applied", i.e. the engine sedimenting, not a human editing). So
            the label is "sediments / 30d", NOT "edits" — it honestly reads as
            "is the brain being actively written to (by the engine + humans)",
            never claiming human authorship. undefined (old daemon) → "—", not a
            confident "0". */}
        <Question testid="ddd-q3-growing" q="Growing?">
          <span className="text-[var(--color-text)]">
            {metrics.recentActivity === undefined ? '—' : metrics.recentActivity} <span className="text-[9px] text-[var(--color-text-faint)]">sediments / 30d</span>
          </span>
          <span className={metrics.escalationPending > 0 ? 'text-[9px] text-[#f0a500]' : 'text-[9px] text-[var(--color-text-faint)]'}>
            {metrics.escalationPending} awaiting review
          </span>
        </Question>

        {/* Q4 — prune? reclaimable noise (+ sinking when the hero provides it) */}
        <Question testid="ddd-q4-prune" q="Prune?">
          <span className={metrics.noise.reclaimable > 0 ? 'text-[#f0a500]' : 'text-[var(--color-text)]'}>
            {metrics.noise.reclaimable} <span className="text-[9px] text-[var(--color-text-faint)]">reclaimable</span>
          </span>
          {health != null && (
            <span className="text-[9px] text-[var(--color-text-faint)]">{health.sinking} sinking</span>
          )}
        </Question>
      </div>

      {/* Recall — experimental, kept but clearly labeled (not one of the 4; a lab signal) */}
      <div className="flex items-center gap-1 text-[9px] text-[var(--color-text-faint)]">
        <span>Recall</span>
        <span
          data-testid="recall-experimental-chip"
          title="Benchmark口径 not yet validated against real usage — trend, not a grade"
          className="text-[8px] px-1 rounded bg-[#3a2f12] text-[#e0b050] uppercase"
        >
          exp
        </span>
        <span>{metrics.recall.value === null ? '—' : String(metrics.recall.value)}</span>
      </div>

      {/* ── 7-type × 3-layer type mix (detail-only; needs consumer-aggregated counts) ── */}
      <TypeMixBar typeCounts={typeCounts} />

      {/* ── DIAGNOSTICS row (demoted: smaller, muted, no action-hint, no status color) ── */}
      {diagFlat.length > 0 && (
        <div data-testid="health-diagnostics" className="flex flex-wrap gap-x-3 gap-y-0.5">
          {diagFlat.map((r) => (
            <span key={r.key} className="text-[9px] text-[var(--color-text-faint)]">
              {r.key}: {r.composite ?? '?'}{r.trust ? ` (${r.trust})` : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Question({ testid, q, children }: { testid: string; q: string; children: React.ReactNode }) {
  return (
    <div data-testid={testid} className="flex flex-col gap-0.5 px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)]">
      <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{q}</span>
      {children}
    </div>
  );
}

/**
 * The brain's "judgment personality" — how its ② knowledge distributes across the
 * 3 cognitive layers (meta / cognitive / operational). Honest label: it covers the
 * canonical knowledge docs, NOT "the whole brain". Omitted entirely when there are
 * no entries (no vanity empty bar — an all-zero distribution says nothing).
 */
function TypeMixBar({ typeCounts }: { typeCounts?: Record<EntryType, number> }) {
  if (!typeCounts) return null;
  const layerTotals = { meta: 0, cognitive: 0, operational: 0 };
  for (const [t, n] of Object.entries(typeCounts) as [EntryType, number][]) {
    layerTotals[LAYER_OF_TYPE[t]] += n;
  }
  const total = layerTotals.meta + layerTotals.cognitive + layerTotals.operational;
  if (total === 0) return null;

  const tip = (Object.entries(typeCounts) as [EntryType, number][])
    .filter(([, n]) => n > 0)
    .map(([t, n]) => `${t}: ${n}`)
    .join(' · ');

  return (
    <div data-testid="ddd-typebar" className="flex flex-col gap-1" title={tip}>
      <span className="text-[9px] uppercase tracking-wide text-[var(--color-text-faint)]">知识文档类型分布</span>
      <div className="flex h-1.5 rounded-sm overflow-hidden">
        {LAYER_META.map((l) => {
          const w = (layerTotals[l.key] / total) * 100;
          if (w === 0) return null;
          return <span key={l.key} style={{ width: `${w}%`, background: l.color }} />;
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 text-[9px]">
        {LAYER_META.map((l) => (
          <span key={l.key} data-testid={`ddd-typelayer-${l.key}`} className="flex items-center gap-1 text-[var(--color-text-faint)]">
            <span className="w-1.5 h-1.5 rounded-sm" style={{ background: l.color }} />
            {l.label} {layerTotals[l.key]}
          </span>
        ))}
      </div>
    </div>
  );
}
