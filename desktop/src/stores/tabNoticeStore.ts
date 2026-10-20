/**
 * Per-tab notice store — where chat-tab and chat-session notices live instead
 * of the app-wide top-right toast stack (which is for system-level events only).
 *
 * Isolation rules (each one closes a cross-tab / cross-session race):
 * - A notice is written for an EXPLICIT tab object captured when the event
 *   happened. The store never reads "the active tab".
 * - `post` drops the notice when the tab is gone or its sessionId no longer
 *   equals the sessionId the caller captured (the tab moved on to another
 *   session in between).
 * - Every write bumps a token. An auto-dismiss timer, or a caller that wants to
 *   retract "its" notice, passes the token back; a newer notice with the same
 *   id is never removed by an older timer or caller.
 * - Each notice records the tab's sessionId at write time. Readers show only
 *   notices of the tab's current session (`visibleNotices`); `syncSession`
 *   then drops the rest for good. A notice written before the tab had any
 *   session (sessionId undefined) is adopted by the first session it binds.
 * - Lists are immutable: every change publishes a new array, so a memoized
 *   reader always sees the change.
 * - `clearTab` drops a closed tab's notices and timers.
 *
 * Key exports:
 * - `TabNotice`, `TabNoticeInput`, `TabNoticeSeverity` — notice types
 * - `createTabNoticeStore` — factory (tests inject fake timers)
 * - `tabNoticeStore`       — the app singleton, keyed by tabId
 * - `visibleNotices`       — pure read-side session filter
 * - `noticeDotSeverity`    — pure: which tab-header dot a notice list earns
 * - `useTabNotices`        — React subscription for one tab
 */
import { useSyncExternalStore } from 'react';

export type TabNoticeSeverity = 'info' | 'success' | 'warning' | 'error';

export interface TabNoticeAction {
  label: string;
  /** Called with the tab and session the notice belongs to — never the active tab. */
  run: (tabId: string, sessionId: string | undefined) => void;
}

export interface TabNoticeInput {
  /** Dedup key: a second post with the same id replaces the first in place. */
  id: string;
  severity: TabNoticeSeverity;
  message: string;
  action?: TabNoticeAction;
  /** Remove the notice after this many ms (only if it was not replaced since). */
  autoDismissMs?: number;
}

export interface TabNotice extends TabNoticeInput {
  tabId: string;
  /** Monotonic write token (see module docstring). */
  token: number;
  /** The tab's sessionId when the notice was written (undefined = not bound yet). */
  sessionId: string | undefined;
}

/** The part of a tab the store needs. Pass the LIVE tab object from tabMapRef. */
export interface NoticeTarget {
  id: string;
  sessionId?: string;
}

/** Most notices a tab keeps; the oldest is dropped beyond this. */
export const MAX_NOTICES_PER_TAB = 5;

const EMPTY: readonly TabNotice[] = Object.freeze([]);

/** Read-side filter: the notices of the tab's current session. A notice written
 *  before the tab had a session is shown too (it is adopted on the next sync). */
export function visibleNotices(
  list: readonly TabNotice[],
  sessionId: string | undefined,
): readonly TabNotice[] {
  if (list.every((n) => n.sessionId === undefined || n.sessionId === sessionId)) return list;
  return list.filter((n) => n.sessionId === undefined || n.sessionId === sessionId);
}

/** Which header dot a notice list earns: the worst of warning/error, else none. */
export function noticeDotSeverity(list: readonly TabNotice[]): 'warning' | 'error' | null {
  if (list.some((n) => n.severity === 'error')) return 'error';
  if (list.some((n) => n.severity === 'warning')) return 'warning';
  return null;
}

interface Timers {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
}

const realTimers: Timers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export function createTabNoticeStore(timers: Timers = realTimers) {
  const lists = new Map<string, readonly TabNotice[]>();
  const handles = new Map<string, Map<string, unknown>>();
  const listeners = new Map<string, Set<() => void>>();
  let seq = 0;

  const emit = (tabId: string) => {
    listeners.get(tabId)?.forEach((l) => l());
  };

  const publish = (tabId: string, next: readonly TabNotice[]) => {
    if (next.length === 0) lists.delete(tabId);
    else lists.set(tabId, next);
    emit(tabId);
  };

  const clearTimer = (tabId: string, id: string) => {
    const byId = handles.get(tabId);
    const h = byId?.get(id);
    if (h === undefined) return;
    timers.clearTimeout(h);
    byId!.delete(id);
    if (byId!.size === 0) handles.delete(tabId);
  };

  /** Adopt not-yet-bound notices into `sessionId` and drop other sessions' notices. */
  const syncSession = (tabId: string, sessionId: string | undefined): void => {
    const cur = lists.get(tabId);
    if (!cur) return;
    let changed = false;
    const next: TabNotice[] = [];
    for (const n of cur) {
      if (n.sessionId === sessionId) next.push(n);
      else if (n.sessionId === undefined) {
        // A notice from before the tab bound a session belongs to that session.
        if (sessionId !== undefined) changed = true;
        next.push(sessionId !== undefined ? { ...n, sessionId } : n);
      } else {
        changed = true;
        clearTimer(tabId, n.id);
      }
    }
    if (changed) publish(tabId, next);
  };

  /** The tab's conversation continues under a new session id (SDK compaction):
   *  re-tag its `from` notices as `to` so they are not dropped as another
   *  session's. Notices of any other session are untouched. */
  const renameSession = (tabId: string, from: string | undefined, to: string): void => {
    const cur = lists.get(tabId);
    if (!cur || from === to || !cur.some((n) => n.sessionId === from)) return;
    publish(tabId, cur.map((n) => (n.sessionId === from ? { ...n, sessionId: to } : n)));
  };

  const dismiss = (tabId: string, id: string, token?: number): void => {
    const cur = lists.get(tabId);
    const n = cur?.find((x) => x.id === id);
    if (!cur || !n) return;
    if (token !== undefined && n.token !== token) return; // a newer notice owns this id
    clearTimer(tabId, id);
    publish(tabId, cur.filter((x) => x !== n));
  };

  /**
   * Write a notice to `tab`. Returns the write token, or null when dropped:
   * no tab (closed), or the tab's session is no longer `expectedSessionId`.
   */
  const post = (
    tab: NoticeTarget | undefined | null,
    expectedSessionId: string | undefined,
    input: TabNoticeInput,
  ): number | null => {
    if (!tab) return null;
    if (tab.sessionId !== expectedSessionId) return null;
    syncSession(tab.id, tab.sessionId);
    const token = ++seq;
    const notice: TabNotice = { ...input, tabId: tab.id, token, sessionId: tab.sessionId };
    const cur = lists.get(tab.id) ?? EMPTY;
    let next = cur.some((n) => n.id === input.id)
      ? cur.map((n) => (n.id === input.id ? notice : n))
      : [...cur, notice];
    while (next.length > MAX_NOTICES_PER_TAB) {
      clearTimer(tab.id, next[0].id);
      next = next.slice(1);
    }
    clearTimer(tab.id, input.id);
    if (input.autoDismissMs && input.autoDismissMs > 0) {
      const tabId = tab.id;
      const h = timers.setTimeout(() => dismiss(tabId, input.id, token), input.autoDismissMs);
      let byId = handles.get(tabId);
      if (!byId) handles.set(tabId, (byId = new Map()));
      byId.set(input.id, h);
    }
    publish(tab.id, next);
    return token;
  };

  /** Drop every notice and timer of a closed tab. */
  const clearTab = (tabId: string): void => {
    const byId = handles.get(tabId);
    byId?.forEach((h) => timers.clearTimeout(h));
    handles.delete(tabId);
    if (lists.delete(tabId)) emit(tabId);
  };

  const getSnapshot = (tabId: string): readonly TabNotice[] => lists.get(tabId) ?? EMPTY;

  const subscribe = (tabId: string, listener: () => void): (() => void) => {
    let set = listeners.get(tabId);
    if (!set) listeners.set(tabId, (set = new Set()));
    set.add(listener);
    return () => {
      set!.delete(listener);
      if (set!.size === 0 && listeners.get(tabId) === set) listeners.delete(tabId);
    };
  };

  /** Pending auto-dismiss timers (test/diagnostic only). */
  const timerCount = (): number => {
    let n = 0;
    handles.forEach((m) => { n += m.size; });
    return n;
  };

  return { post, dismiss, syncSession, renameSession, clearTab, getSnapshot, subscribe, timerCount };
}

export type TabNoticeStore = ReturnType<typeof createTabNoticeStore>;

/** App singleton. Keyed by tabId; entries are removed by `clearTab` on tab close. */
export const tabNoticeStore: TabNoticeStore = createTabNoticeStore();

/** Subscribe a component to one tab's notice list (all sessions; filter with
 *  `visibleNotices`). */
export function useTabNotices(tabId: string | null | undefined): readonly TabNotice[] {
  return useSyncExternalStore(
    (l) => (tabId ? tabNoticeStore.subscribe(tabId, l) : () => {}),
    () => (tabId ? tabNoticeStore.getSnapshot(tabId) : EMPTY),
  );
}
