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
};

export default function FileViewerPanel({
  sessionId,
  pinned,
  onTogglePin,
  muted,
  onToggleMute,
  ...props
}: FileViewerPanelProps) {
  const [width, setWidth] = useState(getStoredWidth);
  const [isDragging, setIsDragging] = useState(false);
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

  return (
    <div
      className="relative flex-shrink-0 flex animate-canvas-slide-in"
      style={{ width, '--spout-tint': canvasTint } as CSSProperties}
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

      {/* Canvas column: output rail (session deliverables) + the file surface. */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Canvas header — output stream + gentle auto-surface controls.
            Rendered only when Canvas props are wired (sessionId provided). */}
        {sessionId !== undefined && (
          <div className="flex-shrink-0 border-b border-[var(--color-border)]">
            <div className="flex items-center justify-between px-2 h-7 text-[11px] text-[var(--color-text-muted)]">
              <span className="font-semibold tracking-wide uppercase">Outputs</span>
              <div className="flex items-center gap-0.5">
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
                  title={expanded ? 'Collapse — restore previous width' : 'Expand — widen the Canvas for review'}
                  aria-label="Toggle Canvas expand"
                  aria-pressed={expanded}
                >
                  <span className="material-symbols-outlined text-[14px]">{expanded ? 'close_fullscreen' : 'open_in_full'}</span>
                </button>
              </div>
            </div>
            <div className="max-h-32 overflow-y-auto">
              <CanvasOutputRail sessionId={sessionId} />
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
