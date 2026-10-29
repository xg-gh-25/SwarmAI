/**
 * openSession tests — the opener a Hive run card uses inside an overlay closes the
 * overlay before it opens the Hive tab (the tab is under the overlay), and there is no
 * opener when ChatPage has not published one; the overlay bridge carries ChatPage's.
 */
import { describe, it, expect, vi } from 'vitest';
import { overlaySessionOpener } from './openSession';
import { getOverlayCtxBridge, setOverlayCtxBridge } from '../layout/overlayRegistry';

describe('overlaySessionOpener', () => {
  it('closes the overlay, then opens the session', () => {
    const order: string[] = [];
    const opener = overlaySessionOpener(() => order.push('close'), (sid, title) => order.push(`open ${sid} ${title}`));
    opener!('sess_1', 'Fix · hive-a');
    expect(order).toEqual(['close', 'open sess_1 Fix · hive-a']);
  });

  it('is null without ChatPage\'s opener', () => {
    expect(overlaySessionOpener(() => {}, undefined)).toBeNull();
  });

  it('the overlay bridge carries ChatPage\'s opener to the surfaces', () => {
    const open = vi.fn();
    setOverlayCtxBridge({ openHiveSession: open });
    expect(getOverlayCtxBridge().openHiveSession).toBe(open);
    setOverlayCtxBridge({});
  });
});
