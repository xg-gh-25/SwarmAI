/**
 * withStopReceipt — the /chat/stop receipt joined to the stopped message.
 *
 * What is tested: blocks are appended once; a message that already carries a
 * turn_stop (a DB reconcile landed first) and an empty receipt leave it as it is.
 */
import { describe, it, expect } from 'vitest';
import { withStopReceipt } from './stopReceipt';
import type { ContentBlock, Message } from '../../types';

const msg = (content: ContentBlock[]): Message =>
  ({ id: 'm1', role: 'assistant', content, timestamp: '2026-10-22T09:00:00Z' }) as Message;
const stop = { type: 'turn_stop', calls: [], agents: [] } as ContentBlock;

describe('withStopReceipt', () => {
  it('appends the receipt blocks', () => {
    const m = msg([{ type: 'text', text: 'go' } as ContentBlock]);
    expect(withStopReceipt(m, [stop]).content.map((b) => b.type)).toEqual(['text', 'turn_stop']);
  });
  it('is a no-op when the message already has a turn_stop or the receipt is empty', () => {
    const m = msg([stop]);
    expect(withStopReceipt(m, [stop])).toBe(m);
    const e = msg([]);
    expect(withStopReceipt(e, [])).toBe(e);
  });
});
