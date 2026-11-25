/**
 * isSendKey: Cmd/Ctrl+Enter sends once per press; a key-repeat event never sends (holding
 * the keys after a send would otherwise send the next item's draft as well).
 */
import { describe, it, expect } from 'vitest';
import { isSendKey } from './ReplyLane';

const k = (o: Partial<{ metaKey: boolean; ctrlKey: boolean; key: string; repeat: boolean }>) =>
  ({ metaKey: false, ctrlKey: false, key: 'Enter', repeat: false, ...o });

describe('isSendKey', () => {
  it('sends on Cmd+Enter and Ctrl+Enter', () => {
    expect(isSendKey(k({ metaKey: true }))).toBe(true);
    expect(isSendKey(k({ ctrlKey: true }))).toBe(true);
  });
  it('does not send on a repeated keydown', () => {
    expect(isSendKey(k({ metaKey: true, repeat: true }))).toBe(false);
    expect(isSendKey(k({ ctrlKey: true, repeat: true }))).toBe(false);
  });
  it('does not send on Enter alone or another key', () => {
    expect(isSendKey(k({}))).toBe(false);
    expect(isSendKey(k({ metaKey: true, key: 'j' }))).toBe(false);
  });
});
