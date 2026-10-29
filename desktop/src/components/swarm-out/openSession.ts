/**
 * Opens a run's Hive session in the Hive tab (ChatPage.openHiveSession, design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4). Provided by
 * SwarmOutDashboard; null outside ChatPage, where run titles stay plain text.
 *
 * Outside Swarm Out (a Brain's Runs tab, inside the Brain Hub overlay) the overlay host
 * provides it through overlaySessionOpener: close the overlay, then open the tab.
 *
 * @exports OpenSessionContext, useOpenRun, overlaySessionOpener
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

/** A Hive run card's opener inside an overlay: close the overlay, then open the run's
 *  Hive session in the Hive tab (the tab sits under the overlay). null when ChatPage has
 *  not published the opener: the cards then show no Open, as outside the app shell. */
export function overlaySessionOpener(close: () => void, open?: (sessionId: string, title: string) => void) {
  if (!open) return null;
  return (sessionId: string, title: string) => { close(); open(sessionId, title); };
}
