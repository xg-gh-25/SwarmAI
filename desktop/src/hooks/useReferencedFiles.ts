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

// The backend emits ONLY write operations (streaming_orchestrator._build_file_change_events
// hardcodes operation:"written"; detection is scoped to write SOURCES — Write/Edit/
// NotebookEdit + parseable Bash redirection — per the run_e626e121 directive #6). There is
// no read/grep/list tracking, so 'read'/'searched' were never-populated dead values; the
// type is narrowed to what is actually emitted. FileRelevance below is SEPARATE and
// load-bearing (incidental = the SSE bridge's fail-closed default for an older backend).
export type FileOperation = 'written';
export type FileRelevance = 'deliverable' | 'incidental' | 'bookkeeping';

/** Unified review-verdict kind (backend needs_human_review, run_dcce7023):
 *  content|knowledge → surface in the rail (+pop); source → aggregated into the
 *  pipeline-finish local PR, NOT the rail; process → never (dropped server-side). */
export type ReviewKind = 'content' | 'knowledge' | 'source' | 'process';

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
}

/** Detail shape of the unified `swarm:file-changed` event (from the SSE bridge). */
export interface FileChangedDetail {
  path: string;
  /** Resolved physical absolute path (backend-resolved once). */
  absolutePath?: string;
  operation: FileOperation;
  /** Backend whitelist classification; bookkeeping is pre-filtered server-side. */
  relevance?: FileRelevance;
  /** Unified review verdict kind (run_dcce7023): content|knowledge → rail; source →
   *  local PR (not rail); process → never. Undefined from an older backend → the
   *  consumer falls back to `relevance` (migration window). */
  kind?: ReviewKind;
  /** Owning tab's session id (stamped by the SSE bridge). Consumers filter on it
   *  to ignore background-tab writes; absent → treated as current (fail-open). */
  sessionId?: string;
}

const MAX_FILES = 100;
const STORAGE_PREFIX = 'swarm:referenced-files:';
const EVENT_NAME = 'swarm:file-changed';

function getStorageKey(sessionId: string): string {
  return `${STORAGE_PREFIX}${sessionId}`;
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || path;
}

function loadFromStorage(sessionId: string): Map<string, ReferencedFile> {
  try {
    const raw = sessionStorage.getItem(getStorageKey(sessionId));
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

function saveToStorage(sessionId: string, files: Map<string, ReferencedFile>): void {
  try {
    const arr = Array.from(files.values());
    sessionStorage.setItem(getStorageKey(sessionId), JSON.stringify(arr));
  } catch { /* quota exceeded — no-op */ }
}

export function useReferencedFiles(sessionId: string | undefined) {
  const [files, setFiles] = useState<Map<string, ReferencedFile>>(new Map());
  const filesRef = useRef<Map<string, ReferencedFile>>(files);
  filesRef.current = files;

  // Load from sessionStorage on mount / session change.
  //
  // BUG1 (run_26981f66): a transient sessionId=undefined during tab-switch /
  // canvas-open used to WIPE the map, so the Canvas outputs rail flashed empty
  // and had to rebuild (slower than file-open). Fix: treat undefined as
  // TRANSIENT — retain the current list and do NOT reload. Only a NEW, DEFINED
  // sessionId (different from the last one we loaded) triggers a reload. This
  // keeps the list stable across the flicker while still preventing cross-session
  // leakage: a genuinely different session id reloads from ITS OWN storage key.
  useEffect(() => {
    if (!sessionId) return; // transient undefined → keep showing current list
    const loaded = loadFromStorage(sessionId);
    setFiles(loaded);
  }, [sessionId]);

  // Listen for file-referenced events
  useEffect(() => {
    if (!sessionId) return;

    const handler = (e: Event) => {
      const { path, absolutePath, operation, relevance, kind, sessionId: evtSessionId } =
        (e as CustomEvent<FileChangedDetail>).detail ?? ({} as FileChangedDetail);
      if (!path) return;
      // Bookkeeping is dropped server-side, but guard defensively (an older
      // backend without relevance fails open → treated as listable).
      if (relevance === 'bookkeeping') return;
      // AC5 (run_dcce7023): the unified verdict decides rail membership.
      //  - process → never in the rail (machine noise; also dropped server-side).
      //  - source  → aggregated into the pipeline-finish local PR, NOT per-file in
      //              the rail (XG: 真实 repo 改动中途不 display, 收尾聚成 PR).
      //  - content|knowledge (or undefined from an older backend) → rail.
      // Undefined kind falls through to the rail (migration: relevance still gates pop).
      if (kind === 'process' || kind === 'source') return;
      // Tab-scope: all tabs are keep-mounted, so a background tab's dispatch
      // would otherwise be recorded into THIS (active) session's store. Ignore
      // events stamped with a DIFFERENT session. Fail OPEN when unstamped
      // (evtSessionId absent) — no regression for any un-updated dispatcher.
      if (evtSessionId && evtSessionId !== sessionId) return;

      setFiles((prev) => {
        const next = new Map(prev);
        const existing = next.get(path);

        if (existing) {
          // Dedup: bump count. operation is always 'written' (the only emitted op),
          // so there is nothing to promote — just refresh the resolved absolutePath if
          // a later event carried one the first lacked.
          next.set(path, {
            ...existing,
            absolutePath: absolutePath || existing.absolutePath,
            count: existing.count + 1,
            kind: kind ?? existing.kind,
          });
        } else {
          // Enforce cap — evict oldest if at limit
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

          next.set(path, {
            path,
            // Backend-resolved PHYSICAL absolute path (copy-path uses this). Falls
            // back to the display path only if the backend couldn't resolve it.
            absolutePath: absolutePath || path,
            fileName: basename(path),
            operation,
            firstSeen: Date.now(),
            count: 1,
            kind,
          });
        }

        // Persist
        saveToStorage(sessionId, next);
        return next;
      });
    };

    window.addEventListener(EVENT_NAME, handler);
    return () => window.removeEventListener(EVENT_NAME, handler);
  }, [sessionId]);

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
    if (sessionId) {
      sessionStorage.removeItem(getStorageKey(sessionId));
    }
    setFiles(new Map());
  }, [sessionId]);

  return { files: grouped, totalCount, clear };
}
