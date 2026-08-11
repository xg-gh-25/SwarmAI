/**
 * dispatchBackfill — the PURE decision for backfilling a ToDo dispatch record
 * once its tab gains its own backend session.
 *
 * WHY (run_2c89bc8d): when a ToDo is dispatched into a tab we record a pending
 * {tabId, todoId, tabLabel} in dispatchPendingRef; the dispatched_session_id is
 * filled in later, when that tab's session materializes. That backfill used to
 * live ONLY in the ChatPage effect keyed on the GLOBAL sessionId — which only
 * updates for the ACTIVE tab. So if the user switched away before session_start
 * returned, a background tab's session bound but its pending record never
 * backfilled (leaked). session_start writes tabState.sessionId for ANY tab, so
 * the fix fires an onTabSessionBound callback there (active OR background) and
 * both that callback and the original effect funnel through THIS one helper —
 * unified logic, no per-branch duplication.
 *
 * Pure by design: it takes the records + (tabId, sessionId) and returns the
 * record to dispatch (if any) plus the pruned list. The caller performs the
 * actual todosService.dispatch + ref write. This keeps ALL ToDo logic out of
 * the streaming hot path (the hook only fires an opaque callback) — the P0
 * chat-experience-decoupling constraint.
 */

export interface DispatchPendingRecord {
  tabId: string;
  todoId: string;
  tabLabel: string;
}

export interface BackfillPlan {
  /** The record to backfill (newest-wins for this tab), or null if none pending. */
  toDispatch: (DispatchPendingRecord & { sessionId: string }) | null;
  /** The records array with THIS tab's records removed (backfill done → prune). */
  remaining: DispatchPendingRecord[];
}

/**
 * Decide the backfill for `tabId` now that it owns `sessionId`.
 *
 * Newest-wins: if the user dispatched into this tab more than once before it
 * got a session, the LAST record is the live one (append-only; earlier ones are
 * superseded). All of this tab's records are then dropped (backfill is done for
 * the tab — bounds growth, Gate-2 MEDIUM #9). Other tabs' records are untouched.
 *
 * No-op safety: empty sessionId/tabId, or no pending record for this tab, yields
 * `toDispatch: null` and the list unchanged (identity-preserved when nothing to
 * prune is not guaranteed — callers should compare toDispatch, not array identity).
 */
export function planDispatchBackfill(
  records: readonly DispatchPendingRecord[],
  tabId: string | undefined,
  sessionId: string | undefined,
): BackfillPlan {
  if (!tabId || !sessionId) {
    return { toDispatch: null, remaining: [...records] };
  }
  const mine = records.filter((r) => r.tabId === tabId);
  if (mine.length === 0) {
    return { toDispatch: null, remaining: [...records] };
  }
  const newest = mine[mine.length - 1];
  return {
    toDispatch: { ...newest, sessionId },
    remaining: records.filter((r) => r.tabId !== tabId),
  };
}
