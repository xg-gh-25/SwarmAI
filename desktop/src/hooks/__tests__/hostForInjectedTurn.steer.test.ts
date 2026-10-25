/**
 * hostForInjectedTurn × steer (chat-steer frontend §8, T-host): a CLI-initiated
 * turn or a continuation renders into the assistant bubble before it. A Waiting
 * steer or a queued message at the bottom is not sent yet: the turn still goes
 * into the bubble above it, not into a new one under it.
 */
import { describe, expect, it } from 'vitest';
import { firstSteerOfTurn, hostForInjectedTurn } from '../streaming-guards';
import type { Message } from '../../types';

const a = (id: string): Message => ({ id, role: 'assistant', content: [], timestamp: '' });
const u = (id: string, over: Partial<Message> = {}): Message => ({ id, role: 'user', content: [], timestamp: '', ...over });

describe('hostForInjectedTurn skips bubbles not sent yet', () => {
  it('past a Waiting steer and a queued message', () => {
    expect(hostForInjectedTurn([u('q'), a('A'), u('S', { steerWaiting: true })], 'T', undefined)?.id).toBe('A');
    expect(hostForInjectedTurn([u('q'), a('A'), u('S', { steerWaiting: true }), u('Q', { isQueued: true })], 'T', undefined)?.id).toBe('A');
  });

  it('a sent user message still starts a new bubble; a read steer is a sent one', () => {
    expect(hostForInjectedTurn([u('q'), a('A'), u('q2')], 'T', undefined)).toBeNull();
    expect(hostForInjectedTurn([u('q'), a('A'), u('S', { steerWaiting: true, metadata: { steered: 'during' } })], 'T', undefined)).toBeNull();
  });
});

describe('firstSteerOfTurn: where a subscription turn puts its first bubble', () => {
  it('after the turn\'s own prompt: its first read or unsent steer', () => {
    expect(firstSteerOfTurn([u('p'), a('A'), u('S', { metadata: { steered: 'during' } }), a('B')], 'p-asst')).toBe('S');
  });

  it('a turn whose prompt is not shown never goes above a read steer of an earlier turn', () => {
    expect(firstSteerOfTurn([u('p'), a('A'), u('S', { metadata: { steered: 'during' } })], 'injected-1-asst')).toBeNull();
    expect(firstSteerOfTurn([u('p'), a('A'), u('S', { metadata: { steered: 'during' } }), u('Q', { isQueued: true })],
      'injected-1-asst')).toBe('Q');
  });
});
