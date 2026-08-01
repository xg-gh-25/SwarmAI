/**
 * resolveResumeTarget — pure decision for "Resume in tab" from the History overlay.
 *
 * Given the target session + the current open tabs + the chat-tab cap, decide
 * WHERE the session should resume, WITHOUT performing any side effect. The
 * caller (ChatPage) executes the returned action using the real tab machinery
 * (handleTabSelect / addTab), so this stays unit-testable and free of coupling.
 *
 * Branch order (matches the product decision):
 *   1. already-open  — the session is already loaded in some open tab → focus it.
 *   2. free-slot     — fewer than chatMax tabs open → open a new tab.
 *   3. reuse-idle    — no free slot, but a STRICTLY idle tab exists → reuse it.
 *   4. all-busy      — every tab is occupied/active → do nothing (caller toasts).
 *
 * "Idle" is strict: only status==='idle'. A tab that is streaming, waiting on a
 * question (waiting_input), holding a pending permission (permission_needed),
 * has an unread completion (complete_unread), or errored (error) is NOT
 * reusable — reusing it would silently abandon that tab's in-flight work.
 */

/** The subset of tab state this decision needs (structural — not the full UnifiedTab). */
export interface ResumeTabInfo {
  id: string;
  sessionId?: string;
  status: string;
  isStreaming: boolean;
}

export type ResumeAction =
  | { action: 'focus'; tabId: string }      // already open → selectTab
  | { action: 'newtab' }                     // free slot → addTab + load
  | { action: 'reuse'; tabId: string }       // reuse an idle tab → seed + handleTabSelect
  | { action: 'busy' };                      // all tabs busy → toast, no change

/** A tab is reusable ONLY when genuinely idle (see module doc). */
export function isReusableIdle(t: ResumeTabInfo): boolean {
  return t.status === 'idle' && !t.isStreaming;
}

export function resolveResumeTarget(
  sessionId: string,
  tabs: ResumeTabInfo[],
  chatMax: number,
): ResumeAction {
  // 1. Already open in some tab → focus it (no new load, no slot spent).
  const open = tabs.find((t) => t.sessionId === sessionId);
  if (open) return { action: 'focus', tabId: open.id };

  // 2. Free slot → open a fresh tab.
  if (tabs.length < chatMax) return { action: 'newtab' };

  // 3. No free slot, but a strictly-idle tab exists → reuse the first one.
  const idle = tabs.find(isReusableIdle);
  if (idle) return { action: 'reuse', tabId: idle.id };

  // 4. Every tab is busy → caller shows a toast and stays in the overlay.
  return { action: 'busy' };
}
