/**
 * resetTabToNewSession — reset ONE tab, named by id, to a fresh session.
 *
 * The recovery_exhausted "Start fresh session" action used to select the tab
 * and then clear "the active tab"; if the active tab changed in between it
 * cleared the wrong chat. This helper never resolves the active tab: it resets
 * the named tab's MessageStore and tabMapRef entry directly, and reports
 * whether that tab is the active one so the caller may sync its React mirror.
 *
 * @exports resetTabToNewSession
 */
import type { UnifiedTab } from '../../hooks/useUnifiedTabState';
import { messageStoreRegistry } from '../../stores/MessageStore';
import { tabNoticeStore } from '../../stores/tabNoticeStore';

export function resetTabToNewSession(
  tabMap: Map<string, UnifiedTab>,
  tabId: string,
  activeTabId: string | null,
  updateTabState: (tabId: string, patch: Partial<Omit<UnifiedTab, 'id' | 'isStreaming'>>) => void,
): { reset: boolean; isActive: boolean } {
  if (!tabMap.has(tabId)) return { reset: false, isActive: false };
  const store = messageStoreRegistry.get(tabId);
  if (store) {
    if (store.phase === 'streaming') store.endStreaming();
    store.replace([]);
  }
  updateTabState(tabId, { sessionId: undefined, messages: [], pendingQuestion: null,
    _unsentRequests: undefined, _openSendCid: undefined,
    _serverQueue: undefined, _serverQueueAt: undefined, _serverQueueSeq: undefined,
    _serverQueueComplete: undefined, _serverQueueSid: undefined, _queuedLocal: undefined,
    _steerLocal: undefined, _serverSteering: undefined, _serverQueuedReason: undefined, _turnSegments: undefined,
    _preparingSend: undefined, _sendToken: undefined });
  tabNoticeStore.syncSession(tabId, undefined);
  return { reset: true, isActive: activeTabId === tabId };
}
