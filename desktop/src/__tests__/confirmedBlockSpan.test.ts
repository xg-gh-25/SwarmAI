/**
 * A repeated start of the same streamed block continues it
 * (docs/design/2026-10-24-bg-agent-stop-livereload.md D4).
 *
 * What is tested (the real applyTextDelta / applyThinkingDelta / updateMessages):
 * - a second fresh start of the SAME streamed block (same backend `block` key) keeps
 *   one provisional block, so its confirmation leaves nothing behind. Before the fix
 *   it opened a second provisional block; the confirmation replaced one and left the
 *   other, so the tail of the answer showed twice live only (session 6a9d322a,
 *   2026-10-24; the record held it once);
 * - a start of ANOTHER block (another key, e.g. the next model message's index 0) is
 *   a new block, even when its text happens to continue the earlier one;
 * - text the model really wrote twice (two blocks) stays twice;
 * - no key (an older backend): every fresh start is a new block, as before.
 * Identity comes from the backend's key, never from comparing text.
 */
import { describe, it, expect } from 'vitest';
import { applyTextDelta, applyThinkingDelta, updateMessages } from '../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../types';

const msg = (): Message => ({ id: 'a', role: 'assistant', content: [], timestamp: 't' } as Message);
const texts = (m: Message, type = 'text') => m.content
  .filter((b) => b.type === type)
  .map((b) => (type === 'text' ? (b as { text: string }).text : (b as { thinking: string }).thinking));
const confirm = (m: Message, blocks: ContentBlock[]) =>
  updateMessages([m], 'a', blocks)[0];

const F = 'Head of the answer. 之后，P5 没覆盖到的 prompt injection。最后一段。';
const cut = F.indexOf('后，P5');

describe('one streamed block, one provisional block', () => {
  it('a repeated start of the same block continues it; the confirmation leaves nothing', () => {
    let m = applyTextDelta(msg(), F.slice(0, cut), true, '1:0');
    m = applyTextDelta(m, F.slice(cut), true, '1:0'); // the start re-delivered mid-block
    expect(texts(m)).toEqual([F]);
    expect(texts(confirm(m, [{ type: 'text', text: F }] as ContentBlock[]))).toEqual([F]);
  });

  it('thinking too', () => {
    let m = applyThinkingDelta(msg(), 'think one ', true, '1:0');
    m = applyThinkingDelta(m, 'think two', true, '1:0');
    expect(texts(m, 'thinking')).toEqual(['think one think two']);
  });

  it('another block is a new block even when its text continues the earlier one', () => {
    let m = applyTextDelta(msg(), 'A', true, '1:0');
    m = applyTextDelta(m, 'A', true, '2:0'); // the next message's index 0
    expect(texts(m)).toEqual(['A', 'A']);
    // the first message's confirmation takes its own block only
    expect(texts(confirm(m, [{ type: 'text', text: 'A' }] as ContentBlock[]))).toEqual(['A', 'A']);
  });

  it('text the model wrote twice stays twice', () => {
    let m = applyTextDelta(msg(), 'Same words.', true, '1:0');
    m = confirm(m, [{ type: 'text', text: 'Same words.' }] as ContentBlock[]);
    m = applyTextDelta(m, 'Same words.', true, '2:0');
    m = confirm(m, [{ type: 'text', text: 'Same words.' }] as ContentBlock[]);
    expect(texts(m)).toEqual(['Same words.', 'Same words.']);
  });

  it('a retry into the same bubble (another run, its count restarted) is a new block', () => {
    let m = applyTextDelta(msg(), 'first attempt, cut off', true, 'r1.1:0');
    m = applyTextDelta(m, 'second attempt', true, 'r2.1:0');
    expect(texts(m)).toEqual(['first attempt, cut off', 'second attempt']);
  });

  it('no key (an older backend): a fresh start is a new block, as before', () => {
    let m = applyTextDelta(msg(), 'one', true);
    m = applyTextDelta(m, 'two', true);
    expect(texts(m)).toEqual(['one', 'two']);
  });
});
