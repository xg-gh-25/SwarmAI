/**
 * resetTabToNewSession: "Start fresh" resets the NAMED tab, never the active one.
 *
 * Race pinned: the notice belongs to background tab B while A is active.
 * Resetting B must leave A's store, session and notices untouched, and must
 * report isActive=false so the caller does not clear the active React mirror.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { resetTabToNewSession } from '../resetTab';
import { messageStoreRegistry } from '../../../stores/MessageStore';
import { tabNoticeStore } from '../../../stores/tabNoticeStore';
import type { UnifiedTab } from '../../../hooks/useUnifiedTabState';

function tab(id: string, sessionId: string): UnifiedTab {
  return { id, sessionId, messages: [], pendingQuestion: null } as unknown as UnifiedTab;
}

const msg = (id: string) => ({ id, role: 'user' as const, content: [{ type: 'text' as const, text: id }], timestamp: '' });

describe('resetTabToNewSession', () => {
  let map: Map<string, UnifiedTab>;
  const update = (id: string, patch: Partial<UnifiedTab>) => { Object.assign(map.get(id)!, patch); };

  beforeEach(() => {
    messageStoreRegistry.clear();
    ['A', 'B'].forEach((id) => tabNoticeStore.clearTab(id));
    map = new Map([['A', tab('A', 'sA')], ['B', tab('B', 'sB')]]);
    messageStoreRegistry.getOrCreate('A').replace([msg('a1')]);
    messageStoreRegistry.getOrCreate('B').replace([msg('b1')]);
    tabNoticeStore.post(map.get('A'), 'sA', { id: 'n', severity: 'info', message: 'A' });
    tabNoticeStore.post(map.get('B'), 'sB', { id: 'n', severity: 'warning', message: 'B' });
  });

  it('resets the background tab, leaves the active tab alone', () => {
    const r = resetTabToNewSession(map, 'B', 'A', update);
    expect(r).toEqual({ reset: true, isActive: false });
    expect(map.get('B')!.sessionId).toBeUndefined();
    expect(messageStoreRegistry.get('B')!.messages).toHaveLength(0);
    expect(tabNoticeStore.getSnapshot('B')).toHaveLength(0);
    expect(map.get('A')!.sessionId).toBe('sA');
    expect(messageStoreRegistry.get('A')!.messages).toHaveLength(1);
    expect(tabNoticeStore.getSnapshot('A')).toHaveLength(1);
  });

  it('reports isActive when the named tab is the active one', () => {
    expect(resetTabToNewSession(map, 'A', 'A', update).isActive).toBe(true);
  });

  it('a send still reading its attachments does not survive the reset', () => {
    Object.assign(map.get('B')!, { _sendToken: 1, _preparingSend: 2 });
    resetTabToNewSession(map, 'B', 'A', update);
    expect(map.get('B')!._sendToken).toBeUndefined();
    expect(map.get('B')!._preparingSend).toBeUndefined();
  });

  it('does nothing for a closed tab', () => {
    expect(resetTabToNewSession(map, 'gone', 'A', update)).toEqual({ reset: false, isActive: false });
    expect(map.get('A')!.sessionId).toBe('sA');
  });
});
