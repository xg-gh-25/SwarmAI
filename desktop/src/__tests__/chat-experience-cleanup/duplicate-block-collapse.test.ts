/**
 * Duplicated thought / paragraph in a streamed reply (2026-10-20).
 *
 * The same thinking block rendered twice in a row while the DB held one copy, and the
 * repeat never went away. Tested here:
 * - collapseAdjacentDuplicateBlocks drops a block equal to the one before it (text and
 *   thinking, >= 20 chars), keeps short repeats and non-adjacent repeats;
 * - updateMessages collapses a doubled provisional thought when it is confirmed;
 * - MessageStore's DB merge lets the DB's single copy replace a doubled local copy
 *   (the "more-complete wins" guard used to keep the double because it was longer).
 */
import { describe, it, expect, vi } from 'vitest';
import { collapseAdjacentDuplicateBlocks } from '../../utils/duplicateBlocks';
import { updateMessages } from '../../hooks/useChatStreamingLifecycle';
import { MessageStore } from '../../stores/MessageStore';
import type { ContentBlock, Message, ChatMessage } from '../../types';

const TH = "The load average is at 20, so the machine's overloaded from other sessions.\n\n";
const th = (t: string, confirmed = false) =>
  ({ type: 'thinking', thinking: t, ...(confirmed ? { _confirmed: true } : {}) }) as ContentBlock;
const tx = (t: string) => ({ type: 'text', text: t }) as ContentBlock;

describe('collapseAdjacentDuplicateBlocks', () => {
  it('drops an adjacent repeat of a thought and of a paragraph', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const P = 'This paragraph is long enough to count as a duplicate.';
    const out = collapseAdjacentDuplicateBlocks([th(TH), th(TH), tx(P), tx(P)], 't');
    expect(out).toEqual([th(TH), tx(P)]);
  });

  it('keeps short repeats, non-adjacent repeats and different types', () => {
    const same = [tx('Done.'), tx('Done.'), th(TH), tx('x'.repeat(30)), th(TH)];
    expect(collapseAdjacentDuplicateBlocks(same, 't')).toBe(same);
  });

  it('keeps the confirmed copy', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = collapseAdjacentDuplicateBlocks([th(TH), th(TH, true)], 't');
    expect(out).toEqual([th(TH, true)]);
  });
});

describe('updateMessages', () => {
  it('confirms a doubled provisional thought down to one block', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const msg: Message = { id: 'a', role: 'assistant', content: [th(TH), th(TH)], timestamp: '' };
    const [next] = updateMessages([msg], 'a', [th(TH)]);
    expect(next.content.filter((b) => b.type === 'thinking')).toHaveLength(1);
  });
});

describe('MessageStore DB merge', () => {
  it('lets the DB single copy replace a doubled local copy', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const local: Message = {
      id: 'local-1-asst', role: 'assistant', content: [th(TH, true), th(TH, true)], timestamp: '',
      metadata: { client_id: 'local-1-asst' },
    } as Message;
    const db: ChatMessage = {
      id: 'db-1', role: 'assistant', content: [th(TH)], createdAt: '2026-10-20T00:00:00Z',
      metadata: { client_id: 'local-1-asst' },
    } as unknown as ChatMessage;
    const store = new MessageStore({ sessionId: 's', fetchMessages: async () => [db] } as never);
    store.replace([local]);
    store.reconcile([db]);
    const content = store.messages[0].content;
    expect(content.filter((b) => b.type === 'thinking')).toHaveLength(1);
  });
});
