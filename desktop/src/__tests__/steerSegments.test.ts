/**
 * Steer: the turn splits into segments where the CLI read the steer
 * (docs/design/2026-10-25-chat-steer-frontend.md §5, §6).
 *
 * What is tested, through the real hook and the session subscription:
 * - T10b / T10c: `user_steered` labels the steer bubble in place (during /
 *   after) and opens the next segment right after it; later content goes into
 *   the new segment, never back into the one before.
 * - T10h: two steers read one after the other: [seg1][S1][segS1][S2][segS2];
 *   S2 stays at the bottom while it waits.
 * - T10k: two steers read at the same tool result: the first one's empty
 *   segment is taken out (the DB has no row for it).
 * - T10o: a read arriving when the tab is not streaming only labels.
 * - A steer bubble the store does not have: a provisional one from the
 *   snapshot's preview.
 * - The turn's error and result land in the current segment.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { StreamEvent, Message } from '../types';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  return { listeners, connected: true, emit(e: unknown) { for (const l of listeners) l(e); } };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {}, stop: () => {}, isConnected: () => fake.connected,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: () => () => {}, watch: () => {}, cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { testTabMap, createMockDeps, initTestTab, makeMessage, resetTestState } from './helpers/streamingTestUtils';

const T = 'tab-s';
const HOST = 'local-u-asst';
const env = (eventSeq: number, extra: Record<string, unknown>) => ({ sessionId: 'S', turnId: 'T1', eventSeq, ...extra });
const text = (t: string) => ({ type: 'assistant', content: [{ type: 'text', text: t }] });
const steered = (cid: string, outcome: 'folded' | 'opened', segments: string[]) =>
  ({ type: 'user_steered', clientId: cid, outcome, rowKey: `${cid}-asst`, segments });
const user = (id: string, over: Partial<Message> = {}) =>
  makeMessage({ id, role: 'user', content: [{ type: 'text', text: id }], metadata: { client_id: id }, ...over });

let seq = 0;
function setup(extraUsers: Message[] = []) {
  seq = 0;
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  act(() => {
    initTestTab(T);
    result.current.setLocalSending(true, T);
  });
  testTabMap.get(T)!.sessionId = 'S';
  act(() => {
    messageStoreRegistry.getOrCreate(T, { sessionId: 'S' }).appendMany(
      [user('local-u'), makeMessage({ id: HOST, role: 'assistant', content: [] })]);
  });
  const post = result.current.createStreamHandler(HOST, T);
  act(() => { post(env(++seq, { type: 'turn_start', turnClientId: HOST }) as StreamEvent); });
  act(() => { fake.emit(env(seq, { type: 'turn_start', turnClientId: HOST })); });
  // the busy sends come after the turn started (turnControls appends them last)
  if (extraUsers.length) act(() => { messageStoreRegistry.get(T)!.appendMany(extraUsers); });
  const emit = (e: Record<string, unknown>) => act(() => { fake.emit(env(++seq, e)); });
  const store = () => messageStoreRegistry.get(T)!;
  const ids = () => store().messages.map((m) => m.id);
  const body = (id: string) => JSON.stringify(store().messages.find((m) => m.id === id)?.content ?? null);
  return { result, post, emit, store, ids, body };
}

beforeEach(() => { resetTestState(); fake.listeners.clear(); fake.connected = true; });

describe('steer splits the turn into segments', () => {
  it('T10b: a folded read labels the bubble in place and opens its segment right after it', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    const s = h.store().messages[2];
    expect(s.metadata?.steered).toBe('during');
    expect(s.steerWaiting).toBeFalsy();
    h.emit(text('second part'));
    expect(h.body('local-s-asst')).toContain('second part');
    expect(h.body(HOST)).toContain('first part');
    expect(h.body(HOST)).not.toContain('second part');
  });

  it('T10c: an opened read is labelled "after"', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('final answer'));
    h.emit(steered('local-s', 'opened', [HOST, 'local-s-asst']));
    expect(h.store().messages[2].metadata?.steered).toBe('after');
    h.emit(text('answer to the steer'));
    expect(h.body('local-s-asst')).toContain('answer to the steer');
  });

  it('T10h: two steers read one after the other; the waiting one stays at the bottom', () => {
    const h = setup([user('local-s1', { steerWaiting: true }), user('local-s2', { steerWaiting: true })]);
    h.emit(text('one'));
    h.emit(steered('local-s1', 'opened', [HOST, 'local-s1-asst']));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s1', 'local-s1-asst', 'local-s2']);
    h.emit(text('two'));
    h.emit(steered('local-s2', 'folded', [HOST, 'local-s1-asst', 'local-s2-asst']));
    h.emit(text('three'));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s1', 'local-s1-asst', 'local-s2', 'local-s2-asst']);
    expect(h.body('local-s1-asst')).toContain('two');
    expect(h.body('local-s1-asst')).not.toContain('three');
    expect(h.body('local-s2-asst')).toContain('three');
  });

  it('T10k: two steers read at the same point: the first one\'s empty segment is taken out', () => {
    const h = setup([user('local-s1', { steerWaiting: true }), user('local-s2', { steerWaiting: true })]);
    h.emit(text('one'));
    h.emit(steered('local-s1', 'folded', [HOST, 'local-s1-asst']));
    h.emit(steered('local-s2', 'folded', [HOST, 'local-s2-asst']));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s1', 'local-s2', 'local-s2-asst']);
    h.emit(text('both'));
    expect(h.body('local-s2-asst')).toContain('both');
  });

  it('a steer bubble the store does not have: a provisional one from the snapshot preview', () => {
    const h = setup();
    testTabMap.get(T)!._serverSteering = [{ clientId: 'local-o', preview: 'from the other tab' }];
    h.emit(steered('local-o', 'folded', [HOST, 'local-o-asst']));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-o', 'local-o-asst']);
    expect(h.store().messages[2]).toMatchObject({ role: 'user', metadata: { client_id: 'local-o', provisional: true, steered: 'during' },
      content: [{ type: 'text', text: 'from the other tab' }] });
  });

  it('T10o: a read when the tab is not streaming only labels the bubble', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    act(() => { h.store().endStreaming(); });
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s']);
    expect(h.store().messages[2].metadata?.steered).toBe('during');
  });

  it('the turn\'s error lands in the current segment, not the first', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit({ type: 'error', error: 'boom', code: 'AGENT_TIMEOUT' });
    const seg = h.store().messages.find((m) => m.id === 'local-s-asst')!;
    const host = h.store().messages.find((m) => m.id === HOST)!;
    expect(seg.isError).toBe(true);
    expect(host.isError).toBeFalsy();
  });

  it('the POST\'s own error handler (made with the first bubble) writes into the current segment', () => {
    fake.connected = false;                       // no subscription: the POST renders
    seq = 0;
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab(T); result.current.setLocalSending(true, T); });
    testTabMap.get(T)!.sessionId = 'S';
    act(() => { messageStoreRegistry.getOrCreate(T, { sessionId: 'S' }).appendMany(
      [user('local-u'), makeMessage({ id: HOST, role: 'assistant', content: [] })]); });
    const post = result.current.createStreamHandler(HOST, T);
    const send = (e: Record<string, unknown>) => act(() => { post(env(++seq, e) as StreamEvent); });
    send({ type: 'turn_start', turnClientId: HOST });
    act(() => { messageStoreRegistry.get(T)!.append(user('local-s', { steerWaiting: true })); });
    send(text('first part'));
    send(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    send(text('second part'));
    testTabMap.get(T)!._healGraceActive = true;    // past the grace period: the error shows
    // made with the first bubble's id, as each caller makes it
    act(() => { result.current.createErrorHandler(HOST, T)(new Error('connection lost')); });
    const ms = messageStoreRegistry.get(T)!.messages;
    expect(ms.map((m) => m.id)).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    expect(ms.find((m) => m.id === 'local-s-asst')!.isError).toBe(true);
    expect(ms.find((m) => m.id === HOST)!.isError).toBeFalsy();
  });

  it('T17: a replay after the split rebuilds it: each segment once, the first one above the steer', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    // reconnect: the subscription replays the turn from its turn_start
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'T1' }); });
    seq = 0;
    h.emit({ type: 'turn_start', turnClientId: HOST });
    // the replay goes above the steer; the steer's segment stays until its read
    // comes again, which reuses it emptied
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    expect(h.body(HOST).match(/first part/g)?.length).toBe(1);
    expect(h.body(HOST)).not.toContain('second part');
    expect(h.body('local-s-asst').match(/second part/g)?.length).toBe(1);
  });

  it('T17b: a replay while the steer still waits goes above it', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'T1' }); });
    seq = 0;
    h.emit({ type: 'turn_start', turnClientId: HOST });
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s']);
    h.emit(text('first part'));
    expect(h.body(HOST).match(/first part/g)?.length).toBe(1);
  });

  it('T17c: a continuation hosted by the steer segment, replayed, keeps its segment and shows its text once', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    h.emit({ type: 'turn_end' });               // the turn ended on its question
    // the answer to a question continues the turn: same client id, a new turn id
    const cont = { type: 'turn_start', turnId: 'T2', source: 'answer', turnClientId: HOST };
    h.emit(cont);
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    h.emit({ ...text('third part'), turnId: 'T2' });
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'T2' }); });
    seq = 0;
    h.emit(cont);
    h.emit({ ...text('third part'), turnId: 'T2' });
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst']);
    expect(h.body('local-s-asst')).toContain('second part');
    expect(h.body('local-s-asst').match(/third part/g)?.length).toBe(1);
    expect(h.body(HOST)).not.toContain('third part');
  });

  it('the next turn never clears the segments of the turn before it', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    // the next turn starts (the backend clears segments only then: the tab still holds T1's)
    h.emit({ type: 'result' });
    h.emit({ type: 'turn_end' });
    act(() => { h.store().append(user('local-n')); });
    h.emit({ type: 'turn_start', turnId: 'T3', turnClientId: 'local-n-asst' });
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'local-s-asst', 'local-n', 'local-n-asst']);
    expect(h.body('local-s-asst')).toContain('second part');
  });

  it('a continuation after the turn-end reconcile keeps its host and the segments before it', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    // the turn ends on a question; the turn-end reconcile gives the bubbles their DB ids
    h.emit({ type: 'turn_end' });
    act(() => { h.store().endStreaming(); h.store().replace(h.store().messages.map((m) => (m.role === 'assistant'
      ? { ...m, id: `db-${m.id}`, metadata: { ...(m.metadata ?? {}), client_id: m.id } } : m))); });
    h.emit({ type: 'turn_start', turnId: 'T2', source: 'answer', turnClientId: HOST });
    expect(h.ids()).toEqual(['local-u', `db-${HOST}`, 'local-s', 'db-local-s-asst']);
    h.emit({ ...text('third part'), turnId: 'T2' });
    expect(h.body('db-local-s-asst')).toContain('second part');
    expect(h.body('db-local-s-asst')).toContain('third part');
    expect(h.body(`db-${HOST}`)).toContain('first part');
  });

  it('a replay after a missed read shows the segment once (it came under a DB id)', () => {
    const h = setup([user('local-s', { steerWaiting: true })]);
    h.emit(text('first part'));
    // the read never reached this tab; the feed brought the steer's label and its segment's row
    act(() => {
      h.store().updateById('local-s', (m) => ({ ...m, steerWaiting: undefined, metadata: { ...m.metadata, steered: 'during' } }));
      h.store().append({ id: 'db-seg', role: 'assistant', timestamp: '', content: [{ type: 'text', text: 'second part' }],
        metadata: { client_id: 'local-s-asst' } } as Message);
    });
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'T1' }); });
    seq = 0;
    h.emit({ type: 'turn_start', turnClientId: HOST });
    h.emit(text('first part'));
    h.emit(steered('local-s', 'folded', [HOST, 'local-s-asst']));
    h.emit(text('second part'));
    expect(h.ids()).toEqual(['local-u', HOST, 'local-s', 'db-seg']);
    expect(h.body('db-seg').match(/second part/g)?.length).toBe(1);
  });

  it('switch off: a replay goes above a queued message at the bottom (the history has no row for it yet)', () => {
    const h = setup([user('local-q', { isQueued: true })]);
    h.emit(text('first part'));
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'T1' }); });
    seq = 0;
    h.emit({ type: 'turn_start', turnClientId: HOST });
    expect(h.ids()).toEqual(['local-u', HOST, 'local-q']);
  });
});
