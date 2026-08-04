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
} as const;

/** clamp(MIN, fraction×viewport, MAX) — the responsive default before any drag. */
function responsiveDefaultWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const raw = Math.round(PANEL_CONSTANTS.DEFAULT_FRACTION * window.innerWidth);
  return Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, raw));
}

/** Width of the collapsed vertical rail (whole panel → a thin clickable strip). */
const RAIL_WIDTH = 38;
const OUTPUTS_COLLAPSED_KEY = 'canvasOutputsCollapsed';
const RAILED_KEY = 'canvasRailed';

function getStoredWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const stored = localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY);
  // No manual drag on record → responsive default (fraction of viewport). A stored
  // value means the user dragged (updateWidth is the ONLY writer) → honor it verbatim.
  if (!stored) return responsiveDefaultWidth();
  const parsed = parseInt(stored, 10);
  if (isNaN(parsed)) return responsiveDefaultWidth();
  return Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, parsed));
}

function getStoredBool(key: string): boolean {
  if (typeof window === 'undefined') return false;
  return localStorage.getItem(key) === '1';
}

function setStoredBool(key: string, val: boolean): void {
  try {
    localStorage.setItem(key, val ? '1' : '0');
  } catch { /* quota — no-op */ }
}

type FileViewerPanelProps = Omit<FileViewerProps, 'variant'> & {
  /** Canvas output rail: active-tab session id (from useSessionMeta). */
  sessionId?: string;
  /** Canvas gentle auto-surface controls. */
  pinned?: boolean;
  onTogglePin?: () => void;
  muted?: boolean;
  onToggleMute?: () => void;
  /** Report panel-internal Canvas state (collapsed + output count) UP to the
   *  parent so it can include them in the swarm:canvas-state proprioception
   *  event. Only these two live inside the panel; pin/mute/open are parent state.
   *  The parent zeroes them when the panel unmounts (Canvas closed). */
  onCanvasMeta?: (meta: { collapsed: boolean; outputCount: number }) => void;
};

function FileViewerPanelImpl({
  sessionId,
  pinned,
  onTogglePin,
  muted,
  onToggleMute,
  onCanvasMeta,
  ...props
}: FileViewerPanelProps) {
  // Stable session id for the Outputs rail (BUG1, run_26981f66). The `sessionId`
  // prop briefly flips to undefined during tab-switch / canvas-open (ChatPage
  // sets it undefined transiently). Gating the rail on the raw prop made the
  // whole Outputs block unmount+remount across that flicker — the visible half
  // of the "outputs slower than file-open" bug (the data half is fixed in
  // useReferencedFiles). Remember the last DEFINED id and drive the rail from it,
  // so a transient undefined keeps the rail mounted showing the same session's
  // outputs. Cross-session safety: a real switch delivers a NEW defined id, which
  // overwrites this ref and reloads (per-tab canvas.isOpen already prevents a
  // fresh tab from mounting the rail at all — Gate-2 Attack 1).
  const lastDefinedSessionIdRef = useRef<string | undefined>(sessionId);
  if (sessionId !== undefined) lastDefinedSessionIdRef.current = sessionId;
  const stableSessionId = sessionId ?? lastDefinedSessionIdRef.current;

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
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);
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
      setWidth(PANEL_CONSTANTS.MAX_WIDTH);
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
  const [outputsCollapsed, setOutputsCollapsed] = useState(() => getStoredBool(OUTPUTS_COLLAPSED_KEY));
  const [railed, setRailed] = useState(() => getStoredBool(RAILED_KEY));
  const toggleOutputs = useCallback(() => {
    setOutputsCollapsed((v) => { const n = !v; setStoredBool(OUTPUTS_COLLAPSED_KEY, n); return n; });
  }, []);
  const collapseToRail = useCallback(() => { setRailed(true); setStoredBool(RAILED_KEY, true); }, []);
  const expandFromRail = useCallback(() => { setRailed(false); setStoredBool(RAILED_KEY, false); }, []);

  // The file shown in Region B — drives the accent left-bar on its output row.
  const selectedPath = props.initialFile?.filePath;

  // Output counts published by the rail — drives the header summary.
  const [counts, setCounts] = useState<{ total: number; neu: number; upd: number }>({ total: 0, neu: 0, upd: 0 });

  // Report panel-internal state (outputCount) UP so the parent can fold it into
  // the swarm:canvas-state proprioception event. `collapsed` is always false now
  // (kept in the payload for a stable contract with the parent + proprioception
  // schema; the dock that set it true is gone). Fires on change; the parent
  // equality-guards the actual DOM dispatch. Stale-count safety does NOT rely on
  // an unmount reset here (this effect has no cleanup): the parent's close()
  // clears outputCount and the count is per-tab slice state, so switching/closing
  // a tab restores/zeroes it at the source.
  useEffect(() => {
    onCanvasMeta?.({ collapsed: false, outputCount: counts.total });
    // Deps: only counts.total. onCanvasMeta is called SYNCHRONOUSLY (not a
    // subscription that must re-bind), and the parent does not always memoize
    // it — including it here re-fired the effect on every parent render, causing
    // a redundant swarm:canvas-state emit (REVIEW F1). The parent value-guards
    // the actual DOM dispatch, but we avoid the wasteful call at the source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [counts.total]);

  // Persist width changes
  const updateWidth = useCallback((newWidth: number) => {
    const clamped = Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, newWidth));
    setWidth(clamped);
    localStorage.setItem(PANEL_CONSTANTS.STORAGE_KEY, String(clamped));
  }, []);

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
  }, [width]);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Dragging the left edge: moving left = wider, right = narrower
      const delta = startXRef.current - e.clientX;
      updateWidth(startWidthRef.current + delta);
    };

    const handleMouseUp = () => {
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
    };
  }, [isDragging, updateWidth]);

  // Responsive default follows the viewport — recompute on window resize, but ONLY
  // when the user has NOT manually sized the panel (no localStorage) — a dragged
  // width is authoritative and immune to resize. Gate-1 CRITICAL fixes:
  //  · `!expanded`  — expand snaps width to MAX_WIDTH via setWidth (NOT updateWidth,
  //    so it never persists); recomputing during expand would clobber MAX_WIDTH and
  //    leave a stale preExpandWidthRef.
  //  · `entered`    — the mount reveal eases MIN→width via a CSS width transition;
  //    a mid-animation setWidth restarts/janks it.
  // NOT persisted here (setWidth, not updateWidth): a resize is not a manual choice,
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
            <span className="canvas-rail-text text-[11px] text-[var(--color-text-muted)]">
              {counts.total} file{counts.total !== 1 ? 's' : ''}
              {counts.neu > 0 && <span className="text-green-400"> · {counts.neu} new</span>}
            </span>
          )}
        </button>
        {/* Rail-count must stay LIVE: the rail displays counts.total, but the rail
            component is the ONLY source of counts (onCounts→setCounts). Keep it
            MOUNTED-but-hidden while railed (display:none) so a file written while
            collapsed still bumps the strip's "N files" — otherwise the count
            freezes at the pre-rail value (the self-suppressing-count class,
            IMPROVEMENT.md:7). Zero-size, no visual footprint. */}
        {stableSessionId !== undefined && (
          <div className="hidden" aria-hidden="true">
            <CanvasOutputRail sessionId={stableSessionId} onCounts={setCounts} selectedPath={selectedPath} />
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
      {/* Resize handle — left edge, doubles as the colorful Canvas↔chat divider */}
      <div
        className={`canvas-divider w-0.5 cursor-col-resize transition-colors flex-shrink-0 ${
          isDragging ? 'opacity-100' : 'hover:opacity-100'
        }`}
        onMouseDown={handleMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={PANEL_CONSTANTS.MIN_WIDTH}
        aria-valuemax={PANEL_CONSTANTS.MAX_WIDTH}
        aria-label="Resize file viewer panel"
        data-testid="panel-resize-handle"
      >
        {/* Wider hit area for easier drag start */}
        <div className="absolute top-0 -left-1 w-3 h-full" aria-hidden="true" />
      </div>

      {/* Canvas column: output rail (session deliverables) + the file surface.
          `canvas-content-fade` eases content opacity in behind the width grow so
          text doesn't smear while the panel is still widening (bug5). */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden canvas-content-fade">
        {/* Canvas header — output stream + gentle auto-surface controls.
            Rendered only when Canvas props are wired (sessionId provided).
            Layout: a min-w-0 truncating title/summary on the left + a
            flex-shrink-0 action cluster on the right, so buttons never get
            occluded on narrow widths (item 3). */}
        {stableSessionId !== undefined && (
          <div
            className="flex-shrink-0 border-b border-[var(--color-primary)] canvas-outputs-navbar"
            data-testid="canvas-region-outputs"
          >
            <div className="flex items-center gap-2 px-2 h-9 text-[11px] text-[var(--color-text-muted)]">
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
                  <span className="truncate text-[var(--color-text-muted)]">
                    {counts.total}
                    {(counts.neu > 0 || counts.upd > 0) && (
                      <span className="text-[var(--color-text-faint,var(--color-text-muted))]">
                        {' ('}
                        {counts.neu > 0 && <span className="text-green-400">{counts.neu} new</span>}
                        {counts.neu > 0 && counts.upd > 0 && ', '}
                        {counts.upd > 0 && <span className="text-yellow-500">{counts.upd} mod</span>}
                        {')'}
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
                  className={`p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors ${pinned ? 'text-[var(--color-primary)]' : ''}`}
                  title={pinned ? 'Pinned — this file won’t be auto-replaced (click to unpin)' : 'Pin this file — keep it open; a new output won’t replace your view'}
                  aria-label="Toggle pin — keep this file open"
                  aria-pressed={!!pinned}
                >
                  <span className="material-symbols-outlined text-[14px]">keep</span>
                </button>
                <button
                  onClick={onToggleMute}
                  className={`p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors ${muted ? 'text-[var(--color-primary)]' : ''}`}
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
                  grouped right. Two-state window model: Panel ⇄ Expanded (one
                  toggle) + Close. No collapse-to-dock (removed, bug6). */}
              <div className="flex items-center gap-0.5 shrink-0" data-testid="canvas-window-controls">
                <button
                  onClick={collapseToRail}
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title="Collapse Canvas to a side rail"
                  aria-label="Collapse Canvas to a side rail"
                  data-testid="canvas-collapse-rail-btn"
                >
                  <span className="material-symbols-outlined text-[14px]">right_panel_close</span>
                </button>
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
                  onClick={props.onClose}
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title="Close Canvas"
                  aria-label="Close Canvas"
                >
                  <span className="material-symbols-outlined text-[14px]">close</span>
                </button>
              </div>
            </div>
            {/* Output list — the caret folds ONLY the list (panel keeps full
                width; space goes to the Region-B file). Kept MOUNTED-but-hidden
                when collapsed (not unmounted) so counts + onCanvasMeta stay live
                — the bar's "N files" summary keeps updating while folded. */}
            <div
              className={outputsCollapsed ? 'hidden' : 'max-h-[140px] overflow-y-auto px-1.5 pb-1.5'}
              data-testid="canvas-outputs-list"
            >
              <CanvasOutputRail sessionId={stableSessionId} onCounts={setCounts} selectedPath={selectedPath} />
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
