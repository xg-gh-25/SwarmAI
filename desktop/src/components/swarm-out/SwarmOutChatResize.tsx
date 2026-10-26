/**
 * Swarm Out chat column width: drag its left edge to widen it to the left.
 *
 * When the Swarm Out tab is active, ChatPage lays the row out as
 * [SwarmOutDashboard (flex 1)] [chat column (this width)] [Canvas]. The chat column
 * takes `width` px; the user drags the seam on its left edge (or uses the arrow keys
 * on it) and the dashboard gives up the space. Double-click resets to the default.
 *
 * - **Bounds.** At least `MIN_WIDTH`; at most what leaves the dashboard
 *   `DASHBOARD_MIN` px, measured at drag start from the two columns' live widths.
 *   The column's CSS `maxWidth` (`chatColumnStyle`) holds the same floor for the
 *   dashboard when the window later shrinks, so a stored width never hides it.
 * - **Zoom.** Pointer deltas are client px; widths are CSS px. The delta is scaled
 *   by `clientToCssScale()` taken at drag start (the same rule the Canvas seam uses).
 * - **Persistence.** One localStorage value, written on drag end / key press, never
 *   per frame. Moves are coalesced to one state update per animation frame.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactElement } from 'react';
import { clientToCssScale } from '../../utils/layoutViewport';

export const SWARM_OUT_CHAT = {
  DEFAULT_WIDTH: 560,
  MIN_WIDTH: 360,
  DASHBOARD_MIN: 420,
  KEY_STEP: 24,
  STORAGE_KEY: 'swarmai.swarmOut.chatWidth',
} as const;

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(SWARM_OUT_CHAT.STORAGE_KEY);
    const n = raw == null ? NaN : Number(raw);
    if (Number.isFinite(n)) return Math.max(SWARM_OUT_CHAT.MIN_WIDTH, Math.round(n));
  } catch { /* storage unavailable: use the default */ }
  return SWARM_OUT_CHAT.DEFAULT_WIDTH;
}

function storeWidth(w: number): void {
  try { localStorage.setItem(SWARM_OUT_CHAT.STORAGE_KEY, String(Math.round(w))); } catch { /* ignore */ }
}

/** The widest the chat column may get: the dashboard keeps DASHBOARD_MIN. `seam` is
 *  the handle; its parent is the chat column, whose previous sibling is the dashboard. */
export function dragCeiling(seam: HTMLElement | null, scale: number): number {
  const column = seam?.parentElement;
  const dashboard = column?.previousElementSibling as HTMLElement | null | undefined;
  if (!column || !dashboard) return Number.POSITIVE_INFINITY;
  const total = (column.getBoundingClientRect().width + dashboard.getBoundingClientRect().width) * scale;
  if (total <= 0) return Number.POSITIVE_INFINITY; // no layout yet (jsdom)
  return Math.max(SWARM_OUT_CHAT.MIN_WIDTH, total - SWARM_OUT_CHAT.DASHBOARD_MIN);
}

export function clampWidth(w: number, ceiling: number): number {
  return Math.round(Math.max(SWARM_OUT_CHAT.MIN_WIDTH, Math.min(ceiling, w)));
}

/** Inline style for the chat column while Swarm Out is active. */
export function chatColumnStyle(width: number): CSSProperties {
  return {
    flex: `0 1 ${width}px`,
    minWidth: SWARM_OUT_CHAT.MIN_WIDTH,
    maxWidth: `calc(100% - ${SWARM_OUT_CHAT.DASHBOARD_MIN}px)`,
    borderLeft: '1px solid var(--color-border)',
    position: 'relative',
  };
}

export function useSwarmOutChatWidth(): { width: number; seam: ReactElement } {
  const [width, setWidth] = useState(readStoredWidth);
  const [dragging, setDragging] = useState(false);
  const seamRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ startX: 0, startW: 0, scale: 1, ceiling: Number.POSITIVE_INFINITY, pending: 0 });
  const rafRef = useRef<number | null>(null);
  const widthRef = useRef(width);
  widthRef.current = width;

  const flush = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      setWidth(drag.current.pending);
    }
  }, []);

  useEffect(() => () => { if (rafRef.current !== null) cancelAnimationFrame(rafRef.current); }, []);

  const onPointerDown = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const scale = clientToCssScale();
    drag.current = {
      startX: e.clientX,
      startW: widthRef.current,
      scale,
      ceiling: dragCeiling(seamRef.current, scale),
      pending: widthRef.current,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    setDragging(true);
  }, []);

  const onPointerMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const d = drag.current;
    // Dragging LEFT (clientX decreasing) widens the column.
    d.pending = clampWidth(d.startW + (d.startX - e.clientX) * d.scale, d.ceiling);
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        setWidth(drag.current.pending);
      });
    }
  }, [dragging]);

  const endDrag = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    flush();
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    setDragging(false);
    storeWidth(drag.current.pending);
  }, [dragging, flush]);

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowLeft' ? SWARM_OUT_CHAT.KEY_STEP : e.key === 'ArrowRight' ? -SWARM_OUT_CHAT.KEY_STEP : 0;
    if (!step) return;
    e.preventDefault();
    const next = clampWidth(widthRef.current + step, dragCeiling(seamRef.current, clientToCssScale()));
    setWidth(next);
    storeWidth(next);
  }, []);

  const onDoubleClick = useCallback(() => {
    setWidth(SWARM_OUT_CHAT.DEFAULT_WIDTH);
    storeWidth(SWARM_OUT_CHAT.DEFAULT_WIDTH);
  }, []);

  const seam = (
    <div
      ref={seamRef}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize Swarm Out chat"
      aria-valuenow={width}
      aria-valuemin={SWARM_OUT_CHAT.MIN_WIDTH}
      tabIndex={0}
      title="拖动调整宽度 · 双击恢复默认"
      data-testid="swarm-out-chat-resize"
      className={`absolute top-0 left-0 h-full w-[6px] z-20 cursor-col-resize transition-colors ${
        dragging ? 'bg-[var(--color-zone-work)]' : 'hover:bg-[color-mix(in_srgb,var(--color-zone-work)_60%,transparent)]'
      }`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
    />
  );

  return { width, seam };
}
