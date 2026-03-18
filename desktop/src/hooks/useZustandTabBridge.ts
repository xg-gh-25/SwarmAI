/**
 * Bridge hook that wraps the Zustand TabStore and exposes the same API
 * as useUnifiedTabState. This allows ChatPage.tsx to migrate to Zustand
 * without changing any of its code — only the import changes.
 *
 * Phase 3 migration strategy:
 * 1. Create this bridge (this file)
 * 2. Change ChatPage import from useUnifiedTabState to useZustandTabBridge
 * 3. Verify identical behavior
 * 4. Later: remove useUnifiedTabState.ts and this bridge, use Zustand directly
 *
 * @module useZustandTabBridge
 */

import { useCallback, useRef, useMemo } from 'react';
import { useTabStore } from '../stores/tabStore';
import type { TabState, TabStatus } from '../stores/tabStore';

// Re-export MAX_OPEN_TABS for backward compatibility
export const MAX_OPEN_TABS = 6;

/**
 * Bridge interface matching useUnifiedTabState's return type.
 * ChatPage.tsx destructures this exact shape.
 */
export interface ZustandTabBridge {
  openTabs: Array<{ id: string; title: string; sessionId: string; agentId: string; isNew: boolean }>;
  activeTabId: string | null;
  addTab: (agentId: string) => string;
  closeTab: (tabId: string) => void;
  selectTab: (tabId: string) => void;
  updateTabTitle: (tabId: string, title: string) => void;
  updateTabSessionId: (tabId: string, sessionId: string) => void;
  setTabIsNew: (tabId: string, isNew: boolean) => void;
  removeInvalidTabs: (validSessionIds: Set<string>) => void;
  tabStatuses: Record<string, TabStatus>;
  updateTabStatus: (tabId: string, status: TabStatus) => void;
  getTabState: (tabId: string) => TabState | undefined;
  updateTabState: (tabId: string, update: Partial<TabState>) => void;
  tabMapRef: React.MutableRefObject<Map<string, TabState>>;
  activeTabIdRef: React.MutableRefObject<string | null>;
  restoreTab: (tabId: string) => void;
  initTabState: (tabId: string) => void;
  restoreFromFile: () => Promise<void>;
}

/**
 * Bridge hook — wraps Zustand store with useUnifiedTabState-compatible API.
 *
 * This is a transitional layer. Once ChatPage is fully migrated to use
 * Zustand selectors directly, this bridge can be removed.
 */
export function useZustandTabBridge(defaultAgentId: string): ZustandTabBridge {
  const store = useTabStore();

  // Build tabMapRef from Zustand state (for backward compat with stream handlers)
  // TEMPORARY: Only needed by useChatStreamingLifecycle stream handlers that
  // haven't migrated to Zustand selectors yet. Remove when all stream handler
  // callers read from useTabStore directly. Callers that still need this:
  // - createStreamHandler (reads tabMapRef for background tab writes)
  // - createErrorHandler (reads tabMapRef for background tab error appends)
  // - useUnifiedAttachments (reads tabMapRef for attachment state)
  const tabMapRef = useRef(new Map<string, TabState>());
  // Sync tabMapRef with Zustand state
  const tabEntries = Object.entries(store.tabs);
  tabMapRef.current.clear();
  for (const [id, tab] of tabEntries) {
    tabMapRef.current.set(id, tab);
  }

  const activeTabIdRef = useRef<string | null>(store.activeTabId);
  activeTabIdRef.current = store.activeTabId;

  // Derive openTabs array from Zustand state
  const openTabs = useMemo(() =>
    Object.values(store.tabs).map((tab) => ({
      id: tab.tabId,
      title: tab.title,
      sessionId: tab.sessionId,
      agentId: tab.agentId,
      isNew: !tab.sessionId,
    })),
    [store.tabs],
  );

  // Derive tabStatuses from Zustand state
  const tabStatuses = useMemo(() => {
    const statuses: Record<string, TabStatus> = {};
    for (const [id, tab] of Object.entries(store.tabs)) {
      statuses[id] = tab.status;
    }
    return statuses;
  }, [store.tabs]);

  const addTab = useCallback((agentId: string) => {
    return store.createTab(agentId);
  }, [store]);

  const closeTab = useCallback((tabId: string) => {
    store.closeTab(tabId);
  }, [store]);

  const selectTab = useCallback((tabId: string) => {
    store.setActiveTab(tabId);
  }, [store]);

  const updateTabTitle = useCallback((tabId: string, title: string) => {
    const tab = store.tabs[tabId];
    if (tab) {
      useTabStore.setState((state) => ({
        tabs: { ...state.tabs, [tabId]: { ...state.tabs[tabId], title } },
      }));
    }
  }, [store.tabs]);

  const updateTabSessionId = useCallback((tabId: string, sessionId: string) => {
    store.setSessionId(tabId, sessionId);
  }, [store]);

  const setTabIsNew = useCallback((_tabId: string, _isNew: boolean) => {
    // No-op in Zustand — "isNew" is derived from !sessionId
  }, []);

  const removeInvalidTabs = useCallback((validSessionIds: Set<string>) => {
    for (const [tabId, tab] of Object.entries(store.tabs)) {
      if (tab.sessionId && !validSessionIds.has(tab.sessionId)) {
        store.closeTab(tabId);
      }
    }
  }, [store]);

  const updateTabStatus = useCallback((tabId: string, status: TabStatus) => {
    store.setStatus(tabId, status);
  }, [store]);

  const getTabState = useCallback((tabId: string) => {
    return store.tabs[tabId];
  }, [store.tabs]);

  const updateTabState = useCallback((tabId: string, update: Partial<TabState>) => {
    useTabStore.setState((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, ...update } },
      };
    });
  }, []);

  const restoreTab = useCallback((_tabId: string) => {
    // No-op — Zustand handles state automatically
  }, []);

  const initTabState = useCallback((_tabId: string) => {
    // No-op — tab state initialized in createTab
  }, []);

  const restoreFromFile = useCallback(async () => {
    // TODO: Load from open_tabs.json via backend API
    // For now, create a default tab if none exist
    if (Object.keys(store.tabs).length === 0) {
      store.createTab(defaultAgentId);
    }
  }, [store, defaultAgentId]);

  return {
    openTabs,
    activeTabId: store.activeTabId,
    addTab,
    closeTab,
    selectTab,
    updateTabTitle,
    updateTabSessionId,
    setTabIsNew,
    removeInvalidTabs,
    tabStatuses,
    updateTabStatus,
    getTabState,
    updateTabState,
    tabMapRef,
    activeTabIdRef,
    restoreTab,
    initTabState,
    restoreFromFile,
  };
}
