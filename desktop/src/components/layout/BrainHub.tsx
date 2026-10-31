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
 * Conductor · Runs · What it learned (BrainLearnedView: CDF's entries, Withdraw) · Eval. Clicking a map
 * part opens its tab. The last tab, Distribute, ships the Brain out (an internal git
 * package or an external GitHub repo). `defaultView` picks the first
 * detail view (default 'overview').
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Tabs } from '../overlay';
import { useQuery } from '@tanstack/react-query';
import {
  useBrainsWithPinned, useBrainDetail, useBrainKnowledge, useDistribution,
  aggregateTypeCounts,
  brainRecall, useBrainSpecs, useBrainSpecDoc,
  getLocalRoots, removeLocalRoot, type LocalRoot,
} from '../../services/ddd';
import type {
  BrainSummary, BrainDetail, BrainKnowledge, GeneralBrain,
  RecallHit, SpecDoc, SpecRepo,
} from '../../services/ddd';
import { DddCard, Ontology } from './DddCard';
import { CodeGraph } from '../code-intel/CodeGraph';
import { getCodeIntelGraphs, getCodeIntelSummary, triggerReindex, type CodeIntelSummary } from '../../services/codeIntel';
import { relativeTime } from './ArtifactsOverlay';
import { LibraryTree } from './LibraryTree';
import { docSignalMap, weeklyReportModel, type WeeklyReportModel } from './dddOverview';
import { OverlayError } from './OverlayState';
import {
  BrainMapView, BrainNeedsView, BrainAgentView, BrainRunsView, BrainEvalView, BrainAddKnowledge,
  type BrainTab, type MapPanel,
} from './BrainMapView';
import { BrainHubHome, Flower } from './BrainHubHome';
import { GeneralBrainView } from './GeneralBrainView';
import { BrainLearnedView } from './BrainLearnedView';
import { bornText, hubSummary } from './brainMapModel';
import { pipelinesService, type PipelineAnalytics } from '../../services/pipelines';
import { buildResumeSetupPrompt } from '../../utils/newBrainDispatch';
import { takeOverlayTarget } from '../../utils/overlayTarget';
import { copyToClipboard } from '../../utils/clipboard';
import { BackToHubButton } from './BackToHubButton';

// ── Root — MASTER→DETAIL nav model ──────────────────────────────────────────────
//
//   • TOP LEVEL = just the Gallery (the Hub home). No greyed tabs.
//   • Selecting a brain enters the DETAIL shell: header + the seven tabs above.
//     Same tab set + order for every brain (no dynamic layout).
//
// `mode` is the top-level state: 'gallery' (list) or 'detail' (a brain is selected).

/** gallery · a Brain's detail · the built-in General Brain's own page */
type Mode = 'gallery' | 'detail' | 'general';
/** the built-in General Brain's name (backend project_agent.GENERAL_BRAIN) */
const GENERAL_NAME = 'General';
/** The detail views: the seven tabs (one per part of the Brain map, plus Overview)
 *  and Distribute, the last step of a Brain's lifecycle (ship it out). */
type DetailView = BrainTab | 'distribute';
const TABS: { key: DetailView; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'needs', label: 'Needs you' },
  { key: 'brain', label: 'Brain (DDD)' },
  { key: 'agent', label: 'Project Agent' },
  { key: 'runs', label: 'Conductor · Runs' },
  { key: 'reflect', label: 'What it learned' },
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
  { onRequestClose, onDispatch, onOpenLibrary, defaultView = 'overview' }:
  { onRequestClose?: () => void; onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
    /** switch to the Library overlay (the General Brain's knowledge) */
    onOpenLibrary?: () => void; defaultView?: DetailView } = {},
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
  /** The built-in General Brain has its own page: never opened by the name, so a
   *  Projects/ Brain that carries the same name keeps its own detail. */
  const openGeneral = useCallback(() => { setMode('general'); }, []);
  const general = bp?.general ?? null;
  /** The Hub home's targets ('map' + a panel, or 'review') → a tab. */
  const openFromHome = useCallback((n: string, t?: { view?: 'map' | 'review'; panel?: MapPanel }) => {
    if (t?.view === 'review') return openBrain(n, 'reflect');
    if (t?.panel === 'need') return openBrain(n, 'needs');
    if (t?.panel === 'agent') return openBrain(n, 'agent');
    if (t?.panel === 'add') return openBrain(n, 'brain', true);
    return openBrain(n);
  }, [openBrain]);

  // Deep-link: `swarm:show-brain-hub` MAY carry `detail.brain` to open a specific
  // brain's detail view directly. No detail → Gallery: the default on mount, and on an
  // already-open Hub the way home (the host title, a nav click, the agent's show).
  useEffect(() => {
    // the event that mounted us has already fired: a target recorded for it waits here
    // a target named exactly "General" is the built-in General Brain (the agent's
    // show-brain-hub, a target recorded before mount); a Projects/ Brain carrying that
    // name in some case is opened from its own card
    const open = (name: string) => (name === GENERAL_NAME ? openGeneral() : openBrain(name));
    const pending = takeOverlayTarget('brain-hub');
    if (pending) open(pending);
    const onShow = (e: Event) => {
      const name = (e as CustomEvent<{ brain?: string }>).detail?.brain;
      takeOverlayTarget('brain-hub');
      if (name) open(name);
      else backToGallery();
    };
    window.addEventListener('swarm:show-brain-hub', onShow);
    return () => window.removeEventListener('swarm:show-brain-hub', onShow);
  }, [openBrain, openGeneral, backToGallery]);

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

  if (mode === 'general') {
    return <GeneralBrainView general={general} onBack={backToGallery} onDispatch={onDispatch} onOpenLibrary={onOpenLibrary} />;
  }

  // GALLERY (master) — the brain list is the ONLY top-level surface.
  if (mode === 'gallery' || !selected) {
    return (
      <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="brain-hub">
        <div className="flex-1 overflow-auto">
          <Gallery brains={brains} selfName={selfName} general={general} onOpenGeneral={openGeneral}
            onOpenLibrary={onOpenLibrary} onOpen={openFromHome} onDispatch={onDispatch} />
        </div>
      </div>
    );
  }

  // DETAIL — key={selected} remounts the shell on a brain switch so no view-local
  // state (tree root, graph, a withdrawal half-confirmed) leaks across brains.
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
  // same count as the map's You: each Run waiting, each Need-You item, a Project Agent draft
  const needs = summary ? hubSummary([summary], analytics).needsYou : 0;

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

      <div className="px-4 py-2.5 border-b border-[var(--color-border)] flex-shrink-0">
        <Tabs label="Brain views" testid="brainhub-detail-nav" value={detailView} onChange={onSelectView}
          items={TABS.map((t) => ({
            key: t.key, label: t.label, testid: `brainhub-tab-${t.key}`,
            // only Needs you carries a count: what the Brain learned asks nothing of the person
            ...(t.key === 'needs' && needs > 0 ? { count: needs, tone: 'alert' as const } : {}),
          }))} />
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
        {detailView === 'reflect' && <BrainLearnedView name={name} />}
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
        <BackToHubButton testid="brainhub-back" onClick={onBack} />
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
  { brains, selfName, general, onOpenGeneral, onOpenLibrary, onOpen, onDispatch }:
  { brains: BrainSummary[] | null; selfName?: string;
    general: GeneralBrain | null; onOpenGeneral: () => void; onOpenLibrary?: () => void;
    onOpen: (n: string, target?: { view?: 'map' | 'review'; panel?: MapPanel }) => void;
    onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean },
) {
  if (brains === null) return <div className="p-4 text-[var(--color-text-muted)] text-body">Loading brains</div>;
  if (brains.length === 0 && !general) return <div className="p-4 text-[var(--color-text-muted)] text-body">No DDD brains found.</div>;
  return <BrainHubHome brains={brains} selfName={selfName} general={general} onOpenGeneral={onOpenGeneral}
    onOpenLibrary={onOpenLibrary} onOpen={onOpen} onDispatch={onDispatch} />;
}


// ── Brain (DDD) tab ──────────────────────────────────────────────────────────
//
// What the Brain knows, top to bottom:
//   Add knowledge — the primary action of this tab (a card that opens in place).
//   §① Ontology (3-layer×7-type) — the existing DddCard density=full IS the ontology.
//   §③ 4 core-doc cards (PRODUCT/TECH/IMPROVEMENT/PROJECT) + [Weekly Report].
//   Files — the real Projects/<name> file tree + Code Graph.

function BrainView(
  { name, onRequestClose, onDispatch, addOpen = false }:
  { name: string; onRequestClose?: () => void;
    onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean; addOpen?: boolean },
) {
  const [showWeekly, setShowWeekly] = useState(false);
  const [adding, setAdding] = useState(addOpen);

  // Cached fetches (run_cfb460ac): detail drives the view; what it learned this week is
  // best-effort (§③ signals + Weekly): its error must NOT blank the tab.
  const { data: detail = null, error: detailErr } = useBrainDetail(name);
  const { data: learned = null } = useBrainKnowledge(name, 'week');
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
        learned={learned}
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
  { detail, learned, onOpenFile, showWeekly, onToggleWeekly }:
  {
    detail: BrainDetail; learned: BrainKnowledge | null; onOpenFile: (p: string) => void;
    showWeekly: boolean; onToggleWeekly: () => void;
  },
) {
  const knowledge = detail.sections.find((s) => s.key === 'knowledge');
  const members = knowledge?.members ?? [];
  // Memoize the signal map + weekly model so an unrelated re-render (weekly toggle,
  // hover) doesn't re-scan the week's entries (matches the useMemo discipline the
  // Gallery already uses for aggregateTypeCounts).
  const signals = useMemo(() => docSignalMap(members, learned), [members, learned]);
  const weekly = useMemo(() => weeklyReportModel(detail, learned), [detail, learned]);
  // §① ontology counts — used for the no-health fallback tier (renders the 3×7
  // ontology from typeCounts alone). undefined when there are no entries at all.
  const ontologyCounts = useMemo(() => aggregateTypeCounts(detail.sections), [detail.sections]);

  return (
    <div className="flex-1 overflow-auto px-4 pb-4 flex flex-col gap-3" data-testid="brainhub-overview">
      {/* §① Ontology — RELOCATED, not rebuilt (Gate-0 C046): the existing
          DddCard density=full IS the 3-layer×7-type ontology + needs-you verdict.
          FIXED SLOT — always the FIRST child so the §①→②→③ order is invariant for
          EVERY brain (Gate-2 HIGH: gating the whole §① on the detail metrics let
          it VANISH for a degenerate/old-daemon brain → §② became first → per-brain
          structural drift, the exact "dynamic makes users lost" failure). Three
          graceful tiers, all in the same slot position:
            • detail metrics present → the full health-strip (ontology + needs-you verdict)
            • no health but typeCounts present → ontology-only (DddCard FullBody renders
              the 3×7 ontology from typeCounts alone, no metrics needed)
            • neither → a muted anchor so the slot still occupies §①'s position. */}
      <div data-testid="brainhub-ontology">
        {typeof detail.health?.escalationPending === 'number' ? (
          <div data-testid="brainhub-healthstrip">
            {/* ontologyOnly (run_115aa182): needs-you lives in the Needs you tab;
                this §① strip is the ontology + facts only. */}
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

/** §③ the 4 core-doc cards + group header (+ Weekly Report panel). */
function CoreDocCards(
  { members, signals, projectName, onOpenFile, showWeekly, onToggleWeekly, weekly }:
  {
    members: BrainDetail['sections'][number]['members'];
    signals: Map<string, { newCount: number }>;
    projectName: string; onOpenFile: (p: string) => void;
    showWeekly: boolean; onToggleWeekly: () => void; weekly: WeeklyReportModel;
  },
) {
  const totalNew = weekly.learnedWeek;
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
          {hasSignal ? `this week · ${totalNew} new · ${totalPending} pending` : 'nothing new this week'}
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
          const sig = signals.get(m.path) ?? { newCount: 0 };
          const marked = sig.newCount > 0;
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
                {sig.newCount > 0 && <span className="text-[var(--color-status-success)]">+{sig.newCount} this week</span>}
                {!marked && <span>nothing new this week</span>}
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
        {projectName} · this week
      </div>
      <div className="flex flex-col gap-1 text-[var(--color-text-muted)]">
        <div><span className="text-[var(--color-status-success)] font-semibold">{weekly.learnedWeek}</span> entr{weekly.learnedWeek === 1 ? 'y' : 'ies'} learned</div>
        <div><span className="text-[var(--color-status-warning)] font-semibold">{weekly.pending}</span> item{weekly.pending === 1 ? '' : 's'} waiting in Need You</div>
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
  // The repos with a graph (design 2026-10-29-code-intel-auto D7): a Brain that
  // reads several shows a picker; fetched only once the disclosure is open.
  const [repos, setRepos] = useState<string[] | null>(null);
  const [repo, setRepo] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (!graphOpen) return;
    let cancelled = false;
    getCodeIntelGraphs(name)
      .then((r) => {
        if (cancelled) return;
        setRepos(r);
        // one graph: no picker and no repo parameter (the endpoints default to it)
        setRepo((cur) => (r.length > 1 ? (cur && r.includes(cur) ? cur : r[0]) : undefined));
      })
      .catch(() => { if (!cancelled) setRepos([]); });
    return () => { cancelled = true; };
  }, [graphOpen, name]);

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

      <LocalRootsCard key={`roots-${name}`} name={name} />

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
          {graphOpen && repos && repos.length > 1 && (
            <div className="flex items-center gap-2 px-3 py-1.5 border-t border-[var(--color-border)] text-meta">
              <span className="text-[var(--color-text-muted)]">Repository</span>
              <select data-testid="codegraph-repo" value={repo} onChange={(e) => setRepo(e.target.value)}
                className="bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded px-1.5 py-0.5 text-meta">
                {repos.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          )}
          {graphOpen && repos && <CodeIntelStatus key={`status-${name}-${repo ?? ''}`} name={name} repo={repo} />}
          {graphOpen && repos && (
            <div className="border-t border-[var(--color-border)] h-[360px]" data-testid="codegraph-panel">
              <CodeGraph key={`graph-${name}-${repo ?? ''}`} project={name} repo={repo} inline />
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ── Local repositories (design 2026-10-29-code-intel-auto D1) ─────────────────
// The decision: "should this Brain keep reading this checkout of mine?" A repository
// declared on this machine is read at once; this card says which ones, who declared
// them, and lets the person take one back. Nothing renders when the Brain has none.

const ROOT_SOURCE: Record<string, string> = {
  'new-brain': 'added when this Brain was created',
  agent: 'added by the agent',
  migrated: "from this Brain's TECH.md",
};

function LocalRootsCard({ name }: { name: string }) {
  const [roots, setRoots] = useState<LocalRoot[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    getLocalRoots(name)
      .then((r) => { if (!cancelled) setRoots(r); })
      .catch(() => { if (!cancelled) setRoots([]); });
    return () => { cancelled = true; };
  }, [name]);

  const remove = (path: string) => {
    setBusy(path);
    setError(null);
    removeLocalRoot(name, path)
      .then((r) => setRoots(r))
      .catch((e: unknown) => {
        const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        setError(typeof detail === 'string' ? detail : 'That did not work');
      })
      .finally(() => setBusy(null));
  };

  if (!roots || roots.length === 0) return null;
  return (
    <div className="mb-3 flex-shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2" style={{ maxWidth: 640 }} data-testid="local-roots-card">
      <div className="text-small mb-1">Local repositories</div>
      {roots.map((r) => (
        <div key={r.path} className="py-1.5" data-testid="local-root">
          <div className="flex items-center gap-2">
            {/* the repo's name first: a path cut at its end would hide which one it is */}
            <span className="text-small font-semibold flex-shrink-0">{r.path.split('/').filter(Boolean).pop() ?? r.path}</span>
            <span className="text-meta text-[var(--color-text-muted)] truncate" style={{ direction: 'rtl', textAlign: 'left' }} title={r.path}>
              {`\u200e${r.path}\u200e`}{/* LRM: keep the path's own order inside rtl */}
            </span>
            <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
              <span className="text-meta text-[var(--color-text-faint)]">
                {r.graphBuilt ? 'read into this Brain' : 'building its code graph…'}
              </span>
              <button
                data-testid="local-root-remove"
                disabled={busy === r.path}
                onClick={() => remove(r.path)}
                className="btn-sm btn-soft disabled:opacity-50"
              >
                Remove
              </button>
            </span>
          </div>
          <div className="text-meta text-[var(--color-text-muted)] mt-0.5">
            {ROOT_SOURCE[r.source ?? ''] ?? 'added on this machine'}. Its code structure (symbols,
            calls, routes) is read into this Brain; the survey can write what it finds into the
            Brain&apos;s docs, which travel to a Hive.
          </div>
        </div>
      ))}
      {error && <div className="text-meta text-[var(--color-status-warning)] mt-1">{error}</div>}
    </div>
  );
}

// ── Code graph status (the one decision: is this brain's graph current, re-index?) ──
// It used to be a bottom-bar badge hard-wired to the project named SwarmAI, so every
// other brain had none. Here it belongs to the brain it describes; fetched only when
// the Code Graph disclosure is opened.

function _indexedAge(iso: string | null): string {
  if (!iso) return 'never indexed';
  // the backend stores UTC without a zone suffix
  const zoned = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const age = relativeTime(zoned);           // 'now' | '5m' | '3h' | '2d' | 'YYYY-MM-DD' | ''
  if (!age) return 'indexed —';
  if (age === 'now') return 'indexed just now';
  return /^\d+[mhd]$/.test(age) ? `indexed ${age} ago` : `indexed on ${age}`;
}

function CodeIntelStatus({ name, repo }: { name: string; repo?: string }) {
  const [summary, setSummary] = useState<CodeIntelSummary | null>(null);
  const [reindexing, setReindexing] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (repo ? getCodeIntelSummary(name, repo) : getCodeIntelSummary(name))
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch(() => { if (!cancelled) setSummary(null); });
    return () => { cancelled = true; };
  }, [name, repo]);

  const reindex = () => {
    setReindexing(true);
    triggerReindex(name)
      // the re-index runs in the background: read the summary again after a moment
      .then(() => new Promise((r) => setTimeout(r, 5000)))
      .then(() => (repo ? getCodeIntelSummary(name, repo) : getCodeIntelSummary(name)))
      .then((s) => setSummary(s))
      .catch(() => {})
      .finally(() => setReindexing(false));
  };

  if (!summary) return null;
  const stale = summary.freshnessStatus === 'stale';
  const rebuilding = summary.freshnessStatus === 'rebuilding';
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-t border-[var(--color-border)] text-meta" data-testid="codeintel-status">
      <span className="tabular-nums text-[var(--color-text-muted)]">{summary.symbolCount.toLocaleString()} symbols</span>
      <span className={stale ? 'text-[var(--color-status-warning)]' : 'text-[var(--color-text-faint)]'} data-testid="codeintel-age">
        · {rebuilding ? 'rebuilding for a new parser' : _indexedAge(summary.lastIndexedAt)}
      </span>
      <button
        data-testid="codeintel-reindex"
        onClick={reindex}
        disabled={reindexing}
        className="btn-sm btn-soft ml-auto disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {reindexing ? 'Indexing…' : 'Re-index'}
      </button>
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
  // the Projects/<name>/ prefix (BrainView.openFile shape). Close-before-dispatch
  // z-index precedent.
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
