/**
 * updateMessages(turnStart): in a bubble shared by several turns, a turn's merge
 * only sees its own blocks. A later turn that repeats an earlier turn's text or
 * thought keeps both (the reload does), while a re-emission inside the turn is
 * still de-duplicated.
 */
import { describe, it, expect } from 'vitest';
import { updateMessages } from '../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../types';

const msg = (content: ContentBlock[]): Message =>
  ({ id: 'a', role: 'assistant', content, timestamp: '' } as Message);
const T = (text: string, confirmed = true) => ({ type: 'text', text, ...(confirmed ? { _confirmed: true } : {}) }) as ContentBlock;
const texts = (m: Message) => m.content.map((b) => (b as { text?: string }).text);

describe('updateMessages turnStart', () => {
  const said = 'the same long sentence twice over';

  it('an earlier turn\'s identical text is kept, not taken as a re-emission', () => {
    const prev = msg([T(said)]);
    const [out] = updateMessages([prev], 'a', [T(said, false)], undefined, 1);
    expect(texts(out)).toEqual([said, said]);
  });

  it('the earlier turn\'s text is not replaced when the turn streamed the same text', () => {
    const prev = msg([T(said), T(said, false)]);      // earlier turn + this turn's streamed copy
    const [out] = updateMessages([prev], 'a', [T(said, false)], undefined, 1);
    expect(texts(out)).toEqual([said, said]);
    expect((out.content[1] as { _confirmed?: boolean })._confirmed).toBe(true);
  });

  it('without turnStart (one turn) a text written again is kept (R6: the record has both)', () => {
    const prev = msg([T(said)]);
    const [out] = updateMessages([prev], 'a', [T(said, false)]);
    expect(texts(out)).toEqual([said, said]);
  });
});
