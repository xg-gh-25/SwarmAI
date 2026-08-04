/**
 * DddCard.tsx — the unified, density-driven DDD card (run_6924b463, cycles 2-3).
 *
 * SSOT for rendering ONE brain's state, replacing BrainHub's old split between
 * `BrainCard` (gallery) and `HealthStrip`/`ActionTile` (detail). Three consumers,
 * TWO densities:
 *   • compact — clickable gallery / Home-calm card: name·kind header + six-section
 *     presence bar + lifecycle progress + 4 CHEAP health signals (from BrainSummary).
 *   • full    — a static, metrics-bearing card whose SUMMARY decorations are
 *     CONDITIONAL, because its two consumers carry different data:
 *       - detail view (BrainView): the endpoint returns BrainDetail, which has NO
 *         lifecycleStage / cheap-health / clean sectionsPresent — only DetailHealth.
 *         So detail passes ONLY `metrics` → renders bare tiles+diagnostics (a
 *         faithful HealthStrip replacement; BrainView keeps its own header+nav).
 *       - Home hero: has BOTH a BrainSummary and the hero's BrainDetail → passes
 *         everything → renders the rich card (header+presence+lifecycle+cheap+tiles).
 *     Each `full` sub-block renders iff its data is present — never crashes, never
 *     shows a header/presence for a consumer that lacks the data.
 *
 * GATE-1 CORRECTION (the load-bearing invariant): the guard is density-scoped, NOT
 * a whole-card `if(!health?.noise) return null`. compact has no `noise` and MUST
 * always render (a blank gallery card is the bug the skeptic caught). full guards
 * ONLY the metric-tiles sub-block on `metrics?.noise` (O023 daemon-skew: `metrics`
 * crosses the API boundary; a partial daemon may send it present-but-noise-missing),
 * so a partial payload degrades the TILES to nothing WITHOUT blanking the card body.
 */
import type { BrainHealth, DetailHealth, SectionKey } from '../../services/ddd';

// ── Shared constants (SSOT — the old BrainHub local copies are gone; DddCard owns them) ──
const SECTION_ORDER: SectionKey[] = ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];
const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;
export type LifecycleStage = (typeof LIFECYCLE_STEPS)[number];

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
   *  view (BrainDetail has no lifecycle/cheap-health). Header+presence+lifecycle+
   *  cheap-health render iff their data is provided. */
  sectionsPresent?: Record<SectionKey, boolean>;
  lifecycleStage?: LifecycleStage;
  health?: BrainHealth;
  metrics?: DetailHealth;       // expensive tiles; OPTIONAL (daemon-skew) — guarded
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
      {props.density === 'full' && <MetricTiles metrics={props.metrics} />}
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

/** 4 expensive action tiles + demoted diagnostics row — cloned from BrainHub
 *  `HealthStrip`. Density-scoped guard: `metrics?.noise` missing → render nothing
 *  (the card body is unaffected — that's the Gate-1 correction). */
function MetricTiles({ metrics }: { metrics?: DetailHealth }) {
  if (!metrics || !metrics.noise) return null;
  const { below, total } = _trustBelowHigh(metrics.trust);
  const trustValue = total === 0 ? '—' : `${below}/${total}`;

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
