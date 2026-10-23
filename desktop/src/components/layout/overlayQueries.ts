/**
 * overlayQueries — the one place an overlay's opening data is defined (design
 * docs/design/left-nav-overlay-single-source.md S3).
 *
 * Each entry is a TanStack Query option set (queryKey + queryFn). The overlay reads
 * it with `useQuery(...)`; the nav prefetches the SAME options on hover/focus
 * (`prefetchNav`). One key, one fetcher, so a reopen inside `OVERLAY_STALE_MS` shows
 * the cached data at once and a hovered item is already loaded when clicked.
 *
 * Writes do not refetch by hand: they call `invalidateOverlay(qc, id)`.
 *
 * Key exports:
 * - `OVERLAY_STALE_MS`   the one freshness window (30 s)
 * - `overlayQuery`       the option builders, per overlay
 * - `NAV_PREFETCH`       what to prefetch for a nav item
 * - `prefetchNav(qc, id)` / `invalidateOverlay(qc, id)`
 */
import type { QueryClient } from '@tanstack/react-query';
import { todosService } from '../../services/todos';
import { pollinateService } from '../../services/pollinate';
import { pipelinesService } from '../../services/pipelines';
import type { OverlayId } from './overlayIds';

/** One freshness window for every overlay: older data is still shown at once, then
 *  refreshed in the background. */
export const OVERLAY_STALE_MS = 30_000;

/** ToDo: the default range and the per-leg fetch cap (a leg at the cap is truncated). */
export const TODO_DEFAULT_DAYS = 30;
export const TODO_FETCH_CAP = 1000;

export const overlayQuery = {
  todo: (windowDays: number | null) => ({
    queryKey: ['overlay', 'todo', windowDays] as const,
    queryFn: async () => {
      // active + windowed terminal, fetched together (the overlay merges them)
      const [list, hist] = await Promise.all([
        todosService.list('swarmws', undefined, TODO_FETCH_CAP),
        todosService.history(TODO_FETCH_CAP, windowDays ?? undefined),
      ]);
      return { list, history: hist.todos };
    },
    staleTime: OVERLAY_STALE_MS,
  }),
  pollinate: () => ({
    queryKey: ['overlay', 'pollinate'] as const,
    queryFn: () => pollinateService.fetchAssets(),
    staleTime: OVERLAY_STALE_MS,
  }),
  pipelineAnalytics: (window: '30d' | 'ytd') => ({
    queryKey: ['overlay', 'pipeline', 'analytics', window] as const,
    queryFn: () => pipelinesService.fetchAnalytics(window),
    staleTime: OVERLAY_STALE_MS,
  }),
  pipelineRunning: () => ({
    queryKey: ['overlay', 'pipeline', 'running'] as const,
    // a light, window-independent read: how many runs are running now
    queryFn: async () => (await pipelinesService.fetchActivePipelines()).filter((r) => r.status === 'running').length,
    staleTime: OVERLAY_STALE_MS,
  }),
};

type AnyOptions = { queryKey: readonly unknown[]; queryFn: () => Promise<unknown>; staleTime: number };

/** What a nav item prefetches: the overlay's default (first-render) queries. */
export const NAV_PREFETCH: Partial<Record<OverlayId, () => AnyOptions[]>> = {
  todo: () => [overlayQuery.todo(TODO_DEFAULT_DAYS)],
  pollinate: () => [overlayQuery.pollinate()],
  pipeline: () => [overlayQuery.pipelineAnalytics('30d'), overlayQuery.pipelineRunning()],
};

/** Prefetch a nav item's overlay data (no-op when fresh). Never throws. */
export function prefetchNav(qc: QueryClient, id: OverlayId): void {
  for (const opts of NAV_PREFETCH[id]?.() ?? []) {
    void qc.prefetchQuery(opts).catch(() => { /* the overlay's own query reports it */ });
  }
}

/** Mark an overlay's data stale after a write; an open overlay refetches. */
export function invalidateOverlay(qc: QueryClient, id: string): Promise<void> {
  return qc.invalidateQueries({ queryKey: ['overlay', id] });
}
