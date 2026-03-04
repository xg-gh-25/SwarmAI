/**
 * React hook for managing Thread-Scoped Cognitive Context (TSCC) state.
 *
 * Fetches system prompt metadata from the backend endpoint on session change.
 * Manages per-thread expand/collapse and pin preferences.
 *
 * Key exports:
 * - ``useTSCCState``  — The main hook accepting a sessionId
 *
 * Return value includes:
 * - ``tsccState``           — Current TSCCState or null
 * - ``promptMetadata``      — System prompt metadata (files, tokens)
 * - ``isExpanded``          — Whether the panel is expanded
 * - ``isPinned``            — Whether the panel is pinned open
 * - ``lifecycleState``      — Current thread lifecycle state
 * - ``toggleExpand``        — Toggle expand/collapse
 * - ``togglePin``           — Toggle pin state
 *
 * Requirements: 6.1, 6.4, 6.8
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  TSCCState,
  ThreadLifecycleState,
  SystemPromptMetadata,
} from '../types';
import { getTSCCState, getSystemPromptMetadata } from '../services/tscc';

// ---------------------------------------------------------------------------
// Per-thread preference maps (module-level, survive re-renders)
// ---------------------------------------------------------------------------
const expandPrefs = new Map<string, boolean>();
const pinPrefs = new Map<string, boolean>();

/** Cap module-level Maps to prevent unbounded growth in long-running sessions. */
const PREFS_MAP_CAP = 200;

function _capMap<K, V>(map: Map<K, V>): void {
  if (map.size > PREFS_MAP_CAP) {
    // Delete oldest entries (first inserted)
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
  promptMetadata: SystemPromptMetadata | null;
  isExpanded: boolean;
  isPinned: boolean;
  lifecycleState: ThreadLifecycleState | null;
  toggleExpand: () => void;
  togglePin: () => void;
}

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------
export function useTSCCState(
  sessionId: string | null,
): UseTSCCStateReturn {
  const [tsccState, setTsccState] = useState<TSCCState | null>(null);
  const [promptMetadata, setPromptMetadata] = useState<SystemPromptMetadata | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isPinned, setIsPinned] = useState(false);

  // Track current sessionId to avoid stale async updates
  const currentSessionRef = useRef<string | null>(sessionId);

  // ---- Fetch TSCC state and system prompt metadata when sessionId changes ----
  useEffect(() => {
    currentSessionRef.current = sessionId;

    if (!sessionId) {
      setTsccState(null);
      setPromptMetadata(null);
      return;
    }

    // Restore per-thread prefs
    setIsExpanded(expandPrefs.get(sessionId) ?? false);
    setIsPinned(pinPrefs.get(sessionId) ?? false);

    // Evict stale entries to prevent unbounded growth
    _capMap(expandPrefs);
    _capMap(pinPrefs);

    let cancelled = false;

    // Fetch TSCC state (for lifecycle label / freshness)
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

    // Fetch system prompt metadata
    getSystemPromptMetadata(sessionId)
      .then((meta) => {
        if (!cancelled && currentSessionRef.current === sessionId) {
          setPromptMetadata(meta);
        }
      })
      .catch(() => {
        // 404 is expected for sessions without metadata yet
        if (!cancelled && currentSessionRef.current === sessionId) {
          setPromptMetadata(null);
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
    promptMetadata,
    isExpanded,
    isPinned,
    lifecycleState: tsccState?.lifecycleState ?? null,
    toggleExpand,
    togglePin,
  };
}
