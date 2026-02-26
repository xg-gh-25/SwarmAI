/**
 * React hook for managing Thread-Scoped Cognitive Context (TSCC) state.
 *
 * Fetches initial TSCC state from the backend, applies incremental telemetry
 * events, and manages per-thread expand/collapse and pin preferences.
 *
 * Key exports:
 * - ``useTSCCState``  — The main hook accepting a threadId
 *
 * Return value includes:
 * - ``tsccState``           — Current TSCCState or null
 * - ``isExpanded``          — Whether the panel is expanded
 * - ``isPinned``            — Whether the panel is pinned open
 * - ``lifecycleState``      — Current thread lifecycle state
 * - ``toggleExpand``        — Toggle expand/collapse
 * - ``togglePin``           — Toggle pin state
 * - ``applyTelemetryEvent`` — Apply an incremental SSE telemetry event
 * - ``setAutoExpand``       — Programmatically set expand state
 * - ``triggerAutoExpand``   — Auto-expand for high-signal events only
 *
 * Auto-expand rules (Requirement 16):
 * - Panel does NOT auto-expand during normal chat message streaming
 * - Auto-expands ONLY for: first plan creation, blocking issue, explicit request
 * - Normal telemetry events update collapsed bar silently
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  TSCCState,
  TSCCLiveState,
  TSCCSource,
  ThreadLifecycleState,
  StreamEvent,
} from '../types';
import { getTSCCState } from '../services/tscc';

// ---------------------------------------------------------------------------
// Auto-expand reason type (Requirement 16.2)
// ---------------------------------------------------------------------------
export type AutoExpandReason = 'first_plan' | 'blocking_issue' | 'explicit_request';

// ---------------------------------------------------------------------------
// Per-thread preference maps (module-level, survive re-renders)
// ---------------------------------------------------------------------------
const expandPrefs = new Map<string, boolean>();
const pinPrefs = new Map<string, boolean>();

/** Tracks whether the first plan has already been created per thread. */
const firstPlanSeen = new Map<string, boolean>();

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
  applyTelemetryEvent: (event: StreamEvent) => void;
  setAutoExpand: (expanded: boolean) => void;
  triggerAutoExpand: (reason: AutoExpandReason) => void;
}

// ---------------------------------------------------------------------------
// Helper: apply a single telemetry event to a live state (pure function)
// ---------------------------------------------------------------------------
function applyEventToLiveState(
  live: TSCCLiveState,
  event: StreamEvent,
): TSCCLiveState {
  switch (event.type) {
    case 'agent_activity': {
      const name = event.agentName ?? '';
      const agents = live.activeAgents.includes(name)
        ? live.activeAgents
        : [...live.activeAgents, name];
      const desc = event.description ?? '';
      const doing = [...live.whatAiDoing, desc].slice(-4);
      return { ...live, activeAgents: agents, whatAiDoing: doing };
    }

    case 'tool_invocation': {
      const desc = event.description ?? event.toolName ?? '';
      const doing = [...live.whatAiDoing, desc].slice(-4);
      return { ...live, whatAiDoing: doing };
    }

    case 'capability_activated': {
      const capType = event.capabilityType as
        | 'skill'
        | 'mcp'
        | 'tool'
        | undefined;
      const capName = event.capabilityName ?? '';
      if (!capType) return live;
      const key =
        capType === 'skill'
          ? 'skills'
          : capType === 'mcp'
            ? 'mcps'
            : 'tools';
      const list = live.activeCapabilities[key];
      if (list.includes(capName)) return live;
      return {
        ...live,
        activeCapabilities: {
          ...live.activeCapabilities,
          [key]: [...list, capName],
        },
      };
    }

    case 'sources_updated': {
      const newSource: TSCCSource = {
        path: event.sourcePath ?? '',
        origin: event.origin ?? '',
      };
      const exists = live.activeSources.some(
        (s) => s.path === newSource.path,
      );
      if (exists) return live;
      return {
        ...live,
        activeSources: [...live.activeSources, newSource],
      };
    }

    case 'summary_updated': {
      const summary = (event.keySummary ?? []).slice(0, 5);
      return { ...live, keySummary: summary };
    }

    default:
      return live;
  }
}

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------
export function useTSCCState(
  threadId: string | null,
): UseTSCCStateReturn {
  const [tsccState, setTsccState] = useState<TSCCState | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isPinned, setIsPinned] = useState(false);

  // Track current threadId to avoid stale async updates
  const currentThreadRef = useRef<string | null>(threadId);

  // ---- Fetch initial state when threadId changes ----
  useEffect(() => {
    currentThreadRef.current = threadId;

    if (!threadId) {
      setTsccState(null);
      return;
    }

    // Restore per-thread prefs
    setIsExpanded(expandPrefs.get(threadId) ?? false);
    setIsPinned(pinPrefs.get(threadId) ?? false);

    let cancelled = false;
    getTSCCState(threadId)
      .then((state) => {
        if (!cancelled && currentThreadRef.current === threadId) {
          setTsccState(state);
        }
      })
      .catch(() => {
        if (!cancelled && currentThreadRef.current === threadId) {
          setTsccState(makeDefaultState(threadId));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [threadId]);

  // ---- Toggle expand (persists per-thread) ----
  const toggleExpand = useCallback(() => {
    setIsExpanded((prev) => {
      const next = !prev;
      if (threadId) expandPrefs.set(threadId, next);
      return next;
    });
  }, [threadId]);

  // ---- Toggle pin (persists per-thread) ----
  const togglePin = useCallback(() => {
    setIsPinned((prev) => {
      const next = !prev;
      if (threadId) pinPrefs.set(threadId, next);
      return next;
    });
  }, [threadId]);

  // ---- Programmatic expand control ----
  const setAutoExpand = useCallback(
    (expanded: boolean) => {
      setIsExpanded(expanded);
      if (threadId) expandPrefs.set(threadId, expanded);
    },
    [threadId],
  );

  // ---- Auto-expand for high-signal events only (Req 16.1, 16.2) ----
  const triggerAutoExpand = useCallback(
    (reason: AutoExpandReason) => {
      if (!threadId) return;

      switch (reason) {
        case 'first_plan': {
          // Only auto-expand for the FIRST plan creation in this thread
          if (firstPlanSeen.get(threadId)) return;
          firstPlanSeen.set(threadId, true);
          setIsExpanded(true);
          expandPrefs.set(threadId, true);
          break;
        }
        case 'blocking_issue':
        case 'explicit_request': {
          setIsExpanded(true);
          expandPrefs.set(threadId, true);
          break;
        }
        default:
          break;
      }
    },
    [threadId],
  );

  // ---- Apply incremental telemetry event ----
  const applyTelemetryEvent = useCallback(
    (event: StreamEvent) => {
      setTsccState((prev) => {
        if (!prev) return prev;
        const newLive = applyEventToLiveState(prev.liveState, event);
        if (newLive === prev.liveState) return prev;
        return {
          ...prev,
          liveState: newLive,
          lastUpdatedAt: new Date().toISOString(),
        };
      });
    },
    [],
  );

  return {
    tsccState,
    isExpanded,
    isPinned,
    lifecycleState: tsccState?.lifecycleState ?? null,
    toggleExpand,
    togglePin,
    applyTelemetryEvent,
    setAutoExpand,
    triggerAutoExpand,
  };
}
