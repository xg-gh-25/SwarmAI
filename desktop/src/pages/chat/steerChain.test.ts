/**
 * steerChain — how the segments of a steered turn are shown (chat-steer frontend
 * §7). Pure, over the message list: the same rule live and after a reload.
 *
 * - `continued`: the next non-system message is a read steer (metadata.steered):
 *   this segment is not the turn's end.
 * - `afterSteer`: the previous non-system message is a read steer: no header.
 * - `headerLive`: the first segment of a chain whose last segment streams keeps
 *   the header pulse.
 * - only a read steer splits: a Waiting / Queued bubble does not.
 */
import { describe, it, expect } from 'vitest';
import { steerChain } from './steerChain';
import type { Message } from '../../types';

const a = (id: string): Message => ({ id, role: 'assistant', content: [{ type: 'text', text: id }], timestamp: '' });
/** a bubble the turn has not written into */
const empty = (id: string): Message => ({ id, role: 'assistant', content: [], timestamp: '' });
const u = (id: string, over: Partial<Message> = {}): Message =>
  ({ id, role: 'user', content: [], timestamp: '', metadata: { client_id: id }, ...over });
const steer = (id: string, how: 'during' | 'after' = 'during') => u(id, { metadata: { client_id: id, steered: how } });
const sys = (id: string): Message => ({ id, role: 'system', content: [], timestamp: '' });
const flags = (ms: Message[], streamingId?: string) =>
  Object.fromEntries([...steerChain(ms, streamingId).entries()].map(([k, v]) => [k, v]));

describe('steerChain', () => {
  it('a segment followed by a read steer is continued; the one after it has no header', () => {
    const f = flags([u('q'), a('A'), steer('S1'), a('B')], 'B');
    expect(f.A).toEqual({ continued: true, afterSteer: false, headerLive: true });
    expect(f.B).toEqual({ continued: false, afterSteer: true, headerLive: false });
  });

  it('nothing streams: no pulse', () => {
    expect(flags([u('q'), a('A'), steer('S1'), a('B')]).A.headerLive).toBe(false);
  });

  it('a Waiting or Queued bubble after a segment does not continue it', () => {
    expect(flags([u('q'), a('A'), u('S2', { steerWaiting: true })], 'A').A?.continued ?? false).toBe(false);
    const f = flags([u('q'), a('A'), steer('S1'), a('B'), u('Q', { isQueued: true })], 'B');
    expect(f.B.continued).toBe(false);
    expect(f.A.headerLive).toBe(true);
  });

  it('two steers in a row; system rows in between are skipped', () => {
    const f = flags([u('q'), a('A'), sys('x'), steer('S1'), steer('S2', 'after'), a('B'), steer('S3'), a('C')], 'C');
    expect(f.A).toEqual({ continued: true, afterSteer: false, headerLive: true });
    expect(f.B).toEqual({ continued: true, afterSteer: true, headerLive: false });
    expect(f.C).toEqual({ continued: false, afterSteer: true, headerLive: false });
  });

  it('T10f: the reloaded history (DB ids, the same metadata) gets the same flags', () => {
    const live = flags([u('q'), a('local-q-asst'), steer('local-s'), a('local-s-asst')]);
    const reload = flags([u('row1'), a('row2'), steer('row3'), a('row4')]);
    expect(Object.values(reload)).toEqual(Object.values(live));
  });

  it('a plain turn: nothing flagged', () => {
    expect(flags([u('q'), a('A'), u('q2'), a('B')], 'B')).toEqual({});
  });

  it('the turn\'s first bubble, empty when the steer was read: not the chain\'s head; live == reload', () => {
    const live = flags([u('q0'), a('A0'), u('q'), empty('A'), steer('S1'), a('B')]);
    const reload = flags([u('q0'), a('A0'), u('q'), steer('S1'), a('B')]);
    expect(live.A).toEqual({ continued: true, afterSteer: false, headerLive: false });
    expect(live.B).toEqual(reload.B);
    expect(reload.B).toBeUndefined();          // B draws the turn's header
  });

  it('a new turn starts a new chain: an earlier turn\'s bubble is not its head', () => {
    expect(flags([u('q0'), a('A0'), u('q'), steer('S1'), a('B')]).B).toBeUndefined();
  });
});
