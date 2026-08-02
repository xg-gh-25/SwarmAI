/**
 * useReferencedFiles — Tracks files the agent touches during a chat session.
 *
 * Listens for 'swarm:file-referenced' custom events dispatched by MergedToolBlock
 * when tool summaries contain file paths. Maintains a deduplicated, grouped list
 * persisted in sessionStorage (survives component remounts, resets on new session).
 *
 * @exports useReferencedFiles hook
 * @exports ReferencedFile interface
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';

export type FileOperation = 'written' | 'read' | 'searched';

export interface ReferencedFile {
  /** File path as emitted (relative or absolute) */
  path: string;
  /** Resolved absolute path (for clipboard copy) */
  absolutePath: string;
  /** Basename for display */
  fileName: string;
  /** How this file was referenced */
  operation: FileOperation;
  /** When first seen (ms since epoch) */
  firstSeen: number;
  /** Number of times referenced */
  count: number;
}

/** Custom event detail shape dispatched by MergedToolBlock */
export interface FileReferencedDetail {
  path: string;
  operation: FileOperation;
  /** Owning tab's session id (stamped by MergedToolBlock). Consumers filter on
   *  it to ignore background-tab writes; absent → treated as current (fail-open). */
  sessionId?: string;
}

const MAX_FILES = 100;
const STORAGE_PREFIX = 'swarm:referenced-files:';
const EVENT_NAME = 'swarm:file-referenced';

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
      const { path, operation, sessionId: evtSessionId } = (e as CustomEvent<FileReferencedDetail>).detail ?? {};
      if (!path) return;
      // Tab-scope: all tabs are keep-mounted, so a background tab's dispatch
      // would otherwise be recorded into THIS (active) session's store. Ignore
      // events stamped with a DIFFERENT session. Fail OPEN when unstamped
      // (evtSessionId absent) — no regression for any un-updated dispatcher.
      if (evtSessionId && evtSessionId !== sessionId) return;

      setFiles((prev) => {
        const next = new Map(prev);
        const existing = next.get(path);

        if (existing) {
          // Update count and operation (promote read→written if now written)
          next.set(path, {
            ...existing,
            count: existing.count + 1,
            operation: operation === 'written' ? 'written' : existing.operation,
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
            absolutePath: path, // Will be resolved by consumer if relative
            fileName: basename(path),
            operation,
            firstSeen: Date.now(),
            count: 1,
          });
        }

        // Persist
        saveToStorage(sessionId, next);
        return next;
      });
    };

    document.addEventListener(EVENT_NAME, handler);
    return () => document.removeEventListener(EVENT_NAME, handler);
  }, [sessionId]);

  // Group files by operation (memoized to avoid unnecessary re-renders)
  const { grouped, totalCount } = useMemo(() => {
    const g: Record<FileOperation, ReferencedFile[]> = {
      written: [],
      read: [],
      searched: [],
    };

    for (const file of files.values()) {
      g[file.operation].push(file);
    }

    // Sort each group by firstSeen descending (newest first)
    for (const key of Object.keys(g) as FileOperation[]) {
      g[key].sort((a, b) => b.firstSeen - a.firstSeen);
    }

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
