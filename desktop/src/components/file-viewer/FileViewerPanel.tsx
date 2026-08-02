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

import { useState, useCallback, useEffect, useRef, type CSSProperties } from 'react';
import FileViewer from './FileViewer';
import type { FileViewerProps } from './FileViewer';
import { CanvasOutputRail } from './CanvasOutputRail';

export const PANEL_CONSTANTS = {
  DEFAULT_WIDTH: 500,
  MIN_WIDTH: 320,
  MAX_WIDTH: 1200,
  STORAGE_KEY: 'fileViewerPanelWidth',
} as const;

/** Width of the collapsed narrow outputs dock (item 4). */
const COLLAPSED_WIDTH = 200;

function getStoredWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const stored = localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY);
  if (!stored) return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const parsed = parseInt(stored, 10);
  if (isNaN(parsed)) return PANEL_CONSTANTS.DEFAULT_WIDTH;
  return Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, parsed));
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

export default function FileViewerPanel({
  sessionId,
  pinned,
  onTogglePin,
  muted,
  onToggleMute,
  onCanvasMeta,
  ...props
}: FileViewerPanelProps) {
  const [width, setWidth] = useState(getStoredWidth);
  const [isDragging, setIsDragging] = useState(false);
  // Width-reveal (bug5): mount at width 0, then flip to the real width on the
  // next frame so the CSS `transition: width` eases the panel open — and, as a
  // flex sibling, eases the chat pane narrower in lockstep (no instant "pop").
  // While dragging we must NOT keep the transition (it would lag the cursor).
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, []);
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

  // Collapse → a NARROW right-edge dock showing ONLY the outputs list (the file
  // surface is hidden; the panel stays mounted at a slim fixed width). Clicking
  // a file in the dock re-expands to the full panel. This replaces the old
  // "collapse = disappear" so outputs stay glanceable. Separate from `expanded`
  // (full-width) — collapse is the opposite end.
  const [collapsed, setCollapsed] = useState(false);
  const preCollapseWidthRef = useRef(width);
  const collapse = useCallback(() => {
    // If currently expanded (width=MAX), remember the user's REAL pre-expand
    // width so uncollapse restores that, not MAX (Gate-2 #4 de-sync fix).
    preCollapseWidthRef.current = expanded ? preExpandWidthRef.current : width;
    setCollapsed(true);
    setExpanded(false);
  }, [expanded, width]);
  const uncollapse = useCallback(() => {
    setCollapsed(false);
    setWidth(preCollapseWidthRef.current);
  }, []);

  // BUG-1 fix (E2E): a NEW file arriving while collapsed must not open behind the
  // dock (the collapsed branch hides the file surface → the output would be set
  // but invisible = silent loss). When initialFile's path changes while
  // collapsed, auto-uncollapse so the product actually shows — the "product flies
  // out to you" intent. (auto-surface already respects mute/pin upstream, so if
  // we got a new initialFile at all, it's meant to be shown.)
  const initialFilePath = props.initialFile?.filePath;
  const prevInitialPathRef = useRef(initialFilePath);
  useEffect(() => {
    if (initialFilePath && initialFilePath !== prevInitialPathRef.current && collapsed) {
      setCollapsed(false);
      setWidth(preCollapseWidthRef.current);
    }
    prevInitialPathRef.current = initialFilePath;
  }, [initialFilePath, collapsed]);

  // Output counts published by the rail — drives the header summary.
  const [counts, setCounts] = useState<{ total: number; neu: number; upd: number }>({ total: 0, neu: 0, upd: 0 });

  // Report panel-internal state (collapsed + output count) UP so the parent can
  // fold it into the swarm:canvas-state proprioception event. Fires on change;
  // the parent equality-guards the actual DOM dispatch. (pin/mute/open are parent
  // state already.) On unmount the parent resets these to neutral — no stale leak.
  useEffect(() => {
    onCanvasMeta?.({ collapsed, outputCount: counts.total });
  }, [collapsed, counts.total, onCanvasMeta]);

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

  // ── Collapsed: a narrow right-edge dock showing ONLY the outputs list ──────
  // The file surface is hidden; a slim rail stays glanceable. Clicking a file
  // (swarm:open-file) re-expands. A small chevron re-opens the full panel too.
  if (collapsed && sessionId !== undefined) {
    return (
      <div
        className="relative flex-shrink-0 flex flex-col border-l-2 border-[var(--color-primary)]/40 bg-[var(--color-card)]"
        style={{ width: COLLAPSED_WIDTH }}
        data-testid="file-viewer-panel-collapsed"
      >
        {/* Dock header: expand (chevron + count) on the left, close (X) on the
            right — so the user can dismiss Canvas directly from collapsed mode
            (BUG-2 fix: previously the only exit was expand-then-close). */}
        <div className="flex items-center h-7 border-b border-[var(--color-border)]">
          <button
            onClick={uncollapse}
            className="flex items-center gap-1 flex-1 min-w-0 px-2 h-full text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]"
            title="Expand Canvas"
            aria-label="Expand Canvas"
          >
            <span className="material-symbols-outlined text-[15px] shrink-0">chevron_left</span>
            <span className="truncate">{counts.total > 0 ? counts.total : 'Outputs'}</span>
          </button>
          <button
            onClick={props.onClose}
            className="shrink-0 px-1.5 h-full flex items-center text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]"
            title="Close Canvas"
            aria-label="Close Canvas"
          >
            <span className="material-symbols-outlined text-[15px]">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto" onClickCapture={uncollapse}>
          <CanvasOutputRail sessionId={sessionId} onCounts={setCounts} />
        </div>
      </div>
    );
  }

  return (
    <div
      className={`relative flex-shrink-0 flex ${isDragging ? '' : 'canvas-width-reveal'}`}
      style={{ width: entered ? width : 0, '--spout-tint': canvasTint } as CSSProperties}
      data-testid="file-viewer-panel"
    >
      {/* Spout — a small triangle sitting IN the panel's left edge (inside the
          panel box, not overhanging chat), pointing left toward the chat: reads
          as "this Canvas spouted out of the conversation." Tinted with the
          session accent. Vertically centered on the header row. */}
      <div className="canvas-spout" aria-hidden="true" data-testid="canvas-spout" />
      {/* Resize handle — left edge, doubles as the colorful Canvas↔chat divider */}
      <div
        className={`canvas-divider w-1 cursor-col-resize transition-colors flex-shrink-0 ${
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
        {sessionId !== undefined && (
          <div className="flex-shrink-0 border-b border-[var(--color-border)]">
            <div className="flex items-center gap-2 px-2 h-7 text-[11px] text-[var(--color-text-muted)]">
              {/* Count summary (item 2): "Outputs · N" + new/modified breakdown. */}
              <div className="flex items-center gap-1.5 min-w-0 flex-1">
                <span className="font-semibold tracking-wide uppercase shrink-0">Outputs</span>
                {counts.total > 0 && (
                  <span className="shrink-0 text-[var(--color-text-faint,var(--color-text-muted))]">·</span>
                )}
                {counts.total > 0 && (
                  <span className="truncate">
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
              <div className="flex items-center gap-0.5 shrink-0">
                {/* Pin = THIS file (keep it open, don't let a new output replace
                    the view; auto-surface resumes when unpinned). Mute = THIS
                    SESSION (stop auto-surfacing new outputs entirely). Distinct
                    scopes — kept as two controls on purpose. */}
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
                <button
                  onClick={toggleExpand}
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title={expanded ? 'Restore width' : 'Expand — widen the Canvas for review'}
                  aria-label="Toggle Canvas expand"
                  aria-pressed={expanded}
                >
                  <span className="material-symbols-outlined text-[14px]">{expanded ? 'close_fullscreen' : 'open_in_full'}</span>
                </button>
                <button
                  onClick={collapse}
                  className="p-0.5 rounded hover:bg-[var(--color-hover)] transition-colors"
                  title="Collapse to a narrow outputs dock"
                  aria-label="Collapse Canvas to outputs dock"
                >
                  <span className="material-symbols-outlined text-[14px]">chevron_right</span>
                </button>
              </div>
            </div>
            <div className="max-h-32 overflow-y-auto">
              <CanvasOutputRail sessionId={sessionId} onCounts={setCounts} />
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
