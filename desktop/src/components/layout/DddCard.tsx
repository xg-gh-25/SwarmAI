/**
 * DddCard.tsx — the unified, density-driven DDD card (run_6924b463, cycle 2).
 *
 * SSOT for rendering ONE brain's state at two densities, replacing the old split
 * between BrainHub's `BrainCard` (gallery) and `HealthStrip` (detail). Cycle 3
 * migrates BrainHub's call sites onto this component; the markup here is cloned
 * verbatim from the shipped BrainHub primitives so that migration keeps the
 * existing `presence-*` / `health-tile-*` / `recall-experimental-chip` testids
 * and visual shape green (R27 contract-preserving).
 *
 * DENSITY (the one axis that varies):
 *   • compact — a clickable gallery card: six-section presence bar + lifecycle
 *     progress + the 4 CHEAP health signals (sinking/pending/uncommitted/
 *     lastChange, all from BrainSummary.health). Carries NO expensive metrics.
 *   • full    — a static detail header: presence bar + lifecycle + the 4
 *     EXPENSIVE action tiles (noise/trust/escalation/recall) PLUS the demoted
 *     per-section diagnostics row — all from DetailHealth (HealthStrip parity).
 *
 * GATE-1 CORRECTION (density-aware guard — the load-bearing invariant):
 *   A naïve unification would put a single `if (!health?.noise) return null`
 *   whole-card guard (lifted from HealthStrip). That is WRONG here: a compact
 *   card legitimately has NO `noise` (BrainSummary omits it), so the guard would
 *   blank EVERY gallery card. The guard is scoped per-density:
 *     - compact: NEVER guards on metrics — always renders.
 *     - full:    guards ONLY the metric-tiles sub-block on `metrics?.noise`
 *                (O023 daemon-skew: `metrics` crosses the API boundary; a
 *                pre-deploy/partial daemon may send it present-but-noise-missing),
 *                so partial payloads degrade the TILES to render-nothing WITHOUT
 *                blanking the card body (presence/lifecycle still render).
 */
import type { BrainHealth, DetailHealth, SectionKey } from '../../services/ddd';

// ── Shared constants (cloned from BrainHub SSOT — cycle 3 will import from here) ──
const SECTION_ORDER: SectionKey[] = ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];
const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;
type LifecycleStage = (typeof LIFECYCLE_STEPS)[number];

const _TRUST_ORDER = ['low', 'moderate', 'high', 'full'] as const;

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

// ── Props: a discriminated union on `density` ────────────────────────────────
interface CommonProps {
  name: string;
  kind: string;
  sectionsPresent: Record<SectionKey, boolean>;
  lifecycleStage: LifecycleStage;
}
interface CompactProps extends CommonProps {
  density: 'compact';
  health: BrainHealth;          // cheap signals, always present in a gallery summary
  onOpen: (name: string) => void;
}
interface FullProps extends CommonProps {
  density: 'full';
  metrics?: DetailHealth;       // expensive; OPTIONAL (daemon-skew) — guarded below
}
type DddCardProps = CompactProps | FullProps;

export function DddCard(props: DddCardProps) {
  const { name, kind, sectionsPresent, lifecycleStage } = props;
  const activeStep = LIFECYCLE_STEPS.indexOf(lifecycleStage);

  const body = (
    <>
      <div className="flex items-center gap-2 mb-2">
        <span className="material-symbols-outlined text-[16px] text-[#f0a500]">psychology</span>
        <span className="text-[13px] font-semibold">{name}</span>
        <span className="ml-auto text-[10px] font-mono text-[var(--color-text-faint)] px-1.5 py-0.5 rounded bg-[var(--color-bg)]">{kind}</span>
      </div>

      {/* six-section presence bar */}
      <div className="flex gap-0.5 mb-2" title="six-section presence">
        {SECTION_ORDER.map((k) => (
          <span
            key={k}
            data-testid={`presence-${name}-${k}`}
            className={`flex-1 h-1.5 rounded-sm ${sectionsPresent[k] ? 'bg-[#3fb950]' : 'bg-[var(--color-hover)]'}`}
          />
        ))}
      </div>

      {/* lifecycle progress */}
      <div className="flex items-center gap-1 mb-2 text-[9px] font-mono">
        {LIFECYCLE_STEPS.map((s, i) => (
          <span key={s} className={i <= activeStep ? 'text-[#3fb950]' : 'text-[#3b4552]'}>
            {s}{i < LIFECYCLE_STEPS.length - 1 ? ' ›' : ''}
          </span>
        ))}
      </div>

      {props.density === 'compact'
        ? <CheapHealth health={props.health} />
        : <MetricTiles metrics={props.metrics} />}
    </>
  );

  // compact = clickable open-button; full = static header. Guard is density-scoped
  // in the sub-blocks above — the card body ALWAYS renders (Gate-1 invariant).
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

/** 4 cheap health signals (compact) — cloned from BrainHub `Health` grid. */
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

/** 4 expensive metric tiles (full) — cloned from BrainHub `HealthStrip` action
 *  tiles. Density-scoped guard: `metrics?.noise` missing → render nothing (the
 *  card body is unaffected — that's the Gate-1 correction). */
function MetricTiles({ metrics }: { metrics?: DetailHealth }) {
  if (!metrics || !metrics.noise) return null;
  const { below, total } = _trustBelowHigh(metrics.trust);
  const trustValue = total === 0 ? '—' : `${below}/${total}`;

  // 5-dim diagnostics row (demoted): flatten doc→section → one muted line each.
  // Cloned verbatim from HealthStrip (BrainHub.tsx) so the detail view keeps ALL
  // its DetailHealth signal after cycle-3 migration — dropping it would be silent
  // information loss (the per-section composite/trust scores).
  const diagFlat: { key: string; composite?: number; trust?: string }[] = [];
  if (metrics.diagnostics) {
    for (const [doc, docData] of Object.entries(metrics.diagnostics)) {
      const sections = docData?.sections ?? {};
      for (const [sec, s] of Object.entries(sections)) {
        diagFlat.push({ key: `${doc}·${sec}`, composite: s?.composite, trust: s?.trust });
      }
    }
  }

  return (
    <>
      {/* ── ACTION tiles (headline) ── */}
      <div className="flex flex-wrap gap-2">
        <MetricTile label="Noise" value={String(metrics.noise.reclaimable)} warn={metrics.noise.reclaimable > 0}
          hint={metrics.noise.reclaimable > 0 ? 'reclaim can strip' : undefined} />
        <MetricTile label="Trust" value={trustValue} warn={below > 0}
          hint={total === 0 ? 'no scheduled score' : (below > 0 ? 'sections below high' : 'all ≥ high')} />
        <MetricTile label="Escalation" value={String(metrics.escalationPending)} warn={metrics.escalationPending > 0}
          hint={metrics.escalationPending > 0 ? 'awaiting review' : undefined} />
        <MetricTile label="Recall" value={metrics.recall.value === null ? '—' : String(metrics.recall.value)}
          experimental={metrics.recall.experimental} />
      </div>
      {/* ── DIAGNOSTICS row (demoted: smaller, muted, no action-hint, no status color) ── */}
      {diagFlat.length > 0 && (
        <div data-testid="health-diagnostics" className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
          {diagFlat.map((r) => (
            <span key={r.key} className="text-[9px] text-[var(--color-text-faint)]">
              {r.key}: {r.composite ?? '?'}{r.trust ? ` (${r.trust})` : ''}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

function MetricTile(
  { label, value, hint, warn, experimental }:
  { label: string; value: string; hint?: string; warn?: boolean; experimental?: boolean },
) {
  return (
    <div
      data-testid={`health-tile-${label.toLowerCase()}`}
      className="flex flex-col gap-0.5 px-2.5 py-1.5 rounded-md bg-[var(--color-card)] border border-[var(--color-border)] min-w-[92px]"
    >
      <div className="flex items-center gap-1">
        <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{label}</span>
        {experimental && (
          <span
            data-testid="recall-experimental-chip"
            title="Benchmark口径 not yet validated against real usage — trend, not a grade"
            className="text-[8px] px-1 rounded bg-[#3a2f12] text-[#e0b050] uppercase"
          >
            exp
          </span>
        )}
      </div>
      <span className={`text-[15px] font-semibold ${warn ? 'text-[#f0a500]' : 'text-[var(--color-text)]'}`}>{value}</span>
      {hint && <span className="text-[9px] text-[var(--color-text-muted)]">{hint}</span>}
    </div>
  );
}
