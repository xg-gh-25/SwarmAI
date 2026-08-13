/**
 * useReferencedFiles — Tracks files the agent touches during a chat session.
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

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { isRailKind, matchesPath } from './railSsot';
import { subscribeFileChanged } from './fileChangedBroker';

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
  /** run_5d9178bf: the file was DELETED this session. The row PERSISTS (struck-through,
   *  Show-Changes disabled) instead of being removed — "the user should see what I
   *  deleted" (XG). Undefined/false = a live written row. */
  deleted?: boolean;
}

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

const MAX_FILES = 100;
const STORAGE_PREFIX = 'swarm:referenced-files:';

// run_26aa6caa: the rail is keyed by the owning TAB id, not the volatile session
// id. tabId is stable from tab creation and never has an "unresolved" window, so
// (a) storage never collapses to a shared '' bucket and (b) the SSE stamp is
// always present → the fail-open cross-tab bleed is structurally gone. This is the
// SAME key useCanvasHost uses, unifying the whole Canvas surface on one key.
function getStorageKey(tabId: string): string {
  return `${STORAGE_PREFIX}${tabId}`;
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || path;
}

function loadFromStorage(tabId: string): Map<string, ReferencedFile> {
  try {
    const raw = sessionStorage.getItem(getStorageKey(tabId));
    if (raw) {
      const arr: ReferencedFile[] = JSON.parse(raw);
      const map = new Map<string, ReferencedFile>();
      for (const file of arr) {
        map.set(file.path, file);
      }
      return map;
    }
  } catch { /* corrupted — start fresh */ }
  return new Map();
}

function saveToStorage(tabId: string, files: Map<string, ReferencedFile>): void {
  try {
    const arr = Array.from(files.values());
    sessionStorage.setItem(getStorageKey(tabId), JSON.stringify(arr));
  } catch { /* quota exceeded — no-op */ }
}

/** Fields of a `written` event the map-mutation logic reads. */
export interface WriteEvent {
  path: string;
  absolutePath?: string;
  operation: FileOperation;
  kind?: ReviewKind;
  baseRef?: string;
}

/** PURE add/dedup/cap logic (SSOT, run_9dd59523). Extracted from the setFiles updater
 *  so BOTH the active-tab path (via setState) AND the background-tab path (load→apply→
 *  save to the owning tab's storage) run IDENTICAL semantics — no duplication/drift.
 *  Returns a NEW map; never mutates the input. `now` is injectable for deterministic
 *  ordering tests (defaults to Date.now()). */
export function applyWrite(
  prev: Map<string, ReferencedFile>,
  e: WriteEvent,
  now: number = Date.now(),
): Map<string, ReferencedFile> {
  const next = new Map(prev);
  const existing = next.get(e.path);
  if (existing) {
    // Dedup: bump count + refresh the resolved absolutePath/kind/baseRef if a later
    // event carried one the first lacked. operation is always 'written'.
    // run_5d9178bf (Gate-2 HIGH): a WRITE is proof the file is live again, so it
    // CLEARS a prior `deleted` mark — else a delete-then-recreate row would stay
    // struck-through + non-clickable + omitted from the pill forever.
    next.set(e.path, {
      ...existing,
      deleted: false,
      absolutePath: e.absolutePath || existing.absolutePath,
      count: existing.count + 1,
      kind: e.kind ?? existing.kind,
      baseRef: e.baseRef ?? existing.baseRef,
    });
  } else {
    // Enforce cap — evict oldest if at limit.
    if (next.size >= MAX_FILES) {
      let oldestKey = '';
      let oldestTime = Infinity;
      for (const [key, file] of next) {
        if (file.firstSeen < oldestTime) {
          oldestTime = file.firstSeen;
          oldestKey = key;
        }
      }
      if (oldestKey) next.delete(oldestKey);
    }
    next.set(e.path, {
      path: e.path,
      absolutePath: e.absolutePath || e.path,
      fileName: basename(e.path),
      operation: e.operation,
      firstSeen: now,
      count: 1,
      kind: e.kind,
      baseRef: e.baseRef,
    });
  }
  return next;
}

/** PURE delete-anchored-match logic (SSOT, run_9dd59523; run_5d9178bf: MARK not remove).
 *  The matched row is now MARKED `deleted:true` (persistent struck-through) instead of
 *  removed — "the user should see what I deleted" (XG). Returns the new map + whether
 *  anything matched (`hit`) so the caller can no-op on a miss (avoid churn). Match
 *  CONSERVATIVELY/anchored via railSsot.matchesPath — exact display/absolute path, or a
 *  '/'+relative segment-anchored suffix; NEVER bare-basename (the D3 bug). */
export function applyDelete(
  prev: Map<string, ReferencedFile>,
  e: { path: string; absolutePath?: string },
): { map: Map<string, ReferencedFile>; hit: boolean } {
  let hit = false;
  const next = new Map<string, ReferencedFile>();
  for (const [key, f] of prev) {
    // SSOT anchored match — exact display/abs path or a '/'+relative segment-anchored
    // suffix; NEVER bare basename (the D3 bug).
    if (matchesPath(f, e)) {
      hit = true;
      next.set(key, { ...f, deleted: true }); // MARK, persist (struck-through)
      continue;
    }
    next.set(key, f);
  }
  return { map: next, hit };
}

export function useReferencedFiles(tabId: string | undefined) {
  const [files, setFiles] = useState<Map<string, ReferencedFile>>(new Map());
  const filesRef = useRef<Map<string, ReferencedFile>>(files);
  filesRef.current = files;

  // Load from sessionStorage on mount / tab change (run_26aa6caa: keyed by tabId).
  //
  // BUG1 (run_26981f66): a transient key=undefined during tab-switch / canvas-open
  // used to WIPE the map, so the Canvas outputs rail flashed empty and had to
  // rebuild. Fix retained: treat undefined as TRANSIENT — keep the current list,
  // do NOT reload. Only a NEW, DEFINED tabId triggers a reload from ITS OWN key.
  // (tabId is far more stable than the old sessionId — it has no unresolved
  // window — so this transient-guard now fires only on a genuine tab switch.)
  useEffect(() => {
    if (!tabId) return; // transient undefined → keep showing current list
    const loaded = loadFromStorage(tabId);
    setFiles(loaded);
  }, [tabId]);

  // Listen for file-referenced events
  useEffect(() => {
    if (!tabId) return;

    const handler = (e: Event) => {
      const { path, absolutePath, operation, relevance, kind, baseRef, tabId: evtTabId } =
        (e as CustomEvent<FileChangedDetail>).detail ?? ({} as FileChangedDetail);
      if (!path) return;
      // AC5 read-only structural backstop (run_5d9178bf, §0′): the rail admits ONLY
      // write/delete operations. The server-side emit gate already never emits a
      // read/grep (its pending map is keyed on write tools), but this frontend
      // allowlist makes "read-only never surfaces" true by CONSTRUCTION here too —
      // a stray non-write/delete event can never add a row.
      if (operation !== 'written' && operation !== 'deleted') return;
      // Bookkeeping is dropped server-side, but guard defensively (an older
      // backend without relevance fails open → treated as listable).
      if (relevance === 'bookkeeping') return;
      // The unified verdict decides rail membership.
      //  - process → never in the rail (machine noise; also dropped server-side).
      //  - source  → a mid-run coding edit; DROPPED here (真实 repo 改动中途不 display).
      //              The finish batch re-emits the run's committed files as
      //              `source-final` (run_b8ea6d5c) — a DISTINCT kind that is NOT
      //              dropped below, so it lands as a persistent PR-review row.
      //  - source-final → the pipeline-finish coding batch → RAIL (via surface_run_outputs).
      //  - content|knowledge (or undefined from an older backend) → rail.
      // Undefined kind falls through to the rail (migration: relevance still gates pop).
      // SSOT: isRailKind drops process/source; keeps content/knowledge/source-final/
      // external-diff/external-nodiff/undefined (railSsot.ts, run_5d9178bf).
      if (!isRailKind(kind)) return;

      // ── Active vs BACKGROUND routing (run_9dd59523) ──────────────────────────
      // The event is stamped with its OWNING tabId (capturedTabId). Previously a
      // background-tab event (evtTabId != this hook's active tabId) was DROPPED, so
      // that tab's outputs were never persisted ('跑完看不到输出' on switch-in). Now:
      //  - ACTIVE tab (evtTabId absent OR == tabId) → setState + save [unchanged].
      //  - BACKGROUND tab (evtTabId && != tabId)    → persist to the OWNING tab's
      //    storage via the staging map, NO setState (that tab isn't rendered here).
      // Both paths run the SAME pure applyWrite/applyDelete (SSOT — no drift).
      const isBackground = !!(evtTabId && evtTabId !== tabId);

      if (isBackground) {
        // Read the owning tab's DURABLE store (sessionStorage), apply, write back.
        // sessionStorage IS the single source of truth for a non-active tab (it has no
        // live hook instance), and this whole handler runs SYNCHRONOUSLY (window
        // dispatch blocks; storage read+write are sync), so consecutive events for the
        // same tab serialize correctly — event B's load() sees event A's save().
        // (run_9dd59523 first used an in-memory staging map here to pre-empt a
        // hypothetical deferred-write race; Gate-2 proved that map INTRODUCED a real
        // data-loss bug — it went stale after a tab cycled active→background and then
        // full-overwrote storage with the stale copy, discarding the active-cycle
        // writes. Storage-direct has no such staleness: there is no second copy to go
        // stale. Real bug > hypothetical bug — the staging map was removed.)
        const ownTab = evtTabId as string;
        const base = loadFromStorage(ownTab);
        if (operation === 'deleted') {
          const { map, hit } = applyDelete(base, { path, absolutePath });
          if (!hit) return; // no-op — don't churn storage
          saveToStorage(ownTab, map);
        } else {
          saveToStorage(ownTab, applyWrite(base, { path, absolutePath, operation, kind, baseRef }));
        }
        return;
      }

      // ── ACTIVE tab (unchanged behavior, now via the shared pure helpers) ──────
      if (operation === 'deleted') {
        setFiles((prev) => {
          const { map, hit } = applyDelete(prev, { path, absolutePath });
          if (!hit) return prev; // no-op — don't churn state/storage
          saveToStorage(tabId, map);
          return map;
        });
        return;
      }

      setFiles((prev) => {
        const next = applyWrite(prev, { path, absolutePath, operation, kind, baseRef });
        saveToStorage(tabId, next);
        return next;
      });
    };

    // D1 (run_5d9178bf): subscribe via the single fileChangedBroker instead of a
    // raw window listener (was the 5th of 5 duplicate listeners). handler signature
    // unchanged — the broker forwards the raw CustomEvent.
    return subscribeFileChanged(handler);
  }, [tabId]);

  // Group files by operation (memoized to avoid unnecessary re-renders).
  // Only 'written' is ever emitted (see FileOperation), so the group is a single
  // key — kept as a Record so CanvasOutputRail's `grouped.written` stays stable.
  const { grouped, totalCount } = useMemo(() => {
    const g: Record<FileOperation, ReferencedFile[]> = { written: [] };

    for (const file of files.values()) {
      g[file.operation].push(file);
    }

    // Newest first.
    g.written.sort((a, b) => b.firstSeen - a.firstSeen);

    return { grouped: g, totalCount: files.size };
  }, [files]);

  const clear = useCallback(() => {
    if (tabId) {
      sessionStorage.removeItem(getStorageKey(tabId));
    }
    setFiles(new Map());
  }, [tabId]);

  return { files: grouped, totalCount, clear };
}
