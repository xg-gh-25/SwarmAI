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
 * - `SerializableTab`           — Fields persisted to open_tabs.json
 * - `UseUnifiedTabStateReturn`  — Hook return interface
 * - `useUnifiedTabState`        — The hook itself
 */

import { useState, useRef, useMemo, useCallback, useEffect } from 'react';
import type { Message } from '../types/index';
import type { PendingQuestion, OpenTab } from '../pages/chat/types';
import {
  tabPersistenceService,
  type OpenTabsFileData,
  type PersistedTab,
} from '../services/tabPersistence';

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
  // --- Metadata (persisted to open_tabs.json) ---
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

/** Fields persisted to ~/.swarm-ai/open_tabs.json (re-exported from tabPersistence service). */
export type SerializableTab = PersistedTab;

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

  // --- File-based tab restore ---
  /** Loads tab state from ~/.swarm-ai/open_tabs.json. Returns true if tabs were restored. */
  restoreFromFile: () => Promise<boolean>;

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
function toSerializable(tab: UnifiedTab): PersistedTab {
  return {
    id: tab.id,
    title: tab.title,
    agentId: tab.agentId,
    isNew: tab.isNew,
    sessionId: tab.sessionId,
  };
}

/** Hydrates a PersistedTab into a full UnifiedTab with default runtime state. */
function hydrateTab(s: PersistedTab): UnifiedTab {
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

  // ---- Initialization (always starts with a default tab) ------------------
  // The actual tab state is loaded asynchronously from open_tabs.json via
  // restoreFromFile(), called by ChatPage on mount after the backend is ready.
  const initialized = useRef(false);
  // fileRestoreDone serves two purposes:
  // 1. Gates restoreFromFile() to run only once (idempotency guard)
  // 2. Gates the save effect — prevents overwriting open_tabs.json with
  //    the temporary default tab before the real tabs are restored
  const fileRestoreDone = useRef(false);
  if (!initialized.current) {
    initialized.current = true;
    const map = tabMapRef.current;
    const defaultTab = createDefaultTab(defaultAgentId);
    map.set(defaultTab.id, defaultTab);
    activeTabIdRef.current = defaultTab.id;
    setActiveTabId(defaultTab.id);
    console.log('[useUnifiedTabState] Init with default tab, awaiting file restore');
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
    const tabId = activeTabIdRef.current;
    if (!tabId) return;
    const tab = tabMapRef.current.get(tabId);
    if (!tab) return;
    // No-op: In the unified hook, the tab map IS the source of truth.
    // Stream handlers write directly to the map via updateTabState.
    // This method exists for API compatibility — callers that need to
    // sync React state into the map should call updateTabState directly.
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

  // ---- File-based tab restore -----------------------------------------------

  /**
   * Loads tab state from ``~/.swarm-ai/open_tabs.json`` via the backend API.
   * Called once by ChatPage after the backend is ready.
   *
   * If the file exists and contains valid tabs, replaces the default tab
   * with the persisted tabs. If the file is missing or empty, keeps the
   * default tab (fresh start).
   */
  const restoreFromFile = useCallback(
    async (): Promise<boolean> => {
      if (fileRestoreDone.current) return false;
      fileRestoreDone.current = true;

      const data = await tabPersistenceService.load();
      if (!data || !data.tabs || data.tabs.length === 0) {
        console.log('[useUnifiedTabState] No open_tabs.json found, keeping default tab');
        return false;
      }

      const map = tabMapRef.current;

      // Race condition guard: if user already started a conversation
      const tabs = [...map.values()];
      if (tabs.length === 1 && tabs[0].sessionId !== undefined) {
        console.log('[useUnifiedTabState] File restore skipped: user already started a conversation');
        return false;
      }

      // Clear default tab and hydrate from file
      map.clear();
      for (const saved of data.tabs.slice(0, MAX_OPEN_TABS)) {
        map.set(saved.id, hydrateTab(saved));
      }

      // Restore activeTabId — validate it exists in the map
      const firstTabId = map.keys().next().value as string;
      if (data.activeTabId && map.has(data.activeTabId)) {
        setActiveTabIdBoth(data.activeTabId);
      } else {
        setActiveTabIdBoth(firstTabId);
      }

      bump();
      console.log(`[useUnifiedTabState] Restored ${data.tabs.length} tabs from open_tabs.json`);
      return true;
    },
    [bump, setActiveTabIdBoth],
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

  // ---- Filesystem persistence effect (debounced) ---------------------------
  // Persists the serializable tab subset to ~/.swarm-ai/open_tabs.json
  // via the backend API. Debounced to avoid excessive writes during rapid
  // tab operations (streaming bumps the counter frequently).
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Skip saving until file restore is complete (avoid overwriting
    // the persisted state with the temporary default tab)
    if (!fileRestoreDone.current) return;

    // Debounce: wait 500ms after last change before writing
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const map = tabMapRef.current;
      const tabs: PersistedTab[] = [];
      for (const tab of map.values()) {
        tabs.push(toSerializable(tab));
      }
      const data: OpenTabsFileData = {
        tabs,
        activeTabId: activeTabIdRef.current,
      };
      tabPersistenceService.save(data);
    }, 500);

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
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

    // File-based tab restore
    restoreFromFile,

    // Direct ref access
    tabMapRef,
    activeTabIdRef,
  };
}
