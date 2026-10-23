/**
 * FileViewerPanel — Resizable right-side panel wrapper for FileViewer.
 *
 * Ported from FileEditorPanel: drag-to-resize via vertical handle,
 * width persistence to localStorage. FileViewer handles all content
 * rendering, tabs, and status bar internally.
 *
 * Key exports:
 * - `FileViewerPanel`   — Panel wrapper (default export)
 * - `PANEL_CONSTANTS`   — Min/max/default width values
 */

import { memo, useState, useCallback, useEffect, useRef, useReducer, type CSSProperties } from 'react';
import FileViewer from './FileViewer';
import type { FileViewerProps } from './FileViewer';
import { CanvasOutputRail } from './CanvasOutputRail';
import { FileTypeIcon } from '../common/FileTypeIcon';
import { outputRowOpenDetail, railOrder } from '../../hooks/railSsot';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';
import { ChangesCount, ChangesDetail, ChangesNav, ChangesRefresh, type ChangesSelection } from './CanvasChanges';
import type { GroupedReferencedFiles } from '../../hooks/useReferencedFiles';
import type { CanvasCollapse, CanvasTabId } from '../../hooks/useCanvasHost';
import { LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { layoutViewportWidth, clientToCssScale, APP_ZOOM_EVENT } from '../../utils/layoutViewport';

export const PANEL_CONSTANTS = {
  DEFAULT_WIDTH: 500,
  MIN_WIDTH: 320,
  MAX_WIDTH: 900,
  STORAGE_KEY: 'fileViewerPanelWidth',
  /** Responsive default = this fraction of the viewport width, clamped to MIN/MAX.
   *  Chat stays the majority pane; Canvas is wide enough for decks/tables. This is
   *  the industry pattern (ChatGPT Canvas / Claude Artifacts / VS Code side panel):
   *  percentage default + px clamp + remember the user's manual drag. */
  DEFAULT_FRACTION: 0.34,
  /** Breathing gap (px) between the panel's right edge and the window edge, so
   *  Canvas never sits flush against the frame (the docked panel is flex-shrink-0
   *  at the chat container's right edge → this margin is its only right gutter). */
  RIGHT_GAP: 12,
  /** SOFT target for the chat column's remaining width. The Canvas width is capped
   *  (availableCanvasMax) so the chat pane keeps at least this many px on a normal
   *  window — the fix for "Canvas too wide, chat starved". This is a SOFT target,
   *  NOT a hard floor: MIN_WIDTH (the Canvas's own hard minimum) WINS below
   *  ~962px (150 sidebar + 480 chat + 320 canvas + 12 gap), where chat yields
   *  first — that is intentional physical reality, not a bug. Kept ≥ the layout's
   *  hard MIN_MAIN_CHAT_PANEL_WIDTH (300, ThreeColumnLayout) so the two never fight. */
  CHAT_MIN_HEALTHY: 480,
} as const;

/** The viewport-aware ceiling for the Canvas panel width: never so wide that the
 *  chat pane drops below CHAT_MIN_HEALTHY. Subtracts the left sidebar + the chat
 *  reserve + the right gap from the window width, then bounds to [MIN_WIDTH, MAX_WIDTH].
 *  clamp order is `max(MIN_WIDTH, min(MAX_WIDTH, available))` — MIN_WIDTH is the HARD
 *  floor (Canvas must stay usable), so on a narrow window (<~962px) the chat reserve
 *  yields and chat gets < CHAT_MIN_HEALTHY. That is deliberate (physical reality); do
 *  NOT assert "chat ≥ CHAT_MIN_HEALTHY unconditionally".
 *  `viewportWidth` is the LAYOUT width in CSS px (layoutViewportWidth()), never
 *  window.innerWidth: under the app's <html> zoom innerWidth stays unzoomed, so at
 *  zoom 2 it is twice the space the widths below are laid out in. */
export function availableCanvasMax(viewportWidth: number): number {
  const available =
    viewportWidth -
    LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH -
    PANEL_CONSTANTS.CHAT_MIN_HEALTHY -
    PANEL_CONSTANTS.RIGHT_GAP;
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(PANEL_CONSTANTS.MAX_WIDTH, available),
  );
}

/** Clamp a Canvas width (CSS px) to [MIN_WIDTH, live viewport-aware ceiling]. The ONE
 *  clamp every width path uses (read-time, drag, persist, expand-restore, zoom), so a
 *  change to the ceiling rule cannot miss a site. Reads the layout width live. */
function clampToCeiling(w: number): number {
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(availableCanvasMax(layoutViewportWidth()), w),
  );
}

/** clamp(MIN, fraction×viewport, availableCanvasMax) — the responsive default before
 *  any drag. The ceiling is availableCanvasMax (viewport-aware), NOT raw MAX_WIDTH, so
 *  the default never starves chat. */
function responsiveDefaultWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const vw = layoutViewportWidth();
  const raw = Math.round(PANEL_CONSTANTS.DEFAULT_FRACTION * vw);
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(availableCanvasMax(vw), raw),
  );
}

/** Width of the collapsed vertical rail (whole panel → a thin clickable strip). */
const RAIL_WIDTH = 38;
// railed / outputsCollapsed are NO LONGER panel-local + global-localStorage (Bug 2:
// that bled collapse state across all chat tabs because the panel never remounts on
// tab switch). They now live in the per-tab CanvasTabState slice, passed in via the
// `collapse` prop + written via `setCollapse`. The former canvasRailed /
// canvasOutputsCollapsed localStorage keys are DELETED.

function getStoredWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const stored = localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY);
  // No manual drag on record → responsive default (fraction of viewport). A stored
  // value means the user dragged (persistWidth is the ONLY writer) → honor it, but
  // clamped AT READ TIME to what currently fits (availableCanvasMax). A width dragged
  // wide on a big monitor must not starve chat when reopened on a smaller window; we
  // clamp only here (read time), NOT on a live resize — the resize-recompute effect
  // stays gated on "no stored value", so a dragged width remains immune to live resize.
  if (!stored) return responsiveDefaultWidth();
  const parsed = parseInt(stored, 10);
  if (isNaN(parsed)) return responsiveDefaultWidth();
  return clampToCeiling(parsed);
}

/**
 * Build the count tooltip / expanded-count words from the new/modified split.
 * Pure — unit-tested. Omits a zero part; returns "" when both are 0 so an
 * unbadged batch (total>0, neu=upd=0) shows no misleading "0 new, 0 modified".
 * Single source for BOTH the shell-bar header count tooltip AND the collapsed
 * rail count, so the two never drift (Gate-1 F1).
 */
export function canvasCountTitle(neu: number, upd: number): string {
  const parts: string[] = [];
  if (neu > 0) parts.push(`${neu} new`);
  if (upd > 0) parts.push(`${upd} modified`);
  return parts.join(', ');
}

type FileViewerPanelProps = Omit<FileViewerProps, 'variant'> & {
  // Canvas output rail scope = props.tabScopeKey (the owning tab id, inherited
  // from FileViewerProps). The former `sessionId` prop is REMOVED (run_26aa6caa) —
  // the rail keys on the stable tabId now, not the volatile session id.
  /** Pin: the Canvas does not change by itself (no auto-surface, no tab switch). */
  pinned?: boolean;
  onTogglePin?: () => void;
  /** The owning chat tab's Canvas tab (useCanvasHost slice). Controlled when set
   *  together with onCanvasTabChange; otherwise the panel keeps its own. */
  canvasTab?: CanvasTabId;
  onCanvasTabChange?: (t: CanvasTabId) => void;
  /** The shown file's unsaved state, owned by the slice (useCanvasHost). Controlled
   *  when set with onFileDirtyChange; otherwise the panel keeps its own. */
  fileDirty?: boolean;
  onFileDirtyChange?: (dirty: boolean) => void;
  /** A user open held back by unsaved edits, and its answer (useCanvasHost). */
  pendingOpen?: { filePath: string; fileName: string } | null;
  onResolvePending?: (discard: boolean) => void;
  /** The referenced-files (rail rows), owned by the RESIDENT useCanvasHost
   *  (run_9e42c066) and passed down. The rail is now pure presentational — the
   *  panel no longer hosts the swarm:file-changed listener, so a write that lands
   *  while this panel is unmounted (Canvas closed) is still captured upstream. */
  referencedFiles: GroupedReferencedFiles;
  /** Per-tab collapse view-state (Bug 2): railed / outputsCollapsed, owned by the
   *  per-tab CanvasTabState slice in useCanvasHost. Was panel-local useState + global
   *  localStorage → bled across tabs (the panel never remounts on tab switch). */
  collapse: CanvasCollapse;
  /** Patch the active tab's collapse state (Bug 2). Must be referentially stable —
   *  this component is memo'd on stable props (see the memo note at the bottom). */
  setCollapse: (p: Partial<CanvasCollapse>) => void;
  /** Whether Canvas is OPEN on the active tab (= slice.file || slice.manuallyOpen,
   *  useCanvasHost.ts). The panel is now ALWAYS mounted by ChatPage (the former
   *  `{canvas.isOpen && …}` gate is gone) so an EMPTY tab still shows a resident
   *  entry rail instead of a vanished column. isOpen=false → resident rail;
   *  isOpen=true → the existing railed/full behavior. This does NOT change isOpen's
   *  SEMANTICS (ChatHeader pill + agent SENSE still read file||manuallyOpen) — it
   *  only selects which of the three render branches shows. */
  isOpen: boolean;
  /** Reveal (manually open) Canvas on the active tab — fired by clicking the
   *  resident empty-state rail. Wired to useCanvasHost.reveal (= manuallyOpen:true
   *  via the same per-tab patch() chokepoint as swarm:open-canvas). Must be
   *  referentially stable (memo). */
  onRevealCanvas: () => void;
};

function FileViewerPanelImpl({
  pinned,
  onTogglePin,
  referencedFiles,
  collapse,
  setCollapse,
  isOpen,
  onRevealCanvas,
  ...props
}: FileViewerPanelProps) {
  // Rail scope key = the owning TAB id (run_26aa6caa). props.tabScopeKey (the same
  // activeTabId ChatPage already passes to clear FileViewer's internal tab list) is
  // stable from tab creation — it has NO unresolved window — so the former
  // `lastDefinedSessionIdRef` stable-hack (which existed ONLY to paper over
  // sessionId's undefined flicker, BUG1/run_26981f66) is now dead code and DELETED.
  // A stable key means the rail neither flashes empty on the flicker NOR bleeds.
  const railTabId = props.tabScopeKey;
  // Canvas tabs: Artifacts (this session's written files, the existing rail + file
  // viewer) and Changes (each code repo's local state, CanvasChanges). The file
  // viewer stays MOUNTED while Changes shows: it holds open files and unsaved edits.
  // The tab lives in the owning chat tab's Canvas slice (useCanvasHost), because
  // this panel never remounts on a chat-tab switch; every file open brings
  // Artifacts forward there. Uncontrolled fallback only when no host wires it.
  const [localCanvasTab, setLocalCanvasTab] = useState<CanvasTabId>('artifacts');
  // Controlled only as a pair: a value without its setter (or the reverse) would
  // show one state and write another.
  const controlledTab = props.canvasTab !== undefined && props.onCanvasTabChange !== undefined;
  const canvasTab = controlledTab ? props.canvasTab! : localCanvasTab;
  const setCanvasTab = controlledTab ? props.onCanvasTabChange! : setLocalCanvasTab;
  const [changesSel, setChangesSel] = useState<ChangesSelection>(null);
  const openedFile = props.initialFile;
  // Uncontrolled only: bring Artifacts forward when a file opens. Controlled, the
  // host's swarm:open-file handler writes canvasTab with the file (one writer).
  useEffect(() => {
    if (openedFile && !controlledTab) setLocalCanvasTab('artifacts');
  }, [openedFile, controlledTab]);

  const [width, setWidth] = useState(getStoredWidth);
  const [isDragging, setIsDragging] = useState(false);
  // Width-reveal (bug5): mount at MIN_WIDTH, then flip to the real width on the
  // next frame so the CSS `transition: width` eases the panel open — and, as a
  // flex sibling, eases the chat pane narrower in lockstep (no instant "pop").
  // We reveal from MIN_WIDTH (not 0) on purpose: FileViewer's content (xterm /
  // lazy renderers) measures its container at mount, and a literal 0-width first
  // frame makes it cache a broken 0-layout (IMPROVEMENT.md xterm-cell-measure
  // class). MIN_WIDTH is always measurable, and 320→target is still a visible
  // ease. While dragging we must NOT keep the transition (it would lag cursor).
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, []);
  const revealWidth = entered ? width : Math.min(PANEL_CONSTANTS.MIN_WIDTH, width);
  // The live drag ceiling (viewport-aware) — exposed as aria-valuemax so a
  // screen-reader user hears the REACHABLE max, not the raw MAX_WIDTH (which
  // setLiveWidth's clamp will never let the width reach on a normal window). Read at
  // render time; refreshes on any re-render (good enough — the panel is not a
  // frequently-resized target, and it is strictly more honest than a fixed 900).
  const dragCeiling =
    typeof window === 'undefined'
      ? PANEL_CONSTANTS.MAX_WIDTH
      : availableCanvasMax(layoutViewportWidth());
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);
  // CSS px per client px, taken at drag start: pointer clientX is in unzoomed
  // client px under the app zoom while the width is in CSS px, so the raw delta
  // would move the edge zoom-times faster than the pointer.
  const dragScaleRef = useRef(1);
  // Last pointer x seen during a drag: a zoom change mid-drag re-takes the drag
  // baseline from here (see the APP_ZOOM_EVENT effect).
  const lastClientXRef = useRef(0);
  const isDraggingRef = useRef(false);
  // rAF-throttle state (run_4b67c510): a mousemove writes the latest requested
  // width to pendingWidthRef and schedules ONE animation frame; the frame applies
  // it (setWidth only). This coalesces 100+Hz mousemove events to ≤1 setWidth per
  // paint, so the (memoized) FileViewer subtree reconciles at most once per frame,
  // not once per mouse event. localStorage is NOT written per frame — only on
  // drag-end (mouseup + cleanup flush), so the drag path has zero synchronous I/O.
  const rafIdRef = useRef<number | null>(null);
  const pendingWidthRef = useRef(0);
  // Accent tint for the divider seam = the primary accent, dimmed by the seam's
  // own `opacity` in index.css. (Earlier this read navSource, but navSource is only
  // set by nav-CARD clicks — the auto-surface path never sets it, so it would show
  // a stale, unrelated region color. A fixed accent is honest; a session-specific
  // tint would need a real prop.)
  // The SOLID hairlines (nav-bar bottom, rail left, spout edges) do NOT read this —
  // they read --canvas-edge, the one shared dimmed frame-line token (index.css).
  const canvasTint = 'var(--color-primary)';
  // Expand/collapse: snap to a wide review width and back. Remembers the
  // pre-expand width so collapse restores exactly what the user had.
  const [expanded, setExpanded] = useState(false);
  const preExpandWidthRef = useRef(width);
  // Compute from current state, then set both independently (no setWidth inside
  // a setExpanded updater — that fires twice under StrictMode).
  const toggleExpand = useCallback(() => {
    if (!expanded) {
      preExpandWidthRef.current = width;
      // Expand snaps to the WIDEST width that still keeps chat healthy, NOT raw
      // MAX_WIDTH — otherwise "expand for review" starves chat on a normal window.
      setWidth(availableCanvasMax(layoutViewportWidth()));
      setExpanded(true);
    } else {
      // Clamp the restored width: the ceiling may have shrunk while expanded (a
      // window shrink or an app zoom-in), and the old width would starve chat.
      setWidth(clampToCeiling(preExpandWidthRef.current));
      setExpanded(false);
    }
  }, [expanded, width]);

  // Window model is now TWO states + close (bug6): Panel (default, resizable) and
  // Expanded (wide review) via `expanded`, plus Close (unmount). The old 200px
  // narrow "outputs dock" (COLLAPSED_WIDTH) is REMOVED — it was the "傻傻分不出"
  // culprit (a half-panel that read as broken). Outputs stay reachable via the
  // Canvas nav card re-open, not a stunted dock.

  // ── Two-level collapse (v6 redesign, run_09431085) ──
  // (1) outputsCollapsed: caret in the OUTPUTS bar folds ONLY the output LIST —
  //     the bar + Region-B file stay, panel keeps full width. Space goes to the file.
  // (2) railed: the whole Canvas collapses to a ~38px vertical strip showing a
  //     rotated "Canvas · Outputs" + count; click the strip to expand. This is
  //     NOT the removed bug6 dock (a stunted HALF-panel that read as broken) — it
  //     is an intentional, obviously-clickable rail with a clear expand affordance.
  // railed / outputsCollapsed are now PER-TAB (Bug 2): read from the `collapse` prop
  // (the active tab's CanvasTabState slice), written via `setCollapse`. No panel-local
  // useState, no localStorage — so collapsing tab A's Canvas no longer bleeds to tab B.
  const { railed, outputsCollapsed } = collapse;
  const toggleOutputs = useCallback(() => setCollapse({ outputsCollapsed: !outputsCollapsed }), [setCollapse, outputsCollapsed]);
  const collapseToRail = useCallback(() => setCollapse({ railed: true }), [setCollapse]);
  const expandFromRail = useCallback(() => setCollapse({ railed: false }), [setCollapse]);

  // ── Default selection (XG 2026-10-23, docs/design/super-canvas.md L3) ──
  // When the Artifacts view BECOMES visible on the SAME tab (a manual open, an
  // expand from the rail, Changes → Artifacts) with no open file and a non-empty
  // list, open the list's first row with the same detail a click builds, plus
  // `auto: true` so auto-surface treats it as Canvas's own choice (a later output
  // may still replace it). Keyed to that transition, not to "no file": on a tab
  // switch the rows can still be the previous tab's for one commit, so a same-key
  // check keeps tab A's file from landing on tab B. Stamped with the tab id so a
  // switch during the resolve cannot move it either. The first mount counts as a
  // transition (prev visible = false). A pinned Canvas never selects by itself.
  const artifactsVisible = isOpen && !railed && canvasTab === 'artifacts';
  const firstOutputRef = useRef<ReturnType<typeof railOrder>[number] | undefined>(undefined);
  firstOutputRef.current = railTabId !== undefined ? railOrder(referencedFiles)[0] : undefined;
  const openedFileRef = useRef(openedFile);
  openedFileRef.current = openedFile;
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const prevVisibleRef = useRef({ key: railTabId, visible: false });
  useEffect(() => {
    const prev = prevVisibleRef.current;
    prevVisibleRef.current = { key: railTabId, visible: artifactsVisible };
    if (prev.key !== railTabId || prev.visible || !artifactsVisible) return;
    const f = firstOutputRef.current;
    if (!f || openedFileRef.current || pinnedRef.current) return;
    document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, {
      detail: {
        ...outputRowOpenDetail(f.path, f.change, f.absolutePath, f.baseRef, f.sessionId),
        auto: true,
        ...(railTabId ? { tabId: railTabId } : {}),
      },
    }));
  }, [railTabId, artifactsVisible]);

  // The file shown in Region B — drives the accent left-bar on its output row.
  const selectedPath = props.initialFile?.filePath;

  // Whether that file has unsaved edits (FileViewer reports it): the folded header
  // shows a dot by its name.
  const [localDirty, setLocalDirty] = useState(false);
  const fileDirty = props.onFileDirtyChange ? !!props.fileDirty : localDirty;
  const reportDirty = props.onFileDirtyChange ?? setLocalDirty;
  // "Stop browsing" (the × on the Browsing row): show the first output instead, the
  // same open a click on that row does; with no output, ask FileViewer to close the
  // file through its unsaved-edits guard (closeRequest), which ends in canvas.close.
  const [closeRequest, setCloseRequest] = useState(0);
  const stopBrowsing = useCallback(() => {
    const f = firstOutputRef.current;
    if (!f) { setCloseRequest((n) => n + 1); return; }
    document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, {
      detail: {
        ...outputRowOpenDetail(f.path, f.change, f.absolutePath, f.baseRef, f.sessionId),
        ...(railTabId ? { tabId: railTabId } : {}),
      },
    }));
  }, [railTabId]);

  // ── Un-rail on new file arrival — MOVED to the write side (Bug 2 root fix, run_5f5e7675) ──
  // A new file reaching Canvas is the "I want to see this" intent → a railed Canvas
  // must reveal. This USED to be a panel render effect keyed on [selectedPath, railTabId],
  // but once `railed` became per-tab that was render-timing-fragile: on a tab switch,
  // railTabId (=activeTabId, immediate) and selectedPath (=slice.file, restored one
  // commit later) diverge across two commits, so a switch-back to a railed tab
  // false-fired the un-rail and popped it open (adversarial HIGH, run_5f5e7675).
  // The invariant now lives at the SINGLE per-tab WRITE chokepoint — useCanvasHost's
  // swarm:open-file handler sets `railed:false` on the owning tab's slice IN THE SAME
  // write that sets the file, only when the path actually changes. A tab SWITCH never
  // runs that handler, so a restored railed tab is never disturbed. No panel effect,
  // no prevFileRef/prevRailTabRef, no cross-commit race. (Capstone lesson: move an
  // invariant to the point where the entity is PRODUCED, don't guard read-side entrances.)

  // Panel-local output counts (neu/upd BREAKDOWN) published by the rail — drives the
  // header summary line + the collapsed-rail "N files · M new" display. This is
  // DISTINCT from the resident outputCount (useCanvasHost, SSOT): that single total
  // feeds proprioception + the ChatHeader pill; this carries the new-vs-modified
  // split for in-panel display only. The onCanvasMeta→outputCount round-trip was
  // REMOVED (run_9e42c066): outputCount now derives from the resident store, so the
  // panel no longer feeds the count up (that was Gate-1 Defect 3, a second writer).
  const [counts, setCounts] = useState<{ total: number; neu: number; upd: number }>({ total: 0, neu: 0, upd: 0 });

  // Clamp helper — the viewport-aware ceiling (availableCanvasMax, NOT raw
  // MAX_WIDTH) so a width can never starve chat. The layout width is read LIVE (per
  // call, never cached) so the ceiling stays correct if the window resizes mid-drag.
  const clampWidth = clampToCeiling;
  // setLiveWidth — the per-FRAME path: update the visual width ONLY, no persist.
  // Called from the rAF callback during a drag (≤1/frame) and from the mouseup/
  // cleanup flush. NO localStorage here — the drag path must stay free of
  // synchronous main-thread I/O (run_4b67c510).
  const setLiveWidth = useCallback((newWidth: number) => {
    setWidth(clampWidth(newWidth));
  }, [clampWidth]);
  // persistWidth — writes localStorage ONCE, at drag-end (mouseup / cleanup). This
  // is the SOLE localStorage writer for width, so a user-dragged width is the only
  // thing that turns the panel "user-sized"; expand (setWidth-direct) still never
  // persists (keeps the resize-recompute effect live). Clamps before persisting.
  const persistWidth = useCallback((newWidth: number) => {
    localStorage.setItem(PANEL_CONSTANTS.STORAGE_KEY, String(clampWidth(newWidth)));
  }, [clampWidth]);
  // Resize drag handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    // A manual resize exits expand mode (matches "maximize then drag = un-maximize"):
    // otherwise the icon shows "collapse" while the width is hand-dragged, and a
    // later collapse would discard the drag by restoring the pre-expand width.
    setExpanded(false);
    setIsDragging(true);
    startXRef.current = e.clientX;
    lastClientXRef.current = e.clientX;
    startWidthRef.current = width;
    dragScaleRef.current = clientToCssScale();
    // Seed pendingWidthRef to the CURRENT width so a click-without-move (mousedown
    // then mouseup, no mousemove) flushes the unchanged width (idempotent no-op) —
    // NOT a stale value from a prior drag, and NOT 0 (which clamps to MIN_WIDTH and
    // would snap+persist a 320px panel on a stray click). Every mousemove overwrites
    // this immediately; it only matters for the zero-move case.
    pendingWidthRef.current = width;
  }, [width]);

  useEffect(() => {
    isDraggingRef.current = isDragging;
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Dragging the left edge: moving left = wider, right = narrower.
      // Record the latest requested width; schedule ONE rAF that applies it
      // (setLiveWidth only — NO persist). Extra mousemoves within the same frame
      // just overwrite pendingWidthRef, coalescing to ≤1 setWidth per paint.
      lastClientXRef.current = e.clientX;
      pendingWidthRef.current =
        startWidthRef.current + (startXRef.current - e.clientX) * dragScaleRef.current;
      if (rafIdRef.current === null) {
        rafIdRef.current = requestAnimationFrame(() => {
          rafIdRef.current = null;
          setLiveWidth(pendingWidthRef.current);
        });
      }
    };

    const handleMouseUp = () => {
      // Flush the final frame synchronously, THEN persist ONCE. Order = cancel →
      // flush → null, so the pending rAF cannot also fire (no double-apply), and
      // the last requested width is always the one committed + persisted.
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      setLiveWidth(pendingWidthRef.current);
      persistWidth(pendingWidthRef.current);
      setIsDragging(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      // Drag-end via ANY path other than mouseup (unmount, isDragging flipped
      // elsewhere): flush + persist the pending width so a drag is never lost.
      // On a normal mouseup this is a no-op (rafIdRef already null). This effect
      // only binds while isDragging, so a non-drag unmount has rafIdRef===null and
      // does nothing.
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
        setLiveWidth(pendingWidthRef.current);
        persistWidth(pendingWidthRef.current);
      }
    };
  }, [isDragging, setLiveWidth, persistWidth]);

  // Responsive default follows the viewport — recompute on window resize, but ONLY
  // when the user has NOT manually sized the panel (no localStorage) — a dragged
  // width is authoritative and immune to resize. Gate-1 CRITICAL fixes:
  //  · `!expanded`  — expand snaps width to MAX_WIDTH via setWidth (NOT persistWidth,
  //    so it never persists); recomputing during expand would clobber MAX_WIDTH and
  //    leave a stale preExpandWidthRef.
  //  · `entered`    — the mount reveal eases MIN→width via a CSS width transition;
  //    a mid-animation setWidth restarts/janks it.
  // NOT persisted here (setWidth, not persistWidth): a resize is not a manual choice,
  // so it must not turn the panel into a "user-sized" one and lock out future resizes.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!entered || expanded) return;
    if (localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY) !== null) return;
    const handleResize = () => {
      // Re-check the guard at fire time: the user may have dragged (→ stored) or
      // expanded since the effect bound.
      if (expanded || localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY) !== null) return;
      setWidth(responsiveDefaultWidth());
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [entered, expanded]);

  // App zoom (Cmd+/Cmd-) changes the CSS-px viewport without a window `resize`, so
  // it gets its own listener. Unlike a resize it also binds a dragged (stored)
  // width, because zooming in shrinks the ceiling and a width that no longer fits
  // would starve chat:
  //  · expanded   -> re-snap to the new ceiling;
  //  · otherwise  -> getStoredWidth(): the responsive default when nothing is
  //    stored, else the STORED width clamped to the new ceiling. Re-deriving from
  //    storage (not clamping the current width) is what makes a zoom round trip
  //    lossless: zoom in narrows the panel, zoom back out restores the user's width.
  //    Never persisted — a zoom is not a manual choice.
  //  · mid-drag   -> leave the width to the drag (its live clamp already reads the
  //    new ceiling) and re-take the drag baseline at the new scale, so the edge
  //    keeps tracking the pointer.
  // The ceiling bump re-renders even when the width is unchanged, so
  // aria-valuemax (dragCeiling) never announces a stale maximum.
  const [, bumpCeiling] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleZoom = () => {
      if (isDraggingRef.current) {
        startXRef.current = lastClientXRef.current;
        startWidthRef.current = clampToCeiling(pendingWidthRef.current);
        dragScaleRef.current = clientToCssScale();
      } else if (expanded) {
        setWidth(availableCanvasMax(layoutViewportWidth()));
      } else {
        setWidth(getStoredWidth());
      }
      bumpCeiling();
    };
    window.addEventListener(APP_ZOOM_EVENT, handleZoom);
    return () => window.removeEventListener(APP_ZOOM_EVENT, handleZoom);
  }, [expanded]);

  // ── Collapsed rail: the whole Canvas as a thin clickable vertical strip ──
  // Click anywhere on it → expand. Vertical "Canvas · Outputs" + count carry the
  // accent (var(--color-primary)) so it still reads as belonging to the active tab.
  // ── EMPTY-STATE RESIDENT RAIL (isOpen=false) — always-present Canvas entry point ──
  // ChatPage now ALWAYS mounts this panel (the former `{canvas.isOpen && …}` gate is
  // gone), so an empty tab (no file, not manually opened) must NOT show a vanished
  // column — it shows a resident 38px rail. This branch MUST early-return BEFORE the
  // full-panel render below (Gate-1 condition b): falling through would mount the
  // heavy FileViewer/FileEditorCore (~1780L, see the memo note) in the empty state.
  // Distinct from the `if (railed)` branch below: that is the collapsed-AFTER-open
  // rail (data-testid=canvas-rail); this is the never-opened resident rail
  // (data-testid=canvas-resident-rail). Clicking reveals (manuallyOpen) the Canvas.
  // CanvasOutputRail stays MOUNTED-but-hidden so the count ("N files") is live even
  // when the user never opened Canvas (same self-suppressing-count guard as railed).
  if (!isOpen) {
    return (
      <div
        className="relative flex-shrink-0 canvas-width-reveal"
        style={{ width: RAIL_WIDTH, marginRight: PANEL_CONSTANTS.RIGHT_GAP, '--spout-tint': canvasTint } as CSSProperties}
        data-testid="file-viewer-panel"
      >
        <div className="canvas-spout" aria-hidden="true" data-testid="canvas-spout" />
        <button
          type="button"
          onClick={onRevealCanvas}
          className="canvas-rail group w-full h-full flex flex-col items-center gap-3 pt-3 cursor-pointer border-l border-[var(--canvas-edge)]"
          title="Open Canvas"
          aria-label="Open Canvas"
          data-testid="canvas-resident-rail"
        >
          <span className="material-symbols-outlined icon-md text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">chevron_left</span>
          <span className="canvas-rail-text text-[11px] font-bold tracking-[0.12em] uppercase text-[var(--color-text)]">Canvas · Artifacts</span>
          {counts.total > 0 && (
            <span
              className="canvas-rail-text text-[11px] text-[var(--color-text-muted)]"
              title={canvasCountTitle(counts.neu, counts.upd) || undefined}
            >
              {counts.total} file{counts.total !== 1 ? 's' : ''}
              {counts.neu > 0 && <span className="text-[var(--color-git-added)]"> · {counts.neu} new</span>}
              {counts.upd > 0 && <span className="text-[var(--color-git-modified)]"> · {counts.upd} mod</span>}
            </span>
          )}
        </button>
        {/* Rail-count must stay LIVE while closed: CanvasOutputRail is the ONLY source
            of counts (onCounts→setCounts). Keep it MOUNTED-but-hidden (display:none)
            so a file produced while Canvas was never opened still bumps the strip's
            "N files" — otherwise the count freezes at 0 (self-suppressing-count class,
            IMPROVEMENT.md:7). Zero-size, no visual footprint. Same shape as the railed
            branch below. */}
        {railTabId !== undefined && (
          <div className="hidden" aria-hidden="true">
            <CanvasOutputRail files={referencedFiles} onCounts={setCounts} selectedPath={selectedPath} />
          </div>
        )}
      </div>
    );
  }

  if (railed) {
    return (
      <div
        className="relative flex-shrink-0 canvas-width-reveal"
        style={{ width: RAIL_WIDTH, marginRight: PANEL_CONSTANTS.RIGHT_GAP, '--spout-tint': canvasTint } as CSSProperties}
        data-testid="file-viewer-panel"
      >
        <div className="canvas-spout" aria-hidden="true" data-testid="canvas-spout" />
        <button
          type="button"
          onClick={expandFromRail}
          className="canvas-rail group w-full h-full flex flex-col items-center gap-3 pt-3 cursor-pointer border-l border-[var(--canvas-edge)]"
          title="Expand Canvas"
          aria-label="Expand Canvas"
          data-testid="canvas-rail"
        >
          <span className="material-symbols-outlined icon-md text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">chevron_left</span>
          <span className="canvas-rail-text text-[11px] font-bold tracking-[0.12em] uppercase text-[var(--color-text)]">Canvas · Artifacts</span>
          {counts.total > 0 && (
            // Consistent with the expanded-header count (Gate-1 F1): git-semantic
            // color vars (not literal green/yellow), both new AND mod, shared
            // canvasCountTitle tooltip. Each part gated on >0 (F2).
            <span
              className="canvas-rail-text text-[11px] text-[var(--color-text-muted)]"
              title={canvasCountTitle(counts.neu, counts.upd) || undefined}
            >
              {counts.total} file{counts.total !== 1 ? 's' : ''}
              {counts.neu > 0 && <span className="text-[var(--color-git-added)]"> · {counts.neu} new</span>}
              {counts.upd > 0 && <span className="text-[var(--color-git-modified)]"> · {counts.upd} mod</span>}
            </span>
          )}
        </button>
        {/* Rail-count must stay LIVE: the rail displays counts.total, but the rail
            component is the ONLY source of counts (onCounts→setCounts). Keep it
            MOUNTED-but-hidden while railed (display:none) so a file written while
            collapsed still bumps the strip's "N files" — otherwise the count
            freezes at the pre-rail value (the self-suppressing-count class,
            IMPROVEMENT.md:7). Zero-size, no visual footprint. */}
        {railTabId !== undefined && (
          <div className="hidden" aria-hidden="true">
            <CanvasOutputRail files={referencedFiles} onCounts={setCounts} selectedPath={selectedPath} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={`relative flex-shrink-0 flex ${isDragging ? '' : 'canvas-width-reveal'}`}
      style={{ width: revealWidth, marginRight: PANEL_CONSTANTS.RIGHT_GAP, '--spout-tint': canvasTint } as CSSProperties}
      data-testid="file-viewer-panel"
    >
      {/* Spout — a small triangle sitting IN the panel's left edge (inside the
          panel box, not overhanging chat), pointing left toward the chat: reads
          as "this Canvas spouted out of the conversation." Tinted with the
          session accent. Vertically centered on the chat window (CSS top:50%
          translateY(-50%)), not pinned to the header row. */}
      <div className="canvas-spout" aria-hidden="true" data-testid="canvas-spout" />
      {/* Resize handle — left edge, doubles as the colorful Canvas↔chat divider.
          `.canvas-divider` is `position:relative` (index.css) so its `::after` grip
          bar centers on the seam; `.canvas-grip-active` shows the grip while dragging
          (the hover state alone can't cover a drag once the cursor leaves the 2px seam). */}
      <div
        className={`canvas-divider w-0.5 cursor-col-resize transition-colors flex-shrink-0 ${
          isDragging ? 'opacity-100 canvas-grip-active' : 'hover:opacity-100'
        }`}
        onMouseDown={handleMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={PANEL_CONSTANTS.MIN_WIDTH}
        aria-valuemax={dragCeiling}
        aria-label="Resize file viewer panel"
        data-testid="panel-resize-handle"
      >
        {/* Wider, reliably-grabbable hit area (11px straddling the seam) for
            easy drag start — invisible; the visible affordance is the grip bar
            rendered by `.canvas-divider::after` (index.css). */}
        <div className="absolute top-0 -left-1.5 w-[11px] h-full" aria-hidden="true" />
      </div>

      {/* Canvas column: output rail (session deliverables) + the file surface.
          `canvas-content-fade` eases content opacity in behind the width grow so
          text doesn't smear while the panel is still widening (bug5). */}
      {/* `contain: layout` bounds the chat textarea's auto-grow reflow so it can
          NOT descend into this column's large un-virtualized Canvas DOM
          (FileEditorCore renders one node per line). Root fix for the recurring
          "Canvas 开着时 chat input 输入卡死" lag (run_1cb87e1a): a keystroke's
          textarea `height='auto'` dirties document layout; without containment the
          browser re-lays-out the whole Canvas subtree (O(lines)) every keypress.
          MUST stay on this INNER content column, NOT the outer panel box — the outer
          box holds the `.canvas-spout` at left:-10px, and `contain` there would
          establish a containing block that clips the overhanging spout. The panel is
          fixed-width (outer box: flex-shrink-0 + explicit style.width), so typing
          never changes Canvas width → `layout` containment is both effective and
          spout-safe. `contain:layout` (NOT paint/content/size) does not clip, so zero
          visual change; absolute children inside the column are positioned relative to
          ancestors inside it (no-op), and popovers are portaled/fixed (unaffected).
          NOTE (run_2daacd0f): paint/composite isolation for the 6th-recurrence lag is
          scoped NARROWLY to HtmlRenderer's root (the iframe report + transform:scale
          sizer — the actual composite-heavy surface), NOT here. Paint containment on
          THIS column would capture FileEditorCore's `position:fixed` popover/modal
          (selection-comment button + unsaved-changes backdrop) as its containing block,
          re-anchoring them to the column and clipping the full-screen modal — a REVIEW
          finding. Keep this column layout-only. */}
      {/* `pointer-events:none` ONLY while dragging (drag-shield): the content column
          hosts HtmlRenderer's sandboxed opaque-origin iframe (w-full h-full). A drag
          started on the seam moves the cursor OVER that iframe, and the document-level
          mousemove listener STOPS firing over an opaque-origin iframe → the drag stalls.
          Making the whole content column inert for the duration of the drag lets the
          document listener keep receiving mousemove. Scoped strictly to isDragging (zero
          effect at rest); the resize handle is a SIBLING of this column, so it stays
          live. `contain:layout` is orthogonal (layout containment, not hit-testing). */}
      <div
        className="flex-1 min-w-0 flex flex-col overflow-hidden canvas-content-fade"
        style={{ contain: 'layout', pointerEvents: isDragging ? 'none' : undefined }}
        data-testid="canvas-content-column"
      >
        {/* Canvas header — output stream + gentle auto-surface controls.
            Rendered only when Canvas props are wired (tabScopeKey provided).
            Layout: a min-w-0 truncating title/summary on the left + a
            flex-shrink-0 action cluster on the right, so buttons never get
            occluded on narrow widths (item 3). */}
        {railTabId !== undefined && (
          <div
            className="flex-shrink-0 canvas-outputs-navbar"
            data-testid="canvas-region-outputs"
          >
            <div className="flex items-center gap-2 px-2 h-[34px] text-[11px] text-[var(--color-text-muted)]">
              {/* Caret — folds ONLY the output list (panel keeps full width). */}
              <button
                onClick={toggleOutputs}
                className="icon-btn shrink-0 hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                title={outputsCollapsed ? 'Show list' : 'Hide list'}
                aria-label={outputsCollapsed ? 'Show list' : 'Hide list'}
                aria-expanded={!outputsCollapsed}
                data-testid="canvas-outputs-caret"
              >
                <span className={`material-symbols-outlined icon-sm block transition-transform duration-200 ${outputsCollapsed ? '-rotate-90' : ''}`}>expand_more</span>
              </button>
              {/* Tabs: Artifacts (this session's files) | Changes (code repos' local state). */}
              <div className="flex items-center min-w-0 flex-1">
                <div role="tablist" aria-label="Canvas" className="inline-flex p-[2px] rounded-lg bg-[color-mix(in_srgb,var(--color-text)_6%,transparent)]">
                  {([
                    ['artifacts', 'Artifacts'],
                    ['changes', 'Changes'],
                  ] as const).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={canvasTab === id}
                      data-testid={`canvas-tab-${id}`}
                      onClick={() => setCanvasTab(id)}
                      title={id === 'artifacts' ? (canvasCountTitle(counts.neu, counts.upd) || undefined) : undefined}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-[2px] rounded-md text-[12px] transition-colors ${canvasTab === id ? 'bg-[var(--color-card)] text-[var(--color-text)] shadow-[0_0_0_1px_var(--color-border)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
                    >
                      {label}
                      {id === 'artifacts'
                        ? counts.total > 0 && <span className="text-[10.5px] px-1.5 rounded-full tabular-nums bg-[color-mix(in_srgb,var(--color-text)_10%,transparent)]">{counts.total}</span>
                        : <ChangesCount />}
                    </button>
                  ))}
                </div>
                {canvasTab === 'changes' && <ChangesRefresh />}
                {/* List folded: the header says which file the card shows. */}
                {outputsCollapsed && canvasTab === 'artifacts' && props.initialFile && (
                  <span className="ml-2 inline-flex items-center gap-1.5 min-w-0 text-[12px] font-semibold text-[var(--color-text)]"
                    title={props.initialFile.filePath} data-testid="canvas-current-file">
                    <FileTypeIcon name={props.initialFile.fileName} />
                    <span className="truncate">{props.initialFile.fileName}</span>
                    {fileDirty && (
                      <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-warning)] shrink-0"
                        title="Unsaved changes" aria-label="Unsaved changes" data-testid="canvas-current-dirty" />
                    )}
                  </span>
                )}
              </div>
              {/* Pin — acts on the whole Canvas (both tabs): pinned, it does not
                  change by itself (no auto-surface, no tab switch). It replaced the
                  separate mute, which suppressed exactly the same thing. */}
              <div className="flex items-center gap-0.5 shrink-0" data-testid="canvas-content-controls">
                <button
                  onClick={onTogglePin}
                  // Idle vs active are MUTUALLY EXCLUSIVE in one color slot (NOT two
                  // stacked text-* utilities): Tailwind resolves same-property utility
                  // conflicts by generated-CSS source order, not className string order,
                  // so a trailing `${pinned ? 'text-primary'}` would LOSE to a leading
                  // idle color and the active accent would never show (Gate-2 CRITICAL).
                  // --color-text-muted is the one idle colour of every Canvas header icon.
                  className={`icon-btn hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors ${pinned ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-muted)]'}`}
                  title={pinned ? 'Pinned — Canvas won’t change by itself (click to unpin)' : 'Pin Canvas — new outputs won’t open or replace what you see (they still list here)'}
                  aria-label="Toggle pin — Canvas does not change by itself"
                  aria-pressed={!!pinned}
                >
                  <span className="material-symbols-outlined icon-sm">keep</span>
                </button>
              </div>
              {/* Divider between the two semantic groups (content | window). */}
              <div className="w-px h-4 bg-[var(--color-border)] shrink-0" data-testid="canvas-controls-divider" aria-hidden="true" />
              {/* WINDOW controls (expand + close) — act on the PANEL itself,
                  grouped right. Window model: Panel ⇄ Expanded (one toggle) + the
                  × button. run_26aa6caa: the × now COLLAPSES to the side rail
                  (collapseToRail) instead of UNMOUNTING — XG directive "close 不是
                  关闭 而是 collapse". The Canvas is thus never destroyed by the user's
                  dismiss gesture; it stays one click away on the rail (its outputs +
                  file survive). True unmount (props.onClose) remains reachable ONLY
                  via the intrinsic "last file tab closed" path inside FileViewer
                  (FileViewer.tsx:489/500) — not a header button. The former separate
                  right_panel_close button is REMOVED (it duplicated this action —
                  R25 merge, don't duplicate). */}
              <div className="flex items-center gap-0.5 shrink-0" data-testid="canvas-window-controls">
                <button
                  onClick={toggleExpand}
                  className="icon-btn hover:bg-[var(--color-hover)] transition-colors"
                  title={expanded ? 'Restore width' : 'Expand — widen the Canvas for review'}
                  aria-label={expanded ? 'Restore Canvas width' : 'Expand Canvas for review'}
                  aria-pressed={expanded}
                >
                  <span className="material-symbols-outlined icon-sm">{expanded ? 'close_fullscreen' : 'open_in_full'}</span>
                </button>
                <button
                  onClick={collapseToRail}
                  className="icon-btn hover:bg-[var(--color-hover)] transition-colors"
                  title="Collapse Canvas to a side rail"
                  aria-label="Collapse Canvas to a side rail"
                  data-testid="canvas-collapse-rail-btn"
                >
                  <span className="material-symbols-outlined icon-sm">right_panel_close</span>
                </button>
              </div>
            </div>
            {/* Output list — the caret folds ONLY the list (panel keeps full
                width; space goes to the Region-B file). Kept MOUNTED-but-hidden
                when collapsed (not unmounted) so the panel-local counts (onCounts→
                setCounts) stay live — the bar's "N files · M new" summary keeps
                updating while folded. */}
            <div
              className={outputsCollapsed || canvasTab !== 'artifacts' ? 'hidden' : 'max-h-[140px] overflow-y-auto px-2 pb-1.5'}
              data-testid="canvas-outputs-list"
            >
              <CanvasOutputRail files={referencedFiles} onCounts={setCounts} selectedPath={selectedPath} onStopBrowsing={stopBrowsing} />
            </div>
            {canvasTab === 'changes' && !outputsCollapsed && (
              <div className="max-h-[220px] overflow-y-auto px-2 pb-1.5" data-testid="canvas-changes-list">
                <ChangesNav selected={changesSel} onSelect={setChangesSel} />
              </div>
            )}
          </div>
        )}
        {/* A user open held back by unsaved edits (super-canvas-fixing.md C1). */}
        {railTabId !== undefined && props.pendingOpen && props.onResolvePending && (
          <div
            className="mx-2 mb-1.5 flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12px] border border-[color-mix(in_srgb,var(--color-warning)_45%,var(--color-border))] bg-[color-mix(in_srgb,var(--color-warning)_10%,var(--color-card))] text-[var(--color-text)]"
            role="alert"
            data-testid="canvas-pending-open"
          >
            <span className="material-symbols-outlined icon-sm text-[var(--color-warning)] shrink-0" aria-hidden="true">warning</span>
            <span className="min-w-0 flex-1 truncate" title={props.initialFile?.filePath}>
              Unsaved changes in <b>{props.initialFile?.fileName}</b>
            </span>
            <button type="button" className="btn-sm btn-ghost shrink-0" onClick={() => props.onResolvePending!(false)}
              data-testid="canvas-pending-keep">Keep editing</button>
            <button type="button" className="btn-sm btn-warning shrink-0" onClick={() => props.onResolvePending!(true)}
              title={props.pendingOpen.filePath} data-testid="canvas-pending-discard">
              Discard and open {props.pendingOpen.fileName}
            </button>
          </div>
        )}
        {/* Content layer: a raised card below the navigation layer (the header and
            its list sit on the recessed canvas surface). */}
        <div className={`flex-1 min-w-0 overflow-hidden ${railTabId !== undefined ? 'canvas-content-card' : ''}`}>
          <div className={canvasTab === 'changes' ? 'hidden' : 'h-full'}>
            <FileViewer
              {...props}
              variant="panel"
              closeRequest={closeRequest}
              onDirtyChange={reportDirty}
            />
          </div>
          {canvasTab === 'changes' && <ChangesDetail selected={changesSel} />}
        </div>
      </div>
    </div>
  );
}

// LOAD-BEARING memo (run_24f98f06, root cause A). ChatPage holds `inputValue` in
// useState and the chat textarea is controlled (value={inputValue}), so EVERY
// keystroke re-renders ChatPage — which would re-render this whole heavy Canvas
// subtree (FileViewer + FileEditorCore ~1780L + CanvasOutputRail) on each key,
// freezing input whenever Canvas is open. All props from the ChatPage call site
// are referentially stable across a keystroke render (useCanvasHost callbacks are
// useCallback dep-[patch]/[patch,activeTabId]; file/pinned/isOpen from slice
// state; sessionId/tabScopeKey primitive; the always-mounted-panel props isOpen
// (primitive bool) + onRevealCanvas (useCallback dep-[patch]) are likewise stable),
// so the default shallow compare blocks
// the re-render. Do NOT remove without eliminating the ChatPage keystroke
// re-render at its source (the deferred lift-input-state-down refactor).
export default memo(FileViewerPanelImpl);
