/**
 * Opens a run's Hive session in the Hive tab (ChatPage.openHiveSession, design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4). Provided by
 * SwarmOutDashboard; null outside ChatPage, where run titles stay plain text.
 *
 * @exports OpenSessionContext, useOpenRun
 */
import { createContext, useContext } from 'react';
import { isRemoteId } from '../../services/remoteSession';
import type { SwarmOutRun } from '../../services/swarmOut';

export const OpenSessionContext = createContext<((sessionId: string, title: string) => void) | null>(null);

/** Opens a run's Hive session in the Hive tab, or null when there is none this Host
 *  knows. The tab is titled run first, then its Hive: a narrow tab still shows which
 *  run it is, and the whole title is on its hover. */
export function useOpenRun(r: SwarmOutRun): (() => void) | null {
  const open = useContext(OpenSessionContext);
  const sid = r.session_id;
  if (!open || !isRemoteId(sid) || r.session_unknown) return null;
  return () => open(sid, `${r.title} · ${r.hive}`);
}
