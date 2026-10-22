/**
 * In-tab notices: the bar above the chat input and the tab-header dot.
 *
 * - `TabNoticeBar` — renders the ACTIVE tab's notices (passed in by tabId and
 *   the tab's live sessionId from tabMapRef). Notices of another session are
 *   never shown; an action re-reads the tab at click time and only runs when
 *   the tab still exists and is still on the notice's session.
 * - `TabNoticeDot` — a header dot for a background tab with a warning/error
 *   notice, so a background problem is visible without a top-right toast.
 *   Read-only (the bar and `post` do the session sync from the live tab).
 */
import { useEffect, useMemo } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import {
  tabNoticeStore, useTabNotices, visibleNotices, noticeDotSeverity,
  type TabNotice, type TabNoticeSeverity, type NoticeTarget,
} from '../../../stores/tabNoticeStore';
import { tf } from '../tf';

const ICON: Record<TabNoticeSeverity, string> = {
  info: 'info',
  success: 'check_circle',
  warning: 'warning',
  error: 'error',
};

const TONE: Record<TabNoticeSeverity, string> = {
  info: 'text-[var(--color-text-muted)]',
  success: 'text-[var(--color-success,#22c55e)]',
  warning: 'text-[var(--color-warning,#f59e0b)]',
  error: 'text-[var(--color-error,#ef4444)]',
};

/** Keep the store's view of a tab in step with the tab's current sessionId.
 *  Pruning drops notices for good, so it reads the LIVE tab inside the effect,
 *  never a value captured during render. */
function useSessionSync(
  tabId: string | null | undefined,
  sessionId: string | undefined,
  getTab: (tabId: string) => NoticeTarget | undefined,
) {
  useEffect(() => {
    if (!tabId) return;
    const live = getTab(tabId);
    if (live) tabNoticeStore.syncSession(tabId, live.sessionId);
  }, [tabId, sessionId, getTab]);
}

export interface TabNoticeBarProps {
  /** The tab whose notices to show (the active tab). */
  tabId: string | null;
  /** Live tab lookup (tabMapRef). Read at render for the tab's current
   *  sessionId and again at click time to re-check tab + session. */
  getTab: (tabId: string) => NoticeTarget | undefined;
}

export function TabNoticeBar({ tabId, getTab }: TabNoticeBarProps) {
  const { t } = useTranslation();
  const all = useTabNotices(tabId);
  // Read the session from the live tab on every render (a store change
  // re-renders this bar), never from a React mirror that can lag a switch.
  const sessionId = tabId ? getTab(tabId)?.sessionId : undefined;
  useSessionSync(tabId, sessionId, getTab);
  const notices = useMemo(() => visibleNotices(all, sessionId), [all, sessionId]);
  if (!tabId || notices.length === 0) return null;

  const runAction = (n: TabNotice) => {
    tabNoticeStore.dismiss(n.tabId, n.id, n.token);
    const live = getTab(n.tabId);
    // Tab closed, or moved to another session since the notice was written:
    // the action no longer applies to anything — dismiss only.
    if (!live) return;
    if (n.sessionId !== undefined && live.sessionId !== n.sessionId) return;
    n.action?.run(n.tabId, live.sessionId);
  };

  return (
    <div className="chat-gutter pt-1" data-testid="tab-notice-bar">
      <div className="chat-column flex flex-col gap-1">
        {notices.map((n) => (
          <div
            key={n.id}
            role={n.severity === 'error' ? 'alert' : 'status'}
            data-notice-id={n.id}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs border border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-text)]"
          >
            <span className={clsx('material-symbols-outlined text-[14px] flex-shrink-0', TONE[n.severity])}>
              {ICON[n.severity]}
            </span>
            <span className="flex-1 min-w-0">{n.message}</span>
            {n.action && (
              <button
                type="button"
                onClick={() => runAction(n)}
                className="flex-shrink-0 px-2 py-0.5 rounded text-[var(--color-primary)] hover:bg-[var(--color-hover)]"
              >
                {n.action.label}
              </button>
            )}
            <button
              type="button"
              onClick={() => tabNoticeStore.dismiss(n.tabId, n.id, n.token)}
              aria-label={tf(t, 'chat.notice.dismiss', 'Dismiss')}
              className="flex-shrink-0 p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]"
            >
              <span className="material-symbols-outlined text-[14px]">close</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export interface TabNoticeDotProps {
  tabId: string;
  sessionId: string | undefined;
  isActive: boolean;
}

/** Header dot for a background tab that has a warning/error notice. Read-only:
 *  `tab.sessionId` here is a derived React value that can lag, so the dot only
 *  filters with it and never prunes the store. */
export function TabNoticeDot({ tabId, sessionId, isActive }: TabNoticeDotProps) {
  const { t } = useTranslation();
  const all = useTabNotices(tabId);
  const level = useMemo(() => noticeDotSeverity(visibleNotices(all, sessionId)), [all, sessionId]);
  if (isActive || !level) return null;
  return (
    <span
      data-testid="tab-notice-dot"
      className={clsx(
        'w-1.5 h-1.5 rounded-full flex-shrink-0 inline-block',
        level === 'error' ? 'bg-[var(--color-error,#ef4444)]' : 'bg-[var(--color-warning,#f59e0b)]',
      )}
      role="img"
      aria-label={level === 'error'
        ? tf(t, 'chat.notice.dotError', 'This tab has an error')
        : tf(t, 'chat.notice.dotWarning', 'This tab has a warning')}
    />
  );
}
