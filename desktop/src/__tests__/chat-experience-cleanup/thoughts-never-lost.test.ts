/**
 * Finished thoughts and responses are never lost or moved while a turn streams
 * (XG 2026-10-18: "你不能把已经完成的 response or thoughts 给我搞丢").
 *
 * What is tested (the real per-message updaters the stream handler uses —
 * applyThinkingDelta / applyTextDelta / updateMessages — and the store reconcile):
 * - the full agentic sequence thinking → tool → result → thinking → tool →
 *   thinking → text keeps every block, in stream order
 * - an earlier thought whose own confirmation never arrived survives the next
 *   thought's confirmation (it used to be dropped with all provisional thinking)
 * - a `thinking_start` boundary keeps two back-to-back thoughts apart (their
 *   deltas used to merge, and the first confirmation then replaced both)
 * - a later thought that starts with an earlier one no longer deletes it
 * - a confirmation lands where its streamed block was, not after later tools
 * - the stream-end reconcile keeps local thoughts when the DB row lacks them
 */
import { describe, it, expect } from 'vitest';
import { applyTextDelta, applyThinkingDelta, updateMessages } from '../../hooks/useChatStreamingLifecycle';
import { MessageStore } from '../../stores/MessageStore';
import type { ContentBlock, Message } from '../../types';

const ID = 'm1';
const msg = (content: ContentBlock[] = []): Message =>
  ({ id: ID, role: 'assistant', content, timestamp: 'T' }) as Message;
const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const use = (id: string): ContentBlock => ({ type: 'tool_use', id, name: 'Bash', summary: 'Running: ls', category: 'bash' }) as ContentBlock;
const res = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false }) as ContentBlock;
const confirm = (m: Message, blocks: ContentBlock[]) => updateMessages([m], ID, blocks)[0];
/** Stream a thought token by token (fresh = a thinking_start opened it). */
function streamThought(m: Message, text: string, fresh = true): Message {
  let out = m;
  text.match(/.{1,7}/g)!.forEach((tok, i) => { out = applyThinkingDelta(out, tok, fresh && i === 0); });
  return out;
}
const shape = (m: Message) => m.content.map((b) => {
  if (b.type === 'thinking') return `th:${(b as { thinking: string }).thinking}`;
  if (b.type === 'text') return `tx:${(b as { text: string }).text}`;
  if (b.type === 'tool_use') return `use:${(b as { id: string }).id}`;
  return `res:${(b as { toolUseId: string }).toolUseId}`;
});

const TH1 = 'First I will read the router to see how sessions are keyed.';
const TH2 = 'The router keys by app session id; now check the unit.';
const TH3 = 'Both agree. Write the answer.';

describe('thoughts are never lost while a turn streams', () => {
  it('the full agentic sequence keeps every block in stream order', () => {
    let m = msg();
    m = streamThought(m, TH1);
    m = confirm(m, [th(TH1)]);
    m = confirm(m, [use('u1')]);
    m = confirm(m, [res('u1')]);
    m = streamThought(m, TH2);
    m = confirm(m, [th(TH2)]);
    m = confirm(m, [use('u2')]);
    m = confirm(m, [res('u2')]);
    m = streamThought(m, TH3);
    m = confirm(m, [th(TH3)]);
    m = applyTextDelta(m, 'Sessions are keyed ', true);
    m = applyTextDelta(m, 'by app id.');
    m = confirm(m, [tx('Sessions are keyed by app id.')]);
    expect(shape(m)).toEqual([`th:${TH1}`, 'use:u1', 'res:u1', `th:${TH2}`, 'use:u2', 'res:u2', `th:${TH3}`,
      'tx:Sessions are keyed by app id.']);
    expect(m.content.every((b) => b.type !== 'thinking' || (b as { _confirmed?: boolean })._confirmed)).toBe(true);
  });

  it('an earlier thought whose confirmation never came survives the next one', () => {
    let m = msg();
    m = streamThought(m, TH1);                 // its [Th1] event is lost
    m = confirm(m, [use('u1')]);
    m = confirm(m, [res('u1')]);
    m = streamThought(m, TH2);
    m = confirm(m, [th(TH2)]);
    expect(shape(m)).toEqual([`th:${TH1}`, 'use:u1', 'res:u1', `th:${TH2}`]);
  });

  it('a thinking_start keeps two back-to-back thoughts apart', () => {
    let m = msg();
    m = streamThought(m, TH1);
    m = streamThought(m, TH2, true);           // second block opened by thinking_start
    m = confirm(m, [th(TH1)]);
    m = confirm(m, [th(TH2)]);
    expect(shape(m)).toEqual([`th:${TH1}`, `th:${TH2}`]);
    // without the boundary the deltas merge into one block (why the flag exists)
    let merged = streamThought(msg(), TH1);
    merged = streamThought(merged, TH2, false);
    expect(merged.content).toHaveLength(1);
  });

  it('a later thought that starts with an earlier one does not delete it', () => {
    let m = confirm(msg(), [th(TH1)]);
    m = confirm(m, [use('u1')]);
    m = confirm(m, [th(`${TH1} And then the unit.`)]);   // no provisional: confirmation only
    expect(shape(m)).toEqual([`th:${TH1}`, 'use:u1', `th:${TH1} And then the unit.`]);
  });

  it('a confirmation lands where its streamed block was', () => {
    let m = applyTextDelta(msg(), 'Let me check the router first.', true);
    m = confirm(m, [use('u1')]);               // the tool arrives before the text's confirmation
    m = confirm(m, [tx('Let me check the router first.')]);
    expect(shape(m)).toEqual(['tx:Let me check the router first.', 'use:u1']);
  });

  it('the reconcile keeps local thoughts when the DB row lacks them', () => {
    const local = msg([th(TH1), use('u1'), tx('Answer.')]);
    const db = msg([use('u1'), tx('Answer.')]);
    const merge = (MessageStore as unknown as {
      _mergePreservingInteractive(d: Message, l: Message): Message;
    })._mergePreservingInteractive;
    expect(shape(merge(db, local))).toEqual([`th:${TH1}`, 'use:u1', 'tx:Answer.']);
  });
});
