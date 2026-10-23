/**
 * railSsot — the two PURE predicates that de-fragment the Canvas rail (D2 + D3).
 *
 * Before this module the "which kinds belong in the rail" rule was copy-pasted in
 * 4 places (CanvasOutputRail / useReferencedFiles countOutputs + arrival-filter /
 * useCanvasAutoSurface) and the "does this event refer to that stored row" match
 * was reimplemented 4 times — one of them (useCanvasAutoSurface basename compare)
 * was BROKEN for cross-repo same-named files. This module is the single source of
 * truth both consume, so a new kind is added in ONE place and the path match can
 * never regress to a basename compare again.
 *
 * @exports isRailKind — rail membership by review-verdict kind
 * @exports matchesPath — anchored file-identity match (never basename)
 * @exports orderOutputs / railOrder — the rail's rows in display order (newest
 *   firstSeen first); the Canvas default selection opens railOrder()[0]
 *   (docs/design/super-canvas.md)
 * @exports outputRowOpenDetail — the open-file detail a rail row (and the default
 *   selection) dispatches
 */

import type { ChangeStatus, GroupedReferencedFiles, ReferencedFile, ReviewKind } from './useReferencedFiles';

/**
 * Does a review-verdict kind belong in the OUTPUTS rail?
 *
 * DROP: `process` (machine noise, also dropped server-side) and `source` (a mid-run
 * coding edit — suppressed while working; the finish batch re-emits as `source-final`).
 * KEEP: content | knowledge | source-final | external-diff | external-nodiff, and
 * `undefined` (an older backend that sends no kind → keep, no regression).
 *
 * SSOT so the ChatHeader output-count pill and the rail's visible rows can never
 * drift apart — both derive rail membership from THIS one predicate.
 */
export function isRailKind(kind: ReviewKind | undefined): boolean {
  return kind !== 'process' && kind !== 'source';
}

/** The stored-row fields matchesPath needs (a subset of ReferencedFile). */
export interface PathMatchTarget {
  /** Display path (usually workspace-relative). */
  path: string;
  /** Resolved physical absolute path. */
  absolutePath: string;
}

/** The incoming-event fields matchesPath reads. */
export interface PathMatchEvent {
  /** Event path — may be the display path OR an absolute path (resolved-vs-raw asymmetry). */
  path: string;
  /** Optional resolved absolute path the backend attached. */
  absolutePath?: string;
}

/**
 * Does an incoming change/delete event refer to the SAME file as a stored row?
 *
 * Match CONSERVATIVELY/anchored:
 *  1. exact display path (`stored.path === event.path`), OR
 *  2. `stored.absolutePath === event.path` (event carries the absolute form), OR
 *  3. `stored.absolutePath === event.absolutePath` (both absolute), OR
 *  4. an absolute `event.path` ending with `'/' + stored.path` (resolved event vs
 *     relative stored path — the segment boundary `'/'` is REQUIRED so `a.ts` does
 *     not match `xa.ts`).
 *
 * NEVER a bare basename compare — that false-matches an unrelated same-named file
 * in a different repo (the D3 bug at useCanvasAutoSurface.ts:206). A miss is a safe
 * no-op (a lingering row == prior behavior); a false match silently drops a real row.
 */
export function matchesPath(stored: PathMatchTarget, event: PathMatchEvent): boolean {
  if (stored.path === event.path) return true;
  if (stored.absolutePath === event.path) return true;
  if (event.absolutePath !== undefined && stored.absolutePath === event.absolutePath) return true;
  // Segment-anchored suffix: the event's absolute path ends with '/'+relative-stored.
  if (event.path.endsWith(`/${stored.path}`)) return true;
  return false;
}

/**
 * Symmetric "are these two paths the SAME file?" — for comparing two path STRINGS
 * where either could be the resolved/absolute or the raw/relative form (e.g. the
 * currently-open file vs the last auto-opened file in useCanvasAutoSurface).
 *
 * Match: exact equality OR one is a `'/'`-segment-anchored suffix of the other.
 * NEVER a bare basename compare — that false-matches `repo1/src/a.ts` against
 * `repo2/pkg/a.ts` (the D3 bug at useCanvasAutoSurface.ts:206). The `'/'` boundary
 * also prevents `a.ts` matching `xa.ts`.
 */
export function samePath(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.endsWith(`/${b}`)) return true;
  if (b.endsWith(`/${a}`)) return true;
  return false;
}

/** The rail's rows in display order: real deliverables (backend `kind`), newest
 *  first by firstSeen. A/M is a badge, not a sort key (XG 2026-10-23: the first row
 *  is the newest output, the same one a default selection opens). Pure; a missing
 *  `written` (stubbed/legacy caller) yields []. */
export function orderOutputs(grouped: GroupedReferencedFiles | undefined): ReferencedFile[] {
  return (grouped?.written ?? [])
    .filter((f) => isRailKind(f.kind))
    .sort((a, b) => b.firstSeen - a.firstSeen);
}

/** The rail's MAIN rows (Activity rows fold under their own toggle) in display
 *  order. The single ordering both the rail and the panel's default selection use. */
export function railOrder(grouped: GroupedReferencedFiles | undefined): ReferencedFile[] {
  return orderOutputs(grouped).filter((f) => f.role !== 'Activity');
}

/**
 * Build the swarm:open-file event detail for an output row. Pure — unit-tested.
 *
 * CONTENT-DEFAULT (run_d3cc1f2c, XG directive): a row (and the pipeline-finish
 * auto-open) renders the file's CONTENT — `autoDiff:false`; the diff is a per-file
 * "Show Changes" toggle inside FileEditorCore. The ABSOLUTE path is the resolve
 * anchor when present (a source-final row's display `path` is repo-relative and would
 * 404 at /workspace/file/resolve). `baseRef` (run_030dc98e) is a source-final row's
 * `<sha>^` diff baseline; `sessionId` (run_c014a4f3) lets an external file render
 * read-only.
 */
export function outputRowOpenDetail(
  path: string,
  _badge: ChangeStatus | undefined,
  absolutePath?: string,
  baseRef?: string,
  sessionId?: string,
): { path: string; autoDiff: boolean; baseRef?: string; sessionId?: string } {
  return { path: absolutePath || path, autoDiff: false, baseRef, sessionId };
}
