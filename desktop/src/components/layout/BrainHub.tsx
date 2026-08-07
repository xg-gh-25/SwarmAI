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
  BrainSummary, BrainDetail,
  ReviewData, ReviewHunk, PendingProposal, DistributionState,
} from '../../services/ddd';
import { DddCard, Ontology } from './DddCard';
import { CodeGraph } from '../code-intel/CodeGraph';
import { LibraryTree } from './LibraryTree';
import { docSignalMap, weeklyReportModel, type WeeklyReportModel } from './dddOverview';

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
        {/* key={selected} ties BrainView's identity to the brain — DEFENSIVE: today
            every `selected` change is a gallery-card click and the gallery tab only
            renders when tab==='gallery', so a brain switch is always brain→gallery→brain
            and this conditional already unmounts BrainView on the gallery step (fresh
            mount on return). The key guards a FUTURE in-place brain switch (e.g. a
            "jump to brain" affordance) from surviving a stale view='graph' or a stale
            Projects/<name> tree root into the new brain. Cheap + intent-clear. */}
        {!error && tab === 'brain' && selected && (
          <BrainView
            key={selected}
            name={selected}
            onRequestClose={onRequestClose}
            onGoToReview={() => setTab('review')}
            /* F3: `uncommitted` lives on the cheap gallery BrainHealth, NOT on the
               detail DetailHealth (which has escalationPending but no uncommitted).
               Thread it down from the already-loaded summary so §② Need-You can show
               it without reading a field that doesn't exist on detail.health. */
            uncommitted={brains?.find((b) => b.name === selected)?.health.uncommitted ?? false}
          />
        )}
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


// ── Brain view — FIXED [Overview | Browse] sub-tabs (run_6c68088f) ──────────────
//
// XG directive (this session): the Brain-detail view has TWO fixed sub-tabs that
// separate two mental modes — NEVER a dynamic per-brain layout ("dynamic makes
// users lost"). Same tab set + same section order for EVERY brain.
//   • OVERVIEW (default) — "what state is this brain in / what should I do":
//       §① Ontology (3-layer×7-type) — RELOCATED from the old health-strip (Gate-0
//          C046: do NOT rebuild — the existing DddCard density=full IS the ontology
//          + needs-you verdict), fixed FIRST, overall-summary.
//       §② Need-You — a FIXED-POSITION action block (never removed): pending
//          proposals → [Go to Review]; uncommitted changes; both zero → a muted
//          "Nothing queued" line still rendered (its position is a stable anchor).
//       §③ 4 core-doc cards (PRODUCT/TECH/IMPROVEMENT/PROJECT) — the judgment-core
//          the user periodically reviews; each shows its new-since-review signal +
//          click→Canvas; a group header sums the signals + hosts [Weekly Report].
//   • BROWSE — "explore the whole picture": the real Projects/<name> file tree +
//     [Files | Code Graph] toggle (MOVED verbatim from the old default content —
//     zero logic change; the tree is the "了解全貌" surface, now second-class).
//
// The tree/graph is the "browse detail" mode; Overview is "check state" — the split
// stops the two from crowding each other vertically (the whole reason for tabs).

type DetailTab = 'overview' | 'browse';

function BrainView(
  { name, onRequestClose, onGoToReview, uncommitted }:
  { name: string; onRequestClose?: () => void; onGoToReview?: () => void; uncommitted?: boolean },
) {
  const [detail, setDetail] = useState<BrainDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('overview');  // fixed default
  // Browse-tab content: file tree (default) or code graph. Unchanged from before —
  // just now lives inside the Browse sub-tab.
  const [view, setView] = useState<'files' | 'graph'>('files');
  const [review, setReview] = useState<ReviewData | null>(null);
  const [showWeekly, setShowWeekly] = useState(false);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    setReview(null);
    setDetailTab('overview');   // reset to Overview when switching brains (fixed default)
    setView('files');
    setShowWeekly(false);
    getBrainDetail(name).then(
      (d) => alive && setDetail(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    // Review data powers §③ per-doc signals + the Weekly Report. Best-effort: a
    // review failure must NOT blank the Overview (the ontology/cards still render);
    // a null review → all-zero signals (docSignalMap is null-safe).
    getReview(name).then(
      (r) => alive && setReview(r),
      () => alive && setReview(null),
    );
    return () => { alive = false; };
  }, [name]);

  // Open a doc/tree file in the app-level CANVAS. Paths are WORKSPACE-RELATIVE
  // (Projects/<name>/… — the LibraryTree + the ③ card both produce this shape) so
  // the useCanvasHost resolver takes them directly. Z-index (Gate-1, swarmws
  // precedent): close THIS overlay BEFORE the dispatch so the Canvas/FileViewer is
  // never rendered UNDER the host.
  const openFile = useCallback((workspaceRelPath: string) => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', {
      detail: { path: workspaceRelPath },
    }));
  }, [onRequestClose]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]">Failed to load brain: {error}</div>;
  if (!detail) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading {name}…</div>;

  const hasCodeIntel = detail.hasCodeIntel === true;   // daemon-skew: undefined → false
  const activeView: 'files' | 'graph' = view === 'graph' && hasCodeIntel ? 'graph' : 'files';

  return (
    <div className="flex flex-col h-full" data-testid="brainhub-brain">
      {/* header: brain name + kind, then the fixed [Overview | Browse] sub-tabs */}
      <div className="flex items-center gap-2 px-4 pt-4 pb-2 flex-shrink-0">
        <span className="text-[14px] font-semibold">{detail.name}</span>
        <span className="text-[10px] font-mono text-[var(--color-text-faint)] px-1.5 py-0.5 rounded bg-[var(--color-card)]">{detail.kind}</span>
        <div className="ml-auto flex items-center rounded-md border border-[var(--color-border)] overflow-hidden text-[11px]" data-testid="brainhub-detail-tabs">
          <button
            data-testid="detail-tab-overview"
            onClick={() => setDetailTab('overview')}
            className={`flex items-center gap-1 px-2.5 py-0.5 ${detailTab === 'overview' ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
          >
            <span className="material-symbols-outlined text-[14px]">insights</span>Overview
          </button>
          <button
            data-testid="detail-tab-browse"
            onClick={() => setDetailTab('browse')}
            className={`flex items-center gap-1 px-2.5 py-0.5 border-l border-[var(--color-border)] ${detailTab === 'browse' ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
          >
            <span className="material-symbols-outlined text-[14px]">account_tree</span>Browse
          </button>
        </div>
      </div>

      {detailTab === 'overview' ? (
        <BrainOverview
          detail={detail}
          review={review}
          uncommitted={uncommitted}
          onGoToReview={onGoToReview}
          onOpenFile={openFile}
          showWeekly={showWeekly}
          onToggleWeekly={() => setShowWeekly((v) => !v)}
        />
      ) : (
        <BrainBrowse
          name={name}
          hasCodeIntel={hasCodeIntel}
          activeView={activeView}
          onSetView={setView}
          onOpenFile={openFile}
        />
      )}
    </div>
  );
}

// ── §Overview — fixed order: ① Ontology → ② Need-You → ③ 4 core-doc cards ───────

function BrainOverview(
  { detail, review, uncommitted, onGoToReview, onOpenFile, showWeekly, onToggleWeekly }:
  {
    detail: BrainDetail; review: ReviewData | null; uncommitted?: boolean;
    onGoToReview?: () => void; onOpenFile: (p: string) => void;
    showWeekly: boolean; onToggleWeekly: () => void;
  },
) {
  const knowledge = detail.sections.find((s) => s.key === 'knowledge');
  const members = knowledge?.members ?? [];
  const pending = detail.health?.escalationPending ?? 0;
  // Memoize the O(hunks) signal map + weekly model so an unrelated re-render (weekly
  // toggle, hover) doesn't re-scan the whole review (meta-review LOW; matches the
  // useMemo discipline the Gallery already uses for aggregateTypeCounts).
  const signals = useMemo(() => docSignalMap(members, review), [members, review]);
  const weekly = useMemo(() => weeklyReportModel(detail, review), [detail, review]);
  // §① ontology counts — used for the no-health fallback tier (renders the 3×7
  // ontology from typeCounts alone). undefined when there are no entries at all.
  const ontologyCounts = useMemo(() => aggregateTypeCounts(detail.sections), [detail.sections]);

  return (
    <div className="flex-1 overflow-auto px-4 pb-4 flex flex-col gap-3" data-testid="brainhub-overview">
      {/* §① Ontology — RELOCATED, not rebuilt (Gate-0 C046): the existing
          DddCard density=full IS the 3-layer×7-type ontology + needs-you verdict.
          FIXED SLOT — always the FIRST child so the §①→②→③ order is invariant for
          EVERY brain (Gate-2 HIGH: gating the whole §① on detail.health?.noise let
          it VANISH for a degenerate/old-daemon brain → §② became first → per-brain
          structural drift, the exact "dynamic makes users lost" failure). Three
          graceful tiers, all in the same slot position:
            • health.noise present → the full health-strip (ontology + needs-you verdict)
            • no health but typeCounts present → ontology-only (DddCard FullBody renders
              the 3×7 ontology from typeCounts alone, no metrics needed)
            • neither → a muted anchor so the slot still occupies §①'s position. */}
      <div data-testid="brainhub-ontology">
        {detail.health?.noise ? (
          <div data-testid="brainhub-healthstrip">
            <DddCard density="full" name={detail.name} kind={detail.kind} metrics={detail.health}
              typeCounts={aggregateTypeCounts(detail.sections)} />
          </div>
        ) : ontologyCounts ? (
          // No scored health (old daemon / not-yet-computed) but we DO have the
          // entry ontology — render the 3×7 ontology directly (NOT via DddCard
          // FullBody, which would show a perpetual MetricsSkeleton waiting for
          // metrics that never arrive). Wrapped to match the strip's card frame.
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
            <Ontology typeCounts={ontologyCounts} />
          </div>
        ) : (
          <div className="text-[11px] text-[var(--color-text-faint)] rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2">
            Knowledge ontology not yet computed for this brain.
          </div>
        )}
      </div>

      {/* §② Need-You — FIXED-position block, never removed. Content varies; the
          block (and its slot) is always present so its position is a stable anchor. */}
      <NeedYouBlock pending={pending} uncommitted={uncommitted} onGoToReview={onGoToReview} />

      {/* §③ 4 core-doc cards + group header with [Weekly Report]. */}
      <CoreDocCards
        members={members}
        signals={signals}
        projectName={detail.name}
        onOpenFile={onOpenFile}
        showWeekly={showWeekly}
        onToggleWeekly={onToggleWeekly}
        weekly={weekly}
      />
    </div>
  );
}

/** §② the fixed-position Need-You block. */
function NeedYouBlock(
  { pending, uncommitted, onGoToReview }:
  { pending: number; uncommitted?: boolean; onGoToReview?: () => void },
) {
  const hasWork = pending > 0 || !!uncommitted;
  return (
    <div
      data-testid="brainhub-needyou"
      className={`rounded-lg border px-3 py-2.5 ${hasWork ? 'bg-[#1e1a0e] border-[#5a4a20]' : 'bg-[var(--color-card)] border-[var(--color-border)]'}`}
    >
      <div className={`text-[9px] uppercase tracking-wide font-semibold mb-1.5 ${hasWork ? 'text-[#f0a500]' : 'text-[var(--color-text-faint)]'}`}>
        {hasWork ? '▲ Needs you' : 'Need you'}
      </div>
      {!hasWork ? (
        <div data-testid="needyou-empty" className="text-[11px] text-[var(--color-text-faint)]">Nothing queued.</div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {pending > 0 && (
            <button
              data-testid="needyou-review"
              onClick={onGoToReview}
              className="flex items-center gap-2 text-[11px] text-left rounded-md px-2 py-1 border border-[#5a4a20] hover:bg-[#241f10]"
            >
              <span className="font-semibold text-[#f0a500] min-w-[22px]">{pending}</span>
              <span className="text-[var(--color-text-muted)]">proposal{pending === 1 ? '' : 's'} awaiting review</span>
              <span className="ml-auto flex items-center gap-0.5 text-[#f0a500]">
                Go to Review <span className="material-symbols-outlined text-[13px]">arrow_forward</span>
              </span>
            </button>
          )}
          {uncommitted && (
            <div data-testid="needyou-uncommitted" className="flex items-center gap-2 text-[11px] px-2 py-1">
              <span className="material-symbols-outlined text-[14px] text-[#db8c3a]">pending_actions</span>
              <span className="text-[var(--color-text-muted)]">uncommitted changes in this brain's subtree</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** §③ the 4 core-doc cards + group header (+ Weekly Report panel). */
function CoreDocCards(
  { members, signals, projectName, onOpenFile, showWeekly, onToggleWeekly, weekly }:
  {
    members: BrainDetail['sections'][number]['members'];
    signals: Map<string, { newCount: number; pendingCount: number }>;
    projectName: string; onOpenFile: (p: string) => void;
    showWeekly: boolean; onToggleWeekly: () => void; weekly: WeeklyReportModel;
  },
) {
  const totalNew = weekly.autoApplied;
  const totalPending = weekly.pending;
  const hasSignal = totalNew > 0 || totalPending > 0;
  return (
    <div data-testid="brainhub-coredocs">
      {/* group header — the aggregate "what moved this week" + Weekly Report entry */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[10px] uppercase tracking-wide font-semibold text-[var(--color-text-faint)]">
          Judgment core · review periodically
        </span>
        <span className="text-[10px] text-[var(--color-text-muted)]" data-testid="coredocs-agg">
          {hasSignal ? `since last review · ${totalNew} new · ${totalPending} pending` : 'up to date since last review'}
        </span>
        <button
          data-testid="coredocs-weekly"
          onClick={onToggleWeekly}
          className="ml-auto flex items-center gap-1 text-[10px] text-[#58a6ff] border border-[#1f3a5a] rounded px-1.5 py-0.5 hover:bg-[#12233a]"
        >
          <span className="material-symbols-outlined text-[12px]">summarize</span>
          {showWeekly ? 'Hide report' : 'Weekly Report'}
        </button>
      </div>

      {showWeekly && <WeeklyReportPanel weekly={weekly} projectName={projectName} />}

      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
        {members.map((m) => {
          const sig = signals.get(m.path) ?? { newCount: 0, pendingCount: 0 };
          const marked = sig.newCount > 0 || sig.pendingCount > 0;
          const docName = m.path.split('/').pop() ?? m.path;
          return (
            <button
              key={m.path}
              data-testid={`coredoc-${docName}`}
              onClick={() => onOpenFile(`Projects/${projectName}/${m.path}`)}
              className={`text-left rounded-lg bg-[var(--color-card)] p-2.5 transition-colors hover:border-[#3b4552] ${
                marked ? 'border-l-[3px] border-l-[#f0a500] border-y border-r border-[#4a3a12]' : 'border border-[var(--color-border)]'
              }`}
            >
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">description</span>
                <span className="text-[12px] font-semibold font-mono">{docName}</span>
                {marked && <span data-testid={`coredoc-mark-${docName}`} className="ml-auto w-1.5 h-1.5 rounded-full bg-[#f0a500]" />}
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)] mb-1">{DOC_ROLE[docName] ?? 'DDD document'}</div>
              <div className="flex items-center gap-2 text-[9px] text-[var(--color-text-faint)]">
                {sig.newCount > 0 && <span className="text-[#7ee787]">{sig.newCount} new</span>}
                {sig.pendingCount > 0 && <span className="text-[#f0a500]">{sig.pendingCount} pending</span>}
                {!marked && <span>up to date</span>}
                {m.mtime && <span className="ml-auto">{m.mtime}</span>}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** One-line role of each canonical doc (what the user reviews it FOR). */
const DOC_ROLE: Record<string, string> = {
  'PRODUCT.md': 'Priorities & non-goals',
  'TECH.md': 'Architecture & constraints',
  'IMPROVEMENT.md': 'Failures & lessons',
  'PROJECT.md': 'Current status & decisions',
};

/** Current-DDD-only Weekly Report — an in-overlay panel (NOT Canvas: the Canvas
 *  open-file path resolves a real file, useCanvasHost.ts:328, so inline HTML can't
 *  ride it). Live-rendered from data already loaded — no global one-pot, no file. */
function WeeklyReportPanel({ weekly, projectName }: { weekly: WeeklyReportModel; projectName: string }) {
  const t = weekly.trustDistribution;
  const scored = t.full + t.high + t.moderate + t.low;
  return (
    <div data-testid="brainhub-weekly-panel" className="rounded-lg border border-[#1f3a5a] bg-[#0e1723] p-3 mb-2 text-[11px]">
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-[#58a6ff]">
        <span className="material-symbols-outlined text-[15px]">summarize</span>
        {projectName} · weekly review {weekly.sinceSha && <span className="text-[9px] font-mono text-[var(--color-text-faint)]">since {weekly.sinceSha}</span>}
      </div>
      <div className="flex flex-col gap-1 text-[var(--color-text-muted)]">
        <div><span className="text-[#7ee787] font-semibold">{weekly.autoApplied}</span> auto-cultivated change{weekly.autoApplied === 1 ? '' : 's'} since last review</div>
        <div><span className="text-[#f0a500] font-semibold">{weekly.pending}</span> proposal{weekly.pending === 1 ? '' : 's'} awaiting your decision</div>
        <div>
          docs touched: {weekly.changedDocs.length > 0
            ? <span className="font-mono text-[var(--color-text)]">{weekly.changedDocs.join(', ')}</span>
            : <span className="text-[var(--color-text-faint)]">none</span>}
        </div>
        {/* F4: trust DISTRIBUTION, never a collapsed percentage. */}
        <div data-testid="weekly-trust-dist">
          section trust: {scored === 0
            ? <span className="text-[var(--color-text-faint)]">not scored yet</span>
            : <span>{t.full} full · {t.high} high · {t.moderate} moderate · {t.low} low{t.unscored ? ` · ${t.unscored} unscored` : ''}</span>}
        </div>
      </div>
    </div>
  );
}

// ── §Browse — the file tree + Code Graph toggle (MOVED verbatim from old default) ─

function BrainBrowse(
  { name, hasCodeIntel, activeView, onSetView, onOpenFile }:
  {
    name: string; hasCodeIntel: boolean; activeView: 'files' | 'graph';
    onSetView: (v: 'files' | 'graph') => void; onOpenFile: (p: string) => void;
  },
) {
  return (
    <div className="flex flex-col flex-1 min-h-0" data-testid="brainhub-browse">
      {/* [Files | Code Graph] segmented toggle — Files = the Projects tree (always),
          Code Graph = the inline force-graph (only when a code_intel.db exists). */}
      {hasCodeIntel && (
        <div className="flex items-center px-4 pb-2 flex-shrink-0">
          <div className="ml-auto flex items-center rounded-md border border-[var(--color-border)] overflow-hidden text-[11px]" data-testid="brainhub-view-toggle">
            <button
              data-testid="view-toggle-files"
              onClick={() => onSetView('files')}
              className={`flex items-center gap-1 px-2 py-0.5 ${activeView === 'files' ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
            >
              <span className="material-symbols-outlined text-[14px]">folder</span>Files
            </button>
            <button
              data-testid="view-toggle-graph"
              onClick={() => onSetView('graph')}
              className={`flex items-center gap-1 px-2 py-0.5 border-l border-[var(--color-border)] ${activeView === 'graph' ? 'bg-[#12233a] text-[#58a6ff]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
            >
              <span className="material-symbols-outlined text-[14px]">hub</span>Code Graph
            </button>
          </div>
        </div>
      )}

      {/* Content: the REAL Projects/<name> file tree, or the inline code graph. */}
      <div className="flex-1 min-h-0" data-testid="brainhub-brain-content">
        {activeView === 'graph' ? (
          <CodeGraph key={`graph-${name}`} project={name} inline />
        ) : (
          <LibraryTree key={`tree-${name}`} rootPath={`Projects/${name}`} onFileOpen={onOpenFile} />
        )}
      </div>
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
  // re-wrap in `Projects/${name}/` (that would double-prefix → 404). Same as
  // BrainView.openFile now (the Projects tree also yields workspace-relative paths).
  // Same close→Canvas z-index precedent (close BEFORE dispatch).
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
