/**
 * overlayTiming — dev-build timing of each overlay open, as a table in the console
 * (docs/design/left-nav-overlay-finish.md §4).
 *
 * For every open, OverlayHost calls `timingOpen` and, once the content's code has
 * mounted, `timingMounted`. The row for that overlay id in
 * `window.__overlayTimings` then holds:
 * - `code_ms` — open → content mounted (its chunk loaded and rendered);
 * - `data_ms` — open → every query the overlay opens with (its NAV_PREFETCH row)
 *   has data; 0 when they all had data at the open;
 * - `cache`   — 'hit' (all data cached at the open), 'miss', or 'n/a' (the overlay
 *   has no prefetch row, e.g. Settings, Workspace);
 * - `at`      — when it was opened.
 * Read it with `console.table(window.__overlayTimings)`. Each row is the latest
 * open of that id. Timing is on only in dev builds (`overlayTiming.enabled`; off in
 * tests and production, and tests flip it to check this module).
 *
 * Key exports: `overlayTiming`, `timingOpen`, `timingMounted`, `timingClose`,
 * `OverlayTimingRow`.
 */
import type { QueryClient } from '@tanstack/react-query';
import type { OverlayId } from './overlayIds';
import { NAV_PREFETCH, type PrefetchCtx } from './overlayQueries';

export interface OverlayTimingRow {
  code_ms: number | null;
  data_ms: number | null;
  cache: 'hit' | 'miss' | 'n/a';
  at: string;
}

export const overlayTiming = { enabled: import.meta.env.DEV && import.meta.env.MODE !== 'test' };

type TimingWindow = Window & { __overlayTimings?: Record<string, OverlayTimingRow> };

let active: { id: OverlayId; t0: number; row: OverlayTimingRow; unsub?: () => void } | null = null;

function report(id: OverlayId, row: OverlayTimingRow): void {
  console.debug(`[overlay] ${id}: code ${row.code_ms ?? '…'} ms, data ${row.data_ms ?? '…'} ms (${row.cache})`);
}

/** Start timing an open of `id` (no-op when timing is off). */
export function timingOpen(qc: QueryClient | undefined, id: OverlayId, ctx: PrefetchCtx = {}): void {
  timingClose();
  if (!overlayTiming.enabled) return;
  const keys = (NAV_PREFETCH[id]?.(ctx) ?? []).map((o) => o.queryKey);
  const allData = () => keys.every((k) => qc?.getQueryData(k) !== undefined);
  const cache: OverlayTimingRow['cache'] = !qc || keys.length === 0 ? 'n/a' : allData() ? 'hit' : 'miss';
  const row: OverlayTimingRow = { code_ms: null, data_ms: cache === 'hit' ? 0 : null, cache, at: new Date().toISOString() };
  const t0 = performance.now();
  const w = window as TimingWindow;
  (w.__overlayTimings ??= {})[id] = row;
  const a: NonNullable<typeof active> = { id, t0, row };
  active = a;
  if (cache === 'miss' && qc) {
    a.unsub = qc.getQueryCache().subscribe(() => {
      if (row.data_ms == null && allData()) {
        row.data_ms = Math.round(performance.now() - t0);
        a.unsub?.();
        a.unsub = undefined;
        report(id, row);
      }
    });
  }
}

/** The content of `id` has mounted (its code is loaded). */
export function timingMounted(id: OverlayId): void {
  if (!active || active.id !== id || active.row.code_ms != null) return;
  active.row.code_ms = Math.round(performance.now() - active.t0);
  report(id, active.row);
}

/** Stop watching the current open (on close, or before the next open). */
export function timingClose(): void {
  active?.unsub?.();
  active = null;
}
