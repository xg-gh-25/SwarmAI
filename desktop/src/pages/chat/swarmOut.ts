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
 * - **Lifecycle.** It cannot be closed, keeps its title, sits right after the strip's
 *   "+" behind a divider (not in the scrolling strip) and is never chosen as the tab a
 *   ToDo, job prompt or history session lands in.
 *
 * The Hive tab (below) sits right after it, in the same group, joined to it by a
 * branch line and drawn as its child (index.css .session-tab--hive).
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

/** The tabs that are the user's own chat tabs (Swarm Out and the Hive tab excluded),
 *  in order. */
export function normalTabs<T extends { id: string }>(tabs: Iterable<T>): T[] {
  const out: T[] = [];
  for (const t of tabs) if (!isSwarmOutTab(t.id) && !isHiveTab(t.id)) out.push(t);
  return out;
}

// ── The Hive tab (docs/design/2026-10-29-swarm-out-hive-session-tab.md §4) ──
//
// A dispatched run's Hive session opened in the app: a chat tab after the Swarm Out
// tab, in its divider group. Its calls go to the Host proxy (services/remoteSession)
// and its live events come from the Host relay. One at a time: opening another run
// closes it and opens a new one. Each Hive session gets its own tab id, so a late
// callback of the old tab has no tab to land on. Unlike Swarm Out it can be closed
// (closing does not stop the run), takes no chat slot and is never a landing target.

export const HIVE_TAB_PREFIX = 'hive-session:';

export function hiveTabId(sessionId: string): string {
  return `${HIVE_TAB_PREFIX}${sessionId}`;
}

export function isHiveTab(tabId: string | null | undefined): boolean {
  return typeof tabId === 'string' && tabId.startsWith(HIVE_TAB_PREFIX);
}

/** The Hive tab among `tabs`, if there is one. */
export function hiveTabOf<T extends { id: string }>(tabs: Iterable<T>): T | undefined {
  for (const t of tabs) if (isHiveTab(t.id)) return t;
  return undefined;
}

/** The longest command the person may approve in a Hive tab (backend
 *  core/swarm_out_bridge.MAX_COMMAND_CHARS: the dashboard's own limit). */
export const MAX_APPROVABLE_COMMAND_CHARS = 2000;

// Characters the person cannot read as they will run: every control (Cc: C0, C1, DEL)
// other than tab, newline and carriage return, every format character (Cf: the bidi
// controls and marks LRM/RLM/ALM, zero-width ones, BOM, tag characters) and the line
// and paragraph separators (Zl, Zp).
const UNREVIEWABLE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
const ALLOWED_CONTROLS = /[\t\n\r]/g;

/** Whether a command shown in a Hive tab may be approved there: short enough to read
 *  whole and free of characters that hide what it does (design §6.4). Otherwise the
 *  card offers Deny only. */
export function hiveCommandApprovable(command: string): boolean {
  return command.length <= MAX_APPROVABLE_COMMAND_CHARS && !UNREVIEWABLE.test(command.replace(ALLOWED_CONTROLS, ''));
}
