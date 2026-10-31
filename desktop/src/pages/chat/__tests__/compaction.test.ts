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
import { applyCompactionEvent, attachCompactions, formatTokensK, reuseCompactions, toCompactionInfo } from '../compaction';

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

describe('attachCompactions — a compaction goes with the turn it happened in', () => {
  it('a mid-turn compaction belongs to that turn\'s last answer, not the list', () => {
    const msgs = [user('u1'), asst('a1', 'x'), sys('compact-1'), asst('a1b', 'after steer'), user('u2'), asst('a2', 'y')];
    const { byAssistant, attached } = attachCompactions(msgs);
    expect(byAssistant.get('a1b')?.map((m) => m.id)).toEqual(['compact-1']);
    expect(byAssistant.has('a1')).toBe(false);
    expect(byAssistant.has('a2')).toBe(false);
    expect([...attached]).toEqual(['compact-1']);
  });

  it('a turn-start compaction before the empty placeholder goes with that placeholder', () => {
    const { byAssistant } = attachCompactions([user('u1'), sys('compact-live-1', { compactPhase: 'running' }), asst('a1')]);
    expect(byAssistant.get('a1')?.map((m) => m.id)).toEqual(['compact-live-1']);
  });

  it('a turn with no answer keeps its compaction in the list; a queued message starts no turn', () => {
    const queued: Message = { ...user('q1'), isQueued: true };
    const { byAssistant, attached } = attachCompactions([
      user('u1'), asst('a1', 'x'), queued, sys('compact-2'),   // still a1's turn
      user('u2'), sys('compact-3'),                             // no answer yet
    ]);
    expect(byAssistant.get('a1')?.map((m) => m.id)).toEqual(['compact-2']);
    expect(attached.has('compact-3')).toBe(false);
  });

  it('a list rebuilt with the same compactions reuses the arrays (memoized bubbles keep their props)', () => {
    const c = sys('compact-1');
    const first = attachCompactions([user('u1'), asst('a1', 'x'), c]).byAssistant;
    const again = reuseCompactions(first, attachCompactions([user('u1'), asst('a1', 'xy'), c]).byAssistant);
    expect(again.get('a1')).toBe(first.get('a1'));
    const changed = reuseCompactions(first, attachCompactions([user('u1'), asst('a1', 'x'), c, sys('compact-2')]).byAssistant);
    expect(changed.get('a1')).not.toBe(first.get('a1'));
  });
});

describe('attachCompactions — steers, CLI turns and answers not painted', () => {
  const steer = (id: string): Message => ({ ...user(id), metadata: { steered: true } });
  const injected = (id: string, text = ''): Message => ({ ...asst(id, text), metadata: { turn_source: 'injected' } });

  it('a read steer does not start a turn: the chain\'s last segment gets the compaction', () => {
    const msgs = [user('u1'), sys('compact-1', { compactPhase: 'running' }), asst('a1'), steer('s1'), asst('a2', 'go on')];
    expect(attachCompactions(msgs).byAssistant.get('a2')?.map((m) => m.id)).toEqual(['compact-1']);
  });

  it('a CLI-started turn does not take the previous turn\'s compaction', () => {
    const msgs = [user('u1'), asst('a1', 'x'), sys('compact-1'), injected('i1', 'answer')];
    const got = attachCompactions(msgs).byAssistant;
    expect(got.get('a1')?.map((m) => m.id)).toEqual(['compact-1']);
    expect(got.has('i1')).toBe(false);
  });

  it('a CLI turn\'s running compaction (turnOf) goes with that turn; once done, by the order as on reload', () => {
    const running = [user('u1'), asst('a1', 'x'), sys('compact-live-2', { compactPhase: 'running', turnOf: 'i1' }), injected('i1', 'answer')];
    const live = attachCompactions(running).byAssistant;
    expect(live.get('i1')?.map((m) => m.id)).toEqual(['compact-live-2']);
    expect(live.has('a1')).toBe(false);
    // done: the record replaced it in place (no turnOf) — the same place a reload gives
    const doneLive = attachCompactions([user('u1'), asst('a1', 'x'), sys('compact-r2', { compaction: { ...REC, id: 'r2' } }), injected('i1', 'answer')]);
    const reloaded = attachCompactions([user('u1'), asst('a1', 'x'), sys('compact-r2', { compaction: { ...REC, id: 'r2' } }), injected('i1', 'answer')]);
    expect(doneLive.byAssistant.get('a1')?.map((m) => m.id)).toEqual(['compact-r2']);
    expect(reloaded.byAssistant.get('a1')?.map((m) => m.id)).toEqual(['compact-r2']);
    // failed keeps its turnOf but is no longer running: placed by the order too
    const failed = attachCompactions([user('u1'), asst('a1', 'x'), sys('compact-live-3', { compactPhase: 'failed', turnOf: 'i1' }), injected('i1', 'answer')]);
    expect(failed.byAssistant.get('a1')?.map((m) => m.id)).toEqual(['compact-live-3']);
  });

  it('an answer the list does not paint keeps its compaction in the list', () => {
    const msgs = [user('u1'), sys('compact-3', { turnOf: 'i1' }), injected('i1')];
    const { attached } = attachCompactions(msgs, (m) => m.content.length > 0);
    expect(attached.has('compact-3')).toBe(false);
  });
});

describe('applyCompactionEvent — the turn a turn-start compaction belongs to', () => {
  it('in front of an empty placeholder a running one carries the placeholder id; the record replaces it in place', () => {
    const store = new MessageStore();
    store.replace([user('u1'), asst('i1')]);
    applyCompactionEvent(store, { type: 'context_compacting', message: 'Compacting' } as never);
    const live = store.messages.find((m) => m.id.startsWith('compact-live-'))!;
    expect(live.metadata?.turnOf).toBe('i1');
    expect(store.messages.map((m) => m.role)).toEqual(['user', 'system', 'assistant']);
    applyCompactionEvent(store, { type: 'context_compacted', message: 'done', compaction: { ...REC, id: 'r9' } } as never);
    const done = store.messages.find((m) => m.id === 'compact-r9')!;
    expect(done.metadata?.turnOf).toBeUndefined();          // placed by the order, as on reload
    expect(toCompactionInfo(done)?.phase).toBe('done');
    expect(store.messages.map((m) => m.id)).toEqual(['u1', 'compact-r9', 'i1']);
  });
});
