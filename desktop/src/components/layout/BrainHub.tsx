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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useBrainsWithPinned, useBrainDetail, useReview, useDistribution,
  approveReview, rejectReviewHunk, approveProposal, rejectProposal, aggregateTypeCounts,
  brainRecall,
} from '../../services/ddd';
import type {
  BrainSummary, BrainDetail,
  ReviewData, ReviewHunk, PendingProposal, RecallHit,
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

// ── Root — MASTER→DETAIL nav model (run_3d371424, item 5) ───────────────────────
//
// XG directive (this session): the old 4 top-level tabs (Gallery | Brain·<name> |
// Review | Distribute) were FALSE PEERS — Gallery selects WHICH brain (a list),
// the other three are VIEWS of the selected brain (a detail). Mounting them as
// siblings meant 3 disabled/greyed tabs on open (empty-state anti-pattern), a split
// grouping (Overview/Browse were sub-tabs under `Brain` while their true siblings
// Review/Distribute sat at the top level), and no way to switch brain without
// returning to Gallery. This is the standard master→detail fix:
//   • TOP LEVEL = just the Gallery (the brain list). No greyed tabs.
//   • Selecting a brain enters the DETAIL shell: a breadcrumb (← All brains / <name>)
//     + a brain SWITCHER, and FOUR TRUE-PEER sub-tabs: Overview | Browse | Review |
//     Distribute. Same tab set + order for every brain (no dynamic layout — "dynamic
//     makes users lost", the prior fixed-sub-tabs directive, now widened to 4).
//
// `mode` is the top-level state: 'gallery' (list) or 'detail' (a brain is selected).
// `detailTab` (below, on the shell) is which of the 4 peer views is showing.

type Mode = 'gallery' | 'detail';
/** The 4 true-peer detail views (item 5). overview/browse were the old inner
 *  sub-tabs; review/distribute were promoted DOWN from false top-level peers. */
type DetailView = 'overview' | 'browse' | 'review' | 'distribute';

/** `onRequestClose` — the host overlay's `ctx.close` (overlaySurfaces passes it).
 *  Approach A (run_a607f2b0): opening a DDD doc closes THIS overlay first, then
 *  dispatches `swarm:open-file` so the Canvas/FileViewer isn't rendered UNDER the
 *  host (the SwarmWS-explorer z-index precedent). OPTIONAL: tests / a non-overlay
 *  mount omit it — file-open then just dispatches without a close (still correct).
 *  `onDispatch` (item 3) — the overlay ctx `dispatchPrompt` bridge: injects + sends
 *  a chat prompt (the Distribute HITL run trigger). OPTIONAL: without it, Distribute
 *  falls back to clipboard-copy (older webview / non-overlay mount). */
export function BrainHub(
  { onRequestClose, onDispatch }:
  { onRequestClose?: () => void; onDispatch?: (msg: string) => boolean } = {},
) {
  const [mode, setMode] = useState<Mode>('gallery');
  const [selected, setSelected] = useState<string | null>(null);
  const [detailView, setDetailView] = useState<DetailView>('overview');

  // Cached gallery list (run_cfb460ac): re-opening the overlay within the 30s window
  // is instant instead of paying the ~4s aggregate scan again. `refetch` powers Retry.
  const { data: bp, error: bpErr, refetch } = useBrainsWithPinned();
  const brains = bp?.brains ?? null;
  const pinned = useMemo(() => bp?.pinned ?? [], [bp]);
  const error = bpErr ? String((bpErr as { message?: string })?.message ?? bpErr) : null;

  // run_d0cd4414: the gallery is now a flat card wall — no primary hero, so NO second
  // getBrainDetail fetch here. The gallery makes exactly ONE data call
  // (useBrainsWithPinned above); a brain's detail is fetched lazily only when opened.

  // Enter the detail shell on a specific brain, at a specific view (default Overview).
  const openBrain = useCallback((name: string, view: DetailView = 'overview') => {
    setSelected(name);
    setDetailView(view);
    setMode('detail');
  }, []);
  const backToGallery = useCallback(() => { setMode('gallery'); }, []);

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

  if (error) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
        <div className="p-4 text-[13px]" data-testid="brainhub-error">
          <div className="text-[var(--color-error,#ef4444)]">Failed to load brains: {error}</div>
          <button
            data-testid="brainhub-retry"
            onClick={() => void refetch()}
            className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
            style={{ background: 'var(--color-error,#ef4444)' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // GALLERY (master) — the brain list is the ONLY top-level surface. No greyed tabs.
  if (mode === 'gallery' || !selected) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
        <div className="flex-1 overflow-auto">
          <Gallery brains={brains} pinned={pinned} onOpen={openBrain} />
        </div>
      </div>
    );
  }

  // DETAIL (a brain is selected) — breadcrumb + switcher + 4 true-peer sub-tabs.
  // key={selected} remounts the shell on a brain switch so no view-local state
  // (tree root, graph, review armed-state) leaks across brains.
  return (
    <BrainDetailShell
      key={selected}
      name={selected}
      brains={brains}
      detailView={detailView}
      onSelectView={setDetailView}
      onSwitchBrain={(n) => openBrain(n, detailView)}
      onBack={backToGallery}
      onRequestClose={onRequestClose}
      onDispatch={onDispatch}
      uncommitted={brains?.find((b) => b.name === selected)?.health.uncommitted ?? false}
    />
  );
}

/** The detail shell: breadcrumb (← All brains / <name>) + a brain SWITCHER + the 4
 *  true-peer sub-tabs (Overview | Browse | Review | Distribute), then the active view.
 *  Replaces the old top-level TabBtn row + the inner [Overview|Browse] toggle — one
 *  consistent 4-peer bar for every brain (item 5). */
function BrainDetailShell(
  { name, brains, detailView, onSelectView, onSwitchBrain, onBack, onRequestClose, onDispatch, uncommitted }:
  {
    name: string; brains: BrainSummary[] | null;
    detailView: DetailView; onSelectView: (v: DetailView) => void;
    onSwitchBrain: (n: string) => void; onBack: () => void;
    onRequestClose?: () => void; onDispatch?: (msg: string) => boolean; uncommitted?: boolean;
  },
) {
  // Review sub-tab badge: the pending count from the cheap gallery summary (zero
  // extra fetch) — surfaces "N awaiting review" right on the tab (wireframe).
  const pending = brains?.find((b) => b.name === name)?.health.pending ?? 0;

  // Shell-local openFile for the search row (Gate-1 fix): openFile is BrainView-local,
  // so instead of threading it up we rebuild the SAME primitive here — close this
  // overlay BEFORE dispatching swarm:open-file so the Canvas isn't rendered under the
  // host (the swarmws z-index precedent, identical to BrainView.openFile).
  const shellOpenFile = useCallback((workspaceRelPath: string) => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: workspaceRelPath } }));
  }, [onRequestClose]);

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
      {/* breadcrumb + brain switcher */}
      <div className="flex items-center gap-2 px-3 h-9 border-b border-[var(--color-border)] flex-shrink-0 text-[12px]" data-testid="brainhub-breadcrumb">
        <button
          data-testid="brainhub-back"
          onClick={onBack}
          className="flex items-center gap-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <span className="material-symbols-outlined text-[15px]">arrow_back</span>
          All brains
        </button>
        <span className="text-[var(--color-text-faint)]">/</span>
        {/* brain switcher — jump to another brain WITHOUT returning to Gallery. A
            native <select> (accessible, zero extra deps); keeps the current view. */}
        {brains && brains.length > 1 ? (
          <select
            data-testid="brainhub-switcher"
            value={name}
            onChange={(e) => onSwitchBrain(e.target.value)}
            className="bg-[var(--color-card)] border border-[var(--color-border)] rounded px-1.5 py-0.5 text-[12px] font-semibold text-[var(--color-text)]"
          >
            {brains.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
        ) : (
          <span className="font-semibold">{name}</span>
        )}
      </div>

      {/* 4 true-peer sub-tabs */}
      <div className="flex items-center gap-1 px-3 h-9 border-b border-[var(--color-border)] flex-shrink-0 text-[12px]" data-testid="brainhub-detail-nav">
        <TabBtn active={detailView === 'overview'} onClick={() => onSelectView('overview')} testid="brainhub-tab-overview">Overview</TabBtn>
        <TabBtn active={detailView === 'browse'} onClick={() => onSelectView('browse')} testid="brainhub-tab-browse">Browse</TabBtn>
        <TabBtn active={detailView === 'review'} onClick={() => onSelectView('review')} testid="brainhub-tab-review">
          Review{pending > 0 ? <span data-testid="review-tab-badge" className="ml-1 inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-[#5a4a20] text-[#f0a500] text-[9px] font-semibold">{pending}</span> : null}
        </TabBtn>
        <TabBtn active={detailView === 'distribute'} onClick={() => onSelectView('distribute')} testid="brainhub-tab-distribute">Distribute</TabBtn>
      </div>

      {/* Search row (AC7): spans all sub-tabs, scoped to this brain. Uses a
          shell-local openFile built from the onRequestClose the shell already holds
          (Gate-1: openFile is BrainView-local, so we do NOT thread it up — the shell
          reconstructs the same close-then-dispatch primitive). */}
      <BrainSearchRow name={name} onOpenFile={shellOpenFile} />

      <div className="flex-1 overflow-auto">
        {detailView === 'overview' && (
          <BrainView
            name={name}
            onRequestClose={onRequestClose}
            onGoToReview={() => onSelectView('review')}
            uncommitted={uncommitted}
          />
        )}
        {detailView === 'browse' && (
          <BrainView
            name={name}
            onRequestClose={onRequestClose}
            onGoToReview={() => onSelectView('review')}
            uncommitted={uncommitted}
            forceBrowse
          />
        )}
        {detailView === 'review' && <ReviewView name={name} onRequestClose={onRequestClose} />}
        {detailView === 'distribute' && <DistributeView name={name} onRequestClose={onRequestClose} onDispatch={onDispatch} />}
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

/**
 * BrainSearchRow (run_d0cd4414, AC7) — the Brain Hub's search box, wired to the
 * pre-existing GET /api/ddd/brains/{name}/recall endpoint via brainRecall(). Lives
 * in the detail shell (scoped to the selected brain, matching the single-brain
 * endpoint). Self-contained: owns its query/hits state + a debounce so it does NOT
 * fetch per keystroke. A conditional results panel renders ONLY when q is non-empty
 * AND hits exist — so it never crowds the view when unused. Clicking a hit calls
 * onOpenFile (the shell-local opener built from onRequestClose — Gate-1: openFile is
 * BrainView-local, so the shell builds its own rather than threading it up).
 */
function BrainSearchRow({ name, onOpenFile }: { name: string; onOpenFile: (p: string) => void }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<RecallHit[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset when switching brains (the shell remounts on name change via key, but guard
  // an in-place name change too — stale hits from another brain would mislead).
  useEffect(() => { setQ(''); setHits([]); }, [name]);

  // Debounced recall: blank q short-circuits (brainRecall itself also guards, but
  // clearing hits here removes the panel immediately). A stale-response guard
  // (`alive`) prevents an out-of-order slow response from overwriting a newer one.
  // Gate-2 meta-review (MED, operational): recall_all is UNCACHED per call (re-reads
  // + re-parses the DDD docs + Knowledge corpus + FTS stores). So (a) require ≥2
  // non-blank chars — a 1-char query scans everything for near-useless results — and
  // (b) 350ms debounce, to bound the worst-case scan rate for a fast typist on a
  // large-corpus brain. Fail-soft endpoint means this is throughput-only, not a
  // correctness gate.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < 2) { setHits([]); return; }
    let alive = true;
    timer.current = setTimeout(() => {
      void brainRecall(name, q).then((h) => { if (alive) setHits(h); }, () => { if (alive) setHits([]); });
    }, 350);
    return () => { alive = false; if (timer.current) clearTimeout(timer.current); };
  }, [q, name]);

  const showPanel = q.trim().length >= 2 && hits.length > 0;

  return (
    <div className="px-3 py-2 border-b border-[var(--color-border)] flex-shrink-0" data-testid="brainhub-search-row">
      <div className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1">
        <span className="material-symbols-outlined text-[15px] text-[var(--color-text-faint)]">search</span>
        <input
          data-testid="brainhub-search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Search ${name}'s knowledge…`}
          className="flex-1 bg-transparent text-[12px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none"
        />
        {q && (
          <button data-testid="brainhub-search-clear" onClick={() => setQ('')}
            className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]">
            <span className="material-symbols-outlined text-[15px]">close</span>
          </button>
        )}
      </div>
      {showPanel && (
        <div className="mt-1.5 flex flex-col gap-1 max-h-[40vh] overflow-auto" data-testid="brainhub-search-results">
          {hits.map((h, i) => (
            <button
              key={`${h.source}-${i}`}
              data-testid="brainhub-search-hit"
              onClick={() => onOpenFile(h.source)}
              className="text-left rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 py-1.5 hover:border-[#3b4552]"
            >
              <div className="flex items-baseline gap-2">
                <span className="text-[12px] font-medium text-[var(--color-text)] truncate">{h.title || h.source}</span>
                <span className="ml-auto text-[9px] font-mono text-[var(--color-text-faint)] flex-shrink-0">{h.domain}</span>
              </div>
              {h.source && <div className="text-[10px] font-mono text-[var(--color-text-faint)] truncate">{h.source}</div>}
              {h.content && <div className="text-[10px] text-[var(--color-text-muted)] line-clamp-2 mt-0.5">{h.content}</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Gallery ──────────────────────────────────────────────────────────────────

/** A compact card straight from a BrainSummary (cheap — no detail fetch).
 *  `isSelf` (run_d0cd4414) threads THROUGH this wrapper into DddCard — the Gallery
 *  renders CompactBrain (not DddCard directly), so the self-marker prop must be
 *  forwarded here or it silently drops. */
function CompactBrain({ b, isSelf, onOpen }: { b: BrainSummary; isSelf?: boolean; onOpen: (n: string) => void }) {
  return (
    <DddCard density="compact" name={b.name} kind={b.kind}
      lifecycleStage={b.lifecycleStage}
      health={b.health} typeCounts={b.typeCounts} description={b.description}
      isSelf={isSelf} onOpen={onOpen} />
  );
}

/**
 * Flat card wall (run_d0cd4414): ONE 3-per-row grid, every brain an equal compact
 * card — no hero, no needs/calm zones, no second getBrainDetail fetch (the gallery
 * makes exactly one call: useBrainsWithPinned). SwarmAI is pinned FIRST via
 * `pinned[0]` (backend get_pinned_projects, always SwarmAI, existence-guarded) and
 * is the ONLY differentiated card (violet top-border + SELF·OS tag via isSelf).
 * A needs card (pending>0) still self-signals with its amber left-border inside the
 * wall. Degrades: pinned empty (old daemon) → SwarmAI simply isn't hoisted/marked,
 * the wall renders in the brains' natural order.
 */
function Gallery(
  { brains, pinned, onOpen }:
  { brains: BrainSummary[] | null; pinned: string[]; onOpen: (n: string) => void },
) {
  if (brains === null) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">Loading brains…</div>;
  if (brains.length === 0) return <div className="p-4 text-[var(--color-text-muted)] text-[13px]">No DDD brains found.</div>;

  // SwarmAI-first ordering, data-driven (pinned[0]), NOT a hardcoded name. The self
  // card is hoisted to the front; the rest keep their incoming order. If pinned is
  // empty or its brain isn't in the list (old daemon), selfName is undefined → no
  // hoist, no marker (graceful degrade).
  const selfName = pinned[0] && brains.some((b) => b.name === pinned[0]) ? pinned[0] : undefined;
  const ordered = selfName
    ? [brains.find((b) => b.name === selfName)!, ...brains.filter((b) => b.name !== selfName)]
    : brains;

  return (
    <div className="p-4" data-testid="brainhub-gallery">
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }} data-testid="brainhub-card-wall">
        {ordered.map((b) => (
          <CompactBrain key={b.name} b={b} isSelf={b.name === selfName} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}


// ── Brain view — Overview / Browse (view switching OWNED by the detail shell) ────
//
// run_3d371424 (item 5): the old INNER [Overview | Browse] toggle was REMOVED — view
// switching moved UP to the BrainDetailShell's 4-peer sub-tab bar (Overview | Browse |
// Review | Distribute). BrainView now renders ONE view per mount, chosen by the
// `forceBrowse` prop the shell passes. The two mental modes are unchanged:
//   • OVERVIEW (forceBrowse=false, default) — "what state / what should I do":
//       §① Ontology (3-layer×7-type) — the existing DddCard density=full IS the
//          ontology + verdict (Gate-0 C046: relocated, not rebuilt); fixed FIRST.
//       §② Need-You — a FIXED-POSITION action block (proposals→[Go to Review];
//          uncommitted; reclaimable; sinking; all-zero → a muted "Nothing queued").
//       §③ 4 core-doc cards (PRODUCT/TECH/IMPROVEMENT/PROJECT) + [Weekly Report].
//   • BROWSE (forceBrowse=true) — the real Projects/<name> file tree + Code Graph.

function BrainView(
  { name, onRequestClose, onGoToReview, uncommitted, forceBrowse = false }:
  { name: string; onRequestClose?: () => void; onGoToReview?: () => void;
    uncommitted?: boolean; forceBrowse?: boolean },
) {
  const [showWeekly, setShowWeekly] = useState(false);

  // Cached fetches (run_cfb460ac): re-opening the same brain no longer refetches
  // within the 30s staleTime. detail drives the view; review is best-effort (§③
  // signals + Weekly): a review error must NOT blank the Overview, so we read only
  // its data (null on error → docSignalMap is null-safe).
  const { data: detail = null, error: detailErr } = useBrainDetail(name);
  const { data: review = null } = useReview(name);
  const error = detailErr ? String((detailErr as { message?: string })?.message ?? detailErr) : null;

  // Reset view-local UI state when switching brains (the shell's keyed remount already
  // does this for a brain switch; this guards a future in-place name change).
  useEffect(() => { setShowWeekly(false); }, [name]);

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

  return (
    <div className="flex flex-col h-full" data-testid="brainhub-brain">
      {forceBrowse ? (
        <BrainBrowse
          name={name}
          hasCodeIntel={hasCodeIntel}
          onOpenFile={openFile}
        />
      ) : (
        <BrainOverview
          detail={detail}
          review={review}
          uncommitted={uncommitted}
          onGoToReview={onGoToReview}
          onOpenFile={openFile}
          showWeekly={showWeekly}
          onToggleWeekly={() => setShowWeekly((v) => !v)}
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
            {/* ontologyOnly (run_115aa182): the §② NeedYouBlock below OWNS needs-you
                (proposals/reclaimable/sinking); this §① strip is the ontology + facts
                only, so the two don't render a duplicate "Needs you" block. */}
            <DddCard density="full" name={detail.name} kind={detail.kind} metrics={detail.health}
              typeCounts={aggregateTypeCounts(detail.sections)} ontologyOnly />
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

      {/* §② Need-You — FIXED-position block, never removed. The SINGLE needs-you
          owner (run_115aa182): besides proposals + uncommitted it also surfaces
          reclaimable + sinking (previously only shown by the §① FullBody needs-you,
          which is now suppressed via ontologyOnly to kill the duplicate block). */}
      <NeedYouBlock
        pending={pending}
        uncommitted={uncommitted}
        reclaimable={detail.health?.noise?.reclaimable ?? 0}
        sinking={detail.health?.sinking ?? 0}
        onGoToReview={onGoToReview}
      />

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

/** §② the fixed-position Need-You block — the SINGLE needs-you owner for the
 *  Overview (run_115aa182): proposals (interactive → Review) + uncommitted +
 *  reclaimable + sinking. reclaimable/sinking moved here from the §① FullBody
 *  needs-you (now suppressed) so they're not lost when the duplicate is removed. */
function NeedYouBlock(
  { pending, uncommitted, reclaimable = 0, sinking = 0, onGoToReview }:
  { pending: number; uncommitted?: boolean; reclaimable?: number; sinking?: number;
    onGoToReview?: () => void },
) {
  const hasWork = pending > 0 || !!uncommitted || reclaimable > 0 || sinking > 0;
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
          {reclaimable > 0 && (
            <div data-testid="needyou-reclaimable" className="flex items-center gap-2 text-[11px] px-2 py-1">
              <span className="font-semibold text-[#f0a500] min-w-[22px]">{reclaimable}</span>
              <span className="text-[var(--color-text-muted)]">reclaimable (run reclaim)</span>
            </div>
          )}
          {sinking > 0 && (
            <div data-testid="needyou-sinking" className="flex items-center gap-2 text-[11px] px-2 py-1">
              <span className="font-semibold text-[#f0a500] min-w-[22px]">{sinking}</span>
              <span className="text-[var(--color-text-muted)]">entr{sinking === 1 ? 'y' : 'ies'} sinking (decaying)</span>
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
  { name, hasCodeIntel, onOpenFile }:
  { name: string; hasCodeIntel: boolean; onOpenFile: (p: string) => void },
) {
  // Code Graph is a SECOND-CLASS, opt-in surface (run_cfb460ac): NOT a peer tab of
  // the tree. It lives in a collapsed disclosure BELOW the tree and only MOUNTS
  // CodeGraph when expanded — so getCodeIntelGraph (an expensive force-graph fetch)
  // never runs unless the user asks. Default collapsed; no 3rd-level tab toggle.
  const [graphOpen, setGraphOpen] = useState(false);

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-auto px-4 pb-4" data-testid="brainhub-browse">
      {/* run_d0cd4414: the tree is now WRAPPED in a card frame (border + bg + a
          "Files" header) so it reads as a peer of the Code Graph disclosure below —
          two framed, hierarchy-clear regions instead of a naked tree above a boxed
          graph. Infra (.artifacts/.db/.lock/dotfiles) is HIDDEN by default
          (showAllFiles dropped → LibraryTree's isNoiseNode filter applies), so the
          tree shows only real browsable DDD content.
          hugContent is KEPT, but the frame carries an explicit maxHeight cap: with a
          cap, the tree's parent-clientHeight measure converges (short tree → frame
          hugs content; tall tree → frame caps at maxHeight → tree scrolls inside),
          so the run_4de3103f feedback ("short tree pins height low forever") can't
          occur — the cap bounds it. Width clamp moves to the frame. */}
      <div
        data-testid="brainhub-browse-tree-frame"
        className="flex flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden"
        style={{ maxWidth: 'clamp(320px, 38%, 560px)', maxHeight: '60vh' }}
      >
        <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--color-border)] text-[11px] font-medium text-[var(--color-text-muted)] flex-shrink-0">
          <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">folder_open</span>
          <span>Files · Projects/{name}</span>
        </div>
        <LibraryTree
          key={`tree-${name}`}
          rootPath={`Projects/${name}`}
          onFileOpen={onOpenFile}
          hugContent
        />
      </div>

      {/* Code Graph — collapsed disclosure BELOW the tree. Only rendered when a
          code_intel.db exists for this brain; only MOUNTED (→ fetched) on expand. */}
      {hasCodeIntel && (
        <div className="mt-3 flex-shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden" style={{ maxWidth: 640 }} data-testid="brainhub-codegraph-disclosure">
          <button
            data-testid="codegraph-toggle"
            aria-expanded={graphOpen}
            onClick={() => setGraphOpen((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-[12px] hover:bg-[var(--color-hover)]"
          >
            <span className="material-symbols-outlined text-[15px] text-[#58a6ff]">hub</span>
            <span className="font-medium">Code Graph</span>
            {!graphOpen && <span className="text-[10px] text-[var(--color-text-faint)]">· expand to load the dependency graph</span>}
            <span className="material-symbols-outlined text-[16px] text-[var(--color-text-faint)] ml-auto">
              {graphOpen ? 'expand_less' : 'expand_more'}
            </span>
          </button>
          {graphOpen && (
            <div className="border-t border-[var(--color-border)] h-[360px]" data-testid="codegraph-panel">
              <CodeGraph key={`graph-${name}`} project={name} inline />
            </div>
          )}
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
  // Cached query (run_cfb460ac) — re-opening Review inside the 30s window is served
  // from cache. A mutating action (approve/reject) calls `reload()` = refetch, which
  // re-hits the network to reflect the write (the write invalidated the queue).
  const { data = null, error: qErr, refetch } = useReview(name);
  const error = qErr ? String((qErr as { message?: string })?.message ?? qErr) : null;
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

  // reload = refetch the cached query + reset the transient per-action UI state.
  // Replaces the old raw-fetch load(); every post-action call site is unchanged.
  // Gate-2 (run_cfb460ac, multi-specialist confirmed): a mutation (approve/reject)
  // ALSO changes the pending count that lives in TWO OTHER cached queries — the
  // gallery badge (['brains-with-pinned']) and the Overview §② Need-You count
  // (['brain-detail', name].health.escalationPending). Before caching, every mount
  // refetched fresh; with useQuery those siblings would show a stale non-zero pending
  // for up to staleTime (30s) after the user clears the queue. Invalidate them here
  // (the single post-action chokepoint) so the cross-query count stays truthful.
  const qc = useQueryClient();
  const load = useCallback(() => {
    setActionError(null);   // clear any transient action error on (re)load
    setArmed(false);        // disarm on every (re)load: brain switch, or after any action
    void refetch();
    void qc.invalidateQueries({ queryKey: ['brain-detail', name] });
    void qc.invalidateQueries({ queryKey: ['brains-with-pinned'] });
  }, [refetch, qc, name]);

  // Disarm + clear the transient action error when switching brains (the query
  // itself re-keys on name via useReview). The cached data does NOT blank on switch.
  useEffect(() => { setActionError(null); setArmed(false); }, [name]);

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

  // Item 6 — de-jargon: the primary header is plain language ("N changes since
  // your last review · M awaiting approval"). The engineer-facing git details
  // (watermark SHA → HEAD, scoped path) are FOLDED into a muted <details> below,
  // still present in the DOM (kept for the power user + the header contract test).
  const changeCount = data.hunks.length;
  const pendingCount = riskyHunks.length;

  return (
    <div className="p-4" data-testid="brainhub-review">
      {/* diff header — plain language first, git internals folded */}
      <div className="mb-3" data-testid="review-diff-header">
        <div className="flex items-center gap-2 text-[13px] text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[16px] text-[#a855f7]">rate_review</span>
          <span>
            {changeCount === 0
              ? 'No changes since your last review'
              : <>{changeCount} change{changeCount === 1 ? '' : 's'} since your last review
                  {pendingCount > 0 && <> · <span className="text-[#f0a500]">{pendingCount} awaiting your approval</span></>}</>}
          </span>
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
            title={armed ? 'This clears the review queue and records everything as seen — click again to confirm' : 'Records all changes below as reviewed'}
          >
            <span className="material-symbols-outlined text-[14px]">{armed ? 'warning' : 'visibility'}</span>
            {armed ? 'Click again to confirm' : 'Mark all seen'}
          </button>
        </div>
        {/* engineer detail — folded, muted; kept for power users */}
        <details className="mt-1">
          <summary className="text-[10px] text-[var(--color-text-muted)] cursor-pointer select-none opacity-70 hover:opacity-100">
            git detail
          </summary>
          <div className="mt-1 text-[10px] font-mono text-[var(--color-text-muted)]">
            diff <span className="text-[var(--color-text)]">Projects/{name}/</span>
            · last-reviewed <span className="text-[var(--color-text)]">{shortSha(data.last_reviewed_sha)}</span>
            → HEAD <span className="text-[var(--color-text)]">{shortSha(data.head_sha)}</span>
          </div>
        </details>
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

// run_8d2ec26c: the target→repo mapping is a READ-ONLY derivation, NOT a stored
// field. aim.json's distribution block carries ONLY {targets, visibility} — the
// "target host" concept was deliberately never added to the backend (IMPROVEMENT
// run_3a657ca6). So the panel DERIVES the where-it-lands label in-UI from the two
// KNOWN_TARGETS (ddd_distribution_policy.py) + visibility; it never fabricates a repo URL.
// SINGLE SOURCE OF TRUTH for the target→repo mapping. BOTH the per-target row label
// AND the guideline legend render from this — never restate the mapping in prose (a
// second copy would drift from this one; Gate-2 meta-review caught exactly that).
const TARGET_REPO_MAP: Record<string, { label: string; install: string }> = {
  'aim-capabilities': { label: 'internal AIM package', install: 'CR/PR → aim plugins install' },
  'open-plugin': { label: 'public code host', install: 'git push → install.sh' },
};
function repoForTarget(target: string): { label: string; install: string } {
  // Unknown target (vocab drift) → passthrough: show the raw name, no fabricated repo.
  return TARGET_REPO_MAP[target] ?? { label: target, install: '' };
}

function DistributeView(
  { name, onRequestClose, onDispatch }:
  { name: string; onRequestClose?: () => void; onDispatch?: (msg: string) => boolean },
) {
  // Cached query (run_cfb460ac): re-opening Distribute inside the 30s window is
  // served from cache — the old useEffect+then refetched on every mount.
  const { data = null, error: qErr } = useDistribution(name);
  const error = qErr ? String((qErr as { message?: string })?.message ?? qErr) : null;

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

  // [Distribute a brain] — item 3 (run_3d371424): the run is STILL human-in-the-loop
  // (s_ddd-distribute confirms targets + content-safety scan + emit≠publish). The
  // change is HOW it's triggered: instead of "copy → paste into a chat tab" (a clunky
  // clipboard round-trip), the panel-native path INJECTS + AUTO-SENDS the command via
  // the overlay `dispatchPrompt` bridge (onDispatch) — the same in-app trigger New
  // Brain / Jobs / Pipeline overlays already use (C042-correct reuse, no new channel).
  // The HITL gate is UNCHANGED: dispatchPrompt only lands the message in chat, where
  // s_ddd-distribute's confirm/scan/emit≠publish gates run. Falls back to clipboard
  // when onDispatch is absent (older webview / non-overlay mount).
  const distributeCmd = `distribute this ddd: ${name}`;
  const [copied, setCopied] = useState(false);
  // AC3 (run_8d2ec26c): collapsed-by-default guideline (reuses the graphOpen disclosure
  // pattern — own local state, not a shared name). Progressive disclosure: no noise until asked.
  const [guidelineOpen, setGuidelineOpen] = useState(false);
  const onDistribute = useCallback(() => {
    if (onDispatch) {
      // Inject + auto-send into the active chat tab, then close the overlay so the
      // user sees the chat pick up the HITL flow (close AFTER dispatch — the message
      // must be delivered first). dispatchPrompt returns false if it couldn't land
      // (no active tab) → fall through to clipboard so the action never dead-ends.
      const sent = onDispatch(distributeCmd);
      if (sent) { onRequestClose?.(); return; }
    }
    // Fallback: copy the command (older webview / no dispatch bridge / no active tab).
    // Guard BOTH the method (clipboard may be absent) AND the returned promise (?. on
    // .then) — Gate-2 HIGH: `clipboard?.writeText(x).then` still throws if clipboard
    // exists but writeText returns undefined.
    const p = navigator.clipboard?.writeText(distributeCmd);
    p?.then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
      () => {},
    );
  }, [distributeCmd, onDispatch, onRequestClose]);

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
                {/* AC1 (run_8d2ec26c): where it lands — derived read-only from target+visibility. */}
                <span className="text-[10px] text-[var(--color-text-muted)]" title={repoForTarget(t).install}>
                  → {repoForTarget(t).label}
                </span>
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
          {/* AC2 (run_8d2ec26c): [Edit aim.json] on the DISTRIBUTABLE branch too — reach
              is declared in aim.json (the SSOT); editing it is a deliberate owner action,
              so the panel offers a one-click deep-link to it (never an in-panel form).
              Distinct testid from the not-distributable [Open aim.json] (declare vs edit). */}
          <button
            onClick={openAimJson}
            data-testid="distribute-edit-aim"
            className="mb-4 flex items-center gap-1.5 text-[10px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-1 hover:bg-[#12233a]"
            title="Open aim.json in the Canvas to edit the declared reach (targets / visibility)"
          >
            <span className="material-symbols-outlined text-[13px]">edit</span>
            Edit aim.json
          </button>
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

      {/* Step 3 · run — [Distribute this brain]: item 3 (run_3d371424) sends the
          command straight into chat (HITL gate runs there); clipboard is the fallback.
          Still NOT auto-run — s_ddd-distribute confirms targets + content-safety scan
          + emit≠publish once the message lands. */}
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
            title={
              !data.distributable ? 'Declare a distribution block first'
                : onDispatch ? 'Send the distribute command to chat (runs s_ddd-distribute — you confirm targets there)'
                : 'Copy the chat command to run s_ddd-distribute'
            }
          >
            <span className="material-symbols-outlined text-[14px]">{onDispatch ? 'send' : 'content_copy'}</span>
            {copied ? 'Copied — paste into a chat tab'
              : onDispatch ? 'Distribute this brain →'
              : 'Distribute a brain'}
          </button>
          {data.distributable && (
            <span className="text-[10px] text-[var(--color-text-faint)] font-mono">→ {distributeCmd}</span>
          )}
        </div>
      </div>

      {/* AC3 (run_8d2ec26c): "How distribution works" — collapsed disclosure (reuses the
          BrainBrowse graphOpen pattern). Explains the 3-step HITL flow, git code-package
          management, emit≠publish, and the target→repo mapping legend. Collapsed by default
          so it never competes with the Step 2/3 primary flow (progressive disclosure). */}
      <div className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
        <button
          data-testid="distribute-guideline-toggle"
          aria-expanded={guidelineOpen}
          onClick={() => setGuidelineOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 text-[12px] hover:bg-[var(--color-hover)]"
        >
          <span className="material-symbols-outlined text-[15px] text-[#58a6ff]">help</span>
          <span className="font-medium">How distribution works</span>
          <span className="material-symbols-outlined text-[16px] text-[var(--color-text-faint)] ml-auto">
            {guidelineOpen ? 'expand_less' : 'expand_more'}
          </span>
        </button>
        {guidelineOpen && (
          <div className="border-t border-[var(--color-border)] px-3 py-2.5 text-[11px] leading-relaxed text-[var(--color-text-muted)] flex flex-col gap-2" data-testid="distribute-guideline-body">
            <div>
              <span className="font-semibold text-[var(--color-text)]">The 3-step flow (human-in-the-loop):</span>
              <div className="mt-0.5">Step 1 · declare a reach in <span className="font-mono">aim.json</span> (targets + visibility — the SSOT, the ceiling).
              Step 2 · confirm which declared target(s) to emit + check freshness.
              Step 3 · run — the command lands in chat, where <span className="font-mono">s_ddd-distribute</span> confirms the subset, runs a content-safety scan, and renders the code package.</div>
            </div>
            <div>
              <span className="font-semibold text-[var(--color-text)]">🎯 Target → where it lands (git code-package management):</span>
              {/* Rendered FROM TARGET_REPO_MAP — the SAME source the per-row label uses,
                  so the legend can never drift from the rows (Gate-2 cross-fix fix). */}
              <div className="mt-0.5 flex flex-col gap-0.5">
                {Object.entries(TARGET_REPO_MAP).map(([t, { label, install }]) => (
                  <div key={t}><span className="font-mono">{t}</span> → <b>{label}</b> (<span className="font-mono">{install}</span>).</div>
                ))}
              </div>
            </div>
            <div>
              <span className="font-semibold text-[var(--color-text)]">🔒 emit ≠ publish:</span> an <span className="font-mono">internal</span> DDD can EMIT a package for a private install, but public publish is refused until <span className="font-mono">visibility</span> is explicitly <span className="font-mono">external</span> — a deliberate owner edit in <span className="font-mono">aim.json</span>, never a UI toggle.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
