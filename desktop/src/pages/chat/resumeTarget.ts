/**
 * resolveResumeTarget — pure decision for landing a session/todo in a chat tab.
 *
 * Shared by BOTH History "Resume in tab" AND ToDo Dispatch (run_5088b841, A2).
 * Given the target session + current open tabs + the chat-tab cap + the active
 * tab id, decide WHERE to land WITHOUT any side effect. The caller (ChatPage)
 * executes the returned action, so this stays unit-testable and coupling-free.
 *
 * Branch order (product decision, revised A2 post-Gate-1):
 *   1. already-open  — session already loaded in some tab → focus it.
 *   2. free-slot     — fewer than chatMax tabs open → open a new tab.
 *   3. reuse-current — NO free slot, and the CURRENTLY-ACTIVE tab is strictly
 *                      idle → clear + reuse THAT tab. This is the ONLY reuse we
 *                      allow: it's the tab you're already on, cleared for new
 *                      content — NOT a background idle tab seeded with a
 *                      different session (that was the old `reuse`, DELETED: it
 *                      rewrote a background tab's MessageStore = OT01 fork risk).
 *   4. needs-close   — no free slot and the active tab is busy → caller toasts
 *                      "close a tab, then retry"; no change.
 *
 * WHY reuse-current is mandatory (Gate-1 CRITICAL, verified): chatMax can be 1
 * (chatMax = Math.max(1, MAX_OPEN_TABS_FALLBACK-1)). A pure delete of reuse would
 * dead-lock a single-tab user — their one idle tab can't take a newtab (slot
 * full) and, without reuse, falls to needs-close ("close your only usable tab").
 * reuse-current unlocks exactly that case with NO store-fork risk.
 *
 * "Idle" is strict: only status==='idle' && !isStreaming. A tab that is
 * streaming, waiting_input, permission_needed, complete_unread, or errored is
 * NOT reusable — reusing it would silently abandon in-flight work.
 */

/** The subset of tab state this decision needs (structural — not the full UnifiedTab). */
export interface ResumeTabInfo {
  id: string;
  sessionId?: string;
  status: string;
  isStreaming: boolean;
}

export type ResumeAction =
  | { action: 'focus'; tabId: string }          // already open → selectTab
  | { action: 'newtab' }                         // free slot → addTab + load
  | { action: 'reuse-current'; tabId: string }   // full + active tab idle → clear + reuse it
  | { action: 'needs-close' };                   // full + active tab busy → toast, no change

/** A tab is reusable ONLY when genuinely idle (see module doc). */
export function isReusableIdle(t: ResumeTabInfo): boolean {
  return t.status === 'idle' && !t.isStreaming;
}

export function resolveResumeTarget(
  sessionId: string,
  tabs: ResumeTabInfo[],
  chatMax: number,
  activeTabId?: string,
): ResumeAction {
  // 1. Already open in some tab → focus it (no new load, no slot spent).
  //    (Dispatch passes a sessionId that never matches — a todo is new work —
  //    so dispatch simply never hits this branch.)
  const open = tabs.find((t) => t.sessionId === sessionId);
  if (open) return { action: 'focus', tabId: open.id };

  // 2. Free slot → open a fresh tab.
  if (tabs.length < chatMax) return { action: 'newtab' };

  // 3. No free slot: reuse the ACTIVE tab IFF it is strictly idle. Never a
  //    background idle tab (that was the deleted OT01-risky reuse).
  const active = activeTabId ? tabs.find((t) => t.id === activeTabId) : undefined;
  if (active && isReusableIdle(active)) return { action: 'reuse-current', tabId: active.id };

  // 4. Full and the active tab is busy → caller toasts "close a tab, then retry".
  return { action: 'needs-close' };
}
