import { useState, useEffect, useCallback } from 'react';
import type { OpenTab } from '../pages/chat/types';
import { OPEN_TABS_STORAGE_KEY, ACTIVE_TAB_STORAGE_KEY } from '../pages/chat/constants';

interface UseTabStateReturn {
  openTabs: OpenTab[];
  activeTabId: string | null;
  addTab: (agentId: string) => OpenTab;
  closeTab: (tabId: string) => void;
  selectTab: (tabId: string) => void;
  updateTabTitle: (tabId: string, title: string) => void;
  updateTabSessionId: (tabId: string, sessionId: string) => void;
  setTabIsNew: (tabId: string, isNew: boolean) => void;
  removeInvalidTabs: (validSessionIds: Set<string>) => void;
}

/**
 * Creates a new session tab with default values
 */
const createNewSessionTab = (agentId: string): OpenTab => ({
  id: crypto.randomUUID(),
  title: 'New Session',
  agentId,
  isNew: true,
});

/**
 * Loads tabs from localStorage, returns null if not found or invalid
 */
const loadTabsFromStorage = (): OpenTab[] | null => {
  try {
    const saved = localStorage.getItem(OPEN_TABS_STORAGE_KEY);
    if (!saved) return null;
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    return parsed;
  } catch {
    return null;
  }
};

/**
 * Loads active tab ID from localStorage
 */
const loadActiveTabIdFromStorage = (): string | null => {
  try {
    return localStorage.getItem(ACTIVE_TAB_STORAGE_KEY);
  } catch {
    return null;
  }
};

/**
 * Custom hook for managing tab state with localStorage persistence
 * 
 * Requirements covered:
 * - 1.7: Tab state persists across app restarts
 * - 3.1: On app load, previously open sessions are restored as tabs
 * - 3.2: If no previous sessions exist, a single "New Session" tab is shown
 * - 3.3: Closing the last remaining tab auto-creates a new "New Session" tab
 * - 2.2: New sessions open with default title "New Session"
 */
export function useTabState(defaultAgentId: string): UseTabStateReturn {
  // Initialize tabs from localStorage or create default
  const [openTabs, setOpenTabs] = useState<OpenTab[]>(() => {
    const savedTabs = loadTabsFromStorage();
    if (savedTabs && savedTabs.length > 0) {
      return savedTabs;
    }
    // No saved tabs - create default "New Session" tab (Req 3.2)
    return [createNewSessionTab(defaultAgentId)];
  });

  // Initialize active tab ID from localStorage or use first tab
  const [activeTabId, setActiveTabId] = useState<string | null>(() => {
    const savedActiveId = loadActiveTabIdFromStorage();
    const savedTabs = loadTabsFromStorage();
    
    // Validate saved active ID exists in tabs
    if (savedActiveId && savedTabs?.some(t => t.id === savedActiveId)) {
      return savedActiveId;
    }
    // Fall back to first tab
    const tabs = savedTabs && savedTabs.length > 0 ? savedTabs : [createNewSessionTab(defaultAgentId)];
    return tabs[0]?.id ?? null;
  });

  // Persist tabs to localStorage on change (Req 1.7)
  useEffect(() => {
    localStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(openTabs));
  }, [openTabs]);

  // Persist active tab ID to localStorage on change
  useEffect(() => {
    if (activeTabId) {
      localStorage.setItem(ACTIVE_TAB_STORAGE_KEY, activeTabId);
    }
  }, [activeTabId]);

  /**
   * Creates a new tab with "New Session" title (Req 2.2)
   * Returns the created tab for immediate use
   */
  const addTab = useCallback((agentId: string): OpenTab => {
    const newTab = createNewSessionTab(agentId);
    setOpenTabs(prev => [...prev, newTab]);
    setActiveTabId(newTab.id);
    return newTab;
  }, []);

  /**
   * Closes a tab, handling the last-tab case (Req 3.3)
   * If closing the active tab, switches to adjacent tab
   */
  const closeTab = useCallback((tabId: string) => {
    setOpenTabs(prev => {
      const remainingTabs = prev.filter(t => t.id !== tabId);
      
      if (remainingTabs.length === 0) {
        // Auto-create new session tab when closing last tab (Req 3.3)
        const newTab = createNewSessionTab(defaultAgentId);
        setActiveTabId(newTab.id);
        return [newTab];
      }
      
      // If closing active tab, switch to adjacent tab
      setActiveTabId(currentActiveId => {
        if (currentActiveId === tabId) {
          const closedIndex = prev.findIndex(t => t.id === tabId);
          const newActiveIndex = Math.min(closedIndex, remainingTabs.length - 1);
          return remainingTabs[newActiveIndex].id;
        }
        return currentActiveId;
      });
      
      return remainingTabs;
    });
  }, [defaultAgentId]);

  /**
   * Selects a tab as active
   */
  const selectTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
  }, []);

  /**
   * Updates a tab's title (used when first message is sent - Req 2.4)
   */
  const updateTabTitle = useCallback((tabId: string, title: string) => {
    setOpenTabs(prev => 
      prev.map(tab => 
        tab.id === tabId ? { ...tab, title } : tab
      )
    );
  }, []);

  /**
   * Links a tab to a backend session ID
   */
  const updateTabSessionId = useCallback((tabId: string, sessionId: string) => {
    setOpenTabs(prev => 
      prev.map(tab => 
        tab.id === tabId ? { ...tab, sessionId } : tab
      )
    );
  }, []);

  /**
   * Marks a tab as no longer new (after first message sent)
   */
  const setTabIsNew = useCallback((tabId: string, isNew: boolean) => {
    setOpenTabs(prev => 
      prev.map(tab => 
        tab.id === tabId ? { ...tab, isNew } : tab
      )
    );
  }, []);

  /**
   * Removes tabs that reference deleted sessions (Req 3.4)
   * Tabs with sessionIds not in validSessionIds have their sessionId cleared
   * This handles the edge case where saved tabs reference sessions that were deleted
   */
  const removeInvalidTabs = useCallback((validSessionIds: Set<string>) => {
    setOpenTabs(prev => {
      const updatedTabs = prev.map(tab => {
        // If tab has a sessionId that doesn't exist in valid sessions, clear it
        if (tab.sessionId && !validSessionIds.has(tab.sessionId)) {
          return { ...tab, sessionId: undefined, isNew: true, title: 'New Session' };
        }
        return tab;
      });
      
      // Check if any tabs were actually modified
      const hasChanges = updatedTabs.some((tab, i) => 
        tab.sessionId !== prev[i].sessionId || 
        tab.isNew !== prev[i].isNew || 
        tab.title !== prev[i].title
      );
      
      return hasChanges ? updatedTabs : prev;
    });
  }, []);

  return {
    openTabs,
    activeTabId,
    addTab,
    closeTab,
    selectTab,
    updateTabTitle,
    updateTabSessionId,
    setTabIsNew,
    removeInvalidTabs,
  };
}
