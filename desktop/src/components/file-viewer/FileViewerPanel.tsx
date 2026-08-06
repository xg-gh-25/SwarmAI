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

import { memo, useState, useCallback, useEffect, useRef, type CSSProperties } from 'react';
import FileViewer from './FileViewer';
import type { FileViewerProps } from './FileViewer';
import { CanvasOutputRail } from './CanvasOutputRail';
import type { GroupedReferencedFiles } from '../../hooks/useReferencedFiles';
import type { CanvasCollapse } from '../../hooks/useCanvasHost';
import { LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';

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
 *  NOT assert "chat ≥ CHAT_MIN_HEALTHY unconditionally". */
export function availableCanvasMax(innerWidth: number): number {
  const available =
    innerWidth -
    LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH -
    PANEL_CONSTANTS.CHAT_MIN_HEALTHY -
    PANEL_CONSTANTS.RIGHT_GAP;
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(PANEL_CONSTANTS.MAX_WIDTH, available),
  );
}

/** clamp(MIN, fraction×viewport, availableCanvasMax) — the responsive default before
 *  any drag. The ceiling is availableCanvasMax (viewport-aware), NOT raw MAX_WIDTH, so
 *  the default never starves chat. */
function responsiveDefaultWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const raw = Math.round(PANEL_CONSTANTS.DEFAULT_FRACTION * window.innerWidth);
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(availableCanvasMax(window.innerWidth), raw),
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
  return Math.max(
    PANEL_CONSTANTS.MIN_WIDTH,
    Math.min(availableCanvasMax(window.innerWidth), parsed),
  );
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
  /** Canvas gentle auto-surface controls. */
  pinned?: boolean;
  onTogglePin?: () => void;
  muted?: boolean;
  onToggleMute?: () => void;
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
};

function FileViewerPanelImpl({
  pinned,
  onTogglePin,
  muted,
  onToggleMute,
  referencedFiles,
  collapse,
  setCollapse,
  ...props
}: FileViewerPanelProps) {
  // Rail scope key = the owning TAB id (run_26aa6caa). props.tabScopeKey (the same
  // activeTabId ChatPage already passes to clear FileViewer's internal tab list) is
  // stable from tab creation — it has NO unresolved window — so the former
  // `lastDefinedSessionIdRef` stable-hack (which existed ONLY to paper over
  // sessionId's undefined flicker, BUG1/run_26981f66) is now dead code and DELETED.
  // A stable key means the rail neither flashes empty on the flicker NOR bleeds.
  const railTabId = props.tabScopeKey;

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
      : availableCanvasMax(window.innerWidth);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);
  // rAF-throttle state (run_4b67c510): a mousemove writes the latest requested
  // width to pendingWidthRef and schedules ONE animation frame; the frame applies
  // it (setWidth only). This coalesces 100+Hz mousemove events to ≤1 setWidth per
  // paint, so the (memoized) FileViewer subtree reconciles at most once per frame,
  // not once per mouse event. localStorage is NOT written per frame — only on
  // drag-end (mouseup + cleanup flush), so the drag path has zero synchronous I/O.
  const rafIdRef = useRef<number | null>(null);
  const pendingWidthRef = useRef(0);
  // Accent tint for the spout + divider = the primary accent. (Earlier this read
  // navSource, but navSource is only set by nav-CARD clicks — the auto-surface
  // path never sets it, so it would show a stale, unrelated region color. A
  // fixed accent is honest; a session-specific tint would need a real prop.)
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
      setWidth(availableCanvasMax(window.innerWidth));
      setExpanded(true);
    } else {
      setWidth(preExpandWidthRef.current);
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

  // The file shown in Region B — drives the accent left-bar on its output row.
  const selectedPath = props.initialFile?.filePath;

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
  // MAX_WIDTH) so a width can never starve chat. innerWidth is read LIVE (per call,
  // never cached) so the ceiling stays correct if the window resizes mid-drag.
  const clampWidth = useCallback(
    (w: number) =>
      Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(availableCanvasMax(window.innerWidth), w)),
    [],
  );
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
    startWidthRef.current = width;
    // Seed pendingWidthRef to the CURRENT width so a click-without-move (mousedown
    // then mouseup, no mousemove) flushes the unchanged width (idempotent no-op) —
    // NOT a stale value from a prior drag, and NOT 0 (which clamps to MIN_WIDTH and
    // would snap+persist a 320px panel on a stray click). Every mousemove overwrites
    // this immediately; it only matters for the zero-move case.
    pendingWidthRef.current = width;
  }, [width]);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Dragging the left edge: moving left = wider, right = narrower.
      // Record the latest requested width; schedule ONE rAF that applies it
      // (setLiveWidth only — NO persist). Extra mousemoves within the same frame
      // just overwrite pendingWidthRef, coalescing to ≤1 setWidth per paint.
      pendingWidthRef.current = startWidthRef.current + (startXRef.current - e.clientX);
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

  // ── Collapsed rail: the whole Canvas as a thin clickable vertical strip ──
  // Click anywhere on it → expand. Vertical "Canvas · Outputs" + count carry the
  // accent (var(--color-primary)) so it still reads as belonging to the active tab.
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
          className="canvas-rail group w-full h-full flex flex-col items-center gap-3 pt-3 cursor-pointer border-l border-[var(--color-primary)]"
          title="Expand Canvas"
          aria-label="Expand Canvas"
          data-testid="canvas-rail"
        >
          <span className="material-symbols-outlined text-[18px] text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">chevron_left</span>
          <span className="canvas-rail-text text-[11px] font-bold tracking-[0.12em] uppercase text-[var(--color-text)]">Canvas · Outputs</span>
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
          ancestors inside it (no-op), and popovers are portaled/fixed (unaffected). */}
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
            className="flex-shrink-0 border-b border-[var(--color-primary)] canvas-outputs-navbar"
            data-testid="canvas-region-outputs"
          >
            <div className="flex items-center gap-2 px-2 h-8 text-[11px] text-[var(--color-text-muted)] bg-[color-mix(in_srgb,var(--color-primary)_6%,transparent)]">
              {/* Caret — folds ONLY the output list (panel keeps full width). */}
              <button
                onClick={toggleOutputs}
                className="shrink-0 p-0.5 -ml-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all"
                title={outputsCollapsed ? 'Show outputs' : 'Hide outputs list'}
                aria-label={outputsCollapsed ? 'Show outputs list' : 'Hide outputs list'}
                aria-expanded={!outputsCollapsed}
                data-testid="canvas-outputs-caret"
              >
                <span className={`material-symbols-outlined text-[18px] block transition-transform duration-200 ${outputsCollapsed ? '-rotate-90' : ''}`}>expand_more</span>
              </button>
              {/* Brand + count: "Canvas · Outputs  N files · M new" */}
              <div className="flex items-baseline gap-1.5 min-w-0 flex-1">
                <span className="font-bold tracking-[0.09em] uppercase shrink-0 text-[var(--color-text)]">
                  Canvas<span className="text-[color-mix(in_srgb,var(--color-primary)_60%,var(--color-text-muted))]"> · Outputs</span>
                </span>
                {counts.total > 0 && (
                  // Compact git-colored count: total, then new(green)/mod(yellow)
                  // split. When narrow → "N a·b" with the words in the tooltip;
                  // when expanded → the words inline. Each part gated on >0 so an
                  // unbadged batch (neu=upd=0) shows just the total (Gate-1 F2).
                  // Git SEMANTIC color vars only (never --color-primary — the accent
                  // is header-identity-only; counts carry git meaning). SSOT tooltip
                  // via canvasCountTitle so it never drifts from the rail (F1).
                  <span
                    className="truncate text-[var(--color-text-muted)]"
                    title={canvasCountTitle(counts.neu, counts.upd) || undefined}
                  >
                    {counts.total}
                    {(counts.neu > 0 || counts.upd > 0) && (
                      <span className="ml-1">
                        {counts.neu > 0 && (
                          <span className="text-[var(--color-git-added)]">
                            {expanded ? `${counts.neu} new` : counts.neu}
                          </span>
                        )}
                        {counts.neu > 0 && counts.upd > 0 && (
                          <span className="text-[var(--color-text-dim)]">{expanded ? ' · ' : '·'}</span>
                        )}
                        {counts.upd > 0 && (
                          <span className="text-[var(--color-git-modified)]">
                            {expanded ? `${counts.upd} mod` : counts.upd}
                          </span>
                        )}
                      </span>
                    )}
                  </span>
                )}
              </div>
              {/* CONTENT controls (pin + mute) — act on WHAT is shown, grouped
                  left beside "Outputs". Pin = THIS file (keep it open, don't let
                  a new output replace the view). Mute = THIS SESSION (stop
                  auto-surfacing new outputs). Distinct scopes → two controls. */}
              <div className="flex items-center gap-0.5 shrink-0" data-testid="canvas-content-controls">
                <button
                  onClick={onTogglePin}
                  // Idle vs active are MUTUALLY EXCLUSIVE in one color slot (NOT two
                  // stacked text-* utilities): Tailwind resolves same-property utility
                  // conflicts by generated-CSS source order, not className string order,
                  // so a trailing `${pinned ? 'text-primary'}` would LOSE to a leading
                  // idle color and the active accent would never show (Gate-2 CRITICAL).
                  // --color-text-dim is a real, defined token (fainter than muted).
                  className={`p-0.5 rounded hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors ${pinned ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-dim)]'}`}
                  title={pinned ? 'Pinned — this file won’t be auto-replaced (click to unpin)' : 'Pin this file — keep it open; a new output won’t replace your view'}
                  aria-label="Toggle pin — keep this file open"
                  aria-pressed={!!pinned}
                >
                  <span className="material-symbols-outlined text-[14px]">keep</span>
                </button>
                <button
                  onClick={onToggleMute}
                  // Mutually-exclusive color slot — see the pin button above (Gate-2 CRITICAL:
                  // stacked text-* utilities let the idle color override the active accent).
                  className={`p-0.5 rounded hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors ${muted ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-dim)]'}`}
                  title={muted ? 'Auto-surface muted for this session (click to unmute)' : 'Mute auto-surface — stop opening new outputs this session (they still list here)'}
                  aria-label="Toggle auto-surface mute for this session"
                  aria-pressed={!!muted}
                >
                  <span className="material-symbols-outlined text-[14px]">{muted ? 'notifications_off' : 'notifications'}</span>
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
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title={expanded ? 'Restore width' : 'Expand — widen the Canvas for review'}
                  aria-label={expanded ? 'Restore Canvas width' : 'Expand Canvas for review'}
                  aria-pressed={expanded}
                >
                  <span className="material-symbols-outlined text-[14px]">{expanded ? 'close_fullscreen' : 'open_in_full'}</span>
                </button>
                <button
                  onClick={collapseToRail}
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title="Collapse Canvas to a side rail"
                  aria-label="Collapse Canvas to a side rail"
                  data-testid="canvas-collapse-rail-btn"
                >
                  <span className="material-symbols-outlined text-[14px]">right_panel_close</span>
                </button>
              </div>
            </div>
            {/* Output list — the caret folds ONLY the list (panel keeps full
                width; space goes to the Region-B file). Kept MOUNTED-but-hidden
                when collapsed (not unmounted) so the panel-local counts (onCounts→
                setCounts) stay live — the bar's "N files · M new" summary keeps
                updating while folded. */}
            <div
              className={outputsCollapsed ? 'hidden' : 'max-h-[140px] overflow-y-auto px-1.5 pb-1.5'}
              data-testid="canvas-outputs-list"
            >
              <CanvasOutputRail files={referencedFiles} onCounts={setCounts} selectedPath={selectedPath} />
            </div>
          </div>
        )}
        {/* FileViewer surface */}
        <div className="flex-1 min-w-0 overflow-hidden">
          <FileViewer
            {...props}
            variant="panel"
          />
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
// useCallback dep-[patch]/[patch,activeTabId]; file/pinned/muted/isOpen from slice
// state; sessionId/tabScopeKey primitive), so the default shallow compare blocks
// the re-render. Do NOT remove without eliminating the ChatPage keystroke
// re-render at its source (the deferred lift-input-state-down refactor).
export default memo(FileViewerPanelImpl);
