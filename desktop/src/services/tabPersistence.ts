/**
 * Filesystem-backed tab persistence service.
 *
 * Reads and writes open tab state via the backend settings API,
 * which persists to ``~/.swarm-ai/open_tabs.json``. Replaces
 * localStorage-based tab persistence (unreliable on macOS Tauri
 * WebKit where localStorage doesn't survive app restarts).
 *
 * Key exports:
 * - ``tabPersistenceService.load()``  — Read saved tab state
 * - ``tabPersistenceService.save()``  — Write current tab state
 * - ``OpenTabsFileData``              — Shape of the persisted JSON
 */

import api from './api';

/** Shape of a single tab entry in open_tabs.json. */
export interface PersistedTab {
  id: string;
  title: string;
  agentId: string;
  isNew: boolean;
  sessionId?: string;
}

/** Shape of the full open_tabs.json file. */
export interface OpenTabsFileData {
  tabs: PersistedTab[];
  activeTabId: string | null;
}

export const tabPersistenceService = {
  /**
   * Load saved tab state from ``~/.swarm-ai/open_tabs.json``.
   * Returns ``null`` if the file doesn't exist or is unreadable.
   */
  async load(): Promise<OpenTabsFileData | null> {
    try {
      const response = await api.get<OpenTabsFileData | null>(
        '/settings/open-tabs',
      );
      return response.data;
    } catch {
      console.warn('[tabPersistence] Failed to load open_tabs.json');
      return null;
    }
  },

  /**
   * Save current tab state to ``~/.swarm-ai/open_tabs.json``.
   * Fire-and-forget — errors are logged but don't block the UI.
   */
  async save(data: OpenTabsFileData): Promise<void> {
    try {
      await api.put('/settings/open-tabs', data);
    } catch (err) {
      console.warn('[tabPersistence] Failed to save open_tabs.json:', err);
    }
  },
};
