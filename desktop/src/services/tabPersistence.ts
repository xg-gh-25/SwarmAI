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

let waiting: OpenTabsFileData | null = null;
let sending: Promise<void> | null = null;

async function drain(): Promise<void> {
  try {
    while (waiting) {
      const data = waiting;
      waiting = null;
      try {
        await api.put('/settings/open-tabs', data);
      } catch (err) {
        console.warn('[tabPersistence] Failed to save open_tabs.json:', err);
      }
    }
  } finally {
    sending = null;
  }
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
    } catch (err: unknown) {
      // Distinguish "file not found" (backend returned null/404) from
      // "backend not ready" (network error). Only swallow the former.
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        // File genuinely doesn't exist — fresh install
        return null;
      }
      // Network error or backend not ready — let caller retry
      console.warn('[tabPersistence] Failed to load open_tabs.json:', status || err);
      throw err;
    }
  },

  /**
   * Save current tab state to ``~/.swarm-ai/open_tabs.json``.
   * Fire-and-forget — errors are logged but don't block the UI. Writes go
   * out one at a time and only the newest waiting state is sent next, so a
   * slower earlier write can never land after a later one.
   */
  save(data: OpenTabsFileData): Promise<void> {
    waiting = data;
    if (!sending) sending = drain();
    return sending;
  },
};
