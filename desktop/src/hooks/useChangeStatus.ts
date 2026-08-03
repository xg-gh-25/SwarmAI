/**
 * useChangeStatus — resolves the git change status (NEW vs UPD) for a set of
 * session-written files, for the Radar ✍ Changes section badges.
 *
 * Why this exists (Run-B fix): the first Changes implementation read the badge
 * from the SwarmWS workspace tree's gitStatus — but the files the agent writes
 * are usually SOURCE-REPO files (e.g. /Users/.../swarmai/backend/core/foo.py),
 * which the workspace tree does not cover → every such file got NO badge. This
 * hook instead asks git directly, per file, via endpoints that DO span repos.
 *
 * For each path it: (1) resolves it to a canonical path
 * (/workspace/file/resolve — a no-op for an absolute path, needed for a
 * repo-relative one), then (2) fetches the committed (HEAD) info
 * (/workspace/file/committed). The badge is driven by the `in_head`
 * discriminator, NOT by content length:
 *   - in_head === false → 'new'  (definitively untracked / not in HEAD)
 *   - in_head === true  → 'upd'  (tracked — INCLUDING a tracked binary or a
 *                                  genuinely-empty tracked file, both of which
 *                                  return empty content but are still modified)
 *   - in_head == null   → null   (undetermined: resolver-rejected / no git repo
 *                                  / git error) → no badge
 * Keying off content length was wrong: a tracked binary or an empty tracked
 * file returns "" yet is 'upd', not 'new' (run_46e7b94c). If resolve OR
 * committed fails, the file is omitted from the map → no badge (fail-soft,
 * never a crash, never a wrong badge).
 *
 * Fetches are debounced (the written list mutates as tools run) and run in
 * parallel per batch.
 *
 * @exports useChangeStatus
 * @exports classifyCommitted — pure in_head→status (unit-tested)
 * @exports ChangeStatus
 */
import { useState, useEffect, useRef } from 'react';
import api from '../services/api';

export type ChangeStatus = 'new' | 'upd';

// Small debounce: coalesce the burst of path-set changes while tools run, but
// short enough that the badges feel near-instant (was 300ms — too slow for the
// Canvas outputs rail, run_aee19d4f).
const DEBOUNCE_MS = 60;

/** Pure classification from the committed endpoint's `in_head` discriminator:
 *  not-in-HEAD → NEW (untracked); in-HEAD → UPD (modified, even if the tracked
 *  content is empty/binary); undetermined (null) → no badge (return null). */
export function classifyCommitted(inHead: boolean | null | undefined): ChangeStatus | null {
  if (inHead === null || inHead === undefined) return null;
  return inHead ? 'upd' : 'new';
}

/**
 * Resolve one path → committed → status. Returns null on any failure (the file
 * git can't locate) so the caller omits it (no badge). `api`'s baseURL already
 * includes `/api`, so paths here are `/workspace/...`.
 */
async function statusForPath(path: string): Promise<ChangeStatus | null> {
  try {
    const r = await api.get<{ resolved_path: string }>('/workspace/file/resolve', {
      params: { path },
    });
    const resolved = r.data?.resolved_path;
    if (!resolved) return null;
    const c = await api.get<{ content: string; in_head?: boolean | null }>(
      '/workspace/file/committed',
      { params: { path: resolved } },
    );
    return classifyCommitted(c.data?.in_head);
  } catch {
    return null; // fail-soft: unresolvable/untraceable → no badge
  }
}

/**
 * Given the written-file paths, return a Map<path, 'new'|'upd'>. Paths that
 * can't be classified are absent from the map.
 *
 * PERF (run_aee19d4f): the Canvas outputs rail felt slow — slower than file-open
 * — because the old version re-fetched (resolve+committed = 2 HTTP) for EVERY
 * path on EVERY path-set change. As the agent writes file N, files 1..N-1 (already
 * resolved) were re-queried, an N² round-trip pile-up behind a 300ms debounce.
 * Two fixes: (1) a per-path result CACHE (`resolvedRef`) — a path is fetched at
 * most once, so a growing output list only ever queries the NEW file; (2)
 * PROGRESSIVE application — each fetch that resolves updates the map immediately
 * (functional setState), so a badge appears as soon as ITS file is classified
 * instead of the whole batch waiting on the slowest file. The rail already renders
 * names with no badge (badge=undefined), so the list is visible instantly and
 * badges fill in behind it — the list never waits on git.
 */
export function useChangeStatus(paths: string[]): Map<string, ChangeStatus> {
  const [statusMap, setStatusMap] = useState<Map<string, ChangeStatus>>(new Map());
  // Per-path memo: path → resolved status (or null = classified-as-no-badge).
  // A path present here (even with null) is NEVER re-fetched — UNLESS invalidated
  // by a fresh write to that same path (below). Ref, not state: it's a cache, not
  // render input (the render input is statusMap).
  const resolvedRef = useRef<Map<string, ChangeStatus | null>>(new Map());
  // Join the paths into a stable key so the effect only re-runs when the SET of
  // paths changes (not on every parent re-render passing a new array identity).
  const key = paths.join('\n');
  // Bumped on cache invalidation so the resolve effect re-runs even when the
  // path SET is unchanged (a re-write of an already-listed file).
  const [healTick, setHealTick] = useState(0);

  // Cache self-healing (Gate-2 MEDIUM, run_aee19d4f): the per-path cache would
  // otherwise pin a path's FIRST-observed status forever, so a file that goes
  // new→committed (or upd→reverted) WITHIN the session keeps a stale badge — and
  // the outputs rail couples click-to-diff to badge==='upd', so a stale badge
  // mis-routes the open. Fix: when the SAME path is written again (the exact
  // signal that its git status may have changed), drop its cache entry + its
  // current badge so the next resolve re-fetches fresh. Consumes the UNIFIED
  // 'swarm:file-changed' event (run_e626e121) — the 4th consumer of the single
  // backend-authoritative signal (window-dispatched, like the other three).
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ path?: string; operation?: string }>).detail;
      const p = detail?.path;
      if (!p || detail?.operation !== 'written') return;
      if (!resolvedRef.current.has(p)) return; // not cached → nothing to heal
      resolvedRef.current.delete(p);
      // Also drop the stale badge from the visible map; the resolve effect below
      // re-fetches it. Functional update to avoid a stale close.
      setStatusMap((prev) => {
        if (!prev.has(p)) return prev;
        const next = new Map(prev);
        next.delete(p);
        return next;
      });
      // Bump the tick so the resolve effect re-runs even if the path SET is
      // unchanged (this same file was already listed) → the now-uncached path
      // lands back in `unresolved` and gets re-fetched.
      setHealTick((t) => t + 1);
    };
    window.addEventListener('swarm:file-changed', handler);
    return () => window.removeEventListener('swarm:file-changed', handler);
  }, []);

  useEffect(() => {
    if (paths.length === 0) {
      // Prune the render map to empty, but KEEP the resolve cache — the same
      // files often reappear (tab switch) and shouldn't re-hit the network.
      setStatusMap((prev) => (prev.size === 0 ? prev : new Map()));
      return;
    }

    // Rebuild the visible map from cache HITS synchronously (no network, no
    // debounce) so known badges show instantly on any path-set change.
    setStatusMap(() => {
      const m = new Map<string, ChangeStatus>();
      for (const p of paths) {
        const cached = resolvedRef.current.get(p);
        if (cached) m.set(p, cached);
      }
      return m;
    });

    // Only the paths we've never resolved need a fetch.
    const unresolved = paths.filter((p) => !resolvedRef.current.has(p));
    if (unresolved.length === 0) return;

    let cancelled = false;
    const timer = setTimeout(() => {
      // Fetch each unresolved path independently; apply its result the moment it
      // lands (progressive) rather than awaiting the whole batch.
      for (const p of unresolved) {
        statusForPath(p).then((s) => {
          if (cancelled) return;
          resolvedRef.current.set(p, s);
          if (!s) return; // classified as no-badge: cached, nothing to show
          setStatusMap((prev) => {
            const next = new Map(prev);
            next.set(p, s);
            return next;
          });
        });
      }
    }, DEBOUNCE_MS);

    return () => {
      // A newer path-set (or unmount) cancels these fetches: the `cancelled` flag
      // makes in-flight results no-ops; clearTimeout drops a not-yet-fired debounce.
      // The resolve cache persists (results already applied stay valid).
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures the path SET; `paths` array identity is unstable; healTick forces a re-resolve after a cache invalidation
  }, [key, healTick]);

  return statusMap;
}
