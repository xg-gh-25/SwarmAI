/**
 * sessionBinding — the PURE guard that decides whether the global `sessionId`
 * may bind to (or trigger backfill for) the currently-active tab.
 *
 * WHY THIS EXISTS (the perpetual-thinking bug, run_3e0672d2): the backfill
 * effect in ChatPage used to stamp *any* ambient global `sessionId` onto the
 * active tab whenever that tab had no session of its own. When an overlay
 * dispatch (or "+ New Session", or resume) opened a fresh tab WHILE the
 * previously-active tab was still streaming, the fresh empty tab inherited the
 * streaming tab's live session — two tabs sharing one session → the empty tab
 * mirrored the stream's "thinking" forever, and its sends queued because that
 * session was busy.
 *
 * The real per-tab binding already happens AT THE SOURCE:
 *   - a genuinely new session: `session_start` writes `tabState.sessionId`
 *     directly (useChatStreamingLifecycle SSE handler);
 *   - a resumed session: `updateTabSessionId(tabId, session.id)` (handleResumeSession).
 * So the effect only needs a *backstop* + the ToDo dispatch backfill — and BOTH
 * must act ONLY on a session that genuinely belongs to THIS tab, never one
 * already owned by a different (e.g. still-streaming) tab.
 *
 * This is the single, unified guard every tab-open entrance funnels through —
 * so the fix is structural (one chokepoint), not a per-branch patch.
 */

/** Minimal per-tab shape this guard needs (a subset of UnifiedTab). */
export interface SessionOwnershipTab {
  id: string;
  sessionId?: string;
}

/**
 * Is `sessionId` already owned by some tab OTHER than `activeTabId`?
 *
 * True → the active tab must NOT bind to / backfill this session (it belongs to
 * another tab, e.g. the previously-active still-streaming one). This is the
 * discriminator that separates "a session freshly created for THIS tab"
 * (safe to bind) from "an ambient session leaking from another tab" (the bug).
 */
export function sessionOwnedByOtherTab(
  sessionId: string | undefined,
  activeTabId: string | undefined,
  tabs: readonly SessionOwnershipTab[],
): boolean {
  if (!sessionId || !activeTabId) return false;
  return tabs.some((t) => t.id !== activeTabId && t.sessionId === sessionId);
}

/**
 * Should the backfill effect bind/act on `sessionId` for `activeTabId`?
 *
 * The unified gate: act ONLY when we have a real active session AND it is not
 * already owned by another tab. Callers still apply their own narrower checks
 * (e.g. "tab has no sessionId yet" for the bind, "a pending dispatch record
 * exists" for the ToDo backfill) — this guard is the shared safety condition
 * that all of them require.
 */
export function canBindSessionToActiveTab(
  sessionId: string | undefined,
  activeTabId: string | undefined,
  tabs: readonly SessionOwnershipTab[],
): boolean {
  if (!sessionId || !activeTabId) return false;
  return !sessionOwnedByOtherTab(sessionId, activeTabId, tabs);
}
