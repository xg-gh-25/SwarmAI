/**
 * Whether the content under it belongs to a Hive session (the Hive tab,
 * pages/chat/swarmOut.ts): its paths name files on the Hive, not here, so a path link
 * renders as text and a workspace image is not loaded from this machine, and an
 * approval is held to the dashboard's rule (design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §6.4, §7). False everywhere
 * else: local tabs render as before.
 *
 * @exports RemoteContentContext
 */
import { createContext } from 'react';

export const RemoteContentContext = createContext(false);
