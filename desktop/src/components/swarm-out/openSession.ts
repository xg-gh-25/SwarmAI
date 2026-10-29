/**
 * Opens a run's Hive session in the Hive tab (ChatPage.openHiveSession, design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4). Provided by
 * SwarmOutDashboard; null outside ChatPage, where run titles stay plain text.
 *
 * @exports OpenSessionContext
 */
import { createContext } from 'react';

export const OpenSessionContext = createContext<((sessionId: string, title: string) => void) | null>(null);
