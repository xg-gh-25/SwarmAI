/** SMOKE: the REAL submit write-path (handleAnswerQuestion's updateLast), not pre-baked answers.
 *  Exercises: streaming-append block (no answers) → user submits → updateLast writes answers
 *  onto the matching block by toolUseId → reconcile with answer-less DB row preserves it.
 *  (run_b549e8ca AC3 write-path + AC5 reconcile) */
import { describe, it, expect } from 'vitest';
import { MessageStore } from '../MessageStore';
import type { Message, ChatMessage } from '../../types';

describe('SMOKE: AskUserQuestion answered-state real submit path', () => {
  it('updateLast writes answers onto block by toolUseId, then survives reconcile', () => {
    const store = new MessageStore();
    // 1. streaming appended an ask_user_question block — NO answers yet (real initial state)
    const streamed: Message = {
      id: 'm1', role: 'assistant', timestamp: new Date().toISOString(),
      content: [
        { type: 'ask_user_question', toolUseId: 'tu_42',
          questions: [{ question: 'Pick one', header: 'Q', multiSelect: false,
            options: [{ label: 'A', description: '' }, { label: 'B', description: '' }] }] } as any,
      ],
    };
    store.append(streamed);

    // 2. user submits — mirror handleAnswerQuestion: scan content[] by toolUseId, write answers
    store.updateLast(
      (m: Message) => ({ ...m, content: m.content.map((b: any) =>
        b.type === 'ask_user_question' && b.toolUseId === 'tu_42'
          ? { ...b, answers: { 'Pick one': 'A' } } : b) }),
      (m: Message) => m.content?.some((b: any) =>
        b.type === 'ask_user_question' && b.toolUseId === 'tu_42'),
    );
    const blk = (store.messages.at(-1)!.content as any[]).find(b => b.type === 'ask_user_question');
    expect(blk.answers).toEqual({ 'Pick one': 'A' });

    // 3. reconcile with DB row lacking the block → local answers must persist (carry-forward)
    const dbRow: ChatMessage = {
      id: 'm1', sessionId: 's1', role: 'assistant', createdAt: new Date().toISOString(),
      content: [] as any,
    };
    store.reconcile([dbRow]);
    const rblk = (store.messages.at(-1)!.content as any[]).find(b => b.type === 'ask_user_question');
    expect(rblk).toBeTruthy();
    expect(rblk.answers).toEqual({ 'Pick one': 'A' });
  });
});
