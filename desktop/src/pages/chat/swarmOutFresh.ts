/**
 * startSwarmOutFresh — give the Swarm Out tab a new session, at the person's press
 * only (components/swarm-out/SwarmOutNewSession). The old session stays in history;
 * a notice on the tab offers to open it in an ordinary tab (the Swarm Out tab only
 * ever holds the current session).
 *
 * Nothing happens while the tab is replying or a question or confirmation waits on
 * the person (`block`): a new session would cut a dispatch off halfway.
 */
import { newSessionBlock, type NewSessionBlock } from '../../components/swarm-out/SwarmOutNewSession';
import { tabNoticeStore, type NoticeTarget } from '../../stores/tabNoticeStore';
import { SWARM_OUT_TAB_ID } from './swarmOut';

export interface FreshTab extends NoticeTarget {
  isStreaming?: boolean;
  pendingQuestion?: unknown;
  pendingPermissionRequestId?: string | null;
  _serverQueue?: readonly unknown[];
  _queuedLocal?: { size: number };
}

export interface FreshDeps {
  getTab: () => FreshTab | undefined;
  /** what the active tab's React state says (null when Swarm Out is not active) */
  liveBlock: NewSessionBlock;
  reset: (tabId: string) => void;
  /** the new session has used no context yet */
  clearContext: () => void;
  openOld: (sessionId: string) => void;
  t: (key: string) => string;
}

/** Returns whether the session was changed. */
export function startSwarmOutFresh(d: FreshDeps): boolean {
  const tab = d.getTab();
  if (!tab || newSessionBlock(tab) || d.liveBlock) return false;
  const oldSid = tab.sessionId;
  d.reset(SWARM_OUT_TAB_ID);
  d.clearContext();
  const fresh = d.getTab();
  if (oldSid && fresh) {
    tabNoticeStore.post(fresh, fresh.sessionId, {
      id: 'swarm-out-fresh', severity: 'info', message: d.t('swarmOut.fresh.done'), autoDismissMs: 15000,
      action: { label: d.t('swarmOut.fresh.openOld'), run: () => d.openOld(oldSid) },
    });
  }
  return true;
}


/** A long session (the context-usage warning level, backend
 *  PromptBuilder._CONTEXT_WARN_PCT): one notice per session saying why a new one
 *  loses nothing. ``noticed`` holds the sessions already told. Returns whether it
 *  posted. */
export function noticeLongSession(tab: NoticeTarget | undefined, level: string | null | undefined,
  pct: number | null | undefined, noticed: Set<string>, t: (k: string, o?: Record<string, unknown>) => string): boolean {
  const sid = tab?.sessionId;
  if (!tab || !sid || (level !== 'warn' && level !== 'critical') || noticed.has(sid)) return false;
  noticed.add(sid);
  tabNoticeStore.post(tab, sid, {
    id: 'swarm-out-long', severity: 'info', message: t('swarmOut.fresh.long', { pct: pct ?? 0 }),
    action: { label: t('swarmOut.fresh.gotIt'), run: () => {} },
  });
  return true;
}
