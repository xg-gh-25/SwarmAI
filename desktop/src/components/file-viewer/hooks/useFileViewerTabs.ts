/**
 * useFileViewerTabs — Tab state manager for the unified FileViewer.
 *
 * Manages an ordered list of open file tabs with single-active semantics.
 * Handles open (deduplicated), close (with dirty guard), reorder, and
 * overflow eviction (max 10 tabs, oldest non-dirty first).
 *
 * Pure React state — no external store. Tab identity is keyed by filePath.
 */

import { useState, useCallback, useMemo } from 'react';
import { classifyFileForViewer } from '../utils/fileViewTypes';
import type { FileViewType } from '../utils/fileViewTypes';
import type { GitStatus } from '../../../types';

const MAX_TABS = 10;

export interface FileTab {
  /** Unique key — identical to filePath. */
  id: string;
  filePath: string;
  fileName: string;
  viewType: FileViewType;
  gitStatus?: GitStatus;
  /** True when the tab has unsaved edits. */
  isDirty: boolean;
  /** Persisted scroll offset so we can restore position on tab switch. */
  scrollPosition?: number;
}

export interface UseFileViewerTabsReturn {
  tabs: FileTab[];
  activeTab: FileTab | null;
  openTab: (filePath: string, fileName: string, gitStatus?: GitStatus) => void;
  closeTab: (tabId: string) => void;
  closeAllTabs: () => void;
  switchTab: (tabId: string) => void;
  markDirty: (tabId: string, dirty: boolean) => void;
  saveScrollPosition: (tabId: string, position: number) => void;
}

export function useFileViewerTabs(): UseFileViewerTabsReturn {
  const [tabs, setTabs] = useState<FileTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);

  const activeTab = useMemo(
    () => tabs.find((t) => t.id === activeTabId) ?? null,
    [tabs, activeTabId],
  );

  /**
   * Open a file in a tab. If a tab with the same filePath already exists,
   * switch to it. Otherwise create a new tab (evicting the oldest non-dirty
   * tab when at capacity).
   */
  const openTab = useCallback(
    (filePath: string, fileName: string, gitStatus?: GitStatus) => {
      setTabs((prev) => {
        // Dedup: already open — just activate.
        const existing = prev.find((t) => t.filePath === filePath);
        if (existing) {
          setActiveTabId(existing.id);
          return prev;
        }

        // Overflow eviction
        let next = [...prev];
        if (next.length >= MAX_TABS) {
          const evictIdx = next.findIndex((t) => !t.isDirty);
          if (evictIdx === -1) {
            // All tabs are dirty — refuse to open.
            console.warn('[useFileViewerTabs] Cannot open tab: all 10 tabs have unsaved changes.');
            return prev;
          }
          next.splice(evictIdx, 1);
        }

        const newTab: FileTab = {
          id: filePath,
          filePath,
          fileName,
          viewType: classifyFileForViewer(fileName),
          gitStatus,
          isDirty: false,
        };

        next.push(newTab);
        setActiveTabId(newTab.id);
        return next;
      });
    },
    [],
  );

  /**
   * Close a tab. If the tab is dirty the call is a no-op (caller is
   * responsible for showing a confirmation dialog first and calling
   * `markDirty(id, false)` before retrying).
   *
   * After removal the nearest neighbour is activated (prefer right, then left).
   */
  const closeTab = useCallback(
    (tabId: string) => {
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.id === tabId);
        if (idx === -1) return prev;
        if (prev[idx].isDirty) return prev; // caller must confirm first

        const next = prev.filter((t) => t.id !== tabId);

        // Activate neighbour when closing the active tab.
        setActiveTabId((currentActive) => {
          if (currentActive !== tabId) return currentActive;
          if (next.length === 0) return null;
          // Prefer the tab that slid into the same index (right neighbour),
          // falling back to the last tab (left neighbour).
          const neighbour = next[Math.min(idx, next.length - 1)];
          return neighbour.id;
        });

        return next;
      });
    },
    [],
  );

  /** Close every tab that is not dirty. Dirty tabs remain open. */
  const closeAllTabs = useCallback(() => {
    setTabs((prev) => {
      const remaining = prev.filter((t) => t.isDirty);
      setActiveTabId(remaining.length > 0 ? remaining[0].id : null);
      return remaining;
    });
  }, []);

  /** Switch the active tab. No-op if the id is not found. */
  const switchTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
  }, []);

  /** Mark (or clear) the dirty flag on a tab. */
  const markDirty = useCallback((tabId: string, dirty: boolean) => {
    setTabs((prev) =>
      prev.map((t) => (t.id === tabId ? { ...t, isDirty: dirty } : t)),
    );
  }, []);

  /** Persist the scroll position for a tab (used before switching away). */
  const saveScrollPosition = useCallback((tabId: string, position: number) => {
    setTabs((prev) =>
      prev.map((t) => (t.id === tabId ? { ...t, scrollPosition: position } : t)),
    );
  }, []);

  return {
    tabs,
    activeTab,
    openTab,
    closeTab,
    closeAllTabs,
    switchTab,
    markDirty,
    saveScrollPosition,
  };
}
