/**
 * Swarm Out — the pinned chat tab for dispatching and managing work on Hive.
 *
 * It is an ordinary chat tab (same MessageStore, streaming, input and Canvas as every
 * other tab) with three differences, all decided here so every consumer agrees:
 *
 * - **Identity.** One reserved tab id, `SWARM_OUT_TAB_ID`. The tab map is keyed by id,
 *   so there can never be two, and `open_tabs.json` needs no new field: the id says
 *   what the tab is.
 * - **Slot.** It holds one of the backend's chat slots (`chat_max`), reserved, so the
 *   user's own chat tabs are capped at `normalChatMax(chatMax)`. The floor
 *   (backend `ResourceMonitor._MIN_TABS_FLOOR` = 1 chat + Swarm Out + 1 channel) keeps
 *   that at least 1.
 * - **Lifecycle.** It cannot be closed, keeps its title, sits at the right end of the
 *   tab row (not in the scrolling strip) and is never chosen as the tab a ToDo, job
 *   prompt or history session lands in.
 */

export const SWARM_OUT_TAB_ID = 'swarm-out';
export const SWARM_OUT_TITLE = 'Swarm Out';

export function isSwarmOutTab(tabId: string | null | undefined): boolean {
  return tabId === SWARM_OUT_TAB_ID;
}

/** The user's own chat tabs: every chat slot except the one Swarm Out holds. */
export function normalChatMax(chatMax: number): number {
  return Math.max(1, chatMax - 1);
}

/** The tabs that are the user's own chat tabs (Swarm Out excluded), in order. */
export function normalTabs<T extends { id: string }>(tabs: Iterable<T>): T[] {
  const out: T[] = [];
  for (const t of tabs) if (!isSwarmOutTab(t.id)) out.push(t);
  return out;
}
