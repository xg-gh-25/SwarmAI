/**
 * Unified tab state hook — single source of truth for all tab state.
 *
 * Replaces the three separate stores (`useTabState`, `tabStateRef`, `tabStatuses`)
 * with a single `useRef<Map<string, UnifiedTab>>` backed by a `useState` re-render
 * counter. Derived views (`openTabs`, `tabStatuses`, `activeTab`) are computed via
 * `useMemo` keyed on the counter.
 *
 * Key exports:
 * - `TabStatus`                 — Tab lifecycle status union type
 * - `UnifiedTab`                — Combined metadata + runtime state for a single tab
 * - `SerializableTab`           — Fields persisted to localStorage
 * - `UseUnifiedTabStateReturn`  — Hook return interface
 * - `useUnifiedTabState`        — The hook itself
 */

import { useState, useRef, useMemo, useCallback, useEffect } from 'react';
import type { Message } from '../types/index';
import type { PendingQuestion, OpenTab } from '../pages/chat/types';
import {
  OPEN_TABS_STORAGE_KEY,
  ACTIVE_TAB_STORAGE_KEY,
} from '../pages/chat/constants';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum number of concurrently open tabs. */
export const MAX_OPEN_TABS = 6;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Tab lifecycle status for header indicators. */
export type TabStatus =
  | 'idle'
  | 'streaming'
  | 'waiting_input'
  | 'permission_needed'
  | 'error'
  | 'complete_unread';

/** Combined metadata + runtime state for a single tab. */
export interface UnifiedTab {
  // --- Metadata (persisted to localStorage) ---
  id: string;
  title: string;
  agentId: string;
  isNew: boolean;
  sessionId?: string;

  // --- Runtime state (not persisted) ---
  messages: Message[];
  pendingQuestion: PendingQuestion | null;
  isStreaming: boolean;
  abortController: AbortController | null;
  streamGen: number;
  status: TabStatus;
}

/** Fields persisted to localStorage. */
export type SerializableTab = Pick<
  UnifiedTab,
  'id' | 'title' | 'agentId' | 'isNew' | 'sessionId'
>;

/** Hook return interface. */
export interface UseUnifiedTabStateReturn {
  // --- Derived views (stable between mutations) ---
  openTabs: OpenTab[];
  activeTabId: string | null;
  activeTab: UnifiedTab | undefined;
  tabStatuses: Record<string, TabStatus>;

  // --- Tab CRUD ---
  addTab: (agentId: string) => OpenTab | undefined;
  closeTab: (tabId: string) => void;
  selectTab: (tabId: string) => void;

  // --- Metadata updates ---
  updateTabTitle: (tabId: string, title: string) => void;
  updateTabSessionId: (tabId: string, sessionId: string) => void;
  setTabIsNew: (tabId: string, isNew: boolean) => void;

  // --- Runtime state ---
  getTabState: (tabId: string) => UnifiedTab | undefined;
  /** Patch excludes `id` to prevent primary key corruption. */
  updateTabState: (
    tabId: string,
    patch: Partial<Omit<UnifiedTab, 'id'>>,
  ) => void;
  updateTabStatus: (tabId: string, status: TabStatus) => void;

  // --- Lifecycle ---
  saveCurrentTab: () => void;
  restoreTab: (tabId: string) => boolean;
  initTabState: (tabId: string, initialMessages?: Message[]) => void;
  cleanupTabState: (tabId: string) => void;

  // --- Cleanup ---
  removeInvalidTabs: (validSessionIds: Set<string>) => void;

  // --- Direct ref access (for synchronous reads in stream handlers) ---
  tabMapRef: React.RefObject<Map<string, UnifiedTab>>;
  activeTabIdRef: React.RefObject<string | null>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Creates a new UnifiedTab with default runtime state. */
function createDefaultTab(agentId: string): UnifiedTab {
  return {
    id: crypto.randomUUID(),
    title: 'New Session',
    agentId,
    isNew: true,
    sessionId: undefined,
    messages: [],
    pendingQuestion: null,
    isStreaming: false,
    abortController: null,
    streamGen: 0,
    status: 'idle',
  };
}

/** Extracts the serializable subset from a UnifiedTab. */
function toSerializable(tab: UnifiedTab): SerializableTab {
  return {
    id: tab.id,
    title: tab.title,
    agentId: tab.agentId,
    isNew: tab.isNew,
    sessionId: tab.sessionId,
  };
}

/** Hydrates a SerializableTab into a full UnifiedTab with default runtime state. */
function hydrateTab(s: SerializableTab): UnifiedTab {
  return {
    ...s,
    messages: [],
    pendingQuestion: null,
    isStreaming: false,
    abortController: null,
    streamGen: 0,
    status: 'idle',
  };
}

/** Safely reads and parses tabs from localStorage. Returns null on failure. */
function loadTabsFromStorage(): SerializableTab[] | null {
  try {
    const raw = localStorage.getItem(OPEN_TABS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    return parsed as SerializableTab[];
  } catch {
    return null;
  }
}

/** Safely reads activeTabId from localStorage. */
function loadActiveTabIdFromStorage(): string | null {
  try {
    return localStorage.getItem(ACTIVE_TAB_STORAGE_KEY);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useUnifiedTabState(
  defaultAgentId: string,
): UseUnifiedTabStateReturn {
  // ---- Tab_Map: authoritative store (useRef so mutations don't re-render) --
  const tabMapRef = useRef<Map<string, UnifiedTab>>(new Map());

  // ---- Render_Counter: increment to trigger useMemo re-derivation ----------
  const [renderCounter, setRenderCounter] = useState<number>(0);
  const bump = useCallback(() => setRenderCounter((c) => c + 1), []);

  // ---- activeTabId with useRef mirror for synchronous reads ----------------
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const activeTabIdRef = useRef<string | null>(null);

  // Keep ref in sync with state
  const setActiveTabIdBoth = useCallback((id: string | null) => {
    activeTabIdRef.current = id;
    setActiveTabId(id);
  }, []);

  // ---- localStorage initialization (runs once via useRef guard) -----------
  const initialized = useRef(false);
  if (!initialized.current) {
    initialized.current = true;
    const map = tabMapRef.current;

    const savedTabs = loadTabsFromStorage();
    if (savedTabs && savedTabs.length > 0) {
      for (const s of savedTabs) {
        map.set(s.id, hydrateTab(s));
      }
    } else {
      const defaultTab = createDefaultTab(defaultAgentId);
      map.set(defaultTab.id, defaultTab);
    }

    // Restore activeTabId — validate it exists in the map
    const savedActiveId = loadActiveTabIdFromStorage();
    const firstTabId = map.keys().next().value as string;
    if (savedActiveId && map.has(savedActiveId)) {
      activeTabIdRef.current = savedActiveId;
    } else {
      activeTabIdRef.current = firstTabId;
    }
    // Sync useState (will be picked up on first render)
    setActiveTabId(activeTabIdRef.current);
  }

  // ---- Derived views via useMemo (keyed on renderCounter) -----------------

  const openTabs: OpenTab[] = useMemo(() => {
    const tabs: OpenTab[] = [];
    for (const t of tabMapRef.current.values()) {
      tabs.push({
        id: t.id,
        title: t.title,
        agentId: t.agentId,
        isNew: t.isNew,
        sessionId: t.sessionId,
      });
    }
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter]);

  const tabStatuses: Record<string, TabStatus> = useMemo(() => {
    const result: Record<string, TabStatus> = {};
    for (const [id, t] of tabMapRef.current.entries()) {
      result[id] = t.status;
    }
    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter]);

  const activeTab: UnifiedTab | undefined = useMemo(() => {
    if (!activeTabId) return undefined;
    return tabMapRef.current.get(activeTabId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter, activeTabId]);

  // ---- Tab CRUD -----------------------------------------------------------

  const addTab = useCallback(
    (agentId: string): OpenTab | undefined => {
      const map = tabMapRef.current;
      if (map.size >= MAX_OPEN_TABS) return undefined;

      const newTab = createDefaultTab(agentId);
      map.set(newTab.id, newTab);
      setActiveTabIdBoth(newTab.id);
      bump();

      return {
        id: newTab.id,
        title: newTab.title,
        agentId: newTab.agentId,
        isNew: newTab.isNew,
        sessionId: newTab.sessionId,
      };
    },
    [bump, setActiveTabIdBoth],
  );

  const closeTab = useCallback(
    (tabId: string) => {
      const map = tabMapRef.current;
      const tab = map.get(tabId);
      if (!tab) return;

      // Abort streaming if active
      if (tab.abortController) {
        try {
          tab.abortController.abort();
        } catch {
          // already aborted — safe to ignore
        }
      }

      // Capture ordered keys before removal for reselection
      const keys = [...map.keys()];
      const closedIndex = keys.indexOf(tabId);
      map.delete(tabId);

      if (map.size === 0) {
        // Auto-create a new tab when closing the last one
        const newTab = createDefaultTab(defaultAgentId);
        map.set(newTab.id, newTab);
        setActiveTabIdBoth(newTab.id);
      } else if (activeTabIdRef.current === tabId) {
        // Reselect adjacent tab (clamped to bounds)
        const remaining = [...map.keys()];
        const newIdx = Math.min(closedIndex, remaining.length - 1);
        setActiveTabIdBoth(remaining[newIdx]);
      }

      bump();
    },
    [bump, defaultAgentId, setActiveTabIdBoth],
  );

  const selectTab = useCallback(
    (tabId: string) => {
      if (tabMapRef.current.has(tabId)) {
        setActiveTabIdBoth(tabId);
        bump();
      }
    },
    [bump, setActiveTabIdBoth],
  );

  // ---- Metadata updates ---------------------------------------------------

  const updateTabTitle = useCallback(
    (tabId: string, title: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.title = title;
      bump();
    },
    [bump],
  );

  const updateTabSessionId = useCallback(
    (tabId: string, sessionId: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.sessionId = sessionId;
      bump();
    },
    [bump],
  );

  const setTabIsNew = useCallback(
    (tabId: string, isNew: boolean) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.isNew = isNew;
      bump();
    },
    [bump],
  );

  // ---- Runtime state ------------------------------------------------------

  const getTabState = useCallback(
    (tabId: string): UnifiedTab | undefined => tabMapRef.current.get(tabId),
    [],
  );

  const updateTabState = useCallback(
    (tabId: string, patch: Partial<Omit<UnifiedTab, 'id'>>) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      Object.assign(tab, patch);
      bump();
    },
    [bump],
  );

  const updateTabStatus = useCallback(
    (tabId: string, status: TabStatus) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.status = status;
      bump();
    },
    [bump],
  );

  // ---- Lifecycle ----------------------------------------------------------

  /**
   * Writes the current foreground React state into the active tab entry.
   * Callers pass the live React state values; the hook merges them into
   * the Tab_Map entry for the active tab.
   */
  const saveCurrentTab = useCallback(() => {
    // No-op — the active tab's state is already in the map.
    // Callers that need to persist specific React state should use
    // updateTabState(activeTabIdRef.current, { ... }) directly.
    // This method exists for API compatibility with the legacy interface.
  }, []);

  const restoreTab = useCallback((tabId: string): boolean => {
    return tabMapRef.current.has(tabId);
  }, []);

  const initTabState = useCallback(
    (tabId: string, initialMessages?: Message[]) => {
      const existing = tabMapRef.current.get(tabId);
      if (existing) {
        // Tab already exists — just update messages if provided
        if (initialMessages) {
          existing.messages = initialMessages;
          bump();
        }
        return;
      }
      const tab: UnifiedTab = {
        id: tabId,
        title: 'New Session',
        agentId: defaultAgentId,
        isNew: true,
        sessionId: undefined,
        messages: initialMessages ?? [],
        pendingQuestion: null,
        isStreaming: false,
        abortController: null,
        streamGen: 0,
        status: 'idle',
      };
      tabMapRef.current.set(tabId, tab);
      bump();
    },
    [bump, defaultAgentId],
  );

  const cleanupTabState = useCallback(
    (tabId: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;

      // Abort streaming if active
      if (tab.abortController) {
        try {
          tab.abortController.abort();
        } catch {
          // already aborted — safe to ignore
        }
      }

      tabMapRef.current.delete(tabId);
      bump();
    },
    [bump],
  );

  // ---- Cleanup ------------------------------------------------------------

  const removeInvalidTabs = useCallback(
    (validSessionIds: Set<string>) => {
      const map = tabMapRef.current;
      let changed = false;

      for (const tab of map.values()) {
        if (tab.sessionId && !validSessionIds.has(tab.sessionId)) {
          tab.sessionId = undefined;
          tab.isNew = true;
          tab.title = 'New Session';
          changed = true;
        }
      }

      if (changed) bump();
    },
    [bump],
  );

  // ---- localStorage persistence effect ------------------------------------
  // Persists only the serializable subset on metadata-changing mutations.
  // Runtime state mutations (updateTabState, updateTabStatus) also bump the
  // counter, but the serializable subset is unchanged so the write is
  // effectively idempotent and cheap (JSON.stringify of small array).

  useEffect(() => {
    const map = tabMapRef.current;
    const serialized: SerializableTab[] = [];
    for (const tab of map.values()) {
      serialized.push(toSerializable(tab));
    }
    try {
      localStorage.setItem(
        OPEN_TABS_STORAGE_KEY,
        JSON.stringify(serialized),
      );
    } catch {
      // Quota exceeded or other error — continue with in-memory state
    }
    try {
      if (activeTabIdRef.current) {
        localStorage.setItem(
          ACTIVE_TAB_STORAGE_KEY,
          activeTabIdRef.current,
        );
      }
    } catch {
      // Quota exceeded or other error — continue with in-memory state
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter, activeTabId]);

  // ---- Return object ------------------------------------------------------

  return {
    // Derived views
    openTabs,
    activeTabId,
    activeTab,
    tabStatuses,

    // Tab CRUD
    addTab,
    closeTab,
    selectTab,

    // Metadata updates
    updateTabTitle,
    updateTabSessionId,
    setTabIsNew,

    // Runtime state
    getTabState,
    updateTabState,
    updateTabStatus,

    // Lifecycle
    saveCurrentTab,
    restoreTab,
    initTabState,
    cleanupTabState,

    // Cleanup
    removeInvalidTabs,

    // Direct ref access
    tabMapRef,
    activeTabIdRef,
  };
}
