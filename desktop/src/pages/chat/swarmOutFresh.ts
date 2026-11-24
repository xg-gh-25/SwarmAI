/**
 * startSwarmOutFresh — give the Swarm Out tab a new session, at the person's press
 * only (components/swarm-out/SwarmOutNewSession). The old session stays in history;
 * a notice on the tab offers to open it in an ordinary tab (the Swarm Out tab only
 * ever holds the current session). The runs the old session answered for move to the
 * new one: `handOverLater` keeps the old id, and once the tab's new session has its id
 * (`planSwarmOutHandover`) the daemon hands them over (POST sessions/hand-over), so a
 * new session loses nothing.
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
  /** keep the old session's id: its runs move to the new session once that has an id */
  handOverLater: (oldSessionId: string) => void;
  t: (key: string) => string;
}

/** Returns whether the session was changed. */
export function startSwarmOutFresh(d: FreshDeps): boolean {
  const tab = d.getTab();
  if (!tab || newSessionBlock(tab) || d.liveBlock) return false;
  const oldSid = tab.sessionId;
  d.reset(SWARM_OUT_TAB_ID);
  d.clearContext();
  if (oldSid) d.handOverLater(oldSid);
  const fresh = d.getTab();
  if (oldSid && fresh) {
    tabNoticeStore.post(fresh, fresh.sessionId, {
      id: 'swarm-out-fresh', severity: 'info', message: d.t('swarmOut.fresh.done'), autoDismissMs: 15000,
      action: { label: d.t('swarmOut.fresh.openOld'), run: () => d.openOld(oldSid) },
    });
  }
  return true;
}


/** The hand-over due now, when the Swarm Out tab's new session just got its id: from the
 *  session kept at New session (`pending`) to the one bound (`boundSessionId`). Null for
 *  any other tab, nothing pending, or the same session. */
export function planSwarmOutHandover(pending: string | null, tabId: string, boundSessionId: string):
  { from: string; to: string } | null {
  if (tabId !== SWARM_OUT_TAB_ID || !pending || !boundSessionId || pending === boundSessionId) return null;
  return { from: pending, to: boundSessionId };
}


/** Waits before each try of a hand-over: the new session's row may land a moment after
 *  its id reaches the tab. */
export const HANDOVER_TRIES_MS = [0, 3_000, 15_000];

/** Hand the runs over, trying again after HANDOVER_TRIES_MS when the daemon refuses or
 *  does not answer. Resolves to the dispatch ids moved, or null when every try failed. */
export async function handOverWithRetry(
  handOver: (from: string, to: string) => Promise<{ moved: string[] }>,
  from: string, to: string,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<string[] | null> {
  for (const ms of HANDOVER_TRIES_MS) {
    if (ms) await wait(ms);
    try {
      return (await handOver(from, to)).moved;
    } catch {
      // tried again below
    }
  }
  return null;
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
