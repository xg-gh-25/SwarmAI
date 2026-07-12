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
import { useState, useEffect } from 'react';
import api from '../services/api';

export type ChangeStatus = 'new' | 'upd';

const DEBOUNCE_MS = 300;

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
 * can't be classified are absent from the map. Debounced + batched.
 */
export function useChangeStatus(paths: string[]): Map<string, ChangeStatus> {
  const [statusMap, setStatusMap] = useState<Map<string, ChangeStatus>>(new Map());
  // Join the paths into a stable key so the effect only re-runs when the SET of
  // paths changes (not on every parent re-render passing a new array identity).
  const key = paths.join('\n');

  useEffect(() => {
    if (paths.length === 0) {
      setStatusMap(new Map());
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      const entries = await Promise.all(
        paths.map(async (p) => [p, await statusForPath(p)] as const),
      );
      if (cancelled) return;
      const next = new Map<string, ChangeStatus>();
      for (const [p, s] of entries) {
        if (s) next.set(p, s);
      }
      setStatusMap(next);
    }, DEBOUNCE_MS);

    return () => {
      // A newer path-set (or unmount) cancels this batch: the `cancelled` flag
      // makes an in-flight batch's result a no-op, so a stale batch can never
      // overwrite a newer one; clearTimeout drops a not-yet-fired debounce.
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures the path SET; `paths` array identity is unstable
  }, [key]);

  return statusMap;
}
