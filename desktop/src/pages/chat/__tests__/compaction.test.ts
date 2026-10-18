/**
 * Compaction divider model (pages/chat/compaction.ts).
 *
 * What is tested:
 * - `toCompactionInfo`: ONE mapper for both routes — the stored snake_case
 *   record (live event or history metadata) → done; the live phase → running /
 *   failed; a legacy text-only notice → null.
 * - The live divider and the history row share `compact-<record id>`, so a
 *   real MessageStore reconcile keeps ONE divider (no duplicate after reload),
 *   in DB order, with the record intact.
 */
import { describe, it, expect } from 'vitest';
import { MessageStore } from '../../../stores/MessageStore';
import type { ChatMessage, Message } from '../../../types';
import { applyCompactionEvent, formatTokensK, toCompactionInfo } from '../compaction';

const REC = { id: 'r1', created_at: '2026-10-18T10:05:00', trigger: 'manual', pre_tokens: 750361, post_tokens: 12833, duration_ms: 54523, summary: '## Overview' };
const sys = (id: string, metadata?: Message['metadata']): Message =>
  ({ id, role: 'system', content: [{ type: 'text', text: 'n' }], timestamp: '2026-10-18T02:05:00Z', metadata });

describe('toCompactionInfo', () => {
  it('maps the stored record (one shape for SSE and history)', () => {
    expect(toCompactionInfo(sys('compact-r1', { compaction: REC }))).toEqual({
      phase: 'done', trigger: 'manual', preTokens: 750361, postTokens: 12833, durationMs: 54523, summary: '## Overview',
    });
  });
  it('maps live phases and ignores junk', () => {
    expect(toCompactionInfo(sys('compact-live-1', { compactPhase: 'running' }))?.phase).toBe('running');
    expect(toCompactionInfo(sys('compact-live-1', { compactPhase: 'failed' }))?.phase).toBe('failed');
    expect(toCompactionInfo(sys('compact-1'))).toBeNull();
    expect(toCompactionInfo(sys('compact-1', { compaction: { summary: 'no id' } }))).toBeNull();
    expect(toCompactionInfo(sys('compact-1', { compaction: { id: 'x', pre_tokens: 'NaN' } }))?.preTokens).toBeUndefined();
  });
  it('formats sizes', () => {
    expect(formatTokensK(750361)).toBe('750K');
  });
});

const user = (id: string): Message => ({ id, role: 'user', content: [{ type: 'text', text: 'q' }], timestamp: 't' });
const asst = (id: string, text = ''): Message =>
  ({ id, role: 'assistant', content: text ? [{ type: 'text', text }] : [], timestamp: 't' });
const ids = (s: MessageStore) => s.messages.map((m) => m.id);

describe('applyCompactionEvent placement and turn scope', () => {
  it('turn-start compaction goes before the empty placeholder; mid-turn goes after the content', () => {
    const s = new MessageStore();
    s.appendMany([user('u1'), asst('a1')]);
    applyCompactionEvent(s, { type: 'context_compacting', phase: 'started', message: 'Compacting' });
    expect(ids(s)[1].startsWith('compact-live-')).toBe(true);
    expect(ids(s)[2]).toBe('a1');
    const s2 = new MessageStore();
    s2.appendMany([user('u1'), asst('a1', 'working…')]);
    applyCompactionEvent(s2, { type: 'context_compacting', phase: 'started', message: 'Compacting' });
    expect(ids(s2).slice(0, 2)).toEqual(['u1', 'a1']);
    expect(ids(s2)[2].startsWith('compact-live-')).toBe(true);
  });

  it("an interrupted turn's divider is never reused by a later compaction", () => {
    const s = new MessageStore();
    s.appendMany([user('u1'), asst('a1', 'x')]);
    applyCompactionEvent(s, { type: 'context_compacting', phase: 'started', message: 'Compacting' });
    const stale = ids(s)[2];
    s.appendMany([user('u2'), asst('a2', 'y')]);  // the user stopped; a new turn
    applyCompactionEvent(s, { type: 'context_compacting', phase: 'started', message: 'Compacting' });
    applyCompactionEvent(s, { type: 'context_compacted', message: 'done', compaction: { ...REC, id: 'r9' } });
    expect(ids(s)[2]).toBe(stale);  // untouched, still at turn 1
    expect(s.messages[2].metadata?.compactPhase).toBe('running');
    expect(ids(s).slice(-1)).toEqual(['compact-r9']);  // turn 2's divider replaced in place
    expect(ids(s).filter((i) => i.startsWith('compact-'))).toHaveLength(2);
  });

  it('stamps dividers in the DB timestamp format (naive local, no Z)', () => {
    const s = new MessageStore();
    applyCompactionEvent(s, { type: 'context_compacting', phase: 'failed', message: 'failed' });
    expect(s.messages[0].timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$/);
  });
});

describe('live divider + history row = one divider', () => {
  it('reconcile keeps one compact-<id>, in DB order, record intact', () => {
    const store = new MessageStore();
    store.append({ id: 'u1', role: 'user', content: [{ type: 'text', text: 'q' }], timestamp: '2026-10-18T02:00:00Z' });
    store.append({ id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'answer' }], timestamp: '2026-10-18T02:00:05Z' });
    applyCompactionEvent(store, { type: 'context_compacting', phase: 'started', message: 'Compacting' });
    applyCompactionEvent(store, { type: 'context_compacted', message: 'Conversation above has been summarized', compaction: REC });
    const db: ChatMessage[] = [
      { id: 'u1', sessionId: 's', role: 'user', content: [{ type: 'text', text: 'q' }], createdAt: '2026-10-18T10:00:00' },
      { id: 'a1', sessionId: 's', role: 'assistant', content: [{ type: 'text', text: 'answer' }], createdAt: '2026-10-18T10:00:05' },
      { id: 'compact-r1', sessionId: 's', role: 'system', content: [{ type: 'text', text: 'Conversation above has been summarized' }],
        createdAt: '2026-10-18T10:05:00', metadata: { compaction: REC } },
    ];
    store.reconcile(db);
    expect(store.messages.map((m) => m.id)).toEqual(['u1', 'a1', 'compact-r1']);
    expect(toCompactionInfo(store.messages[2])?.summary).toBe('## Overview');
  });
});
