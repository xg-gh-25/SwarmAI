/**
 * overlayShell — the thin, SHARED frame primitives for workbench surfaces
 * (OverlayHost subsystem, M4, run_fdeaead8). These are NOT a "WorkbenchShell"
 * god-component: each surface keeps its own views / banners / forms / drawer
 * CONTENT. What these capture is the genuinely-identical, repeated chrome that
 * was hand-rolled 3-5× across ToDo/Jobs/Pipeline/Pollinate:
 *
 *   • fmtTs           — absolute "YYYY-MM-DD HH:MM" stamp (XG: no "1 hour ago").
 *                       3 byte-identical copies collapsed to one.
 *   • WorkbenchToolbar — the sub-header bar: left slot + "Loading…" + right slot.
 *                       4 copies (`flex items-center px-4 py-2 border-b`).
 *   • OverlayDrawer   — the right-side detail/create drawer POSITIONING shell
 *                       (`absolute inset-y-0 right-0 z-N`, border-l, stop-propagation).
 *                       5 copies. Extracting the *positioning* keeps geometry in one
 *                       place (the D5 "one geometry authority" principle applied to
 *                       drawers too) while each drawer supplies its own header+body
 *                       as children — so no header structure is forced.
 *
 * Everything here is presentational + stateless. The host (OverlayHost) still owns
 * the outer scrim/panel/spout; these live INSIDE a surface's render(ctx).
 */
import type { ReactNode } from 'react';
import clsx from 'clsx';

/** Absolute timestamp (XG rule: no relative "1 hour ago"). Tolerates null/invalid → —.
 *  The single source for what were 3 identical per-overlay copies. */
export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * The workbench sub-header bar (below the host's chrome header): a left region
 * (view toggle / label), an optional "Loading…" hint, a flex spacer, and a right
 * region (actions / window toggle). `gap` matches the original per-surface spacing.
 */
export function WorkbenchToolbar({
  left, right, loading, gap = 2, testid,
}: {
  left?: ReactNode;
  right?: ReactNode;
  loading?: boolean;
  gap?: 1 | 2;
  testid?: string;
}) {
  return (
    <div
      className={clsx(
        'flex items-center px-4 py-2 border-b border-[var(--color-border)]',
        gap === 1 ? 'gap-1' : 'gap-2',
      )}
      data-testid={testid}
    >
      {left}
      {loading && <span className="ml-2 text-[11px] text-[var(--color-text-faint)]">Loading…</span>}
      <div className="flex-1" />
      {right}
    </div>
  );
}

/**
 * The right-side drawer POSITIONING shell (detail drawers + inline create forms).
 * Absolute, anchored inset-y/right-0 inside the surface's `relative` root, so the
 * roster/board never compresses (it is layered OVER, not a flex sibling). Owns ONLY
 * the frame + width + z-order + click-stop; each drawer passes its own header+body
 * as `children` (and its own `data-testid` for the tests that assert it).
 *
 * z: 10 = detail drawer (default); 20 = a create form that must sit ABOVE a detail
 * drawer (Jobs' New Job over a selected job — mutual-exclusion is the surface's job,
 * the z just guarantees stacking order if both ever render).
 */
export function OverlayDrawer({
  widthPx, maxWidthPct = 90, z = 10, testid, children, stopPropagation = true,
}: {
  /** Fixed drawer width in px (per-surface: 360 ToDo, 420 Jobs/Pipeline, 460 Pollinate). */
  widthPx: number;
  /** Cap as a % of the surface so it never overflows on a narrow panel. */
  maxWidthPct?: number;
  z?: 10 | 20;
  testid?: string;
  children: ReactNode;
  /** Detail/create drawers stop clicks from bubbling to a scrim/board behind them. */
  stopPropagation?: boolean;
}) {
  return (
    <div
      className="absolute inset-y-0 right-0 bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl flex flex-col"
      style={{ width: widthPx, maxWidth: `${maxWidthPct}%`, zIndex: z }}
      data-testid={testid}
      onClick={stopPropagation ? (e) => e.stopPropagation() : undefined}
    >
      {children}
    </div>
  );
}
