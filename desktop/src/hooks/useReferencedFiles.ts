/**
 * useReferencedFiles — the Canvas Artifacts rail rows of one chat session.
 *
 * The rows are the BACKEND's (services/canvasSession → core/session_outputs: the
 * session's attribution record, written as its turns run, joined with the product
 * catalog). This hook maps them to the rail's ReferencedFile shape; it keeps no list
 * of its own, so live, reloaded and restarted sessions read the same rows. The live
 * `swarm:file-changed` events only trigger the store's refresh.
 *
 * (Before: the rail was this hook's own sessionStorage list built from the events;
 * the description below of the event payload still holds.)
 *
 * Consumes the UNIFIED backend file-change event `swarm:file-changed`
 * (run_e626e121) — the single backend-authoritative Canvas signal that replaced
 * the old frontend summary-parse trigger. The event carries a RESOLVED physical
 * `absolutePath` (so copy-path yields a real path, not an unresolved string) and a
 * `relevance` classification; bookkeeping files are already dropped server-side, so
 * everything arriving here is a deliverable or incidental (rail) file. Maintains a
 * deduplicated, grouped list persisted in sessionStorage (survives remounts, resets
 * on new session).
 *
 * @exports useReferencedFiles hook
 * @exports ReferencedFile interface
 */

import { useMemo } from 'react';
import { isRailKind } from './railSsot';
import {
  cachedArtifacts, sessionForTab, useCanvasSessionArtifacts, type SessionArtifact,
} from '../services/canvasSession';

// The backend emits ONLY write operations (streaming_orchestrator._build_file_change_events
// hardcodes operation:"written"; detection is scoped to write SOURCES — Write/Edit/
// NotebookEdit + parseable Bash redirection — per the run_e626e121 directive #6). There is
// no read/grep/list tracking, so 'read'/'searched' were never-populated dead values; the
// type is narrowed to what is actually emitted. FileRelevance below is SEPARATE and
// load-bearing (incidental = the SSE bridge's fail-closed default for an older backend).
// 'written' is the only op STORED in the rail; 'deleted' is a transient SIGNAL
// (G1, run_5a7be540) that REMOVES a stored row — it is never itself stored.
export type FileOperation = 'written';
export type FileChangeOperation = 'written' | 'deleted';
export type FileRelevance = 'deliverable' | 'incidental' | 'bookkeeping';

/** Unified review-verdict kind (backend needs_human_review):
 *  - content|knowledge → surface in the rail (+auto-pop) — immediate, per change.
 *  - source → mid-run coding edit; DROPPED from the rail (suppressed mid-run).
 *  - source-final → the pipeline-FINISH PR-review batch (run_b8ea6d5c): coding
 *    files a run committed, emitted once at COMPLETE via surface_run_outputs.
 *    ACCEPTED into the rail (persistent rows) but does NOT auto-pop (a finish batch
 *    of N files must not hijack the Canvas — the user clicks a row to review it).
 *  - external-diff → a file the session touched OUTSIDE SwarmWS that lives in a git
 *    repo (run_5d9178bf): surfaced with a per-file diff (baseRef=<sha>^).
 *  - external-nodiff → a session-touched file outside SwarmWS with NO owning git
 *    repo (plain FS): LISTED only, Show-Changes disabled (no diff to show).
 *  - process → never (dropped server-side). */
export type ReviewKind =
  | 'content'
  | 'knowledge'
  | 'source'
  | 'source-final'
  | 'external-diff'
  | 'external-nodiff'
  | 'process';

export interface ReferencedFile {
  /** File path as emitted (workspace-relative for display) */
  path: string;
  /** Resolved PHYSICAL absolute path (for clipboard copy) — real path, not a copy of `path` */
  absolutePath: string;
  /** Basename for display */
  fileName: string;
  /** How this file was referenced */
  operation: FileOperation;
  /** When first seen (ms since epoch) */
  firstSeen: number;
  /** Number of times referenced */
  count: number;
  /** Unified review verdict kind (undefined from an older backend). */
  kind?: ReviewKind;
  /** Git ref to diff this file AGAINST (run_030dc98e): a source-final row carries
   *  `<sha>^` (the pre-run parent of the commit that introduced this run's change) so
   *  the OUTPUTS row opens on the this-run diff, not an empty HEAD-vs-working-tree one.
   *  Absent for content/knowledge rows → they diff against HEAD (correct, uncommitted). */
  baseRef?: string;
  /** run_c014a4f3: the owning session id that SURFACED this row. Threaded to the
   *  render fetch (GET /workspace/file?session_id=) so an EXTERNAL (outside-SwarmWS)
   *  file this session surfaced can render read-only. Absent for SwarmWS-internal
   *  rows (they render via the home-only guard, no session id needed). */
  sessionId?: string;
  /** run_5d9178bf: the file was DELETED this session. The row PERSISTS (struck-through,
   *  Show-Changes disabled) instead of being removed — "the user should see what I
   *  deleted" (XG). Undefined/false = a live written row. */
  deleted?: boolean;
  /** NEW (not in its repo's HEAD) / UPD (in HEAD), from the backend's `inHead`;
   *  undefined when it cannot be told (no repo, no file). */
  change?: ChangeStatus;
  /** Product catalog role (core/product_registry): Deliverables | Knowledge |
   *  Pipeline | Activity | Other. The rail folds Activity rows. */
  role?: string;
}

/** The rail's git badge: NEW = not in the repo's HEAD, UPD = in it. */
export type ChangeStatus = 'new' | 'upd';

/** Detail shape of the unified `swarm:file-changed` event (from the SSE bridge). */
export interface FileChangedDetail {
  path: string;
  /** Resolved physical absolute path (backend-resolved once). */
  absolutePath?: string;
  /** 'written' adds/updates a row; 'deleted' (G1) REMOVES the matching row. */
  operation: FileChangeOperation;
  /** Backend whitelist classification; bookkeeping is pre-filtered server-side. */
  relevance?: FileRelevance;
  /** Unified review verdict kind: content|knowledge → rail (immediate); source →
   *  dropped (mid-run coding edit); source-final → rail (the pipeline-finish PR-review
   *  batch, run_b8ea6d5c); process → never. Undefined from an older backend → the
   *  consumer falls back to `relevance` (migration window). */
  kind?: ReviewKind;
  /** Git ref to diff against (run_030dc98e) — a source-final row's `<sha>^`. Threaded
   *  onto the stored ReferencedFile so the row's click carries it to the diff baseline. */
  baseRef?: string;
  /** Owning TAB id (stamped by the SSE bridge, run_26aa6caa — was sessionId). The
   *  consumer filters on it to ignore background-tab writes. tabId is stable and
   *  always present (unlike sessionId, which is undefined during a new tab's first
   *  turn — the unstamped fail-open window that bled writes across tabs). Absent →
   *  treated as current (fail-open, older-backend migration only). */
  tabId?: string;
  /** Owning SESSION id (run_c014a4f3, distinct from tabId). Emitted by the backend
   *  ONLY for an external (outside-SwarmWS) surfaced file, so the render fetch can
   *  prove "this session surfaced this path" to GET /workspace/file. Absent for
   *  SwarmWS-internal rows. NOT used for tab-scope filtering (that's tabId). */
  sessionId?: string;
}

/** The grouped shape this hook returns (and that CanvasOutputRail consumes). Only
 *  the 'written' key is ever populated (see FileOperation). Exported so the resident
 *  owner (useCanvasHost) can type the value it lifts + passes down to the rail. */
export type GroupedReferencedFiles = Record<FileOperation, ReferencedFile[]>;

/** SSOT for the ChatHeader output-count PILL = the count of LIVE outputs the user
 *  hasn't reviewed. Drops process/source machine-noise + mid-run coding edits (via
 *  isRailKind); keeps content/knowledge/source-final/external-* (+ undefined kind from
 *  an older backend → keep, no regression). run_5d9178bf: ALSO excludes `deleted` rows
 *  — a struck-through deleted row PERSISTS in the rail as activity history, but it is
 *  NOT a pending output to nag about, so the pill (an "unseen new outputs" badge) must
 *  not count it. The rail's VISIBLE rows (CanvasOutputRail `outputs`) still INCLUDE the
 *  deleted rows (the ledger) — so the pill count and the rail row count legitimately
 *  differ by the number of deleted rows; both derive their kind-filter from isRailKind. */
export function countOutputs(grouped: GroupedReferencedFiles | undefined): number {
  if (!grouped) return 0;
  return (grouped.written ?? []).filter((f) => isRailKind(f.kind) && !f.deleted).length;
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || path;
}

/** One backend artifact as a rail row. */
export function toReferencedFile(a: SessionArtifact, sessionId?: string): ReferencedFile {
  const t = Date.parse(a.firstAt);
  return {
    path: a.path,
    absolutePath: a.absolutePath,
    fileName: basename(a.path),
    operation: 'written',
    firstSeen: Number.isNaN(t) ? 0 : t,
    count: a.writes,
    kind: a.kind as ReviewKind | undefined,
    baseRef: a.baseRef,
    sessionId,
    deleted: a.deleted || !a.exists,
    role: a.role,
    change: a.inHead === true ? 'upd' : a.inHead === false ? 'new' : undefined,
  };
}

/** The rail's grouping: by operation, newest first. */
export function groupArtifacts(artifacts: SessionArtifact[] | undefined, sessionId?: string): GroupedReferencedFiles {
  const written = (artifacts ?? []).map((a) => toReferencedFile(a, sessionId));
  written.sort((a, b) => b.firstSeen - a.firstSeen);
  return { written };
}

/** Output count of ANY tab, from its session's cached rows (the store). */
export function countStoredOutputs(tabId: string): number {
  const sid = sessionForTab(tabId);
  return countOutputs(groupArtifacts(cachedArtifacts(sid), sid ?? undefined));
}

/** The rail rows of *sessionId* (empty until its first load). */
export function useReferencedFiles(sessionId: string | null | undefined) {
  const sid = sessionId ?? null;
  const artifacts = useCanvasSessionArtifacts(sid);
  const files = useMemo(() => groupArtifacts(artifacts, sid ?? undefined), [artifacts, sid]);
  return { files, totalCount: files.written.length };
}
