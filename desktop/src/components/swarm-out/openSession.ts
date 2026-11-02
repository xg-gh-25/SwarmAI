/**
 * Opens a run's Hive session in the Hive tab (ChatPage.openHiveSession, design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4). Provided by
 * SwarmOutDashboard; null outside ChatPage, where run titles stay plain text.
 *
 * Outside Swarm Out (a Brain's Runs tab, inside the Brain Hub overlay) the overlay host
 * provides it through overlaySessionOpener: close the overlay, then open the tab.
 *
 * @exports OpenSessionContext, PanelHiddenContext, useOpenRun, overlaySessionOpener
 */
import { createContext, useContext } from 'react';
import { isRemoteId } from '../../services/remoteSession';
import type { SwarmOutRun } from '../../services/swarmOut';

/** ``readOnly``: the run is accepted: its session can be read, nothing sent to it
 *  (the Host proxy refuses writes; the Hive tab shows a line instead of the input). */
export type OpenSession = (sessionId: string, title: string, readOnly?: boolean) => void;

export const OpenSessionContext = createContext<OpenSession | null>(null);

/** True while the Swarm Out panel is not on screen (another tab is active): a run's big
 *  card, drawn above the page, closes then (SwarmOutDashboard provides it). */
export const PanelHiddenContext = createContext(false);

/** Opens a run's Hive session in the Hive tab, or null when there is none this Host
 *  knows, or the run's commands do not include opening it (a closed run:
 *  backend core/swarm_out_settle; design 2026-10-31-swarm-out-run-review §3.4). The tab
 *  is titled run first, then its Hive: a narrow tab still shows which run it is, and
 *  the whole title is on its hover. */
export function useOpenRun(r: SwarmOutRun): (() => void) | null {
  const open = useContext(OpenSessionContext);
  const sid = r.session_id;
  if (!open || !isRemoteId(sid) || r.session_unknown) return null;
  const action = r.actions ? r.actions.find((a) => a.key === 'open_session') : { key: 'open_session', variant: undefined };
  if (!action) return null;
  const readOnly = action.variant === 'read_only';
  return () => open(sid, `${r.title} · ${r.hive}`, readOnly);
}

/** A Hive run card's opener inside an overlay: close the overlay, then open the run's
 *  Hive session in the Hive tab (the tab sits under the overlay). null when ChatPage has
 *  not published the opener: the cards then show no Open, as outside the app shell. */
export function overlaySessionOpener(close: () => void, open?: OpenSession) {
  if (!open) return null;
  return (sessionId: string, title: string, readOnly?: boolean) => { close(); open(sessionId, title, readOnly); };
}
