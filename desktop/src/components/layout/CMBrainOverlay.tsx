/**
 * CMBrainOverlay — the C&M (Context & Memory) Global Brain overlay.
 *
 * The cognition-zone "Context / C&M" nav card opens this fullscreen overlay. Its
 * purpose (XG): opening it should teach the user WHAT is loaded into the agent's
 * head right now, WHAT it has learned, and HOW the brain works — a teaching +
 * control surface, not a settings panel.
 *
 * Run 1 scope (run_5f7d4fe1): the 3-tab shell (Context / Memory / Guideline) with
 * ONLY the Context tab implemented + a fixed overview rail. Memory + Guideline are
 * labeled placeholders filled by later runs.
 *
 * Data is backend-primary (IMPROVEMENT.md:367 — "when a symptom shows in a UI
 * widget, the fix layer is often the backend that feeds it"): the Context tab +
 * overview rail CONSUME the calibrated token_block and the north star readings
 * (core.north_star.measures_for_ui) from GET /eval/context-health/lite;
 * the frontend invents no numbers. Opens on the existing `swarm:show-context`
 * window event via useExclusiveOverlay (single-overlay mux + back-to-chat).
 *
 * @exports CMBrainOverlay
 */
import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery } from './overlayQueries';
import api, { classifyLoadError } from '../../services/api';
import { OverlayError } from './OverlayState';
import { SectionLabel, StatusBadge, tableHeadClass, type Tone, Tabs } from '../overlay';

// ── Types (mirror the backend context-health token_block, snake_case as served) ──
type HealthTag = 'fresh' | 'idle' | 'growing' | 'oversized';
interface TokenFileRow {
  name: string;
  tokens: number;          // DISK size (conservative headline)
  pct: number;             // share of DISK total
  owner: 'system' | 'user' | 'agent' | 'auto';
  priority: number;
  locked: boolean;
  health?: HealthTag;
  // Knowledge-health counts: null for prose files; the entry-structured files
  // carry their entry count and how many entries repeat another exactly.
  health_counts?: {
    entries: number;
    duplicate: number;
  } | null;
}

// Health tag → tint (backend decides the tag; UI only colors it). fresh=calm,
// idle=muted, growing=amber-warn, oversized=red-risk.
const HEALTH_TONE: Record<HealthTag, Tone> = {
  fresh: 'success', idle: 'neutral', growing: 'warning', oversized: 'error',
};
const HEALTH_TINT: Record<HealthTag, string> = {
  fresh: 'var(--color-status-success)',
  idle: 'var(--color-text-muted)',
  growing: 'var(--color-status-warning)',
  oversized: 'var(--color-status-error)',
};
interface TokenBlock {
  total_tokens: number;         // DISK total == prompt load (always full-injected)
  budget: number;
  warning_threshold: number;
  emergency_threshold: number;
  over_budget: boolean;
  per_file: TokenFileRow[];
}
// Lite first-paint payload (GET /eval/context-health/lite) — exactly 3 keys, no
// heavy scans. The overlay's Context tab + rail (load, north star) + Governance count
// consume this.
interface CognitionMeasures {
  week: string;
  sessions: number;
  corrected_rate: number;              // share of the week's sessions the person corrected
  earlier_mean: number | null;
  earlier_weeks: number;
  direction: 'better' | 'worse' | 'same' | null;   // fewer corrected sessions is better
  repeat_rate: number | null;
  corrections: number | null;
}
interface CdfMeasures {
  useful_rate: number | null;
  target: number | null;
  below_target: string[];
  checks_total: number;
  checks_failing: string[];
}
interface NorthStar {
  at: string;
  measures: { cognition?: CognitionMeasures | null; cdf?: CdfMeasures | null };
}
interface ContextHealthLite {
  token_block?: TokenBlock | null;
  governance_pending_count?: number;
  north_star?: NorthStar | null;
}

type TabKey = 'context' | 'memory' | 'guideline' | 'evolution';

// ── Archive browse types (mirror GET /eval/archive-list + /eval/archive-search) ──
// Two families share ONE shape (only `source` differs) → one reusable ArchivePanel.
type ArchiveSource = 'memory' | 'evolution';
// Run C: archive-list returns a FILE list (one row per shard), NOT a per-entry dump.
interface ArchiveFile {
  name: string;          // shard filename, e.g. MEMORY-archive-2026-07.md
  bytes: number;         // file size on disk
  period: string;        // 'YYYY-MM' from the name, or 'legacy' for the undated shard
  entry_count: number;   // parsed entries in the shard
}
interface ArchiveListResp { files: ArchiveFile[]; total_files: number; source: ArchiveSource; }
interface ArchiveSearchHit { title: string; snippet: string; source_file: string; shard: string; }
interface ArchiveSearchResp { results: ArchiveSearchHit[]; q: string; source: ArchiveSource; }

// The Needs-you queue's intent-bearing label + a one-line explainer stating WHAT it
// governs and what its `conf` means there. (Knowledge needs no queue: CDF judges and
// writes it.)
const QUEUE_META: Record<'approve', { label: string; conf_meaning: string; explainer: string }> = {
  approve: {
    label: 'Governance',
    conf_meaning: 'recurrence confidence',
    explainer: 'Proposed rules for how the agent behaves: Governance Candidates with 3× evidence, already checked against the existing rules.',
  },
};

const OWNER_LABEL: Record<TokenFileRow['owner'], string> = {
  system: 'system',
  user: 'user',
  agent: 'agent',
  auto: 'auto',
};

// Ownership → accent tint (aligns to the workspace ownership color model).
const OWNER_TINT: Record<TokenFileRow['owner'], string> = {
  system: 'var(--color-text-muted)', // slate — system-owned, non-editable
  user: 'var(--color-primary)', // teal — user-owned
  agent: 'var(--color-status-success)', // cognition green — agent-owned (memory/evolution)
  auto: 'var(--color-text-secondary)', // violet — auto-generated
};

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(n);
}

const LITE_QUERY_KEY = overlayQuery.contextLite().queryKey;

function useContextHealth(enabled: boolean) {
  // AC1: first paint hits the THIN endpoint (token_block + counts only) so the overlay
  // opens instantly — the heavy /eval/context-health (5 scans) is never fetched here.
  // Memory tab keeps its own lazy brain-graph/brain-trend queries.
  return useQuery({ ...overlayQuery.contextLite<ContextHealthLite>(), enabled });
}

/**
 * CMBrainContent — the C&M Global Brain surface content (M3: migrated to the
 * OverlayHost registry). The host owns the Modal chrome + mount lifecycle (it only
 * mounts this while `activeOverlay === 'context'`), so this component always renders
 * its content — no `open`/`close`/`useExclusiveOverlay` self-management. Data queries
 * are `enabled: true` because the component only exists while the surface is open.
 */
export function CMBrainContent() {
  const [tab, setTab] = useState<TabKey>('context');
  const qc = useQueryClient();

  // Always fetch: the host mounts this only while the surface is open.
  const { data, isError: healthErr, error: healthError, refetch: refetchHealth } = useContextHealth(true);
  const block = data?.token_block ?? null;
  // AC1: Approve BADGE count comes from the lite payload — no first-paint
  // governance/pending fetch. The governance LIST is fetched lazily (below) only
  // when the user opens the Approve list.
  const approveCount = data?.governance_pending_count ?? 0;

  // Which Needs-you list is filtered into the main area (null = show the tab).
  const [needsFilter, setNeedsFilter] = useState<null | 'approve'>(null);

  // Lazy governance LIST — only fetched once the Approve list is opened (the badge
  // count already came from lite). Keeps first paint to a single lite request.
  const { data: gov, isError: govErr, error: govError, refetch: refetchGov } = useQuery<{ proposals: unknown[]; total: number }>({
    queryKey: ['cm-governance-pending'],
    queryFn: async () => (await api.get<{ proposals: unknown[]; total: number }>('/eval/governance/pending')).data,
    staleTime: 30_000,
    enabled: needsFilter === 'approve',
  });
  // B1: a failed fetch used to fall back to 0 / '—' silently — surface it.
  const needsErr = healthErr || (needsFilter === 'approve' && govErr);

  // Growth-trend series for the rail (same source as the Memory size-trend).
  const { data: trend } = useQuery(overlayQuery.contextTrend<BrainTrend>());

  // Decision on a governance candidate. On success, invalidate the lite query (the
  // badge) and the governance list.
  async function decide(_kind: 'approve', id: string, decision: 'accept' | 'reject' | 'defer') {
    await api.post('/eval/governance/decision', { proposal_id: id, decision });
    await Promise.all([
      qc.invalidateQueries({ queryKey: LITE_QUERY_KEY }),
      qc.invalidateQueries({ queryKey: ['cm-governance-pending'] }),
    ]);
  }

  // items:null == "still loading" (distinct from [] == "loaded, empty") so the list
  // never shows a FALSE "nothing to approve" during the lazy governance fetch (Gate-2 HIGH-1).
  const govLoading = needsFilter === 'approve' && gov === undefined && !govErr;
  const needsMeta = {
    approve: { label: 'Approve', count: approveCount, items: govLoading ? null : ((gov?.proposals ?? []) as Array<Record<string, unknown>>) },
  };

  return (
      <div className="flex h-full min-h-0" data-testid="cm-brain-overlay">
        {/* ── Left overview rail (fixed 264px, tab-independent) ── */}
        <aside
          className="w-[264px] shrink-0 flex flex-col gap-4 border-r border-[var(--color-border)] p-4 overflow-y-auto"
          data-testid="cm-overview-rail"
        >
          <NorthStarRail ns={data?.north_star} failed={healthErr && !data} />

          <div>
            <SectionLabel>Current load</SectionLabel>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span
                className="text-display font-semibold"
                style={{ color: block?.over_budget ? 'var(--color-status-error)' : 'var(--color-text)' }}
              >
                {block ? fmtTokens(block.total_tokens) : '—'}
              </span>
              <span className="text-small text-[var(--color-text-muted)]">
                / {block ? fmtTokens(block.budget) : '—'} warning line
              </span>
            </div>
            {healthErr && !block && (
              <div className="mt-1 text-meta text-[var(--color-status-warning)]">couldn’t load — not “0”</div>
            )}
            {block?.over_budget && <BudgetAlert block={block} />}
          </div>

          {/* 30-day token growth — from the daily snapshot series (collecting until >=2 pts) */}
          <div>
            <SectionLabel>30-day token growth</SectionLabel>
            <div className="mt-1.5">
              <RailTrend trend={trend} />
            </div>
          </div>

          <div>
            <SectionLabel>Needs you</SectionLabel>
            {needsErr ? (
              // B1: don't show a false "0" — say the fetch failed + offer Retry.
              <OverlayError
                compact
                className="mt-2"
                testid="cm-needs-error"
                retryTestid="cm-needs-retry"
                message={classifyLoadError(healthError ?? govError, 'the queue', 'Couldn’t load the queue — the backend may be unavailable. This is NOT “nothing to do”.')}
                onRetry={() => { void refetchHealth(); void refetchGov(); }}
              />
            ) : (
              <div className="mt-2 flex flex-col gap-1.5">
                <NeedsBtn testid="cm-needs-approve" label={QUEUE_META.approve.label} count={approveCount} tint="var(--color-status-warning)" active={needsFilter === 'approve'} onClick={() => setNeedsFilter('approve')} />
              </div>
            )}
          </div>
        </aside>

        {/* ── Main area: tabs + panel, OR the Needs-you filtered list ── */}
        <div className="flex-1 min-w-0 flex flex-col">
          {needsFilter ? (
            <NeedsList
              kind={needsFilter}
              meta={needsMeta[needsFilter]}
              backLabel={tab === 'context' ? 'Context' : tab === 'memory' ? 'Memory' : tab === 'evolution' ? 'Evolution' : 'Guideline'}
              onBack={() => setNeedsFilter(null)}
              onDecide={decide}
            />
          ) : (
            <>
              <div className="border-b border-[var(--color-border)] px-4 py-2.5">
                <Tabs label="Context & Memory" value={tab} onChange={setTab} items={[
                  { key: 'context', label: 'Context', testid: 'cm-tab-context', count: block?.per_file.length },
                  { key: 'memory', label: 'Memory', testid: 'cm-tab-memory' },
                  { key: 'evolution', label: 'Evolution', testid: 'cm-tab-evolution' },
                  { key: 'guideline', label: 'Guideline', testid: 'cm-tab-guideline' },
                ]} />
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-4">
                {tab === 'context' && <ContextTab block={block} />}
                {tab === 'memory' && <MemoryTab enabled={tab === 'memory'} />}
                {tab === 'evolution' && <EvolutionTab enabled={tab === 'evolution'} />}
                {tab === 'guideline' && <GuidelineTab />}
              </div>
            </>
          )}
        </div>
      </div>
  );
}

const pct = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`);
const DIRECTION_TINT = {
  better: 'var(--color-status-success)', worse: 'var(--color-status-warning)', same: 'var(--color-text-muted)',
} as const;

// "Am I getting better?" — the north star readings (SWARMAI.md § My North Star), from
// the same snapshot file the prompt reads. One verdict stands out (the share of
// sessions the person corrected, against the weeks before); repeats and the knowledge
// flow are the supporting lines. Read-only; an absent snapshot says so, never "0".
function NorthStarRail({ ns, failed }: { ns: NorthStar | null | undefined; failed: boolean }) {
  const cog = ns?.measures?.cognition;
  const cdf = ns?.measures?.cdf;
  const at = ns?.at ? new Date(ns.at) : null;
  return (
    <div data-testid="cm-north-star">
      <SectionLabel>Getting better?</SectionLabel>
      {!ns || (!cog && !cdf) ? (
        <div data-testid="cm-north-star-empty" className="mt-1 text-meta text-[var(--color-text-faint)]">
          {failed ? 'couldn’t load — not “0”' : 'not measured yet'}
        </div>
      ) : (
        <div className="mt-1 flex flex-col gap-1.5">
          {cog && (
            <div data-testid="cm-ns-corrected">
              <div className="flex items-baseline gap-1.5">
                <span className="text-display font-semibold text-[var(--color-text)]">{pct(cog.corrected_rate)}</span>
                <span className="text-small text-[var(--color-text-muted)]">of sessions corrected</span>
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">
                {cog.week} · {cog.sessions} sessions
                {cog.direction && cog.earlier_mean != null && (
                  <span data-testid="cm-ns-direction" style={{ color: DIRECTION_TINT[cog.direction] }}>
                    {' '}· {cog.direction === 'same' ? 'about the same as' : `${cog.direction} than`} {pct(cog.earlier_mean)} before
                  </span>
                )}
              </div>
            </div>
          )}
          {cog && cog.repeat_rate != null && (
            <div data-testid="cm-ns-repeats" className="text-small text-[var(--color-text-muted)]">
              Repeats <span className="tabular-nums text-[var(--color-text)]">{pct(cog.repeat_rate)}</span>
              {cog.corrections != null && <> of {cog.corrections} corrections</>}
            </div>
          )}
          {cdf && (
            <div data-testid="cm-ns-cdf" className="text-small text-[var(--color-text-muted)]">
              Knowledge useful{' '}
              <span className="tabular-nums" style={{ color: cdf.useful_rate != null && cdf.target != null && cdf.useful_rate < cdf.target ? 'var(--color-status-warning)' : 'var(--color-text)' }}>
                {pct(cdf.useful_rate)}
              </span>
              {cdf.target != null && <> · target {pct(cdf.target)}</>}
            </div>
          )}
          {at && (
            <div className="text-meta text-[var(--color-text-faint)]">
              as of {at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// The rail's compact growth-trend — prompt_tokens over time. R30: <2 points →
// explicit "collecting" text, never a fabricated line.
function RailTrend({ trend }: { trend: BrainTrend | undefined }) {
  const pts = trend?.points ?? [];
  if (pts.length < 2) {
    return (
      <div className="text-meta text-[var(--color-text-faint)]">
        Collecting since {trend?.launch_date ?? 'launch'}…
      </div>
    );
  }
  const vals = pts.map((p) => p.prompt_tokens);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const W = 232, H = 40;
  const path = pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * W;
    const y = H - ((p.prompt_tokens - min) / range) * H;
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg data-testid="cm-rail-trend-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-10">
      <path d={path} fill="none" stroke="var(--color-status-success)" strokeWidth="1.5" />
    </svg>
  );
}

// Over-the-warning-line alert (AC7) — honest: how much over + which files carry the
// load + an OPEN action (never a fake "distill" that does nothing). Every file is
// injected whole (nothing is truncated), so the alert asks for consolidation. Only
// rendered when block.over_budget is true.
function BudgetAlert({ block }: { block: TokenBlock }) {
  const over = block.total_tokens - block.budget;
  // The offenders: biggest files that are growing/oversized (the ones worth acting on).
  const offenders = [...block.per_file]
    .filter((f) => f.health === 'oversized' || f.health === 'growing')
    .sort((a, b) => b.tokens - a.tokens)
    .slice(0, 2);
  const top = offenders.length ? offenders : [...block.per_file].sort((a, b) => b.tokens - a.tokens).slice(0, 2);
  return (
    <div
      data-testid="cm-budget-alert"
      className="mt-2 rounded-md border px-2.5 py-2"
      style={{ borderColor: 'color-mix(in srgb, var(--color-status-error) 45%, var(--color-border))', background: 'color-mix(in srgb, var(--color-status-error) 8%, transparent)' }}
    >
      <div className="text-meta font-semibold" style={{ color: 'var(--color-status-error)' }}>
        Over the warning line by {fmtTokens(over)} — nothing is cut; consolidate the biggest files
      </div>
      <div className="mt-1 text-meta leading-snug text-[var(--color-text-muted)]">
        {top.map((f) => (
          <span key={f.name} className="block">
            <span className="text-[var(--color-text)]">{f.name}</span> {fmtTokens(f.tokens)}
            {f.health && <span style={{ color: HEALTH_TINT[f.health] }}> ({f.health})</span>}
          </span>
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {top.map((f) => (
          <button
            key={f.name}
            data-testid={`cm-budget-open-${f.name}`}
            onClick={() => document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: `.context/${f.name}` } }))}
            className="btn-sm btn-danger"
          >
            Open {f.name}
          </button>
        ))}
      </div>
    </div>
  );
}

// A Need-You governance candidate rendered as a What / Where / Why card with actions —
// the id is a demoted footnote, never the subject.
function ProposalCard({
  kind, item, onDecide,
}: {
  kind: 'approve';
  item: Record<string, unknown>;
  onDecide: (kind: 'approve', id: string, decision: 'accept' | 'reject' | 'defer') => void | Promise<void>;
}) {
  const id = String(item.id ?? 'unknown');
  const [state, setState] = useState<null | string>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const conf = typeof item.confidence === 'number' ? (item.confidence as number) : null;

  // A Governance Candidate (EVOLUTION.md GC row): 3× evidence, already checked
  // against the existing rules. The GC id is the tag; the Checked text is the why.
  const gcId = String(item.gc_id ?? id);
  const occ = item.occurrence_count;
  const what = `Add rule: ${String(item.proposed_rule ?? '(no rule text)')}`;
  const where: ReactNode = <>governance rule · EVOLUTION.md {gcId}</>;
  const why = `${String(item.checked ?? '')}${typeof occ === 'number' ? ` · seen ${occ}×` : ''}`;
  const tag: ReactNode = (
    <span
      data-testid="cm-gc-id"
      className="mr-1.5 rounded px-1.5 py-[1px] text-meta font-semibold font-mono"
      style={{ color: 'var(--color-primary)', background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}
    >
      {gcId}
    </span>
  );

  async function act(decision: 'accept' | 'reject' | 'defer') {
    setState(decision);
    setRefused(null);
    try { await onDecide(kind, id, decision); } catch (err) {
      // the backend's reason (e.g. the row quotes a phrase the memory guard blocks) is
      // shown on the card; without it a refused decision looked like a dead button
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setRefused(typeof detail === 'string' && detail ? detail : 'The decision could not be saved.');
      setState(null);
    }
  }

  return (
    <div
      data-testid={`cm-proposal-${id}`}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-3"
      style={{ opacity: state ? 0.4 : 1 }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div data-testid="cm-card-what" className="text-body font-semibold leading-snug text-[var(--color-text)]">{tag}{what}</div>
          <div className="mt-0.5 text-meta text-[var(--color-text-muted)]">{where}</div>
        </div>
        {conf != null && <span className="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-[1px] text-meta text-[var(--color-text-faint)]">conf {conf.toFixed(2)}</span>}
      </div>
      {why && <div className="mt-2 border-l-2 border-[var(--color-border)] pl-2.5 text-small leading-relaxed text-[var(--color-text-muted)]">{why}</div>}
      {refused && <div data-testid="cm-card-refused" role="alert" className="mt-2 text-small text-[var(--color-status-error)]">{refused}</div>}
      <div className="mt-2.5 flex items-center justify-between">
        <span className="font-mono text-meta text-[var(--color-text-faint)]">{id}</span>
        {state ? (
          <span className="text-meta text-[var(--color-text-muted)]">{state}ed</span>
        ) : (
          <div className="flex gap-1.5">
            {/* Gate-2 MEDIUM: disabled during an in-flight decision — no double-submit. */}
            <button disabled={state != null} data-testid="cm-card-accept" onClick={() => act('accept')} className="btn-sm btn-success disabled:opacity-50">Accept</button>
            <button disabled={state != null} data-testid="cm-card-reject" onClick={() => act('reject')} className="btn-sm btn-secondary disabled:opacity-50">Reject</button>
            <button disabled={state != null} data-testid="cm-card-defer" onClick={() => act('defer')} className="btn-sm btn-secondary disabled:opacity-50">Defer</button>
          </div>
        )}
      </div>
    </div>
  );
}

// Needs-you filtered list — swaps into the main area with an explicit Back header +
// breadcrumb; the rail button stays active (AC6). Items render as ProposalCards.
function NeedsList({
  kind, meta, backLabel, onBack, onDecide,
}: {
  kind: 'approve';
  meta: { label: string; count: number; items: Array<Record<string, unknown>> | null };
  backLabel: string;
  onBack: () => void;
  onDecide: (kind: 'approve', id: string, decision: 'accept' | 'reject' | 'defer') => void | Promise<void>;
}) {
  const items = meta.items;
  const qm = QUEUE_META[kind];
  return (
    <>
      <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-2.5">
        <button data-testid="cm-needs-back" onClick={onBack} className="btn-sm btn-secondary">← Back to {backLabel}</button>
        <span className="text-body font-semibold text-[var(--color-text)]">{qm.label} <span className="tabular-nums text-[var(--color-text-faint)]">({meta.count})</span></span>
      </div>
      {/* AC1: one-line explainer — what this queue governs + what its conf means here. */}
      <div data-testid="cm-needs-explainer" className="border-b border-[var(--color-border)] px-4 py-2 text-meta leading-relaxed text-[var(--color-text-muted)]">
        {qm.explainer}
      </div>
      <div data-testid="cm-needs-list" className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-2.5">
        {items === null ? (
          // Gate-2 HIGH-1: loading ≠ empty — never a false "nothing to approve".
          <div data-testid="cm-needs-loading" className="text-meta text-[var(--color-text-faint)]">Loading {qm.label.toLowerCase()}…</div>
        ) : items.length === 0 ? (
          <div className="text-meta text-[var(--color-text-faint)]">Nothing in {qm.label.toLowerCase()} right now.</div>
        ) : (
          items.map((it, i) => (
            // key includes kind — a re-appearing same-id in a DIFFERENT queue remounts
            // fresh (Gate-2 HIGH-2: no stale optimistic state carried across refetch).
            <ProposalCard key={`${kind}-${String(it.id ?? i)}`} kind={kind} item={it} onDecide={onDecide} />
          ))
        )}
      </div>
    </>
  );
}

function ContextTab({ block }: { block: TokenBlock | null }) {
  const rows = block?.per_file ?? [];
  return (
    <div data-testid="cm-panel-context" className="flex flex-col gap-1">
      <div className="mb-2 text-body text-[var(--color-text-muted)]">
        The always-injected system prompt — {rows.length} files, priority-ordered, each injected in full.
        Nothing is cut: above the warning line, the files are kept lean by consolidating entries.
      </div>
      <div className="mb-3 text-meta text-[var(--color-text-faint)]">
        click a file to open it in Canvas · bar = share of the context (disk size = prompt load) ·
        lock = system-owned, opens read-only · Health: fresh / idle / growing / oversized
      </div>
      {/* Column header — fixed widths so every row's cells line up (AC4 alignment).
          Grid columns match the row grid below exactly. */}
      {rows.length > 0 && (
        <div
          data-testid="cm-file-header"
          className={`grid items-center gap-3 px-3 pb-1 max-w-3xl ${tableHeadClass}`}
          style={{ gridTemplateColumns: '2rem 0.375rem 1fr 4rem 4rem 6rem 4rem 1.5rem' }}
        >
          <span>Pri</span>
          <span aria-hidden />
          <span>File</span>
          <span className="text-right">Owner</span>
          <span className="text-right">Tokens</span>
          <span>Share</span>
          <span className="text-center">Health</span>
          <span aria-hidden />
        </div>
      )}
      {rows.length === 0 && (
        <div className="py-8 text-center text-body text-[var(--color-text-faint)]">
          Context budget not available yet.
        </div>
      )}
      {rows.map((f) => {
        // Every file is full-injected (new architecture 2026-08-14): disk size IS the
        // prompt load — no selective mode, no "injected floor below disk".
        const tokenTitle = `${fmtTokens(f.tokens)} tokens (full-injected — disk == prompt load)`;
        return (
        // The WHOLE row opens the file in Canvas. Layout is a fixed-column GRID
        // (AC4): every cell aligns across rows — Pri | owner-dot | name(flex) |
        // owner | tokens | share-bar+% | health | lock.
        <button
          key={f.name}
          type="button"
          data-testid={`cm-file-row-${f.name}`}
          data-owner={f.owner}
          title={`Open ${f.name} in Canvas${f.locked ? ' (read-only)' : ''}`}
          onClick={() =>
            document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: `.context/${f.name}` } }))
          }
          className="grid w-full items-center gap-3 rounded-md px-3 py-2 max-w-3xl text-left hover:bg-[var(--color-hover)]"
          style={{ gridTemplateColumns: '2rem 0.375rem 1fr 4rem 4rem 6rem 4rem 1.5rem' }}
        >
          <span className="tabular-nums text-small text-[var(--color-text-faint)]">P{f.priority}</span>
          <span
            className="w-1.5 h-4 rounded-full"
            style={{ background: OWNER_TINT[f.owner] }}
            aria-hidden
          />
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-body text-[var(--color-text)]">
              {f.name}
            </span>
            {/* Knowledge-health: exact duplicates only, and only when non-zero
                (a healthy file shows nothing). */}
            {f.health_counts && f.health_counts.duplicate > 0 && (
              <span
                data-testid={`cm-health-counts-${f.name}`}
                className="mt-0.5 flex gap-2 text-meta tabular-nums text-[var(--color-text-faint)]"
                title="Knowledge health — entries that repeat another entry of the same section exactly"
              >
                {f.health_counts.duplicate > 0 && <span className="text-[var(--color-warning,var(--color-status-error))]">{f.health_counts.duplicate} dup</span>}
              </span>
            )}
          </span>
          <span className="text-right text-meta text-[var(--color-text-faint)]">{OWNER_LABEL[f.owner]}</span>
          {/* Tokens — DISK size, right-aligned, fixed col */}
          <span className="text-right tabular-nums text-small text-[var(--color-text-muted)]" title={tokenTitle}>
            {fmtTokens(f.tokens)}
          </span>
          {/* AC4: SHARE — an explicit visible bar + % number (user-requested; this
              intentionally overrides the prior subtle-tint approach). Bar width =
              pct of on-disk total, owner-tinted; the % number is shown to its right. */}
          <span className="flex items-center gap-1.5" title={`${f.pct}% of the on-disk context`}>
            <span className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-border)]">
              {typeof f.pct === 'number' && f.pct > 0 && (
                <span
                  data-testid="cm-pct-bar"
                  aria-hidden
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{ width: `${Math.max(2, Math.min(100, f.pct))}%`, background: OWNER_TINT[f.owner] }}
                />
              )}
            </span>
            <span data-testid="cm-pct-num" className="w-8 shrink-0 text-right font-mono text-meta text-[var(--color-text-faint)]">
              {typeof f.pct === 'number' ? `${f.pct}%` : '—'}
            </span>
          </span>
          {f.health ? (
            <StatusBadge testid="cm-health" tone={HEALTH_TONE[f.health]}>{f.health}</StatusBadge>
          ) : <span aria-hidden />}
          {f.locked ? (
            <span data-testid="cm-lock" className="material-symbols-outlined icon-xs text-center text-[var(--color-text-faint)]" title="System-owned · opens read-only">lock</span>
          ) : <span aria-hidden />}
        </button>
        );
      })}
    </div>
  );
}

// ── Memory tab: the sedimented 7-type knowledge (DoD2) ──────────────────────
interface GraphNode { type: string; count: number; }
interface DrillEntry { title: string; meta: string; }
interface BrainGraph { nodes: GraphNode[]; drill: Record<string, DrillEntry[]>; total: number; }
interface TrendPoint { date: string; prompt_tokens: number; memory_bytes: number; }
interface BrainTrend { points: TrendPoint[]; count: number; launch_date: string | null; }

// 7-type tint (aligns to the ontology; stable across renders). Evolution kinds get
// their own tints; an unmapped kind falls back to slate in the render (?? 'var(--color-text-muted)').
const TYPE_TINT: Record<string, string> = {
  principle: 'var(--color-status-success)', correction: 'var(--color-status-error)', decision: 'var(--color-primary)', guideline: 'var(--color-text-secondary)',
  pitfall: 'var(--color-status-warning)', process: 'var(--color-text-muted)', model: 'var(--color-primary)',
  // evolution kinds
  class: 'var(--color-status-error)', 'meta-correction': 'var(--color-status-error)', 'root-cause': 'var(--color-primary)',
  'data-point': 'var(--color-primary)', directive: 'var(--color-text-secondary)', 'failed-evolution': 'var(--color-status-warning)', entry: 'var(--color-text-muted)',
};

/**
 * KnowledgeGraph — the shared graph+bars+drill surface for BOTH the Memory tab
 * (7-type ontology) and the Evolution tab (evolution kinds). Backend returns nodes
 * ALREADY in display-priority order (Run C); this renders them IN-ORDER and NEVER
 * re-sorts by count (R30 — order is a backend decision). `ontology` is true for
 * memory (the 7 types) and false for evolution (its kinds). Nodes are count-only:
 * every entry in a main file is in force. One component, two sources (R25).
 */
function KnowledgeGraph({ graph, ontology }: { graph: BrainGraph | undefined; ontology: boolean }) {
  const [selType, setSelType] = useState<string | null>(null);
  const nodes = graph?.nodes ?? [];
  const maxCount = Math.max(1, ...nodes.map((n) => n.count));
  const drill = (selType && graph?.drill[selType]) || [];

  return (
    <>
      {/* type/kind graph — nodes sized by count, rendered in backend priority order */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          Knowledge graph · {ontology ? '7 types' : 'kinds'} as nodes (click to drill in)
        </div>
        <div className="flex flex-wrap gap-3">
          {nodes.map((n) => {
            const tint = TYPE_TINT[n.type] ?? 'var(--color-text-muted)';
            const size = 44 + Math.round((n.count / maxCount) * 40); // 44-84px by count
            const sel = selType === n.type;
            return (
              <button
                key={n.type}
                data-testid={`cm-graph-node-${n.type}`}
                data-viz
                onClick={() => setSelType(sel ? null : n.type)}
                title={`${n.type}: ${n.count}`}
                className="flex flex-col items-center justify-center rounded-full border-2 transition-transform hover:scale-105 shrink-0"
                style={{
                  width: size, height: size, borderColor: tint,
                  background: `color-mix(in srgb, ${tint} 16%, transparent)`,
                  boxShadow: sel ? `0 0 0 3px color-mix(in srgb, ${tint} 40%, transparent)` : 'none',
                }}
              >
                <span className="tabular-nums text-body font-extrabold" style={{ color: tint }}>{n.count}</span>
                <span className="tabular-nums text-meta font-semibold" style={{ color: tint }}>{n.type.slice(0, 4)}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-1.5 text-meta text-[var(--color-text-faint)]">
          node size = entry count
        </div>
      </section>

      {/* by-type distribution bars (also drill) */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          By-{ontology ? 'type' : 'kind'} distribution
        </div>
        <div className="flex flex-col gap-1">
          {nodes.map((n) => {
            const tint = TYPE_TINT[n.type] ?? 'var(--color-text-muted)';
            return (
              <button
                key={n.type}
                data-testid={`cm-bar-${n.type}`}
                onClick={() => setSelType(n.type)}
                className="flex items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--color-hover)]"
              >
                <span className="w-24 shrink-0 tabular-nums text-meta text-[var(--color-text-muted)]">{n.type}</span>
                <span className="flex-1 h-2 rounded-full bg-[var(--color-border)] max-w-md">
                  <span className="block h-2 rounded-full" style={{ width: `${Math.max(3, (n.count / maxCount) * 100)}%`, background: tint }} />
                </span>
                <span className="w-8 shrink-0 text-right tabular-nums text-meta text-[var(--color-text-faint)]">{n.count}</span>
              </button>
            );
          })}
        </div>
      </section>

      {/* drill-down list */}
      <section>
        <div data-testid="cm-drill-list" className="rounded-lg border border-[var(--color-border)] p-3">
          {!selType ? (
            <div className="text-meta text-[var(--color-text-faint)]">Click a graph node (or a bar) → latest entries of that {ontology ? 'type' : 'kind'}</div>
          ) : drill.length === 0 ? (
            <div className="text-meta text-[var(--color-text-faint)]">No <b>{selType}</b> entries yet.</div>
          ) : (
            <>
              <div className="mb-1.5 text-meta font-semibold text-[var(--color-text)]">Latest {selType} ({drill.length})</div>
              <div className="flex flex-col gap-1">
                {drill.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 text-small">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: 'var(--color-status-success)' }} />
                    <span className="min-w-0 flex-1 truncate text-[var(--color-text)]">{e.title}</span>
                    {e.meta && <span className="shrink-0 tabular-nums text-meta text-[var(--color-text-faint)]">{e.meta}</span>}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>
    </>
  );
}

// ── ArchivePanel: the reusable Archived-list + Recall-search surface ─────────
// ONE component for BOTH families (Memory + Evolution) — only `source` differs
// (R4/R25: no duplicated list/search UI). Design (s_frontend-design, R15): this is
// a LIST-with-search (Surface 5 knowledge-view), NOT a tile dashboard — one row per
// entry, whitespace-separated (no bordered-box wall), the search box demoted until
// used, honest loading/empty/error states. A live query SWAPS the list for results.
//
// Gate-0 REVISE baked in: date can be null (→ '—'), archived_from can be '' (→ hidden).
function ArchivePanel({ source, enabled }: { source: ArchiveSource; enabled: boolean }) {
  const [q, setQ] = useState('');
  const query = q.trim();
  const searching = query.length > 0;

  // Archived list — the default view (fetched on tab open). Disabled while searching
  // so we don't hold a stale list query; the results query takes over.
  const { data: list, isError: listErr, error: listError, refetch: refetchList } = useQuery<ArchiveListResp>({
    queryKey: ['cm-archive-list', source],
    queryFn: async () => (await api.get<ArchiveListResp>(`/eval/archive-list?source=${source}`)).data,
    staleTime: 30_000,
    enabled: enabled && !searching,
  });
  // Recall search — only fires when the query is non-empty (Gate-0 #5 enabled-gating).
  const { data: hits, isError: hitErr, error: hitError, isFetching: hitFetching, refetch: refetchHits } = useQuery<ArchiveSearchResp>({
    queryKey: ['cm-archive-search', source, query],
    queryFn: async () => (await api.get<ArchiveSearchResp>(`/eval/archive-search?source=${source}&q=${encodeURIComponent(query)}`)).data,
    staleTime: 30_000,
    enabled: enabled && searching,
  });

  const files = list?.files ?? [];
  const results = hits?.results ?? [];

  return (
    <section data-testid={`cm-archive-${source}`}>
      <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
        Archived {source} · older shards
      </div>
      <div className="mb-2 text-meta leading-relaxed text-[var(--color-text-muted)]">
        Entries moved out of live {source} by size before that stopped (nothing is moved out
        now). Open a shard to read it, or search to recall a single entry.
      </div>

      {/* Search box — one clear input; results replace the list while a query is active */}
      <div className="relative mb-3 max-w-md">
        <input
          data-testid={`cm-archive-search-input-${source}`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Recall from archived ${source}…`}
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-body text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-status-success)] focus:outline-none"
        />
        {searching && (
          <button
            data-testid={`cm-archive-search-clear-${source}`}
            onClick={() => setQ('')}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-small text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
          >
            clear
          </button>
        )}
      </div>

      {searching ? (
        // ── Recall results ──
        hitErr ? (
          <ArchiveError what={`recall ${source}`} error={hitError} onRetry={() => void refetchHits()} />
        ) : hitFetching && results.length === 0 ? (
          <div data-testid={`cm-archive-searching-${source}`} className="text-meta text-[var(--color-text-faint)]">Recalling…</div>
        ) : results.length === 0 ? (
          <div className="text-meta text-[var(--color-text-faint)]">No archived {source} matches “{query}”.</div>
        ) : (
          <div data-testid={`cm-archive-results-${source}`} className="flex flex-col gap-2.5">
            <div className="text-meta text-[var(--color-text-faint)]">{results.length} recall hit{results.length === 1 ? '' : 's'}</div>
            {results.map((r, i) => (
              <div key={`${r.shard}-${i}`} className="border-l-2 border-[var(--color-status-success)] pl-2.5">
                <div className="text-body leading-snug text-[var(--color-text)]">{r.title || '(untitled)'}</div>
                <div className="mt-0.5 text-small leading-relaxed text-[var(--color-text-muted)]">{r.snippet}</div>
                <div className="mt-0.5 font-mono text-meta text-[var(--color-text-faint)]">{r.shard}</div>
              </div>
            ))}
          </div>
        )
      ) : (
        // ── Archived FILE list (default) — one row per shard, click to open ──
        listErr ? (
          <ArchiveError what={`archived ${source}`} error={listError} onRetry={() => void refetchList()} />
        ) : list === undefined ? (
          <div data-testid={`cm-archive-loading-${source}`} className="text-meta text-[var(--color-text-faint)]">Loading archived {source}…</div>
        ) : files.length === 0 ? (
          <div data-testid={`cm-archive-empty-${source}`} className="text-meta text-[var(--color-text-faint)]">
            Nothing archived yet — live {source} is fully injected.
          </div>
        ) : (
          <div data-testid={`cm-archive-list-${source}`} className="flex flex-col gap-1">
            {files.map((f) => (
              // Whole row opens the shard in Canvas (like the Context-tab file rows).
              <button
                key={f.name}
                type="button"
                data-testid={`cm-archive-file-${source}`}
                title={`Open ${f.name} in Canvas`}
                onClick={() =>
                  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: `.context/${f.name}` } }))
                }
                className="flex items-baseline gap-3 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-hover)]"
              >
                {/* period is the primary scannable key; filename demoted to mono detail */}
                <span className="w-16 shrink-0 tabular-nums text-small text-[var(--color-text)]">{f.period}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-meta text-[var(--color-text-muted)]" title={f.name}>{f.name}</span>
                <span className="shrink-0 text-meta text-[var(--color-text-muted)]">{f.entry_count} entr{f.entry_count === 1 ? 'y' : 'ies'}</span>
                <span className="w-14 shrink-0 text-right tabular-nums text-meta text-[var(--color-text-faint)]">{fmtBytes(f.bytes)}</span>
              </button>
            ))}
          </div>
        )
      )}
    </section>
  );
}

// Byte-size → compact human string (KB/MB) for the archive file rows.
function fmtBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}MB`;
  if (n >= 1000) return `${Math.round(n / 1000)}KB`;
  return `${n}B`;
}

// Shared archive error banner — honest "couldn't load, NOT empty" + optional Retry.
function ArchiveError({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  return (
    <OverlayError
      compact
      message={classifyLoadError(error, what, `Couldn’t load ${what} — the backend may be unavailable. This is NOT “nothing archived”.`)}
      onRetry={onRetry}
    />
  );
}

function MemoryTab({ enabled }: { enabled: boolean }) {
  const { data: graph } = useQuery<BrainGraph>({
    queryKey: ['cm-brain-graph', 'memory'],
    queryFn: async () => (await api.get<BrainGraph>('/eval/brain-graph?source=memory')).data,
    staleTime: 30_000, enabled,
  });
  const { data: trend } = useQuery<BrainTrend>({
    queryKey: ['cm-brain-trend', 'memory'],
    queryFn: async () => (await api.get<BrainTrend>('/eval/brain-trend?source=memory')).data,
    staleTime: 30_000, enabled,
  });

  return (
    <div data-testid="cm-panel-memory" className="flex flex-col gap-5 max-w-4xl">
      <div className="text-body text-[var(--color-text-muted)]">
        The judgment I've sedimented across all conversations — a 7-type ontology.
        What is in the file is in force: an entry leaves only when a newer decision overrides it or it is withdrawn.
      </div>

      {/* graph + bars + drill (shared component) */}
      <KnowledgeGraph graph={graph} ontology />

      {/* MEMORY.md size trend (from the daily snapshot series) */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          MEMORY.md size trend
        </div>
        <TrendChart trend={trend} field="memory_bytes" />
      </section>

      {/* Archived list + Recall search — older shards, from before entries stopped being moved out by size */}
      <ArchivePanel source="memory" enabled={enabled} />

      <div className="text-meta text-[var(--color-text-faint)]">
        How it works: MEMORY.md is injected whole into every session. New entries come from the review after a session: the session's own agent declares what it learned, and the code checks the evidence before writing. An entry leaves the file only when a newer decision overrides it or it is withdrawn; what was replaced is recorded outside the file. Older archive shards stay readable here.
      </div>
    </div>
  );
}

// ── Evolution tab: MIRRORS the Memory tab (graph + bars + drill + trend + archive)
// for EVOLUTION.md's own kinds (class/correction/data-point/directive/…). Structural
// parity with MemoryTab via the shared KnowledgeGraph — the only differences are
// honest: source=evolution, and ontology=false (its kinds, not the 7 types).
function EvolutionTab({ enabled }: { enabled: boolean }) {
  const { data: graph } = useQuery<BrainGraph>({
    queryKey: ['cm-brain-graph', 'evolution'],
    queryFn: async () => (await api.get<BrainGraph>('/eval/brain-graph?source=evolution')).data,
    staleTime: 30_000, enabled,
  });
  const { data: trend } = useQuery<BrainTrend>({
    queryKey: ['cm-brain-trend', 'evolution'],
    queryFn: async () => (await api.get<BrainTrend>('/eval/brain-trend?source=evolution')).data,
    staleTime: 30_000, enabled,
  });

  return (
    <div data-testid="cm-panel-evolution" className="flex flex-col gap-5 max-w-4xl">
      <div className="text-body text-[var(--color-text-muted)]">
        How I evolve — the corrections I've captured and the recurring failure patterns
        they cluster into (class · correction · data-point · directive · …), ordered by
        which matters most to my judgment.
      </div>

      {/* graph + bars + drill (shared component; evolution kinds) */}
      <KnowledgeGraph graph={graph} ontology={false} />

      {/* EVOLUTION.md size trend — real per-file bytes from the daily snapshot series */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          EVOLUTION.md size trend
        </div>
        <TrendChart trend={trend} field="memory_bytes" />
      </section>

      {/* Archived file list + Recall search (source=evolution) */}
      <ArchivePanel source="evolution" enabled={enabled} />

      <div className="text-meta text-[var(--color-text-faint)]">
        How it works: each correction is an entry written by the review after a session. A recurring
        one becomes a governance candidate; a candidate with specific rule text, checked against the
        existing rules and backed by 3× evidence, is proposed for your approval (Needs you → Governance).
        EVOLUTION.md is injected whole; nothing is moved out by size. Older archive shards stay readable here.
      </div>
    </div>
  );
}

// Trend line chart from the daily size-snapshot series. R30: NEVER fabricates a
// baseline — <2 real points shows an explicit "collecting since launch" state.
function TrendChart({ trend, field }: { trend: BrainTrend | undefined; field: 'memory_bytes' | 'prompt_tokens' }) {
  const pts = trend?.points ?? [];
  if (pts.length < 2) {
    const since = trend?.launch_date;
    return (
      <div data-testid="cm-trend-collecting" className="rounded-lg border border-dashed border-[var(--color-border)] p-4 text-center text-meta text-[var(--color-text-faint)]">
        Collecting since {since ?? 'launch'} — the trend appears after 2 daily snapshots.
      </div>
    );
  }
  const vals = pts.map((p) => p[field]);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const W = 300, H = 60;
  const path = pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * W;
    const y = H - ((p[field] - min) / range) * H;
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg data-testid="cm-trend-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-14">
      <path d={path} fill="none" stroke="var(--color-status-success)" strokeWidth="1.5" />
    </svg>
  );
}

// ── Guideline tab: static teaching content — "how a powerful agent brain works".
// R30: describes MECHANISMS, not counts — NO baked numbers (they'd drift). Every line
// must stay true of the running system: re-check it when a mechanism changes.
const LIFECYCLE: Array<{ key: string; icon: string; title: string; desc: string }> = [
  { key: 'assemble', icon: 'move_to_inbox', title: 'Assemble', desc: 'every context file injected whole, by priority' },
  { key: 'recall', icon: 'search', title: 'Recall', desc: 'each message pulls related project, Library and past-session knowledge' },
  { key: 'judge', icon: 'psychology', title: 'Judge', desc: 'the model reasons on that context' },
  { key: 'sediment', icon: 'water_drop', title: 'Sediment', desc: 'after a session, its agent declares what it learned; the code checks the evidence' },
  { key: 'revise', icon: 'published_with_changes', title: 'Revise', desc: 'a newer decision overrides; a withdrawn entry leaves' },
];
// What runs by itself. One list (it used to be told twice: here and in an "under the
// hood" list). Nothing decays or is cut by size: an entry leaves only by an override or
// a withdrawal (design 2026-10-30-brain-driven §2.3).
const AUTO_ITEMS: Array<{ icon: string; name: string; desc: string; tag: string }> = [
  { icon: 'search', name: 'Recall', desc: 'each message: the project brain, Library, past sessions, code graph', tag: 'hook' },
  { icon: 'water_drop', name: 'After-session review', desc: 'writes what a session learned to MEMORY, EVOLUTION, a project brain, Library or Timeline', tag: 'job' },
  { icon: 'published_with_changes', name: 'Override & withdraw', desc: 'a declared newer decision replaces an entry; the record stays outside the file', tag: 'job' },
  { icon: 'event_note', name: 'Session record', desc: 'what each session did and where it stopped, read by the next one', tag: 'hook' },
  { icon: 'genetics', name: 'Rule proposals', desc: 'a recurring correction with 3× evidence becomes a rule for you to approve', tag: 'job' },
  { icon: 'monitoring', name: 'North star', desc: 'measures whether I am getting better (see the left rail)', tag: 'job' },
];
const MANUAL_ITEMS: Array<{ icon: string; name: string; desc: string; tag: string }> = [
  { icon: 'explore', name: 'STEERING rules', desc: 'your standing directives (highest precedence)', tag: 'file' },
  { icon: 'person', name: 'USER profile', desc: 'who you are, how you like to work', tag: 'file' },
  { icon: 'extension', name: 'Skills & MCP servers', desc: 'which capabilities this agent may use (Capabilities)', tag: 'config' },
  { icon: 'folder_copy', name: 'Create a project brain', desc: 'a domain brain per project (Brain Hub)', tag: 'chat' },
  { icon: 'bookmark_add', name: 'Remember this', desc: 'ask me to save a lesson or decision now', tag: 'chat' },
  { icon: 'gavel', name: 'Approve a rule', desc: 'accept or reject a proposal (Needs you → Governance)', tag: 'click' },
];

function GuidelineTab() {
  return (
    <div data-testid="cm-panel-guideline" className="flex flex-col gap-5 max-w-4xl">
      <div className="text-body text-[var(--color-text-muted)]">
        How a powerful agent brain works — the lifecycle every message flows through, and what
        runs itself vs what you steer.
      </div>

      {/* Lifecycle flow */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          Lifecycle — every message flows through this
        </div>
        <div data-testid="cm-guideline-lifecycle" className="flex items-stretch gap-2">
          {LIFECYCLE.map((s, i) => (
            <div key={s.key} className="flex items-center gap-2 flex-1 min-w-0">
              <div
                data-testid={`cm-lc-${s.key}`}
                className="flex-1 min-w-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5 text-center"
              >
                <span className="material-symbols-outlined icon-lg text-[var(--color-text-muted)]">{s.icon}</span>
                <div className="mt-1 text-small font-semibold text-[var(--color-text)]">{s.title}</div>
                <div className="mt-0.5 text-meta leading-tight text-[var(--color-text-muted)]">{s.desc}</div>
              </div>
              {i < LIFECYCLE.length - 1 && <span className="shrink-0 text-[var(--color-text-faint)]">→</span>}
            </div>
          ))}
        </div>
      </section>

      {/* Automatic vs Manual */}
      <section>
        <div className="mb-2 text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
          Automatic vs Manual — what runs itself, what you steer
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <AmColumn testid="cm-guideline-automatic" head="Runs itself" badge="AUTOMATIC" items={AUTO_ITEMS} accent="var(--color-status-success)" />
          <AmColumn testid="cm-guideline-manual" head="You configure" badge="MANUAL" items={MANUAL_ITEMS} accent="var(--color-primary)" />
        </div>
      </section>

    </div>
  );
}

function AmColumn({
  testid, head, badge, items, accent,
}: { testid: string; head: string; badge: string; items: Array<{ icon: string; name: string; desc: string; tag: string }>; accent: string }) {
  return (
    <div data-testid={testid} className="rounded-lg border border-[var(--color-border)] p-3">
      <div className="mb-2 flex items-center gap-2 text-body font-semibold text-[var(--color-text)]">
        {head}
        <span className="rounded-full px-2 py-[1px] tabular-nums text-meta tracking-wider" style={{ background: `color-mix(in srgb, ${accent} 16%, transparent)`, color: accent }}>{badge}</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {items.map((it) => (
          <div key={it.name} className="flex items-center gap-2">
            <span className="material-symbols-outlined icon-sm shrink-0 text-[var(--color-text-muted)]">{it.icon}</span>
            <div className="min-w-0 flex-1">
              <div className="text-small text-[var(--color-text)]">{it.name}</div>
              <div className="truncate text-meta text-[var(--color-text-muted)]">{it.desc}</div>
            </div>
            <span className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-[1px] tabular-nums text-meta text-[var(--color-text-faint)]">{it.tag}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NeedsBtn({ testid, label, count, tint, active, onClick }: { testid: string; label: string; count: number; tint: string; active?: boolean; onClick?: () => void }) {
  return (
    <button
      data-testid={testid}
      data-active={active ? 'true' : 'false'}
      onClick={onClick}
      className={
        'flex w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-body text-left transition-colors ' +
        (active
          ? 'border-[color-mix(in_srgb,var(--color-status-success)_50%,var(--color-border))] bg-[var(--color-hover)]'
          : 'border-[var(--color-border)] hover:bg-[var(--color-hover)]')
      }
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: tint }} aria-hidden />
      <span className="flex-1 text-[var(--color-text-muted)]">{label}</span>
      <span className="tabular-nums font-semibold text-[var(--color-text)]">{count}</span>
    </button>
  );
}
