/**
 * React hook for managing Thread-Scoped Cognitive Context (TSCC) state.
 *
 * Fetches TSCC lifecycle state from the backend on session change.
 * Manages per-thread expand/collapse and pin preferences.
 *
 * System prompt metadata is delivered via SSE events
 * (``system_prompt_metadata``) and managed by ``useChatStreamingLifecycle``.
 * This hook only manages lifecycle state and UI preferences.
 *
 * Key exports:
 * - ``useTSCCState``         — The main hook accepting a sessionId
 * - ``UseTSCCStateReturn``   — Return type interface
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import type { TSCCState, ThreadLifecycleState } from '../types';
import { getTSCCState } from '../services/tscc';

// ---------------------------------------------------------------------------
// Per-thread preference maps (module-level, survive re-renders)
// ---------------------------------------------------------------------------
const expandPrefs = new Map<string, boolean>();
const pinPrefs = new Map<string, boolean>();

/** Cap module-level Maps to prevent unbounded growth in long-running sessions. */
const PREFS_MAP_CAP = 200;

function _capMap<K, V>(map: Map<K, V>): void {
  if (map.size > PREFS_MAP_CAP) {
    const excess = map.size - PREFS_MAP_CAP;
    const iter = map.keys();
    for (let i = 0; i < excess; i++) {
      const key = iter.next().value;
      if (key !== undefined) map.delete(key);
    }
  }
}

// ---------------------------------------------------------------------------
// Default empty state factory
// ---------------------------------------------------------------------------
function makeDefaultState(threadId: string): TSCCState {
  return {
    threadId,
    projectId: null,
    scopeType: 'workspace',
    lastUpdatedAt: new Date().toISOString(),
    lifecycleState: 'new',
    liveState: {
      context: {
        scopeLabel: 'Workspace: SwarmWS (General)',
        threadTitle: '',
        mode: undefined,
      },
      activeAgents: [],
      activeCapabilities: { skills: [], mcps: [], tools: [] },
      whatAiDoing: [],
      activeSources: [],
      keySummary: [],
    },
  };
}

// ---------------------------------------------------------------------------
// Hook return type
// ---------------------------------------------------------------------------
export interface UseTSCCStateReturn {
  tsccState: TSCCState | null;
  isExpanded: boolean;
  isPinned: boolean;
  lifecycleState: ThreadLifecycleState | null;
  toggleExpand: () => void;
  togglePin: () => void;
}

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------
export function useTSCCState(sessionId: string | null): UseTSCCStateReturn {
  const [tsccState, setTsccState] = useState<TSCCState | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const currentSessionRef = useRef<string | null>(sessionId);

  // ---- Fetch TSCC state when sessionId changes ----
  useEffect(() => {
    currentSessionRef.current = sessionId;

    if (!sessionId) {
      setTsccState(null);
      return;
    }

    // Restore per-thread prefs
    setIsExpanded(expandPrefs.get(sessionId) ?? false);
    setIsPinned(pinPrefs.get(sessionId) ?? false);

    // Evict stale entries to prevent unbounded growth
    _capMap(expandPrefs);
    _capMap(pinPrefs);

    let cancelled = false;

    getTSCCState(sessionId)
      .then((state) => {
        if (!cancelled && currentSessionRef.current === sessionId) {
          setTsccState(state);
        }
      })
      .catch(() => {
        if (!cancelled && currentSessionRef.current === sessionId) {
          setTsccState(makeDefaultState(sessionId));
        }
      });

    return () => { cancelled = true; };
  }, [sessionId]);

  // ---- Toggle expand (persists per-session) ----
  const toggleExpand = useCallback(() => {
    setIsExpanded((prev) => {
      const next = !prev;
      if (sessionId) expandPrefs.set(sessionId, next);
      return next;
    });
  }, [sessionId]);

  // ---- Toggle pin (persists per-session) ----
  const togglePin = useCallback(() => {
    setIsPinned((prev) => {
      const next = !prev;
      if (sessionId) pinPrefs.set(sessionId, next);
      return next;
    });
  }, [sessionId]);

  return {
    tsccState,
    isExpanded,
    isPinned,
    lifecycleState: tsccState?.lifecycleState ?? null,
    toggleExpand,
    togglePin,
  };
}
