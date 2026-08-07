/**
 * BrainHub — the real (product) DDD Brain Hub Phase-1 lens, Run 1.
 *
 * Replaces the static demo iframe. A read-only visualization over the live DDD
 * state exposed by GET /api/ddd/brains[/{name}] (which projects ddd_paths +
 * parse_entries + git — no new source of truth, no stored metric). Two internal
 * tab views (the seam for Run2 Review / Run3 Distribute tabs — no router needed):
 *
 *   ① Gallery — one card per DDD brain: six-section presence bar + lifecycle
 *      progress + 4 live health signals (Sinking / Pending / Uncommitted /
 *      Last-change). NO recall-heat/crown number (ref_count is dead — R30#4).
 *   ② Brain   — six-section tree (grouped ①..⑥, each file with curator label +
 *      git-status dot), 7-type chips + decay coloring from real parse_entries,
 *      an empty ③Gates explicitly marked "complete, not broken" (R31). Clicking
 *      a file opens it in the app-level CANVAS (close-overlay → swarm:open-file,
 *      the SwarmWS-explorer precedent), NOT an in-hub modal. ② knowledge members
 *      carry live mtime + entryCount (run_a607f2b0, Approach A).
 *
 * Reuses: the app Canvas via swarm:open-file. No new tree/editor/modal built.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getBrainsWithPinned, getBrainDetail, getReview, approveReview, rejectReviewHunk,
  approveProposal, rejectProposal, getDistribution, aggregateTypeCounts,
} from '../../services/ddd';
import type {
  BrainSummary, BrainDetail, BrainSection, KnowledgeEntry, EntryType, DecayState, SectionKey,
  ReviewData, ReviewHunk, PendingProposal, DistributionState,
} from '../../services/ddd';
import { DddCard } from './DddCard';
import { CodeGraph } from '../code-intel/CodeGraph';
import { getCodeIntelSummary, type CodeIntelSummary } from '../../services/codeIntel';

// ── Visual constants ──────────────────────────────────────────────────────────

const SECTION_NUM: Record<string, string> = {
  identity: '①', knowledge: '②', gates: '③',
  capabilities: '④', delivery: '⑤', refresher: '⑥',
};

const DECAY_STYLE: Record<DecayState, string> = {
  active: 'text-[var(--color-text)]',
  dormant: 'text-[var(--color-text-muted)] opacity-70',
  archived: 'text-[var(--color-text-faint)] line-through opacity-50',
};

const TYPE_COLOR: Record<EntryType, string> = {
  guideline: '#3b82f6', pitfall: '#ef4444', decision: '#a855f7',
  model: '#14b8a6', process: '#f59e0b', principle: '#eab308',
  correction: '#ec4899',
};

// Project-relative dir where spec-details/*.spec.md live (mirrors the backend
// SPEC_DETAILS_DIR constant); used to build the file-preview open path.
const SPEC_DETAILS_REL = 'spec-details';

// ── Asset nav keys ──────────────────────────────────────────────────────────
// Specs + Code-Intelligence are ASSET PROJECTIONS, NOT the six canonical DDD
// sections (R31: the SectionKey union + backend _SECTIONS stay untouched). They
// live in the left nav under a divided "Assets" group, selected exactly like a
// section, but keyed on a SEPARATE channel so they can NEVER collide with a real
// SectionKey (the `asset:` prefix guarantees disjointness) and so widening never
// breaks the section-derived currentKey fall-through (Gate-1 F4).
type AssetKey = 'asset:specs' | 'asset:codeintel';
const isAssetKey = (k: string | null): k is AssetKey =>
  k === 'asset:specs' || k === 'asset:codeintel';

const GIT_DOT: Record<string, string> = {
  clean: 'transparent', modified: '#f0a500', added: '#3fb950',
  untracked: 'var(--color-text-muted)', deleted: '#ef4444', renamed: '#a855f7', conflicting: '#ef4444',
};

/** hunkSummary — derive a plain-language what/where from a single-hunk `diff_text`
 *  (Run 2, run_32cd6a60). PURE + exported so prod render + tests share one source
 *  (GUI30 extract-intent-to-pure-helper). Frontend-only: the backend `ReviewHunk`
 *  carries only {file,signature,tag,diff_text}, so we parse the diff text.
 *
 *  - adds/dels: body lines starting with `+`/`-`, EXCLUDING the `+++`/`---` file
 *    headers (mirrors backend `_hunk_signature`, ddd_brain.py:924) — a naive count
 *    would miscount the two file-header lines.
 *  - startLine: the NEW-side start `C` from `@@ -A(,B)? +C(,D)? @@`. The count part
 *    is OPTIONAL — git emits `@@ -1 +1 @@` (no comma) for single-line changes; a
 *    regex assuming `+C,D` would NaN on that (Gate-1 caught this against the fixture).
 *  - section: the trailing @@ heading, ONLY when non-empty. For markdown DDD docs git
 *    has no funcname driver, so this is usually empty → undefined (never '' / garbage). */
export function hunkSummary(diffText: string): {
  adds: number; dels: number; startLine: number | null; section?: string;
} {
  let adds = 0, dels = 0, startLine: number | null = null, section: string | undefined;
  for (const ln of diffText.split('\n')) {
    if (ln.startsWith('@@')) {
      // @@ -A(,B)? +C(,D)? @@ optional-heading
      const m = ln.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      if (m) {
        startLine = parseInt(m[1], 10);
        const heading = m[2].trim();
        if (heading) section = heading;
      }
      continue;
    }
    if (ln.startsWith('+++') || ln.startsWith('---')) continue;  // file headers, not changes
    if (ln.startsWith('+')) adds += 1;
    else if (ln.startsWith('-')) dels += 1;
  }
  return { adds, dels, startLine, section };
}

// ── Root ───────────────────────────────────────────────────────────────────────

type Tab = 'gallery' | 'brain' | 'review' | 'distribute';

/** `onRequestClose` — the host overlay's `ctx.close` (overlaySurfaces passes it).
 *  Approach A (run_a607f2b0): opening a DDD doc closes THIS overlay first, then
 *  dispatches `swarm:open-file` so the Canvas/FileViewer isn't rendered UNDER the
 *  host (the SwarmWS-explorer z-index precedent). OPTIONAL: tests / a non-overlay
 *  mount omit it — file-open then just dispatches without a close (still correct). */
export function BrainHub({ onRequestClose }: { onRequestClose?: () => void } = {}) {
  const [tab, setTab] = useState<Tab>('gallery');
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [pinned, setPinned] = useState<string[]>([]);
  // The pinned primary (SwarmAI) renders as a FULL card in the gallery top row →
  // one lazy detail fetch (mirrors the Welcome hero pattern; the only detail the
  // gallery pays for — the rest stay cheap compact cards).
  const [primaryDetail, setPrimaryDetail] = useState<BrainDetail | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0); // B10: retry trigger for getBrains
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    setPrimaryDetail(undefined);  // reset so a retry never shows the prior primary's detail (stale-render)
    getBrainsWithPinned().then(
      ({ brains: b, pinned: p }) => {
        if (!alive) return;
        setBrains(b);
        setPinned(p);
        // lazily fetch the primary (first pinned) as a full card
        const primary = p[0];
        if (primary) {
          getBrainDetail(primary).then(
            (d) => alive && setPrimaryDetail(d),
            () => alive && setPrimaryDetail(undefined),  // degrade to cheap card
          );
        }
      },
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [reloadTick]);

  const openBrain = useCallback((name: string) => {
    setSelected(name);
    setTab('brain');
  }, []);

  // Deep-link: `swarm:show-brain-hub` MAY carry `detail.brain` to open a specific
  // brain's detail view directly (Brain Home calm-card click). No detail → Gallery,
  // the default. The overlay is already open by the time this fires (OverlayContext
  // handles the same event to mount us), so we only need to route the sub-view.
  useEffect(() => {
    const onShow = (e: Event) => {
      const name = (e as CustomEvent<{ brain?: string }>).detail?.brain;
      if (name) openBrain(name);
    };
    window.addEventListener('swarm:show-brain-hub', onShow);
    return () => window.removeEventListener('swarm:show-brain-hub', onShow);
  }, [openBrain]);

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
      <div className="flex items-center gap-1 px-3 h-9 border-b border-[var(--color-border)] flex-shrink-0 text-[12px]">
        <TabBtn active={tab === 'gallery'} onClick={() => setTab('gallery')} testid="brainhub-tab-gallery">Gallery</TabBtn>
        <TabBtn active={tab === 'brain'} onClick={() => setTab('brain')} disabled={!selected} testid="brainhub-tab-brain">
          Brain{selected ? ` · ${selected}` : ''}
        </TabBtn>
        <TabBtn active={tab === 'review'} onClick={() => setTab('review')} disabled={!selected} testid="brainhub-tab-review">
          Review
        </TabBtn>
        <TabBtn active={tab === 'distribute'} onClick={() => setTab('distribute')} disabled={!selected} testid="brainhub-tab-distribute">
          Distribute
        </TabBtn>
      </div>

      <div className="flex-1 overflow-auto">
        {error && (
          <div className="p-4 text-[13px]" data-testid="brainhub-error">
            <div className="text-[var(--color-error,#ef4444)]">Failed to load brains: {error}</div>
            <button
              data-testid="brainhub-retry"
              onClick={() => setReloadTick((t) => t + 1)}
              className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
              style={{ background: 'var(--color-error,#ef4444)' }}
            >
              Retry
            </button>
          </div>
        )}
        {!error && tab === 'gallery' && <Gallery brains={brains} pinned={pinned} primaryDetail={primaryDetail} onOpen={openBrain} />}
        {/* key={selected} ties BrainView's identity to the brain — DEFENSIVE (Gate-2
            MED, verified NOT-currently-reachable): today every `selected` change is a
            gallery-card click, and the gallery tab only renders when tab==='gallery',
            so a brain switch is always brain→gallery→brain and THIS conditional already
            unmounts BrainView on the gallery step (fresh mount on return). The key
            guards a FUTURE in-place brain switch (e.g. a "jump to brain" affordance in
            the brain view) from surviving a stale activeKey='asset:codeintel' and
            transiently firing CodeIntelPanel's O(n) fetch for the new brain. Cheap +
            intent-clear; no test asserts it because the transient isn't reachable yet. */}
        {!error && tab === 'brain' && selected && <BrainView key={selected} name={selected} onRequestClose={onRequestClose} />}
        {!error && tab === 'review' && selected && <ReviewView name={selected} onRequestClose={onRequestClose} />}
        {!error && tab === 'distribute' && selected && <DistributeView name={selected} onRequestClose={onRequestClose} />}
      </div>
    </div>
  );
}

function TabBtn({ active, disabled, onClick, testid, children }: {
  active: boolean; disabled?: boolean; onClick: () => void; testid: string; children: React.ReactNode;
}) {
  return (
    <button
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
      className={`px-3 py-1 rounded-md transition-colors ${
        active ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
      } ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
    >
      {children}
    </button>
  );
}

// ── Gallery ──────────────────────────────────────────────────────────────────

/** A compact card straight from a BrainSummary (cheap — includes the 3-layer bar
 *  via summary.typeCounts, no detail fetch). */
function CompactBrain({ b, onOpen }: { b: BrainSummary; onOpen: (n: string) => void }) {
  return (
    <DddCard density="compact" name={b.name} kind={b.kind}
      lifecycleStage={b.lifecycleStage}
      health={b.health} typeCounts={b.typeCounts} onOpen={onOpen} />
  );
}

/**
 * Bento gallery: pinned top row = the primary (SwarmAI) as a FULL ontology card on
 * the left + up to 2 pinned brains as small cards stacked on the right; then the
 * REST of the brains 3-per-row. Pinned order + primary detail come from the parent
 * (backend-driven, existence-guarded). Degrades: if primaryDetail hasn't loaded,
 * the big card shows its cheap summary + fills in (DddCard full-body guards on
 * metrics). If pinned is empty (old daemon), falls back to a flat compact grid.
 */
function Gallery(
  { brains, pinned, primaryDetail, onOpen }:
  { brains: BrainSummary[] | null; pinned: string[]; primaryDetail?: BrainDetail; onOpen: (n: string) => void },
) {
  if (brains === null) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading brains…</div>;
  if (brains.length === 0) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">No DDD brains found.</div>;

  const byName = new Map(brains.map((b) => [b.name, b]));
  const primaryName = pinned[0];
  const primary = primaryName ? byName.get(primaryName) : undefined;
  const rightPins = pinned.slice(1).map((n) => byName.get(n)).filter((b): b is BrainSummary => !!b);
  const pinnedSet = new Set([primaryName, ...rightPins.map((b) => b.name)].filter(Boolean));
  const rest = brains.filter((b) => !pinnedSet.has(b.name));

  // Memoize the O(entries) type aggregation so it doesn't re-scan ~1000 entries on
  // every unrelated re-render (hover/selection) — recompute only when the detail
  // (or the primary summary fallback) changes. Computed before any early return
  // (hooks rule). Falls back to the summary's cheap typeCounts until detail loads.
  const primaryTypeCounts = useMemo(
    () => (primaryDetail ? aggregateTypeCounts(primaryDetail.sections) : primary?.typeCounts),
    [primaryDetail, primary?.typeCounts],
  );

  // Fallback: no pinned resolved → still verdict-first two zones (old flat grid
  // was the data-dump). Partition ALL brains by pending.
  if (!primary) {
    return (
      <div className="p-4 flex flex-col gap-3" data-testid="brainhub-gallery">
        <ZonedGrid brains={brains} onOpen={onOpen} />
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-3" data-testid="brainhub-gallery">
      {/* top row: primary full card (left) + 2 pinned small stacked (right).
          The primary hero is verdict-first — NO presence/lifecycle/cheap widgets
          (its FullBody ontology+needs-you+facts IS the signal); metrics is the
          only detail-derived prop (lazy). */}
      <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px' }} data-testid="brainhub-pinned-row">
        <DddCard density="full" name={primary.name} kind={primary.kind}
          metrics={primaryDetail?.health}
          health={primary.health}
          typeCounts={primaryTypeCounts}
          onOpen={onOpen} />
        {rightPins.length > 0 && (
          <div className="flex flex-col gap-3">
            {rightPins.map((b) => <CompactBrain key={b.name} b={b} onOpen={onOpen} />)}
          </div>
        )}
      </div>
      {/* rest: verdict-first two zones (needs-you above calm) */}
      {rest.length > 0 && <ZonedGrid brains={rest} onOpen={onOpen} />}
    </div>
  );
}

/**
 * Verdict-first partition of a brain list into a NEEDS-YOU zone (health.pending>0,
 * amber, above) and a CALM zone (pending==0, muted, below). The whole answer to
 * "which brains need me?" is the zone split — pending is the ONLY gate (sinking/
 * uncommitted are facts on the card, never zone gates: gating on them would pull
 * most brains up). Pure O(N) filter on the already-loaded cheap summaries — ZERO
 * getBrainDetail. A zone with no members is omitted (no empty-zone noise).
 */
function ZonedGrid({ brains, onOpen }: { brains: BrainSummary[]; onOpen: (n: string) => void }) {
  const needs = brains.filter((b) => b.health.pending > 0);
  const calm = brains.filter((b) => b.health.pending === 0);
  return (
    <>
      {needs.length > 0 && (
        <div data-testid="brainhub-needs-zone">
          <div className="text-[10px] uppercase tracking-wider font-semibold text-[#f0a500] mb-2">
            ▲ Needs you · {needs.length} of {brains.length}
          </div>
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
            {needs.map((b) => <CompactBrain key={b.name} b={b} onOpen={onOpen} />)}
          </div>
        </div>
      )}
      {calm.length > 0 && (
        <div data-testid="brainhub-calm-zone" className={needs.length > 0 ? 'mt-3' : ''}>
          <div className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-faint)] mb-2">
            Calm · nothing queued
          </div>
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
            {calm.map((b) => <CompactBrain key={b.name} b={b} onOpen={onOpen} />)}
          </div>
        </div>
      )}
    </>
  );
}


// ── Brain view ─────────────────────────────────────────────────────────────────

function BrainView({ name, onRequestClose }: { name: string; onRequestClose?: () => void }) {
  const [detail, setDetail] = useState<BrainDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // #8 2-pane: which nav item is shown in the right content pane — a section
  // OR an asset projection (Specs / Code Intelligence). Asset keys ride a
  // separate channel from SectionKey (see AssetKey) so they never collide.
  const [activeKey, setActiveKey] = useState<SectionKey | AssetKey | null>(null);
  // #10: mount the existing full-screen CodeGraph overlay on demand.
  const [showGraph, setShowGraph] = useState(false);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    setActiveKey(null);   // reset selection when switching brains
    setShowGraph(false);
    getBrainDetail(name).then(
      (d) => alive && setDetail(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  // ESC-routing guard (Gate-2 meta-review MED): the Brain Hub lives inside a shared
  // Modal that installs a document-level ESC→onClose listener; the full-screen
  // CodeGraph overlay (shared with BottomBar, not ours to edit) has NO ESC handler.
  // Without this, pressing ESC with the graph open would close the ENTIRE Brain Hub
  // instead of the graph. We intercept ESC in the CAPTURE phase while the graph is
  // open, close only the graph, and stop it reaching the Modal's handler.
  useEffect(() => {
    if (!showGraph) return;
    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setShowGraph(false);
      }
    };
    document.addEventListener('keydown', onKeyDownCapture, true);  // capture: runs before Modal's bubble listener
    return () => document.removeEventListener('keydown', onKeyDownCapture, true);
  }, [showGraph]);

  const openFile = useCallback((sectionMemberPath: string, gitStatus?: string) => {
    // Approach A (run_a607f2b0): open a DDD doc in the app-level CANVAS, not an
    // in-hub modal — the SwarmWS-explorer precedent. useCanvasHost's swarm:open-file
    // resolver takes `path` against the cached SwarmWS root, so we pass a
    // WORKSPACE-RELATIVE path (Projects/<name>/<member>); a bare/absolute path 404s.
    // Z-index (Gate-1, swarmws precedent): close THIS overlay BEFORE the dispatch so
    // the Canvas/FileViewer is never rendered under the host.
    const workspaceRelPath = `Projects/${name}/${sectionMemberPath}`;
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', {
      detail: { path: workspaceRelPath, gitStatus },
    }));
  }, [name, onRequestClose]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]">Failed to load brain: {error}</div>;
  if (!detail) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading {name}…</div>;

  // Gate-1 CRITICAL: nav AND content both derive from the RUNTIME detail.sections
  // (never a hardcoded SECTION_ORDER) — so a backend that drops/reorders a section
  // can't strand the nav against a missing card. Default active = the first section
  // the backend returned; the `.find()` is guarded (active may be undefined).
  // Gate-1 F4: currentKey MUST accept an asset key too — else selecting Specs/CodeIntel
  // (not in sections) would fall through to sections[0] and the section-0 card would
  // render instead of the asset panel.
  const sections = detail.sections;
  const specs = detail.specs ?? [];
  const hasCodeIntel = detail.hasCodeIntel === true;   // daemon-skew: undefined → false
  const hasSpecs = specs.length > 0;
  const hasAssets = hasSpecs || hasCodeIntel;
  const currentKey: SectionKey | AssetKey | null =
    activeKey && (isAssetKey(activeKey) || sections.some((s) => s.key === activeKey))
      ? activeKey
      : (sections[0]?.key ?? null);
  const active = isAssetKey(currentKey)
    ? null
    : (sections.find((s) => s.key === currentKey) ?? null);

  return (
    <div className="flex flex-col h-full" data-testid="brainhub-brain">
      <div className="flex items-center gap-2 px-4 pt-4 pb-3 flex-shrink-0">
        <span className="text-[14px] font-semibold">{detail.name}</span>
        <span className="text-[10px] font-mono text-[var(--color-text-faint)] px-1.5 py-0.5 rounded bg-[var(--color-card)]">{detail.kind}</span>
        {/* #10 — open the EXISTING CodeGraph overlay for THIS brain (project={name},
            never a hardcoded literal). Gated on hasCodeIntel (a live code_intel.db
            presence check), NOT on kind: every DDD resolves to kind='knowledge'
            (aim.json carries brain_kind, never kind), so a kind gate NEVER fires —
            SwarmAI + IVTHub have a real graph but the button was unreachable. */}
        {hasCodeIntel && (
          <button
            onClick={() => setShowGraph(true)}
            data-testid="open-codegraph"
            className="ml-auto flex items-center gap-1 text-[11px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-0.5 hover:bg-[#12233a]"
            title="Open the code intelligence graph for this brain"
          >
            <span className="material-symbols-outlined text-[14px]">hub</span>
            View code graph
          </button>
        )}
      </div>

      {/* Detail-view health metrics (design 2026-08-04) — unified DddCard in full
          density, metrics-only (BrainView keeps its own header+nav above/below).
          Renders nothing on an old daemon that omits health OR noise (density-scoped
          daemon-skew guard inside DddCard/MetricTiles, O023). */}
      {detail.health?.noise && (
        <div className="px-4 pb-3 flex-shrink-0" data-testid="brainhub-healthstrip">
          <DddCard density="full" name={detail.name} kind={detail.kind} metrics={detail.health}
            typeCounts={aggregateTypeCounts(detail.sections)} />
        </div>
      )}

      {/* #8 — 2-pane: left section-nav + right content pane (one section at a time) */}
      <div className="flex-1 flex min-h-0">
        <nav
          data-testid="brainhub-brain-nav"
          className="w-44 flex-shrink-0 border-r border-[var(--color-border)] overflow-y-auto py-2"
        >
          {sections.map((s) => {
            const isActive = s.key === currentKey;
            return (
              <button
                key={s.key}
                data-testid={`nav-item-${s.key}`}
                onClick={() => setActiveKey(s.key)}
                className={`w-full flex items-center gap-2 text-left px-3 py-1.5 text-[12px] transition-colors ${
                  isActive ? 'bg-[var(--color-hover)] text-[var(--color-text)] border-l-2 border-[#f0a500]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-l-2 border-transparent'
                }`}
              >
                <span className="font-mono text-[#f0a500]">{SECTION_NUM[s.key] ?? s.num}</span>
                <span className="truncate">{s.label}</span>
                {s.members.length > 0 && (
                  <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">{s.members.length}</span>
                )}
              </button>
            );
          })}

          {/* Assets group — Specs + Code Intelligence are ASSET PROJECTIONS, not
              the six canonical sections (R31). Rendered as a visually-divided
              sub-group BELOW the sections; each is a nav item selected exactly like
              a section (right pane swaps). AC4: the whole group is hidden when the
              brain has neither — no empty-group noise. */}
          {hasAssets && (
            <>
              <div
                data-testid="assets-group-header"
                className="mt-2 pt-2 px-3 border-t border-[var(--color-border)] text-[9px] uppercase tracking-wider text-[var(--color-text-faint)] font-semibold"
              >
                Assets
              </div>
              {hasSpecs && (
                <AssetNavItem
                  assetKey="asset:specs"
                  icon="description"
                  label="Specs"
                  count={specs.length}
                  active={currentKey === 'asset:specs'}
                  onSelect={setActiveKey}
                />
              )}
              {hasCodeIntel && (
                <AssetNavItem
                  assetKey="asset:codeintel"
                  icon="hub"
                  label="Code Intelligence"
                  active={currentKey === 'asset:codeintel'}
                  onSelect={setActiveKey}
                />
              )}
            </>
          )}
        </nav>

        {/* Gate-1 F4: EXACTLY ONE of {asset panel, section card, empty} renders —
            an if/else short-circuit, never asset-panel ALONGSIDE a section card. */}
        <div data-testid="brainhub-brain-content" className="flex-1 overflow-y-auto p-4">
          {currentKey === 'asset:specs' ? (
            // key={name} → clean remount on brain switch (avoid stale-across-brains).
            <SpecsPanel key={`specs-${name}`} specs={specs} onOpenFile={openFile} />
          ) : currentKey === 'asset:codeintel' ? (
            <CodeIntelPanel key={`ci-${name}`} project={name} />
          ) : active ? (
            <SectionCard key={active.key} section={active} onOpenFile={openFile} />
          ) : (
            <div className="text-[12px] text-[var(--color-text-faint)] italic">No sections to display.</div>
          )}
        </div>
      </div>

      {/* #10 — sibling-mount the existing full-screen CodeGraph overlay (BottomBar
          pattern). Rendered as a sibling inside the host overlay; it owns its own
          ESC handling via the capture-phase guard above. project={name} — NEVER a
          hardcoded literal (Gate-1 flag). */}
      {showGraph && (
        <CodeGraph project={name} onClose={() => setShowGraph(false)} />
      )}
    </div>
  );
}

// One left-nav item for an asset projection (Specs / Code Intelligence). Mirrors
// the section nav button's shape so the two groups read as one consistent list.
function AssetNavItem({
  assetKey, icon, label, count, active, onSelect,
}: {
  assetKey: AssetKey; icon: string; label: string; count?: number;
  active: boolean; onSelect: (k: AssetKey) => void;
}) {
  return (
    <button
      data-testid={`nav-item-${assetKey}`}
      onClick={() => onSelect(assetKey)}
      className={`w-full flex items-center gap-2 text-left px-3 py-1.5 text-[12px] transition-colors ${
        active ? 'bg-[var(--color-hover)] text-[var(--color-text)] border-l-2 border-[#58a6ff]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-l-2 border-transparent'
      }`}
    >
      <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">{icon}</span>
      <span className="truncate">{label}</span>
      {count != null && count > 0 && (
        <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">{count}</span>
      )}
    </button>
  );
}

// Specs panel — spec-details/*.spec.md filenames (AC1/AC2). Now rendered in the
// right content pane when the "Specs" nav item is selected (no self-toggle — the
// nav selection IS the show/hide). Clicking a spec opens it in the Canvas via the
// same onOpenFile path the section members use (close-overlay → swarm:open-file).
function SpecsPanel({ specs, onOpenFile }: { specs: string[]; onOpenFile: (p: string) => void }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-2.5" data-testid="specs-panel">
      <div className="flex items-center gap-2 mb-1.5 text-[12px] font-semibold">
        <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">description</span>
        <span>Specs</span>
        <span className="text-[9px] text-[var(--color-text-faint)] font-normal">{specs.length}</span>
      </div>
      <div className="flex flex-col gap-0.5">
        {specs.map((f) => (
          <button
            key={f}
            onClick={() => onOpenFile(`${SPEC_DETAILS_REL}/${f}`)}
            data-testid={`spec-${f}`}
            className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-left px-1 py-0.5 rounded hover:bg-[var(--color-hover)]"
          >
            <span className="font-mono truncate">{f}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// Code-intel panel — reuses the EXISTING GET /api/code-intel/{project}/summary
// (getCodeIntelSummary). Still ON-DEMAND: this component only MOUNTS when its
// "Code Intelligence" nav item is selected (Gate-1 F5), so the fetch — which
// triggers find_dead_code (O(n) full scan) — never runs on brain load, only when
// the user opens the asset. Fetches on mount (not a redundant inner toggle click).
// null (no db / 404 — the service maps 404→null) or a 0-symbol/stale db renders a
// graceful line (a bare .exists() can surface an empty/foreign db — Gate-1 F6).
function CodeIntelPanel({ project }: { project: string }) {
  const [summary, setSummary] = useState<CodeIntelSummary | null>(null);
  const [state, setState] = useState<'loading' | 'loaded' | 'error'>('loading');

  useEffect(() => {
    let alive = true;
    setState('loading');
    getCodeIntelSummary(project).then(
      (s) => { if (alive) { setSummary(s); setState('loaded'); } },
      () => { if (alive) setState('error'); },
    );
    return () => { alive = false; };
  }, [project]);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-2.5" data-testid="code-intel-panel">
      <div className="flex items-center gap-2 mb-1.5 text-[12px] font-semibold">
        <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">hub</span>
        <span>Code Intelligence</span>
      </div>
      <div className="text-[11px] text-[var(--color-text-muted)]" data-testid="code-intel-body">
        {state === 'loading' && <div className="italic text-[var(--color-text-faint)]">Loading code map…</div>}
        {state === 'error' && <div className="italic text-[var(--color-text-faint)]">Code intel unavailable.</div>}
        {state === 'loaded' && summary === null && (
          <div className="italic text-[var(--color-text-faint)]">No code intelligence indexed for this brain.</div>
        )}
        {state === 'loaded' && summary && (
          <>
            <div className="mb-1">{summary.symbolCount} symbols indexed</div>
            <div className="flex flex-col gap-0.5">
              {summary.modulesTop5.map((mod) => (
                <div key={mod.name} className="flex items-center gap-2" data-testid={`ci-module-${mod.name}`}>
                  <span className="font-mono truncate">{mod.name}</span>
                  <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">
                    {mod.function_count}fn / {mod.class_count}cls / {mod.file_count}f
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SectionCard({ section, onOpenFile }: { section: BrainSection; onOpenFile: (p: string, gitStatus?: string) => void }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-2.5" data-testid={`section-${section.key}`}>
      <div className="flex items-center gap-2 mb-1.5 text-[12px]">
        <span className="text-[#f0a500] font-mono">{SECTION_NUM[section.key] ?? section.num}</span>
        <span className="font-semibold">{section.label}</span>
        <span className={`text-[9px] px-1 py-0.5 rounded font-mono ${
          section.ownGovern === 'OWN' ? 'bg-[#1f3a2e] text-[#3fb950]' : 'bg-[#3a2e1f] text-[#f0a500]'
        }`}>{section.ownGovern}</span>
        <span className="ml-auto text-[10px] text-[var(--color-text-faint)]">{section.curator}</span>
      </div>

      {section.members.length === 0 ? (
        <div className="text-[11px] text-[var(--color-text-faint)] italic" data-testid={`empty-${section.key}`}>
          {section.completeNotBroken ? 'empty — complete, not broken' : 'empty'}
        </div>
      ) : (
        <div className="flex flex-col gap-0.5">
          {section.members.map((m) => (
            <button
              key={m.path}
              onClick={() => onOpenFile(m.path, m.gitStatus)}
              data-testid={`member-${m.path}`}
              className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-left px-1 py-0.5 rounded hover:bg-[var(--color-hover)]"
            >
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: GIT_DOT[m.gitStatus] ?? 'transparent' }} title={m.gitStatus} />
              <span className="font-mono truncate">{m.path.split('/').pop()}</span>
              {/* ② knowledge members carry live mtime + entryCount (run_a607f2b0);
                  other sections omit them (undefined → nothing rendered, daemon-skew
                  safe). "Is this doc active, and how big" without opening it. */}
              {(m.mtime || m.entryCount != null) && (
                <span className="ml-auto flex items-center gap-1.5 text-[9px] text-[var(--color-text-faint)] flex-shrink-0">
                  {m.entryCount != null && <span data-testid={`member-entrycount-${m.path}`}>{m.entryCount} entries</span>}
                  {m.mtime && <span data-testid={`member-mtime-${m.path}`}>{m.mtime}</span>}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {section.entries.length > 0 && <KnowledgeEntries entries={section.entries} />}
    </div>
  );
}

function KnowledgeEntries({ entries }: { entries: KnowledgeEntry[] }) {
  // 7-type composition bar
  const counts: Record<string, number> = {};
  for (const e of entries) counts[e.entryType] = (counts[e.entryType] ?? 0) + 1;

  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)]">
      <div className="flex gap-0.5 mb-1.5 h-1.5 rounded-sm overflow-hidden" title="7-type composition">
        {/* F5: STABLE order — canonical TYPE_COLOR order first (deterministic across
            brains, vs Object.keys(counts) insertion order). Gate-2: also append any
            UNKNOWN type (not in TYPE_COLOR) with the fallback color so the bar stays
            exhaustive + consistent with the per-entry dot (:below), never silently
            dropping a segment. (Backend clamps to VALID_TYPES, so unknowns are rare,
            but the bar must not disagree with the entry list if one slips through.) */}
        {[
          ...(Object.keys(TYPE_COLOR) as EntryType[]).filter((t) => counts[t] > 0),
          ...Object.keys(counts).filter((t) => !(t in TYPE_COLOR) && counts[t] > 0),
        ].map((t) => (
          <span
            key={t}
            data-testid={`typebar-${t}`}
            style={{ background: TYPE_COLOR[t as EntryType] ?? 'var(--color-text-faint)', flex: counts[t] }}
          />
        ))}
      </div>
      <div className="text-[10px] text-[var(--color-text-faint)] mb-1">{entries.length} entries</div>
      {/* AC3: entries GROUPED BY 7-type (collapsible), not a flat list — structure
          is visible without scrolling 700 uniform titles. Group order = canonical
          TYPE_COLOR order + any unknown type appended (matches the composition bar).
          `entry-line` + `typebar-*` testids preserved (Gate-1 F5 regression guard). */}
      <div className="flex flex-col gap-1 max-h-64 overflow-auto">
        {[
          ...(Object.keys(TYPE_COLOR) as EntryType[]).filter((t) => counts[t] > 0),
          ...Object.keys(counts).filter((t) => !(t in TYPE_COLOR) && counts[t] > 0),
        ].map((t) => (
          <EntryGroup
            key={t}
            type={t}
            entries={entries.filter((e) => e.entryType === t)}
          />
        ))}
      </div>
    </div>
  );
}

// One collapsible type-group in the grouped Knowledge list (AC3). Collapsed by
// default so the 7 group headers ARE the structure the owner reads first; expand
// to see that type's entries. Caps rendered rows per group (a type can have
// hundreds) with a "+N more" line, mirroring the old flat-list cap.
function EntryGroup({ type, entries }: { type: string; entries: KnowledgeEntry[] }) {
  const [open, setOpen] = useState(false);
  const CAP = 40;
  return (
    <div data-testid={`entry-group-${type}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        data-testid={`entry-group-toggle-${type}`}
        className="w-full flex items-center gap-1.5 text-[10px] text-left px-1 py-0.5 rounded hover:bg-[var(--color-hover)]"
      >
        <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: TYPE_COLOR[type as EntryType] ?? 'var(--color-text-faint)' }} />
        <span className="font-mono text-[var(--color-text-muted)]">{type}</span>
        <span className="text-[9px] text-[var(--color-text-faint)]">{entries.length}</span>
        <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 pl-3">
          {entries.slice(0, CAP).map((e, i) => (
            <div key={`${e.file}-${i}`} className="flex items-center gap-1.5 text-[10px]" data-testid="entry-line">
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: TYPE_COLOR[e.entryType] ?? 'var(--color-text-faint)' }} title={e.entryType} />
              <span className={`truncate font-mono ${DECAY_STYLE[e.decayState] ?? DECAY_STYLE.active}`}>{e.title}</span>
            </div>
          ))}
          {entries.length > CAP && <div className="text-[10px] text-[var(--color-text-faint)] italic">+{entries.length - CAP} more…</div>}
        </div>
      )}
    </div>
  );
}

// ── Review view (Run 2) ──────────────────────────────────────────────────────

function shortSha(sha: string): string {
  return sha ? sha.slice(0, 8) : '—';
}

function ReviewView({ name, onRequestClose }: { name: string; onRequestClose?: () => void }) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-ACTION errors (reject/proposal 409s etc.) are TRANSIENT + inline — they must
  // NOT blank the whole queue via the top-level `if (error)` return (Gate-2: a routine
  // retryable 409 shouldn't wipe the review view). Separate channel from the load error.
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // H2: "Mark all seen" advances the watermark IRREVERSIBLY (wipes the review
  // queue from the diff). Require a two-click armed confirm so a single stray
  // click can't wipe it. `armed` MUST reset on any context change (reload after
  // any action, brain switch) so it never persists stale — reset in load() below.
  const [armed, setArmed] = useState(false);

  const load = useCallback(() => {
    let alive = true;
    setData(null);
    setError(null);
    setActionError(null);   // clear any transient action error on (re)load
    setArmed(false);   // disarm on every (re)load: brain switch, or after any action
    getReview(name).then(
      (d) => alive && setData(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  useEffect(() => load(), [load]);  // load() returns its own alive-cleanup, invoked on unmount/re-run

  const onApproveAll = useCallback(async () => {
    if (!armed) { setArmed(true); return; }   // first click arms; no API call yet
    setBusy(true);
    // ALWAYS disarm (finally) — Gate-2: if approveReview throws, armed must NOT stay
    // stuck true (a stale-armed button would let the next single click re-fire the
    // POST, bypassing the two-click guard). Surface the error instead of swallowing.
    try {
      await approveReview(name);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally {
      setArmed(false);
      setBusy(false);
    }
  }, [name, load, armed]);

  const onRejectHunk = useCallback(async (h: ReviewHunk) => {
    setBusy(true);
    // F3: surface API errors (parity with onApproveAll) via the TRANSIENT actionError
    // channel — a rejected git apply -R (409/404/500) must not fail silently, but it
    // also must NOT blank the whole queue (Gate-2: a routine retryable 409 is inline).
    try {
      await rejectReviewHunk(name, h.file, h.signature);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally { setBusy(false); }
  }, [name, load]);

  const onProposal = useCallback(async (p: PendingProposal, accept: boolean) => {
    setBusy(true);
    try {
      if (accept) await approveProposal(p.id, name);
      else await rejectProposal(p.id, name);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally { setBusy(false); }
  }, [name, load]);

  // Open a hunk's file in the Canvas (Run 2). ⚠️ Gate-1: hunk.file is ALREADY
  // WORKSPACE-relative (backend runs `git diff` at the workspace root with a
  // `Projects/<name>` pathspec → cur_file = "Projects/<name>/…", ddd_brain.py:162/964;
  // verified test fixture:150 + reject call:564). So dispatch it DIRECTLY — do NOT
  // re-wrap in `Projects/${name}/` (that would double-prefix → 404). This DIFFERS
  // from BrainView.openFile, whose SectionMember.path is bare project-relative and
  // DOES need the prefix. Same close→Canvas z-index precedent (close BEFORE dispatch).
  const openHunkFile = useCallback((workspaceRelFile: string, gitStatus?: string) => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', {
      detail: { path: workspaceRelFile, gitStatus },
    }));
  }, [onRequestClose]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]" data-testid="review-error">Failed to load review: {error}</div>;
  if (!data) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading review…</div>;

  // Zone A = auto-applied hunks; Zone C = pending risky proposals. (F1: the former
  // Zone B "decay·sinking" was removed — the backend never emitted that tag, so it
  // was a permanently-empty misleading zone; the Gallery's health.sinking count
  // already surfaces dormant/archived entries.)
  const zoneA = data.hunks.filter((h) => h.tag === 'cultivation·auto-applied');
  const riskyHunks = data.hunks.filter((h) => h.tag === 'risky·staged');

  return (
    <div className="p-4" data-testid="brainhub-review">
      {/* diff header */}
      <div className="flex items-center gap-2 mb-3 text-[11px] font-mono text-[var(--color-text-muted)]" data-testid="review-diff-header">
        <span className="material-symbols-outlined text-[15px] text-[#a855f7]">commit</span>
        diff <span className="text-[var(--color-text)]">Projects/{name}/</span>
        · last-reviewed <span className="text-[var(--color-text)]">{shortSha(data.last_reviewed_sha)}</span>
        → HEAD <span className="text-[var(--color-text)]">{shortSha(data.head_sha)}</span>
        <button
          onClick={onApproveAll}
          // F8: NEVER advance the watermark when the diff is incomplete (timed out) —
          // the empty/partial hunk list would silently mark unreviewed work as seen.
          disabled={busy || data.hunks.length === 0 || data.diff_incomplete}
          data-testid="review-approve-all"
          className={`ml-auto flex items-center gap-1 text-[11px] rounded-md px-2 py-0.5 disabled:opacity-40 ${
            armed
              ? 'text-[#f0a500] border border-[#5a4a1f] bg-[#241f10] hover:bg-[#2e2814]'
              : 'text-[#3fb950] border border-[#1f5a2a] hover:bg-[#132918]'
          }`}
          title={armed ? 'This advances the watermark and clears the review queue — click again to confirm' : undefined}
        >
          <span className="material-symbols-outlined text-[14px]">{armed ? 'warning' : 'visibility'}</span>
          {armed ? 'Click again to confirm — advances watermark' : 'Mark all seen → advance watermark'}
        </button>
      </div>

      {/* F8: loud degraded state — the diff timed out, so the queue below is INCOMPLETE.
          Gate-2: give an explicit Retry affordance (not just "retry shortly" text) so a
          large-repo timeout isn't a dead-end lockout of the review. */}
      {data.diff_incomplete && (
        <div className="flex items-center gap-1.5 mb-3 text-[11px] text-[#f0a500] bg-[#241f10] border border-[#5a4a1f] rounded-md px-2 py-1" data-testid="review-diff-incomplete">
          <span className="material-symbols-outlined text-[14px]">warning</span>
          The review diff timed out — this queue may be incomplete. "Mark all seen" is disabled to avoid skipping unreviewed changes.
          <button onClick={() => load()} disabled={busy} data-testid="review-diff-retry"
            className="ml-auto flex items-center gap-1 text-[10px] text-[#f0a500] border border-[#5a4a1f] rounded px-1.5 py-0.5 hover:bg-[#2e2814] disabled:opacity-40">
            <span className="material-symbols-outlined text-[12px]">refresh</span>Retry
          </button>
        </div>
      )}

      {/* F3: transient per-action error (reject/proposal 409 etc.) — inline, does NOT
          blank the whole queue (Gate-2). Dismissible; also cleared on next load(). */}
      {actionError && (
        <div className="flex items-center gap-1.5 mb-3 text-[11px] text-[#ff9a94] bg-[#2a1214] border border-[#5a1f1f] rounded-md px-2 py-1" data-testid="review-action-error">
          <span className="material-symbols-outlined text-[14px]">error</span>
          Action failed: {actionError}
          <button onClick={() => setActionError(null)} data-testid="review-action-error-dismiss"
            className="ml-auto text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1.5">dismiss</button>
        </div>
      )}

      {/* Zone A — auto-cultivated (already committed) */}
      <ReviewZone
        testid="review-zone-a" title="Auto-cultivated · already in brain"
        desc="applied + git-committed — NOT awaiting approval. Reject reverts that hunk."
        color="#58a6ff"
      >
        {zoneA.length === 0 ? <ZoneEmpty text="no auto-applied changes since last review" />
          : zoneA.map((h) => (
            <HunkCard key={h.signature} hunk={h} busy={busy}
              onReject={() => onRejectHunk(h)}
              onOpenFile={() => openHunkFile(h.file)} />
          ))}
      </ReviewZone>

      {/* Zone C — pending risky proposals (the ONLY true Approve/Reject gate) */}
      <ReviewZone
        testid="review-zone-c" title="Pending approval · NOT yet in brain"
        desc="risky proposals in .artifacts/proposals/ — the real gate."
        color="#f0a500"
      >
        {data.proposals.length === 0 && riskyHunks.length === 0
          ? <ZoneEmpty text="no proposals awaiting decision" />
          : data.proposals.map((p) => (
            <div key={p.id} className="rounded-md border border-dashed border-[#3a2e12] bg-[#1a1710] p-2 mb-1.5" data-testid="review-proposal">
              <div className="flex items-center gap-1.5 mb-1 text-[11px]">
                <span className="font-mono text-[#f0a500]">{p.target_doc}</span>
                <span className="text-[var(--color-text-faint)]">· {p.target_section}</span>
                {/* F4: confidence is the human-gate decision signal — render it
                    (null-guarded: an un-scored proposal shows "—", never "null"). */}
                <span className="text-[10px] text-[var(--color-text-muted)]" data-testid="proposal-confidence" title="cultivation confidence">
                  conf {p.confidence != null ? p.confidence.toFixed(2) : '—'}
                </span>
                <div className="ml-auto flex gap-1">
                  <button onClick={() => onProposal(p, true)} disabled={busy}
                    className="text-[10px] text-[#3fb950] border border-[#1f5a2a] rounded px-1.5 py-0.5 hover:bg-[#132918] disabled:opacity-40">Approve</button>
                  <button onClick={() => onProposal(p, false)} disabled={busy}
                    className="text-[10px] text-[#ef4444] border border-[#5a1f1f] rounded px-1.5 py-0.5 hover:bg-[#2a1214] disabled:opacity-40">Reject</button>
                </div>
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)] line-clamp-2">{p.content}</div>
            </div>
          ))}
      </ReviewZone>
    </div>
  );
}

function ReviewZone({ testid, title, desc, color, children }: {
  testid: string; title: string; desc: string; color: string; children: React.ReactNode;
}) {
  return (
    <div className="mb-4" data-testid={testid}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[12px] font-semibold" style={{ color }}>{title}</span>
        <span className="text-[10px] text-[var(--color-text-faint)]">{desc}</span>
      </div>
      {children}
    </div>
  );
}

function ZoneEmpty({ text }: { text: string }) {
  return <div className="text-[11px] text-[var(--color-text-faint)] italic px-1 py-1">{text}</div>;
}

function HunkCard({ hunk, busy, onReject, onOpenFile }: {
  hunk: ReviewHunk; busy: boolean; onReject: () => void; onOpenFile: () => void;
}) {
  // Run 2: raw @@ diff COLLAPSED by default — the plain-language summary is the
  // primary read; the diff is opt-in behind [View diff]. Keyed by signature so each
  // card toggles independently.
  const [showDiff, setShowDiff] = useState(false);
  const s = hunkSummary(hunk.diff_text);
  const fileName = hunk.file.split('/').pop() ?? hunk.file;
  const sig = hunk.signature;
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[#12161c] mb-1.5 overflow-hidden" data-testid="review-hunk">
      {/* Plain-language summary line (AC1) — always visible. Primary derivation is
          file + counted +/- + line-range (always reliable); section is best-effort
          (empty for .md — Gate-1), shown only when git gave a non-empty heading. */}
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[var(--color-border)]" data-testid={`hunk-summary-${sig}`}>
        <span className="font-mono text-[11px] text-[var(--color-text)]">{fileName}</span>
        <span className="text-[10px] font-mono">
          <span className="text-[#7ee787]">+{s.adds}</span>
          <span className="text-[var(--color-text-faint)]"> / </span>
          <span className="text-[#ff9a94]">-{s.dels}</span>
        </span>
        {s.startLine != null && (
          <span className="text-[9px] text-[var(--color-text-faint)]">around line {s.startLine}</span>
        )}
        {s.section && (
          <span className="text-[9px] text-[var(--color-text-faint)] truncate italic">· {s.section}</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setShowDiff((v) => !v)}
            data-testid={`hunk-toggle-diff-${sig}`}
            className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] border border-[var(--color-border)] rounded px-1.5 py-0.5 hover:bg-[var(--color-hover)]"
          >
            <span className="material-symbols-outlined text-[12px]">{showDiff ? 'expand_less' : 'code'}</span>
            {showDiff ? 'Hide diff' : 'View diff'}
          </button>
          <button
            onClick={onOpenFile}
            data-testid={`hunk-open-file-${sig}`}
            className="flex items-center gap-1 text-[10px] text-[#58a6ff] border border-[#1f3a5a] rounded px-1.5 py-0.5 hover:bg-[#12233a]"
            title="Open this file in the Canvas"
          >
            <span className="material-symbols-outlined text-[12px]">open_in_new</span>
            Open file
          </button>
          <button
            onClick={onReject}
            disabled={busy}
            data-testid="review-reject-hunk"
            className="flex items-center gap-1 text-[10px] text-[#ef4444] border border-[#5a1f1f] rounded px-1.5 py-0.5 hover:bg-[#2a1214] disabled:opacity-40"
          >
            <span className="material-symbols-outlined text-[13px]">undo</span>
            Revert hunk
          </button>
        </div>
      </div>
      {showDiff && (
        <pre className="text-[10px] font-mono leading-relaxed px-2 py-1 overflow-x-auto max-h-40" data-testid={`hunk-diff-${sig}`}>
          {hunk.diff_text.split('\n').slice(0, 20).map((ln, i) => {
            const c = ln.startsWith('+') && !ln.startsWith('+++') ? '#7ee787'
              : ln.startsWith('-') && !ln.startsWith('---') ? '#ff9a94'
              : 'var(--color-text-faint)';
            return <div key={i} style={{ color: c }}>{ln || ' '}</div>;
          })}
        </pre>
      )}
    </div>
  );
}

// ── Distribute view (Run 3) ──────────────────────────────────────────────────

function DistributeView({ name, onRequestClose }: { name: string; onRequestClose?: () => void }) {
  const [data, setData] = useState<DistributionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    getDistribution(name).then(
      (d) => alive && setData(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  // Open aim.json in the Canvas so the owner can edit the distribution block (Run 3,
  // Step 1). aim.json is PROJECT-relative (lives at Projects/<name>/aim.json) → needs
  // the Projects/<name>/ prefix (BrainView.openFile shape), NOT the already-workspace-
  // relative hunk.file shape from ReviewView. Close-before-dispatch z-index precedent.
  const openAimJson = useCallback(() => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', {
      detail: { path: `Projects/${name}/aim.json` },
    }));
  }, [name, onRequestClose]);

  // [Distribute a brain] does NOT auto-run — s_ddd-distribute is human-in-the-loop
  // (confirms targets + content-safety scan + emit≠publish). We surface the exact
  // chat command; the user invokes it in a chat tab where the HITL gate runs.
  const distributeCmd = `distribute this ddd: ${name}`;
  const [copied, setCopied] = useState(false);
  const onDistribute = useCallback(() => {
    // Guard BOTH the method (clipboard may be absent in an older webview) AND the
    // returned promise (?. on .then too) — Gate-2 HIGH: `clipboard?.writeText(x).then`
    // still throws if clipboard exists but writeText returns undefined.
    const p = navigator.clipboard?.writeText(distributeCmd);
    p?.then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
      () => {},
    );
  }, [distributeCmd]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]" data-testid="distribute-error">Failed to load distribution: {error}</div>;
  if (!data) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading distribution…</div>;

  return (
    <div className="p-4" data-testid="brainhub-distribute">
      {/* declared reach — guided Step 2 (confirm targets & freshness) */}
      {data.distributable ? (
        <>
          <div className="flex items-center gap-2 mb-1.5 text-[10px] uppercase tracking-wider font-semibold text-[#3fb950]" data-testid="distribute-step" data-step="2">
            Step 2 · confirm targets &amp; freshness
          </div>
          <div className="flex items-center gap-2 mb-3 text-[12px]">
            <span className="material-symbols-outlined text-[16px] text-[#3fb950]">outbound</span>
            <span className="font-semibold text-[var(--color-text)]">Distributable</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${data.visibility === 'external' ? 'bg-[#3a2412] text-[#f0a500]' : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]'}`}>
              {data.visibility}
            </span>
          </div>
          <div className="flex flex-col gap-1.5 mb-4" data-testid="distribute-targets">
            {data.declared_targets.map((t) => (
              <div key={t} className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[#12161c] px-2.5 py-1.5" data-testid="distribute-target-row">
                <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">deployed_code</span>
                <span className="font-mono text-[11px] text-[var(--color-text)]">{t}</span>
                {/* F2 TRISTATE — three EXPLICIT branches. `null` (freshness unknown,
                    uncommitted output) must NOT fall into "up to date" (the old
                    `!source_changed_since` did exactly that, re-burying staleness). */}
                {!data.has_output ? (
                  <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">never distributed</span>
                ) : data.source_changed_since === true ? (
                  <span className="ml-auto text-[9px] text-[#f0a500]" title="knowledge changed since last distribute">● source changed since last distribute</span>
                ) : data.source_changed_since === false ? (
                  <span className="ml-auto text-[9px] text-[var(--color-text-faint)]">up to date</span>
                ) : (
                  <span className="ml-auto text-[9px] text-[var(--color-text-muted)]" title="the distribute output isn't git-committed, so there's no stable anchor to compare against — commit the output to enable freshness tracking">freshness unknown</span>
                )}
              </div>
            ))}
          </div>
          {data.has_output && (
            <div className="text-[10px] text-[var(--color-text-faint)] mb-3 font-mono">
              last output: {data.output_path} {data.last_distribute_time ? `· ${data.last_distribute_time.slice(0, 10)}` : ''}
            </div>
          )}
        </>
      ) : (
        <div className="rounded-md border border-dashed border-[#3a2e12] bg-[#1a1710] p-3 mb-4" data-testid="distribute-not-distributable">
          {/* Guided header — Gate-1: an ORPHANED brain (has_output && !distributable)
              already completed the flow once; labeling it "Step 1 · declare reach"
              would contradict the orphaned warning below. So split on has_output:
              orphaned → re-declare (a regression state); else → honest Step 1. */}
          {data.has_output ? (
            <div className="flex items-center gap-2 mb-1.5 text-[10px] uppercase tracking-wider font-semibold text-[#db8c3a]" data-testid="distribute-redeclare">
              Reach removed · re-declare to resume distribution
            </div>
          ) : (
            <div className="flex items-center gap-2 mb-1.5 text-[10px] uppercase tracking-wider font-semibold text-[#f0a500]" data-testid="distribute-step" data-step="1">
              Step 1 · declare a reach
            </div>
          )}
          <div className="flex items-center gap-2 text-[12px] text-[#f0a500] mb-1">
            <span className="material-symbols-outlined text-[16px]">block</span>
            Not distributable
          </div>
          <div className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">
            This brain has no <span className="font-mono">distribution</span> block in its <span className="font-mono">aim.json</span>.
            The owner must declare a reach before it can be distributed — add to <span className="font-mono">aim.json</span>:
            <code className="block mt-1 px-2 py-1 rounded bg-[var(--color-bg)] text-[var(--color-text-muted)] whitespace-pre">{'"distribution": { "targets": ["open-plugin"], "visibility": "internal" }'}</code>
            {data.warnings.length > 0 && <span className="block mt-1 text-[#ff9a94]">⚠ {data.warnings.join('; ')}</span>}
            {/* Gate-2 MED: a stale output with the block since removed — surface it, don't hide it. */}
            {data.has_output && (
              <span className="block mt-1 text-[#db8c3a]" data-testid="distribute-stale-output">
                ⚠ an orphaned distribute output still exists (<span className="font-mono">{data.output_path}</span>) — the reach was declared before, then removed.
              </span>
            )}
          </div>
          {/* [Open aim.json] — Gate-1: ONLY on the not-distributable branch, where
              editing aim.json IS the next action. Opens it in the Canvas so the owner
              can add/fix the distribution block. (On the distributable branch the block
              is already valid → no edit affordance, avoids noise.) */}
          <button
            onClick={openAimJson}
            data-testid="distribute-open-aim"
            className="mt-2.5 flex items-center gap-1.5 text-[10px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-1 hover:bg-[#12233a]"
            title="Open aim.json in the Canvas to declare the distribution block"
          >
            <span className="material-symbols-outlined text-[13px]">open_in_new</span>
            Open aim.json
          </button>
        </div>
      )}

      {/* Step 3 · run — [Distribute a brain] is guidance, NOT auto-run (HITL):
          s_ddd-distribute confirms targets + content-safety scan + emit≠publish. */}
      <div className="flex flex-col gap-1.5">
        {data.distributable && (
          <div className="text-[10px] uppercase tracking-wider font-semibold text-[#3fb950]" data-testid="distribute-step" data-step="3">
            Step 3 · run
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={onDistribute}
            disabled={!data.distributable}
            data-testid="distribute-button"
            className="flex items-center gap-1.5 text-[11px] text-[#3fb950] border border-[#1f5a2a] rounded-md px-2.5 py-1 hover:bg-[#132918] disabled:opacity-40 disabled:cursor-not-allowed"
            title={data.distributable ? 'Copy the chat command to run s_ddd-distribute' : 'Declare a distribution block first'}
          >
            <span className="material-symbols-outlined text-[14px]">content_copy</span>
            {copied ? 'Copied — paste into a chat tab' : 'Distribute a brain'}
          </button>
          {data.distributable && (
            <span className="text-[10px] text-[var(--color-text-faint)] font-mono">→ {distributeCmd}</span>
          )}
        </div>
      </div>
    </div>
  );
}
