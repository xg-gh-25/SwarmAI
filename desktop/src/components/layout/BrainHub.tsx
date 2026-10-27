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
 *
 * Brain Hub (2026-10-25): the Gallery is the Hub home (BrainHubHome: a summary row
 * whose counts filter the cards, one Needs-you queue, one card per brain, and a
 * Conductor / Eval footer). A brain opens on a two-line header (icon, name as the
 * Brain switcher, status; description; More menu) and seven tabs that mirror the
 * parts of the Brain map: Overview (the map, Needs you, Eval summary) · Needs you ·
 * Brain (DDD) (Add knowledge, docs, files) · Project Agent (definition, Start a Run) ·
 * Conductor · Runs · Reflect · Evolution (the review queue) · Eval. Clicking a map
 * part opens its tab. The last tab, Distribute, ships the Brain out (an internal git
 * package or an external GitHub repo). `defaultView` picks the first
 * detail view (default 'overview').
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  useBrainsWithPinned, useBrainDetail, useReview, useDistribution,
  approveReview, rejectReviewHunk, approveProposal, rejectProposal, aggregateTypeCounts,
  brainRecall, useBrainSpecs, useBrainSpecDoc,
} from '../../services/ddd';
import type {
  BrainSummary, BrainDetail,
  ReviewData, ReviewHunk, PendingProposal, RecallHit, SpecDoc, SpecRepo,
} from '../../services/ddd';
import { DddCard, Ontology } from './DddCard';
import { CodeGraph } from '../code-intel/CodeGraph';
import { LibraryTree } from './LibraryTree';
import { docSignalMap, weeklyReportModel, type WeeklyReportModel } from './dddOverview';
import { OverlayError } from './OverlayState';
import { Hint } from '../overlay';
import {
  BrainMapView, BrainNeedsView, BrainAgentView, BrainRunsView, BrainEvalView, BrainAddKnowledge,
  type BrainTab, type MapPanel,
} from './BrainMapView';
import { BrainHubHome, Flower } from './BrainHubHome';
import { bornText, hubSummary } from './brainMapModel';
import { pipelinesService, type PipelineAnalytics } from '../../services/pipelines';
import { buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import { takeOverlayTarget } from '../../utils/overlayTarget';
import { copyToClipboard } from '../../utils/clipboard';

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

// ── Root — MASTER→DETAIL nav model ──────────────────────────────────────────────
//
//   • TOP LEVEL = just the Gallery (the Hub home). No greyed tabs.
//   • Selecting a brain enters the DETAIL shell: header + the seven tabs above.
//     Same tab set + order for every brain (no dynamic layout).
//
// `mode` is the top-level state: 'gallery' (list) or 'detail' (a brain is selected).

type Mode = 'gallery' | 'detail';
/** The detail views: the seven tabs (one per part of the Brain map, plus Overview)
 *  and Distribute, the last step of a Brain's lifecycle (ship it out). */
type DetailView = BrainTab | 'distribute';
const TABS: { key: DetailView; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'needs', label: 'Needs you' },
  { key: 'brain', label: 'Brain (DDD)' },
  { key: 'agent', label: 'Project Agent' },
  { key: 'runs', label: 'Conductor · Runs' },
  { key: 'reflect', label: 'Reflect · Evolution' },
  { key: 'eval', label: 'Eval' },
  { key: 'distribute', label: 'Distribute' },
];

/** `onRequestClose` — the host overlay's `ctx.close` (overlaySurfaces passes it).
 *  Approach A (run_a607f2b0): opening a DDD doc closes THIS overlay first, then
 *  dispatches `swarm:open-file` so the Canvas/FileViewer isn't rendered UNDER the
 *  host (the SwarmWS-explorer z-index precedent). OPTIONAL: tests / a non-overlay
 *  mount omit it — file-open then just dispatches without a close (still correct).
 *  `onDispatch` (item 3) — the overlay ctx `dispatchPrompt` bridge: injects + sends
 *  a chat prompt. OPTIONAL: without it, sends fall back to the clipboard. */
export function BrainHub(
  { onRequestClose, onDispatch, defaultView = 'overview' }:
  { onRequestClose?: () => void; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean; defaultView?: DetailView } = {},
) {
  const [mode, setMode] = useState<Mode>('gallery');
  const [selected, setSelected] = useState<string | null>(null);
  const [detailView, setDetailView] = useState<DetailView>(defaultView);
  // the Hub home's Add knowledge lands on Brain (DDD) with the add card open
  const [addOpen, setAddOpen] = useState(false);

  // Cached gallery list (run_cfb460ac): re-opening the overlay within the 30s window
  // is instant instead of paying the ~4s aggregate scan again. `refetch` powers Retry.
  const { data: bp, error: bpErr, refetch } = useBrainsWithPinned();
  const brains = bp?.brains ?? null;
  const pinned = useMemo(() => bp?.pinned ?? [], [bp]);
  const selfName = pinned[0] && brains?.some((b) => b.name === pinned[0]) ? pinned[0] : undefined;
  const error = bpErr ? String((bpErr as { message?: string })?.message ?? bpErr) : null;

  // Enter the detail shell on a specific brain, at a specific view.
  const openBrain = useCallback((name: string, view: DetailView = defaultView, add = false) => {
    setSelected(name);
    setDetailView(view);
    setAddOpen(add);
    setMode('detail');
  }, [defaultView]);
  const backToGallery = useCallback(() => { setMode('gallery'); }, []);
  /** The Hub home's targets ('map' + a panel, or 'review') → a tab. */
  const openFromHome = useCallback((n: string, t?: { view?: 'map' | 'review'; panel?: MapPanel }) => {
    if (t?.view === 'review') return openBrain(n, 'reflect');
    if (t?.panel === 'need') return openBrain(n, 'needs');
    if (t?.panel === 'agent') return openBrain(n, 'agent');
    if (t?.panel === 'add') return openBrain(n, 'brain', true);
    return openBrain(n);
  }, [openBrain]);

  // Deep-link: `swarm:show-brain-hub` MAY carry `detail.brain` to open a specific
  // brain's detail view directly. No detail → Gallery, the default.
  useEffect(() => {
    // the event that mounted us has already fired: a target recorded for it waits here
    const pending = takeOverlayTarget('brain-hub');
    if (pending) openBrain(pending);
    const onShow = (e: Event) => {
      const name = (e as CustomEvent<{ brain?: string }>).detail?.brain;
      takeOverlayTarget('brain-hub');
      if (name) openBrain(name);
    };
    window.addEventListener('swarm:show-brain-hub', onShow);
    return () => window.removeEventListener('swarm:show-brain-hub', onShow);
  }, [openBrain]);

  if (error) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
        <OverlayError
          testid="brainhub-error"
          retryTestid="brainhub-retry"
          message={<>Failed to load brains: {error}</>}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  // GALLERY (master) — the brain list is the ONLY top-level surface.
  if (mode === 'gallery' || !selected) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
        <div className="flex-1 overflow-auto">
          <Gallery brains={brains} selfName={selfName} onOpen={openFromHome} />
        </div>
      </div>
    );
  }

  // DETAIL — key={selected} remounts the shell on a brain switch so no view-local
  // state (tree root, graph, review armed-state) leaks across brains.
  return (
    <BrainDetailShell
      key={selected}
      name={selected}
      brains={brains}
      selfName={selfName}
      detailView={detailView}
      onSelectView={setDetailView}
      onSwitchBrain={(n) => openBrain(n, detailView)}
      onBack={backToGallery}
      onRequestClose={onRequestClose}
      onDispatch={onDispatch}
      addOpen={addOpen}
    />
  );
}

/** The detail shell: a two-line header (icon, name as the Brain switcher, status;
 *  the description under it; More menu on the right), the seven tabs, then the active
 *  tab's body. Actions live in the tab they belong to (Search and Add knowledge in
 *  Brain (DDD), Start a Run in Project Agent). */
function BrainDetailShell(
  { name, brains, selfName, detailView, onSelectView, onSwitchBrain, onBack, onRequestClose, onDispatch, addOpen }:
  {
    name: string; brains: BrainSummary[] | null; selfName?: string;
    detailView: DetailView; onSelectView: (v: DetailView) => void;
    onSwitchBrain: (n: string) => void; onBack: () => void;
    onRequestClose?: () => void; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
    addOpen?: boolean;
  },
) {
  const summary = brains?.find((b) => b.name === name);
  const { data: analytics = null } = useQuery<PipelineAnalytics>({
    queryKey: ['pipeline-analytics', '30d'], queryFn: () => pipelinesService.fetchAnalytics('30d'), staleTime: 30_000,
  });
  // same count as the map's You: each Run waiting, each proposal, a Project Agent draft
  const needs = summary ? hubSummary([summary], analytics).needsYou : 0;
  const pending = summary?.health.pending ?? 0;

  // close THIS overlay BEFORE dispatching swarm:open-file so the Canvas isn't
  // rendered under the host (the swarmws z-index precedent)
  const shellOpenFile = useCallback((workspaceRelPath: string) => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: workspaceRelPath } }));
  }, [onRequestClose]);
  const sendResume = () => {
    const ok = onDispatch ? onDispatch(buildResumeSetupPrompt(name), { autoSend: true }) : false;
    if (ok) onRequestClose?.();
  };
  const goTab = (t: BrainTab) => onSelectView(t);

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
      <BrainHeader name={name} summary={summary} brains={brains ?? []} isSelf={name === selfName} needs={needs}
        onBack={onBack} onSwitch={onSwitchBrain}
        onOpenFile={shellOpenFile}
        onResume={summary?.setupState === 'setting-up' ? sendResume : undefined} />

      <div className="flex items-center gap-5 px-4 border-b border-[var(--color-border)] flex-shrink-0" role="tablist" data-testid="brainhub-detail-nav">
        {TABS.map((t) => {
          const on = detailView === t.key;
          const count = t.key === 'needs' ? needs : t.key === 'reflect' ? pending : 0;
          return (
            <button key={t.key} role="tab" aria-selected={on} data-testid={`brainhub-tab-${t.key}`}
              onClick={() => onSelectView(t.key)}
              className={`py-2 -mb-px border-b-2 text-body whitespace-nowrap ${on
                ? 'border-[var(--color-primary)] text-[var(--color-text)] font-semibold'
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
              {t.label}
              {count > 0 && <span className="ml-1.5 text-meta font-semibold text-[var(--color-status-error)]"
                data-testid={t.key === 'reflect' ? 'review-tab-badge' : `brainhub-tab-count-${t.key}`}>{count}</span>}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-auto">
        {detailView === 'overview' && (
          <BrainMapView name={name} onGoTo={goTab} onDispatch={onDispatch} onRequestClose={onRequestClose} />
        )}
        {detailView === 'needs' && <BrainNeedsView name={name} onGoTo={goTab} />}
        {detailView === 'brain' && (
          <BrainView name={name} onRequestClose={onRequestClose} onDispatch={onDispatch} addOpen={addOpen} />
        )}
        {detailView === 'agent' && <BrainAgentView name={name} onDispatch={onDispatch} onRequestClose={onRequestClose} />}
        {detailView === 'runs' && <BrainRunsView name={name} />}
        {detailView === 'reflect' && <ReviewView name={name} onRequestClose={onRequestClose} />}
        {detailView === 'eval' && <BrainEvalView name={name} />}
        {detailView === 'distribute' && <DistributeView name={name} onRequestClose={onRequestClose} onDispatch={onDispatch} />}
      </div>
    </div>
  );
}

/** Header: icon + name (the Brain switcher) + status on one line, the description
 *  under it, and the More menu (Continue setup, Edit aim.json). */
function BrainHeader({ name, summary, brains, isSelf, needs, onBack, onSwitch, onOpenFile, onResume }: {
  name: string; summary?: BrainSummary; brains: BrainSummary[]; isSelf: boolean; needs: number;
  onBack: () => void; onSwitch: (n: string) => void;
  onOpenFile: (p: string) => void; onResume?: () => void;
}) {
  const [menu, setMenu] = useState<'none' | 'switch' | 'more'>('none');
  const close = () => setMenu('none');
  const born = summary?.setupState === 'setting-up';
  const last = summary?.health.lastChangeRelative;
  return (
    <div className="flex items-start gap-3 px-4 pt-3 pb-2 flex-shrink-0" data-testid="brainhub-breadcrumb">
      {summary ? <Flower b={summary} size={44} /> : <span className="w-11" />}
      <div className="flex-1 min-w-0">
        <button data-testid="brainhub-back" onClick={onBack}
          className="text-meta text-[var(--color-text-muted)] hover:text-[var(--color-text)]">‹ All brains</button>
        <div className="flex items-center gap-2 relative">
          <button data-testid="brainhub-switcher" aria-haspopup="menu" aria-expanded={menu === 'switch'}
            onClick={() => setMenu(menu === 'switch' ? 'none' : 'switch')}
            className="flex items-center gap-1 text-title font-semibold hover:text-[var(--color-primary)]">
            {name}<span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">expand_more</span>
          </button>
          {isSelf && <span className="text-meta font-semibold px-1.5 rounded-full text-[var(--color-status-success)] bg-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)]">SELF</span>}
          <span className="flex items-center gap-1.5 text-small text-[var(--color-text-muted)]" data-testid="brainhub-status">
            <span className={`w-2 h-2 rounded-full ${born ? 'bg-[var(--color-status-warning)]' : 'bg-[var(--color-status-success)]'}`} />
            {born ? bornText(summary?.setupProgress) : 'Ready'}
            {last && last !== '--' && <span>· last landed {last}</span>}
            {needs > 0 && <span className="text-[var(--color-status-error)]">· {needs} need{needs === 1 ? 's' : ''} you</span>}
          </span>
          {menu === 'switch' && (
            <>
              <div className="fixed inset-0 z-[9]" onClick={close} aria-hidden="true" />
              <div role="menu" className="absolute left-0 top-full mt-1 z-10 w-64 max-h-80 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] py-1">
                {brains.map((b) => (
                  <button key={b.name} role="menuitem" data-testid={`brainhub-switch-${b.name}`}
                    onClick={() => { close(); if (b.name !== name) onSwitch(b.name); }}
                    className={`w-full text-left px-3 py-1.5 text-body hover:bg-[var(--color-hover)] ${b.name === name ? 'font-semibold' : ''}`}>{b.name}</button>
                ))}
              </div>
            </>
          )}
        </div>
        <div className={`text-small ${summary?.description ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-faint)]'}`} data-testid="brainhub-desc">
          {summary?.description || 'No description yet. Add one line to aim.json.'}
        </div>
      </div>
      <div className="relative">
        <button className="icon-btn" aria-label="More" aria-haspopup="menu" aria-expanded={menu === 'more'} data-testid="brainhub-more"
          onClick={() => setMenu(menu === 'more' ? 'none' : 'more')}>
          <span className="material-symbols-outlined icon-sm">more_horiz</span>
        </button>
        {menu === 'more' && (
          <>
            <div className="fixed inset-0 z-[9]" onClick={close} aria-hidden="true" />
            <div role="menu" className="absolute right-0 top-full mt-1 z-10 w-56 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] py-1">
              {onResume && <MenuItem testid="brainhub-more-resume" onClick={() => { close(); onResume(); }}>Continue setup in chat</MenuItem>}
              <MenuItem testid="brainhub-more-aim" onClick={() => { close(); onOpenFile(`Projects/${name}/aim.json`); }}>Edit aim.json</MenuItem>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function MenuItem({ onClick, testid, children }: { onClick: () => void; testid: string; children: React.ReactNode }) {
  return (
    <button role="menuitem" data-testid={testid} onClick={onClick}
      className="w-full text-left px-3 py-1.5 text-body hover:bg-[var(--color-hover)]">{children}</button>
  );
}

/**
 * BrainSearchBox (run_d0cd4414, AC7) — search what this Brain knows (its DDD docs,
 * Knowledge notes and memory), at the top of the Brain (DDD) tab: next to what it
 * searches. Wired to GET /api/ddd/brains/{name}/recall via brainRecall(). Owns its query/
 * hits state + a debounce so it does NOT fetch per keystroke. The results drop down
 * under the box only when q is non-empty AND hits exist. Clicking a hit calls
 * onOpenFile (the shell's close-then-open primitive).
 */
function BrainSearchBox({ name, onOpenFile }: { name: string; onOpenFile: (p: string) => void }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<RecallHit[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setQ(''); setHits([]); }, [name]);

  // Debounced recall: ≥2 non-blank chars and 350ms (recall_all is uncached per call),
  // with a stale-response guard so a slow older answer never overwrites a newer one.
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
    <div className="relative" data-testid="brainhub-search-row">
      <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 h-9 focus-within:border-[var(--color-primary)]">
        <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)]">search</span>
        <input
          data-testid="brainhub-search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Search what ${name} knows: its docs, notes and decisions`}
          className="flex-1 min-w-0 bg-transparent text-body text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none"
        />
        {q && (
          <button data-testid="brainhub-search-clear" onClick={() => setQ('')} aria-label="Clear search"
            className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]">
            <span className="material-symbols-outlined icon-sm">close</span>
          </button>
        )}
      </div>
      {showPanel && (
        <div className="absolute left-0 right-0 top-full mt-1 z-10 flex flex-col gap-1 max-h-[50vh] overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-1.5"
          data-testid="brainhub-search-results">
          {hits.map((h, i) => (
            <button
              key={`${h.source}-${i}`}
              data-testid="brainhub-search-hit"
              onClick={() => onOpenFile(h.source)}
              className="text-left rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 py-1.5 hover:border-[var(--color-primary)]"
            >
              <div className="flex items-baseline gap-2">
                <span className="text-small text-[var(--color-text)] truncate">{h.title || h.source}</span>
                <span className="ml-auto text-meta tabular-nums text-[var(--color-text-faint)] flex-shrink-0">{h.domain}</span>
              </div>
              {h.source && <div className="text-meta font-mono text-[var(--color-text-faint)] truncate">{h.source}</div>}
              {h.content && <div className="text-meta text-[var(--color-text-muted)] line-clamp-2 mt-0.5">{h.content}</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Gallery ──────────────────────────────────────────────────────────────────

/**
 * Brain Hub home (2026-10-25 redesign): summary row + one queue + one card per Brain,
 * in BrainHubHome. SwarmAI is marked as the self Brain via `pinned[0]` (backend
 * get_pinned_projects, existence-guarded); pinned empty (old daemon) → no marker.
 */
function Gallery(
  { brains, selfName, onOpen }:
  { brains: BrainSummary[] | null; selfName?: string;
    onOpen: (n: string, target?: { view?: 'map' | 'review'; panel?: MapPanel }) => void },
) {
  if (brains === null) return <div className="p-4 text-[var(--color-text-muted)] text-body">Loading brains</div>;
  if (brains.length === 0) return <div className="p-4 text-[var(--color-text-muted)] text-body">No DDD brains found.</div>;
  return <BrainHubHome brains={brains} selfName={selfName} onOpen={onOpen} />;
}


// ── Brain (DDD) tab ──────────────────────────────────────────────────────────
//
// What the Brain knows, top to bottom:
//   Add knowledge — the primary action of this tab (a card that opens in place).
//   §① Ontology (3-layer×7-type) — the existing DddCard density=full IS the ontology.
//   §② Knowledge upkeep — sinking and reclaimable entries.
//   §③ 4 core-doc cards (PRODUCT/TECH/IMPROVEMENT/PROJECT) + [Weekly Report].
//   Files — the real Projects/<name> file tree + Code Graph.

function BrainView(
  { name, onRequestClose, onDispatch, addOpen = false }:
  { name: string; onRequestClose?: () => void;
    onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean; addOpen?: boolean },
) {
  const [showWeekly, setShowWeekly] = useState(false);
  const [adding, setAdding] = useState(addOpen);

  // Cached fetches (run_cfb460ac): detail drives the view; review is best-effort (§③
  // signals + Weekly): a review error must NOT blank the tab.
  const { data: detail = null, error: detailErr } = useBrainDetail(name);
  const { data: review = null } = useReview(name);
  const error = detailErr ? String((detailErr as { message?: string })?.message ?? detailErr) : null;

  useEffect(() => { setShowWeekly(false); }, [name]);

  // Open a doc/tree file in the app-level CANVAS (workspace-relative paths). Close
  // THIS overlay BEFORE the dispatch so the FileViewer is never rendered under it.
  const openFile = useCallback((workspaceRelPath: string) => {
    onRequestClose?.();
    document.dispatchEvent(new CustomEvent('swarm:open-file', {
      detail: { path: workspaceRelPath },
    }));
  }, [onRequestClose]);

  if (error) return <div className="p-4 text-[var(--color-status-error)] text-body">Failed to load brain: {error}</div>;
  if (!detail) return <div className="p-4 text-[var(--color-text-muted)] text-body">Loading {name}</div>;

  const hasCodeIntel = detail.hasCodeIntel === true;   // daemon-skew: undefined → false
  const hasSpecs = detail.hasSpecs === true;           // daemon-skew: undefined → false

  return (
    <div className="flex flex-col pt-3" data-testid="brainhub-brain">
      <div className="px-4 pb-3 flex flex-col gap-2">
        <BrainSearchBox name={name} onOpenFile={openFile} />
        {adding ? (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-small font-semibold">Add knowledge</span>
              <button className="btn-sm btn-ghost" onClick={() => setAdding(false)} data-testid="bm-add-close">Close</button>
            </div>
            <BrainAddKnowledge name={name} onDispatch={onDispatch} onRequestClose={onRequestClose} />
          </div>
        ) : (
          <div className="flex items-center gap-3 rounded-lg border border-dashed border-[var(--color-border)] px-3 py-2">
            <span className="text-small text-[var(--color-text-muted)] flex-1">Teach this Brain from files, folders, links or another repo. The agent distills what is worth keeping.</span>
            <button className="btn-sm btn-primary" onClick={() => setAdding(true)} data-testid="bm-add-open">
              <span className="material-symbols-outlined icon-xs">add</span> Add knowledge
            </button>
          </div>
        )}
      </div>
      <BrainOverview
        detail={detail}
        review={review}
        onOpenFile={openFile}
        showWeekly={showWeekly}
        onToggleWeekly={() => setShowWeekly((v) => !v)}
      />
      <div className="px-4 pt-1 pb-1.5 text-small font-semibold">Files</div>
      <BrainBrowse name={name} hasCodeIntel={hasCodeIntel} hasSpecs={hasSpecs} onOpenFile={openFile} />
    </div>
  );
}

// ── §Overview — fixed order: ① Ontology → ② Need-You → ③ 4 core-doc cards ───────

function BrainOverview(
  { detail, review, onOpenFile, showWeekly, onToggleWeekly }:
  {
    detail: BrainDetail; review: ReviewData | null; onOpenFile: (p: string) => void;
    showWeekly: boolean; onToggleWeekly: () => void;
  },
) {
  const knowledge = detail.sections.find((s) => s.key === 'knowledge');
  const members = knowledge?.members ?? [];
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
          <div className="text-meta text-[var(--color-text-faint)] rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2">
            Knowledge ontology not yet computed for this brain.
          </div>
        )}
      </div>

      {/* §② Knowledge upkeep — sinking and reclaimable entries. What waits on the
          person (Runs, proposals) lives in the Needs you tab, not here. */}
      <UpkeepBlock
        reclaimable={detail.health?.noise?.reclaimable ?? 0}
        sinking={detail.health?.sinking ?? 0}
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

/** §② Knowledge upkeep: entries sinking toward the archive and reclaimable noise. */
function UpkeepBlock({ reclaimable = 0, sinking = 0 }: { reclaimable?: number; sinking?: number }) {
  const hasWork = reclaimable > 0 || sinking > 0;
  return (
    <div data-testid="brainhub-upkeep" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5">
      <div className="text-small font-semibold mb-1">Knowledge upkeep</div>
      {!hasWork ? (
        <div data-testid="upkeep-empty" className="text-meta text-[var(--color-text-muted)]">Nothing to tidy. Every entry is in use.</div>
      ) : (
        <div className="flex flex-col gap-1">
          {sinking > 0 && (
            <div data-testid="upkeep-sinking" className="flex items-center gap-2 text-small">
              <span className="font-semibold text-[var(--color-status-warning)] min-w-[28px]">{sinking}</span>
              <span className="text-[var(--color-text-muted)]">entr{sinking === 1 ? 'y is' : 'ies are'} sinking: not used for a while, on the way to the archive</span>
            </div>
          )}
          {reclaimable > 0 && (
            <div data-testid="upkeep-reclaimable" className="flex items-center gap-2 text-small">
              <span className="font-semibold text-[var(--color-status-warning)] min-w-[28px]">{reclaimable}</span>
              <span className="text-[var(--color-text-muted)]">can be reclaimed as noise (ask the agent to run reclaim)</span>
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
        <span className="text-meta uppercase tracking-wide font-semibold text-[var(--color-text-faint)]">
          Judgment core · review periodically
        </span>
        <span className="text-meta text-[var(--color-text-muted)]" data-testid="coredocs-agg">
          {hasSignal ? `since last review · ${totalNew} new · ${totalPending} pending` : 'up to date since last review'}
        </span>
        <button
          data-testid="coredocs-weekly"
          onClick={onToggleWeekly}
          className="btn-sm btn-soft ml-auto"
        >
          <span className="material-symbols-outlined icon-xs">summarize</span>
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
              className={`text-left rounded-lg bg-[var(--color-card)] p-2.5 transition-colors hover:border-[var(--color-border)] ${
                marked ? 'border-l-[3px] border-l-[var(--color-status-warning)] border-y border-r border-[color-mix(in_srgb,var(--color-status-warning)_30%,transparent)]' : 'border border-[var(--color-border)]'
              }`}
            >
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="material-symbols-outlined icon-xs text-[var(--color-text-muted)]">description</span>
                <span className="text-small font-semibold font-mono">{docName}</span>
                {marked && <span data-testid={`coredoc-mark-${docName}`} className="ml-auto w-1.5 h-1.5 rounded-full bg-[var(--color-status-warning)]" />}
              </div>
              <div className="text-meta text-[var(--color-text-muted)] mb-1">{DOC_ROLE[docName] ?? 'DDD document'}</div>
              <div className="flex items-center gap-2 text-meta text-[var(--color-text-faint)]">
                {sig.newCount > 0 && <span className="text-[var(--color-status-success)]">{sig.newCount} new</span>}
                {sig.pendingCount > 0 && <span className="text-[var(--color-status-warning)]">{sig.pendingCount} pending</span>}
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
    <div data-testid="brainhub-weekly-panel" className="rounded-lg border border-[color-mix(in_srgb,var(--color-primary)_30%,transparent)] bg-[color-mix(in_srgb,var(--color-primary)_6%,transparent)] p-3 mb-2 text-meta">
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-[var(--color-primary)]">
        <span className="material-symbols-outlined icon-sm">summarize</span>
        {projectName} · weekly review {weekly.sinceSha && <span className="text-meta font-mono text-[var(--color-text-faint)]">since {weekly.sinceSha}</span>}
      </div>
      <div className="flex flex-col gap-1 text-[var(--color-text-muted)]">
        <div><span className="text-[var(--color-status-success)] font-semibold">{weekly.autoApplied}</span> auto-cultivated change{weekly.autoApplied === 1 ? '' : 's'} since last review</div>
        <div><span className="text-[var(--color-status-warning)] font-semibold">{weekly.pending}</span> proposal{weekly.pending === 1 ? '' : 's'} awaiting your decision</div>
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
  { name, hasCodeIntel, hasSpecs, onOpenFile }:
  { name: string; hasCodeIntel: boolean; hasSpecs: boolean; onOpenFile: (p: string) => void },
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
        <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--color-border)] text-meta text-[var(--color-text-muted)] flex-shrink-0">
          <span className="material-symbols-outlined icon-xs text-[var(--color-primary)]">folder_open</span>
          <span>Files · Projects/{name}</span>
        </div>
        <LibraryTree
          key={`tree-${name}`}
          rootPath={`Projects/${name}`}
          onFileOpen={onOpenFile}
          hugContent
        />
      </div>

      {hasSpecs && <RepoDocuments name={name} />}

      {/* Code Graph — collapsed disclosure BELOW the tree. Only rendered when a
          code_intel.db exists for this brain; only MOUNTED (→ fetched) on expand. */}
      {hasCodeIntel && (
        <div className="mt-3 flex-shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden" style={{ maxWidth: 640 }} data-testid="brainhub-codegraph-disclosure">
          <button
            data-testid="codegraph-toggle"
            aria-expanded={graphOpen}
            onClick={() => setGraphOpen((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-small hover:bg-[var(--color-hover)]"
          >
            <span className="material-symbols-outlined icon-sm text-[var(--color-primary)]">hub</span>
            <span className="">Code Graph</span>
            {!graphOpen && <span className="text-meta text-[var(--color-text-faint)]">· expand to load the dependency graph</span>}
            <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)] ml-auto">
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


// ── Repo documents (design 2026-10-27 §5.3) ──────────────────────────────────
// The decision this answers: "which of the repo's own documents do I read to
// understand it?" So tier 1 (read first) is the open list; design records,
// conventions, API contracts and the rest wait behind their own disclosure. A
// collapsed disclosure like Code Graph: nothing is fetched until it is opened.

const SPEC_GROUPS: { tier: SpecDoc['tier']; label: string }[] = [
  { tier: 2, label: 'Design records' },
  { tier: 3, label: 'Conventions and changes' },
  { tier: 4, label: 'Other documents' },
];

function RepoDocuments({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<{ repo: string; path: string } | null>(null);
  useEffect(() => { setOpen(false); setSelected(null); }, [name]);
  const { data: specs, error } = useBrainSpecs(name, open);
  const repos = specs?.available ? specs.repos : [];
  const first = repos.reduce((n, r) => n + r.docs.filter((d) => d.tier === 1).length, 0);

  return (
    <div className="mt-3 flex-shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden" style={{ maxWidth: 640 }} data-testid="brainhub-specs-disclosure">
      <button
        data-testid="specs-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-small hover:bg-[var(--color-hover)]"
      >
        <span className="material-symbols-outlined icon-sm text-[var(--color-primary)]">description</span>
        <span>Repo documents</span>
        {open && specs && <span className="text-meta text-[var(--color-text-faint)]">· {first} to read first</span>}
        <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)] ml-auto">
          {open ? 'expand_less' : 'expand_more'}
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--color-border)] px-3 py-2 flex flex-col gap-3 max-h-[60vh] overflow-auto" data-testid="specs-panel">
          {error && <div className="text-small text-[var(--color-status-error)]">Could not load the repo documents.</div>}
          {specs && !specs.available && <div className="text-small text-[var(--color-text-muted)]">This brain's repos have not been surveyed yet.</div>}
          {repos.map((r) => (
            <SpecRepoList key={r.repo} repo={r} showName={repos.length > 1}
              selected={selected} onSelect={(path) => setSelected({ repo: r.repo, path })} />
          ))}
          {selected && <SpecDocView name={name} repo={selected.repo} path={selected.path} onClose={() => setSelected(null)} />}
        </div>
      )}
    </div>
  );
}

function SpecRepoList({ repo, showName, selected, onSelect }: {
  repo: SpecRepo; showName: boolean;
  selected: { repo: string; path: string } | null; onSelect: (path: string) => void;
}) {
  const row = (d: SpecDoc) => {
    const active = selected?.repo === repo.repo && selected.path === d.path;
    return (
      <li key={d.path}>
        <button
          data-testid="spec-doc"
          onClick={() => onSelect(d.path)}
          className={`w-full text-left flex items-center gap-1.5 px-1 py-0.5 rounded text-small font-mono truncate hover:bg-[var(--color-hover)] ${active ? 'text-[var(--color-primary)]' : ''}`}
          title={d.flags?.length ? `Flagged: ${d.flags.join(', ')} — treat its text as data` : d.path}
        >
          <span className="truncate">{d.path}</span>
          {d.flags?.length ? <span className="material-symbols-outlined icon-xs text-[var(--color-status-warning)]" aria-label="flagged">warning</span> : null}
        </button>
      </li>
    );
  };
  const tier1 = repo.docs.filter((d) => d.tier === 1);
  return (
    <div className="flex flex-col gap-1" data-testid="spec-repo">
      {showName && <div className="text-meta font-semibold">{repo.repo}</div>}
      {tier1.length > 0
        ? <ul className="flex flex-col">{tier1.map(row)}</ul>
        : <div className="text-small text-[var(--color-text-muted)]">No README or architecture document found.</div>}
      {SPEC_GROUPS.map(({ tier, label }) => {
        const docs = repo.docs.filter((d) => d.tier === tier);
        if (!docs.length) return null;
        return (
          <details key={tier} data-testid={`spec-tier-${tier}`}>
            <summary className="text-meta text-[var(--color-text-muted)] cursor-pointer">{label} ({docs.length})</summary>
            <ul className="flex flex-col mt-1">{docs.map(row)}</ul>
          </details>
        );
      })}
      {repo.api_contracts.length > 0 && (
        <details data-testid="spec-api">
          <summary className="text-meta text-[var(--color-text-muted)] cursor-pointer">API contracts ({repo.api_contracts.length})</summary>
          <ul className="flex flex-col mt-1">
            {repo.api_contracts.map((a) => (
              <li key={a.path}>
                <button onClick={() => onSelect(a.path)} className="w-full text-left px-1 py-0.5 rounded text-small font-mono truncate hover:bg-[var(--color-hover)]">
                  {a.path} <span className="text-[var(--color-text-faint)] font-sans">· {a.format}, {a.items}</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function SpecDocView({ name, repo, path, onClose }: { name: string; repo: string; path: string; onClose: () => void }) {
  const { data: text, error } = useBrainSpecDoc(name, repo, path);
  return (
    <div className="border-t border-[var(--color-border)] pt-2" data-testid="spec-doc-view">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-small font-mono truncate flex-1">{path}</span>
        <button className="btn-sm btn-ghost" onClick={onClose} data-testid="spec-doc-close">Close</button>
      </div>
      <div className="text-meta text-[var(--color-text-faint)] mb-1">Repository content, shown as plain text.</div>
      {text === undefined && !error && <div className="text-small text-[var(--color-text-muted)]">Loading</div>}
      {error && <div className="text-small text-[var(--color-status-error)]">Could not open this document.</div>}
      {text !== undefined && (
        <pre className="text-small whitespace-pre-wrap break-words max-h-[40vh] overflow-auto">{text}</pre>
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

  // Open a hunk's file in the Canvas (Run 2). WARNING Gate-1: hunk.file is ALREADY
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

  if (error) return <div className="p-4 text-[var(--color-status-error)] text-body" data-testid="review-error">Failed to load review: {error}</div>;
  if (!data) return <div className="p-4 text-[var(--color-text-muted)] text-body">Loading review…</div>;

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
      <div className="mb-3"><Hint>After every Run, Reflect writes what it learned. Facts go straight into the Brain and can be undone here; a change to its rules waits for your OK.</Hint></div>
      {/* diff header — plain language first, git internals folded */}
      <div className="mb-3" data-testid="review-diff-header">
        <div className="flex items-center gap-2 text-body text-[var(--color-text)]">
          <span className="material-symbols-outlined icon-sm text-[var(--color-primary)]">rate_review</span>
          <span>
            {changeCount === 0
              ? 'No changes since your last review'
              : <>{changeCount} change{changeCount === 1 ? '' : 's'} since your last review
                  {pendingCount > 0 && <> · <span className="text-[var(--color-status-warning)]">{pendingCount} awaiting your approval</span></>}</>}
          </span>
          <button
            onClick={onApproveAll}
            // F8: NEVER advance the watermark when the diff is incomplete (timed out) —
            // the empty/partial hunk list would silently mark unreviewed work as seen.
            disabled={busy || data.hunks.length === 0 || data.diff_incomplete}
            data-testid="review-approve-all"
            className={`ml-auto btn-sm disabled:opacity-40 ${armed ? 'btn-warning' : 'btn-success'}`}
            title={armed ? 'This clears the review queue and records everything as seen — click again to confirm' : 'Records all changes below as reviewed'}
          >
            <span className="material-symbols-outlined icon-xs">{armed ? 'warning' : 'visibility'}</span>
            {armed ? 'Click again to confirm' : 'Mark all seen'}
          </button>
        </div>
        {/* engineer detail — folded, muted; kept for power users */}
        <details className="mt-1">
          <summary className="text-meta text-[var(--color-text-muted)] cursor-pointer select-none opacity-70 hover:opacity-100">
            git detail
          </summary>
          <div className="mt-1 text-meta font-mono text-[var(--color-text-muted)]">
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
        <div className="flex items-center gap-1.5 mb-3 text-meta text-[var(--color-status-warning)] bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)] border border-[color-mix(in_srgb,var(--color-status-warning)_32%,transparent)] rounded-md px-2 py-1" data-testid="review-diff-incomplete">
          <span className="material-symbols-outlined icon-xs">warning</span>
          The review diff timed out — this queue may be incomplete. "Mark all seen" is disabled to avoid skipping unreviewed changes.
          <button onClick={() => load()} disabled={busy} data-testid="review-diff-retry"
            className="btn-sm btn-warning ml-auto disabled:opacity-40">
            <span className="material-symbols-outlined icon-xs">refresh</span>Retry
          </button>
        </div>
      )}

      {/* F3: transient per-action error (reject/proposal 409 etc.) — inline, does NOT
          blank the whole queue (Gate-2). Dismissible; also cleared on next load(). */}
      {actionError && (
        <div className="flex items-center gap-1.5 mb-3 text-meta text-[var(--color-status-error)] bg-[color-mix(in_srgb,var(--color-status-error)_10%,transparent)] border border-[color-mix(in_srgb,var(--color-status-error)_30%,transparent)] rounded-md px-2 py-1" data-testid="review-action-error">
          <span className="material-symbols-outlined icon-xs">error</span>
          Action failed: {actionError}
          <button onClick={() => setActionError(null)} data-testid="review-action-error-dismiss"
            className="ml-auto text-meta text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline">dismiss</button>
        </div>
      )}

      {/* Zone C — pending risky proposals (the ONLY true Approve/Reject gate) */}
      <ReviewZone
        testid="review-zone-c" title="Waiting for your OK"
        desc="Changes to how this Brain judges (its rules). Nothing is written until you approve."
        color="var(--color-status-warning)"
      >
        {data.proposals.length === 0 && riskyHunks.length === 0
          ? <ZoneEmpty text="No rule changes waiting for you." />
          : data.proposals.map((p) => (
            <div key={p.id} className="rounded-md border border-dashed border-[color-mix(in_srgb,var(--color-status-warning)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_6%,transparent)] p-2 mb-1.5" data-testid="review-proposal">
              <div className="flex items-center gap-1.5 mb-1 text-meta">
                <span className="font-mono text-[var(--color-status-warning)]">{p.target_doc}</span>
                <span className="text-[var(--color-text-faint)]">· {p.target_section}</span>
                {/* F4: confidence is the human-gate decision signal — render it
                    (null-guarded: an un-scored proposal shows "—", never "null"). */}
                <span className="text-meta text-[var(--color-text-muted)]" data-testid="proposal-confidence" title="cultivation confidence">
                  conf {p.confidence != null ? p.confidence.toFixed(2) : '—'}
                </span>
                <div className="ml-auto flex gap-1">
                  <button onClick={() => onProposal(p, true)} disabled={busy}
                    className="btn-sm btn-success disabled:opacity-40">Approve</button>
                  <button onClick={() => onProposal(p, false)} disabled={busy}
                    className="btn-sm btn-danger disabled:opacity-40">Reject</button>
                </div>
              </div>
              <div className="text-meta text-[var(--color-text-muted)] line-clamp-2">{p.content}</div>
            </div>
          ))}
      </ReviewZone>

      {/* Zone A — auto-cultivated (already committed) */}
      <ReviewZone
        testid="review-zone-a" title="Written automatically"
        desc="Facts Reflect added from Runs. They are already in the Brain; Reject undoes one."
        color="var(--color-primary)"
      >
        {zoneA.length === 0 ? <ZoneEmpty text="Nothing written automatically since your last review." />
          : zoneA.map((h) => (
            <HunkCard key={h.signature} hunk={h} busy={busy}
              onReject={() => onRejectHunk(h)}
              onOpenFile={() => openHunkFile(h.file)} />
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
        <span className="text-small font-semibold" style={{ color }}>{title}</span>
        <span className="text-meta text-[var(--color-text-faint)]">{desc}</span>
      </div>
      {children}
    </div>
  );
}

function ZoneEmpty({ text }: { text: string }) {
  return <div className="text-meta text-[var(--color-text-faint)] italic px-1 py-1">{text}</div>;
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
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-code-bg)] mb-1.5 overflow-hidden" data-testid="review-hunk">
      {/* Plain-language summary line (AC1) — always visible. Primary derivation is
          file + counted +/- + line-range (always reliable); section is best-effort
          (empty for .md — Gate-1), shown only when git gave a non-empty heading. */}
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[var(--color-border)]" data-testid={`hunk-summary-${sig}`}>
        <span className="font-mono text-meta text-[var(--color-text)]">{fileName}</span>
        <span className="text-meta tabular-nums">
          <span className="text-[var(--color-status-success)]">+{s.adds}</span>
          <span className="text-[var(--color-text-faint)]"> / </span>
          <span className="text-[var(--color-status-error)]">-{s.dels}</span>
        </span>
        {s.startLine != null && (
          <span className="text-meta text-[var(--color-text-faint)]">around line {s.startLine}</span>
        )}
        {s.section && (
          <span className="text-meta text-[var(--color-text-faint)] truncate italic">· {s.section}</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setShowDiff((v) => !v)}
            data-testid={`hunk-toggle-diff-${sig}`}
            className="btn-sm btn-secondary"
          >
            <span className="material-symbols-outlined icon-xs">{showDiff ? 'expand_less' : 'code'}</span>
            {showDiff ? 'Hide diff' : 'View diff'}
          </button>
          <button
            onClick={onOpenFile}
            data-testid={`hunk-open-file-${sig}`}
            className="btn-sm btn-soft"
            title="Open this file in the Canvas"
          >
            <span className="material-symbols-outlined icon-xs">open_in_new</span>
            Open file
          </button>
          <button
            onClick={onReject}
            disabled={busy}
            data-testid="review-reject-hunk"
            className="btn-sm btn-danger disabled:opacity-40"
          >
            <span className="material-symbols-outlined icon-xs">undo</span>
            Revert hunk
          </button>
        </div>
      </div>
      {showDiff && (
        <pre className="text-meta font-mono leading-relaxed px-2 py-1 overflow-x-auto max-h-40" data-testid={`hunk-diff-${sig}`}>
          {hunk.diff_text.split('\n').slice(0, 20).map((ln, i) => {
            const c = ln.startsWith('+') && !ln.startsWith('+++') ? 'var(--color-status-success)'
              : ln.startsWith('-') && !ln.startsWith('---') ? 'var(--color-status-error)'
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
  'aim-capabilities': { label: 'internal git package', install: 'CR → aim plugins install' },
  'open-plugin': { label: 'external GitHub repo', install: 'git push → install.sh' },
};
function repoForTarget(target: string): { label: string; install: string } {
  // Unknown target (vocab drift) → passthrough: show the raw name, no fabricated repo.
  return TARGET_REPO_MAP[target] ?? { label: target, install: '' };
}

function DistributeView(
  { name, onRequestClose, onDispatch }:
  { name: string; onRequestClose?: () => void; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean },
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
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null);
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
    // Fallback: copy the command (no dispatch bridge / no active tab). Goes through
    // copyToClipboard: navigator.clipboard is denied in the Tauri webview.
    void copyToClipboard(distributeCmd).then((ok) => {
      setCopied(ok ? 'ok' : 'failed');
      setTimeout(() => setCopied(null), ok ? 2000 : 2500);
    });
  }, [distributeCmd, onDispatch, onRequestClose]);

  if (error) return <div className="p-4 text-[var(--color-status-error)] text-body" data-testid="distribute-error">Failed to load distribution: {error}</div>;
  if (!data) return <div className="p-4 text-[var(--color-text-muted)] text-body">Loading distribution…</div>;

  return (
    <div className="p-4" data-testid="brainhub-distribute">
      {/* declared reach — guided Step 2 (confirm targets & freshness) */}
      {data.distributable ? (
        <>
          <div className="flex items-center gap-2 mb-1.5 text-meta uppercase tracking-wider font-semibold text-[var(--color-status-success)]" data-testid="distribute-step" data-step="2">
            Step 2 · confirm targets &amp; freshness
          </div>
          <div className="flex items-center gap-2 mb-3 text-small">
            <span className="material-symbols-outlined icon-sm text-[var(--color-status-success)]">outbound</span>
            <span className="font-semibold text-[var(--color-text)]">Distributable</span>
            <span className={`text-meta px-1.5 py-0.5 rounded tabular-nums ${data.visibility === 'external' ? 'bg-[color-mix(in_srgb,var(--color-status-warning)_18%,transparent)] text-[var(--color-status-warning)]' : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]'}`}>
              {data.visibility}
            </span>
          </div>
          <div className="flex flex-col gap-1.5 mb-4" data-testid="distribute-targets">
            {data.declared_targets.map((t) => (
              <div key={t} className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-code-bg)] px-2.5 py-1.5" data-testid="distribute-target-row">
                <span className="material-symbols-outlined icon-xs text-[var(--color-primary)]">deployed_code</span>
                <span className="font-mono text-meta text-[var(--color-text)]">{t}</span>
                {/* AC1 (run_8d2ec26c): where it lands — derived read-only from target+visibility. */}
                <span className="text-meta text-[var(--color-text-muted)]" title={repoForTarget(t).install}>
                  → {repoForTarget(t).label}
                </span>
                {/* F2 TRISTATE — three EXPLICIT branches. `null` (freshness unknown,
                    uncommitted output) must NOT fall into "up to date" (the old
                    `!source_changed_since` did exactly that, re-burying staleness). */}
                {!data.has_output ? (
                  <span className="ml-auto text-meta text-[var(--color-text-faint)]">never distributed</span>
                ) : data.source_changed_since === true ? (
                  <span className="ml-auto text-meta text-[var(--color-status-warning)]" title="knowledge changed since last distribute">● source changed since last distribute</span>
                ) : data.source_changed_since === false ? (
                  <span className="ml-auto text-meta text-[var(--color-text-faint)]">up to date</span>
                ) : (
                  <span className="ml-auto text-meta text-[var(--color-text-muted)]" title="the distribute output isn't git-committed, so there's no stable anchor to compare against — commit the output to enable freshness tracking">freshness unknown</span>
                )}
              </div>
            ))}
          </div>
          {data.has_output && (
            <div className="text-meta text-[var(--color-text-faint)] mb-3 font-mono">
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
            className="btn-sm btn-soft mb-4"
            title="Open aim.json in the Canvas to edit the declared reach (targets / visibility)"
          >
            <span className="material-symbols-outlined icon-xs">edit</span>
            Edit aim.json
          </button>
        </>
      ) : (
        <div className="rounded-md border border-dashed border-[color-mix(in_srgb,var(--color-status-warning)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_6%,transparent)] p-3 mb-4" data-testid="distribute-not-distributable">
          {/* Guided header — Gate-1: an ORPHANED brain (has_output && !distributable)
              already completed the flow once; labeling it "Step 1 · declare reach"
              would contradict the orphaned warning below. So split on has_output:
              orphaned → re-declare (a regression state); else → honest Step 1. */}
          {data.has_output ? (
            <div className="flex items-center gap-2 mb-1.5 text-meta uppercase tracking-wider font-semibold text-[var(--color-status-warning)]" data-testid="distribute-redeclare">
              Reach removed · re-declare to resume distribution
            </div>
          ) : (
            <div className="flex items-center gap-2 mb-1.5 text-meta uppercase tracking-wider font-semibold text-[var(--color-status-warning)]" data-testid="distribute-step" data-step="1">
              Step 1 · declare a reach
            </div>
          )}
          <div className="flex items-center gap-2 text-small text-[var(--color-status-warning)] mb-1">
            <span className="material-symbols-outlined icon-sm">block</span>
            Not distributable
          </div>
          <div className="text-meta text-[var(--color-text-muted)] leading-relaxed">
            This brain has no <span className="font-mono">distribution</span> block in its <span className="font-mono">aim.json</span>.
            The owner must declare a reach before it can be distributed — add to <span className="font-mono">aim.json</span>:
            <code className="block mt-1 px-2 py-1 rounded bg-[var(--color-bg)] text-[var(--color-text-muted)] whitespace-pre">{'"distribution": { "targets": ["open-plugin"], "visibility": "internal" }'}</code>
            {data.warnings.length > 0 && <span className="block mt-1 text-[var(--color-status-error)]">{data.warnings.join('; ')}</span>}
            {/* Gate-2 MED: a stale output with the block since removed — surface it, don't hide it. */}
            {data.has_output && (
              <span className="block mt-1 text-[var(--color-status-warning)]" data-testid="distribute-stale-output">
                An orphaned distribute output still exists (<span className="font-mono">{data.output_path}</span>) — the reach was declared before, then removed.
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
            className="btn-sm btn-soft mt-2.5"
            title="Open aim.json in the Canvas to declare the distribution block"
          >
            <span className="material-symbols-outlined icon-xs">open_in_new</span>
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
          <div className="text-meta uppercase tracking-wider font-semibold text-[var(--color-status-success)]" data-testid="distribute-step" data-step="3">
            Step 3 · run
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={onDistribute}
            disabled={!data.distributable}
            data-testid="distribute-button"
            className="btn-sm btn-success disabled:opacity-40 disabled:cursor-not-allowed"
            title={
              !data.distributable ? 'Declare a distribution block first'
                : onDispatch ? 'Send the distribute command to chat (runs s_ddd-distribute — you confirm targets there)'
                : 'Copy the chat command to run s_ddd-distribute'
            }
          >
            <span className="material-symbols-outlined icon-xs">{onDispatch ? 'send' : 'content_copy'}</span>
            {copied === 'ok' ? 'Copied — paste into a chat tab'
              : copied === 'failed' ? 'Could not copy — open a chat tab and retry'
              : onDispatch ? 'Distribute this brain →'
              : 'Distribute a brain'}
          </button>
          {data.distributable && (
            <span className="text-meta text-[var(--color-text-faint)] font-mono">→ {distributeCmd}</span>
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
          className="w-full flex items-center gap-2 px-3 py-2 text-small hover:bg-[var(--color-hover)]"
        >
          <span className="material-symbols-outlined icon-sm text-[var(--color-primary)]">help</span>
          <span className="">How distribution works</span>
          <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)] ml-auto">
            {guidelineOpen ? 'expand_less' : 'expand_more'}
          </span>
        </button>
        {guidelineOpen && (
          <div className="border-t border-[var(--color-border)] px-3 py-2.5 text-meta leading-relaxed text-[var(--color-text-muted)] flex flex-col gap-2" data-testid="distribute-guideline-body">
            <div>
              <span className="font-semibold text-[var(--color-text)]">The 3-step flow (human-in-the-loop):</span>
              <div className="mt-0.5">Step 1 · declare a reach in <span className="font-mono">aim.json</span> (targets + visibility — the SSOT, the ceiling).
              Step 2 · confirm which declared target(s) to emit + check freshness.
              Step 3 · run — the command lands in chat, where <span className="font-mono">s_ddd-distribute</span> confirms the subset, runs a content-safety scan, and renders the code package.</div>
            </div>
            <div>
              <span className="font-semibold text-[var(--color-text)]">Target → where it lands (git code-package management):</span>
              {/* Rendered FROM TARGET_REPO_MAP — the SAME source the per-row label uses,
                  so the legend can never drift from the rows (Gate-2 cross-fix fix). */}
              <div className="mt-0.5 flex flex-col gap-0.5">
                {Object.entries(TARGET_REPO_MAP).map(([t, { label, install }]) => (
                  <div key={t}><span className="font-mono">{t}</span> → <b>{label}</b> (<span className="font-mono">{install}</span>).</div>
                ))}
                <div data-testid="distribute-cloud-unsupported">Cloud storage (e.g. S3) → <b>not supported yet</b>: no packager target writes there.</div>
              </div>
            </div>
            <div>
              <span className="font-semibold text-[var(--color-text)]">emit ≠ publish:</span> an <span className="font-mono">internal</span> DDD can EMIT a package for a private install, but public publish is refused until <span className="font-mono">visibility</span> is explicitly <span className="font-mono">external</span> — a deliberate owner edit in <span className="font-mono">aim.json</span>, never a UI toggle.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
