/**
 * Live == reload, across generated scenarios (the frontend half).
 *
 * The fixture (fixtures/liveReload/scenarios.json) is produced by the REAL backend
 * (backend/tests/test_live_reload_scenarios.py): per scenario, the steps a tab
 * sees — its own user bubbles, its permission decisions and answers, every
 * published session envelope — and the history JSON a reload returns.
 *
 * Each scenario is replayed through the real useChatStreamingLifecycle (the
 * session-subscription path, events parsed by the real parseSSEEvent), with the
 * tab's own writes mirrored from ChatPage (optimistic user bubble, decision /
 * answers written onto their block). Two views are compared with the reload
 * (toMessageCamelCase -> toDisplayMessage, as ChatPage loads a session):
 *
 * - STREAM: what the stream alone rendered (no timer runs: no reconcile, no mirror);
 * - RECONCILED: the stream view after MessageStore.reconcile(history), what a tab
 *   shows once its turn-end reconcile lands.
 *
 * Plus one property over the whole run: a keyed block (tool step, result, question,
 * permission, background notice, Stop receipt) that either tab ever showed is in the
 * reload ("shown live, gone on reload").
 *
 * Connection steps: `disconnect` (the window's subscription drops; envelopes marked
 * `sub: false` reach only the sending tab's POST), `reconnect` (what the real hub sent
 * for the window's cursor — a replay, or a resync and the active turn — then the DB read
 * a resync makes, as it was at that moment), `restart` (the daemon went away: the open
 * POST fails with a network error; the new daemon's hub answers the stale cursor).
 *
 * Both for the tab that sends (T) and for another tab showing the same session (T2,
 * "other tab:" signatures): T2 gets every session event but none of T's own writes.
 *
 * Blocks are compared on what the user sees (type + text / ids / status /
 * decision), not on incidental fields. Every divergence gets a signature; the
 * report lists them all. KNOWN holds the signatures already accepted or queued
 * for a fix; a new signature fails the test.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import fixture from './fixtures/liveReload/scenarios.json';
import type { Message, ContentBlock, StreamEvent } from '../types';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  const conn = new Set<(c: boolean) => void>();
  return {
    listeners,
    conn,
    connected: true,
    emit(e: unknown) { for (const l of listeners) l(e); },
    setConnected(c: boolean) { this.connected = c; for (const l of conn) l(c); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => fake.connected,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: (l: (c: boolean) => void) => { fake.conn.add(l); return () => fake.conn.delete(l); },
    watch: () => {},
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService, parseSSEEvent, toMessageCamelCase } from '../services/chat';
import { toDisplayMessage } from '../pages/chat/utils';
import { testTabMap, createMockDeps, initTestTab, resetTestState } from './helpers/streamingTestUtils';

type Step =
  | { kind: 'user'; clientId: string; text: string; post?: Record<string, unknown>[]; ended?: boolean }
  | { kind: 'stop'; receipt: Record<string, unknown>[] }
  | { kind: 'event'; env: Record<string, unknown>; sub?: false }
  | { kind: 'disconnect' }
  | { kind: 'reconnect' | 'restart'; envs: Record<string, unknown>[]; history: Record<string, unknown>[] }
  | { kind: 'decide'; requestId: string; decision: 'approve' | 'deny' }
  | { kind: 'answer'; toolUseId: string; answers: Record<string, string> }
  | { kind: 'reconcile'; history: Record<string, unknown>[] };
interface Scenario { name: string; sid: string; shape: string[]; steps: Step[]; history: Record<string, unknown>[] }


/** What the user sees of one block. */
function seen(b: ContentBlock): string {
  const x = b as unknown as Record<string, unknown>;
  switch (x.type) {
    case 'text': return `text:${String(x.text ?? '').trim()}`;
    case 'thinking': return `thinking:${String(x.thinking ?? '').trim()}`;
    case 'tool_use': return `tool_use:${x.id}:${x.name}`;
    case 'tool_result': return `tool_result:${x.toolUseId}:${x.isError ? 'error' : 'ok'}`;
    case 'ask_user_question':
      return `question:${x.toolUseId}:${x.answers ? `answered=${JSON.stringify(x.answers)}` : 'open'}`;
    case 'cmd_permission_request': return `permission:${x.requestId}:${x.decision ?? 'undecided'}`;
    case 'background_finished':
      return `background_finished:${((x.agents as { toolUseId: string }[]) ?? []).map((a) => a.toolUseId).join(',')}`;
    default: return String(x.type);
  }
}
/** A block with an identity of its own (its id / request id), or undefined: text and
 * thinking are shown while still streaming, so a partial one has no stable key. */
const KEYED = new Set(['tool_use', 'tool_result', 'question', 'permission', 'background_finished', 'turn_stop']);
function blockKey(b: ContentBlock): string | undefined {
  const v = seen(b);
  const kind = v.split(':')[0];
  if (!KEYED.has(kind)) return undefined;
  return kind === 'tool_use' || kind === 'background_finished' || kind === 'turn_stop'
    ? v : v.split(':').slice(0, 2).join(':');
}
const keysOf = (ms: readonly Message[]) =>
  new Set(ms.flatMap((m) => (m.content ?? []).map(blockKey).filter((k): k is string => !!k)));

const view = (ms: readonly Message[]) =>
  ms.map((m) => ({ role: m.role, blocks: (m.content ?? []).map(seen),
    source: String((m.metadata as { turn_source?: string } | undefined)?.turn_source ?? '') }));

/** Signatures of how *live* differs from *reload* (both from view()). */
function divergences(live: ReturnType<typeof view>, reload: ReturnType<typeof view>): string[] {
  const out = new Set<string>();
  const roles = (v: typeof live) => v.map((m) => m.role[0]).join('');
  const collapse = (v: typeof live) => v.filter((m) => !(m.role === 'assistant' && m.blocks.length === 0));
  if (live.some((m) => m.role === 'assistant' && m.blocks.length === 0)) out.add('live: empty assistant bubble');
  const l = collapse(live);
  if (roles(l) !== roles(reload)) {
    const la = l.filter((m) => m.role === 'assistant').length;
    const ra = reload.filter((m) => m.role === 'assistant').length;
    const lu = l.filter((m) => m.role === 'user').length;
    const ru = reload.filter((m) => m.role === 'user').length;
    if (lu !== ru) out.add(`user bubbles: live ${lu > ru ? 'more' : 'fewer'} than reload`);
    if (la < ra) out.add('assistant bubbles: live fewer than reload');
    // an assistant bubble right after another one: the reload has them as one
    l.forEach((m, i) => {
      if (i > 0 && m.role === 'assistant' && l[i - 1].role === 'assistant') {
        out.add(`assistant bubbles: a ${m.source || 'unlabelled'} turn has its own bubble`);
      }
    });
    const ls = l.filter((m) => m.role === 'system').length;
    const rs = reload.filter((m) => m.role === 'system').length;
    if (ls !== rs) out.add(`system rows (compaction): live ${ls > rs ? 'more' : 'fewer'} than reload`);
    if (lu === ru && la === ra && ls === rs) out.add('bubble order differs');
  }
  const flat = (v: typeof live) => v.flatMap((m) => m.blocks.map((b) => `${m.role}|${b}`));
  const lf = flat(l), rf = flat(reload);
  const count = (xs: string[]) => xs.reduce((m, x) => m.set(x, (m.get(x) ?? 0) + 1), new Map<string, number>());
  const lc = count(lf), rc = count(rf);
  const kind = (k: string) => k.split('|')[1].split(':')[0];
  for (const [k, n] of lc) {
    const r = rc.get(k) ?? 0;
    if (n > r) out.add(r === 0 ? `only live: ${kind(k)}` : `live repeats: ${kind(k)}`);
  }
  const STATEFUL = new Set(['tool_result', 'question', 'permission']);
  for (const [k, n] of rc) {
    const lv = lc.get(k) ?? 0;
    if (n <= lv) continue;
    const kd = kind(k);
    if (STATEFUL.has(kd)) {
      // the same block with another status / decision / answer state?
      const stem = k.split(':').slice(0, -1).join(':');
      const other = [...lc.keys()].find((x) => x !== k && x.startsWith(stem + ':'));
      if (other) { out.add(`state differs: ${kd} (live ${other.split(':').pop()}, reload ${k.split(':').pop()})`); continue; }
    }
    out.add(lv === 0 ? `only reload: ${kd}` : `live drops a repeated ${kd}`);
  }
  if (out.size === 0 && lf.join('\n') !== rf.join('\n')) out.add('block order differs');
  return [...out];
}

type Lifecycle = ReturnType<typeof useChatStreamingLifecycle>;
async function replay(s: Scenario, renderLifecycle: () => { current: Lifecycle }, withReconcile: boolean) {
  resetTestState();
  fake.listeners.clear();
  fake.conn.clear();
  fake.connected = true;
  // a resync's DB read (resyncTabFromDb) returns the history as it was at that moment
  let dbNow: Record<string, unknown>[] = [];
  vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
  vi.spyOn(chatService, 'getSessionMessagesReconcileTail')
    .mockImplementation(async () => dbNow.map((r) => toMessageCamelCase(r)) as never);
  // the since_rev check: the recorded snapshot is the record at that moment, so
  // the harness always answers "changed" (the reload then reads it)
  vi.spyOn(chatService, 'getTranscriptSince')
    .mockImplementation(async () => ({ rev: 0, reset: true, rows: [] }));
  // the window's subscription back: what the hub sent for its cursor, then the DB read
  const resubscribe = async (envs: Record<string, unknown>[], history: Record<string, unknown>[]) => {
    dbNow = history;
    act(() => { fake.setConnected(true); });
    for (const env of envs) act(() => { fake.emit(parseSSEEvent(JSON.stringify(env))); });
    await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); });
  };
  const hook = renderLifecycle();
  const SID = s.sid;
  act(() => { initTestTab('T'); initTestTab('T2'); });
  testTabMap.get('T')!.sessionId = SID;
  testTabMap.get('T2')!.sessionId = SID;   // another tab showing the same session; it sends nothing
  const store = messageStoreRegistry.getOrCreate('T', { sessionId: SID });
  const store2 = messageStoreRegistry.getOrCreate('T2', { sessionId: SID });
  let numericId = 1_700_000_000_000;
  // The sending tab's own POST stream, as ChatPage opens it for a send / an answer /
  // a decision: it receives the same envelopes as the subscription (the turn router
  // renders each once) and completes at the turn's end.
  let post: { handler: (e: StreamEvent) => void; complete: () => void; error: (e: Error) => void; open: boolean } | null = null;
  const openPost = (bubbleId: string) => {
    post = { handler: hook.current.createStreamHandler(bubbleId, 'T'),
             complete: hook.current.createCompleteHandler('T'),
             error: hook.current.createErrorHandler(bubbleId, 'T'), open: true };
  };
  const endPost = () => { if (post?.open) { post.open = false; act(() => { post!.complete(); }); } post = null; };
  // ChatPage.handleAnswerQuestion / handlePermissionDecision: the continuation's
  // own POST placeholder, a numeric id
  const continuationPlaceholder = () => {
    const id = String(numericId++);
    act(() => {
      store.append({ id, role: 'assistant', content: [], timestamp: new Date().toISOString() } as Message);
    });
    openPost(id);
  };
  // every keyed block either tab ever showed (property: what was shown is not lost)
  const shown = [new Set<string>(), new Set<string>()] as const;
  const noteShown = () => {
    for (const k of keysOf(store.messages)) shown[0].add(k);
    for (const k of keysOf(store2.messages)) shown[1].add(k);
  };
  for (const st of s.steps) {
    noteShown();
    if (st.kind === 'user') {
      // ChatPage send: the user bubble and the turn's assistant placeholder
      act(() => {
        store.appendMany([
          { id: st.clientId, role: 'user', content: [{ type: 'text', text: st.text }] as ContentBlock[],
            timestamp: new Date().toISOString(), metadata: { client_id: st.clientId } } as Message,
          { id: `${st.clientId}-asst`, role: 'assistant', content: [], timestamp: new Date().toISOString() } as Message,
        ]);
      });
      openPost(`${st.clientId}-asst`);
      // what only this send's POST got: the acceptance receipt, then (SESSION_BUSY,
      // kept pending server-side) the reply that ended it without a turn
      for (const ev of st.post ?? []) act(() => { post!.handler(parseSSEEvent(JSON.stringify(ev))); });
      if (st.ended) endPost();
    } else if (st.kind === 'stop') {
      // ChatPage.handleStop: the stream is aborted and /chat/stop called; the
      // receipt it persists comes back on the transcript feed (an event step)
    } else if (st.kind === 'reconcile') {
      // the turn-end reconcile (useChatStreamingLifecycle scheduleTurnEndReconcile)
      if (withReconcile) {
        act(() => {
          store.reconcile(st.history.map((r) => toMessageCamelCase(r)));
          store2.reconcile(st.history.map((r) => toMessageCamelCase(r)));
        });
      }
    } else if (st.kind === 'disconnect') {
      // the window's subscription drops (sessionEvents: connected → false)
      act(() => { fake.setConnected(false); });
    } else if (st.kind === 'reconnect') {
      await resubscribe(st.envs, st.history);
    } else if (st.kind === 'restart') {
      // the daemon went away: subscription down, this tab's open POST fails
      // (fetch: a network error), then the new daemon answers the reconnect
      if (fake.connected) act(() => { fake.setConnected(false); });
      if (post?.open) {
        const p = post;
        p.open = false;
        post = null;
        act(() => { p.error(new TypeError('network error')); });
      }
      await resubscribe(st.envs, st.history);
    } else if (st.kind === 'event') {
      // missed by the subscription while it was down (`sub: false`): only the POST gets it
      if (st.sub !== false) act(() => { fake.emit(parseSSEEvent(JSON.stringify(st.env))); });
      // the same envelope on the POST of the send that started this turn
      // (a transcript_row / background_agents envelope is published outside any
      // turn: never on a POST)
      if (post?.open && st.env.turnId && st.env.source !== 'drain' && st.env.source !== 'injected') {
        const ev = parseSSEEvent(JSON.stringify(st.env));
        act(() => { post!.handler(ev); });
        if (st.env.type === 'turn_end') endPost();
      }
    } else if (st.kind === 'decide') {
      // ChatPage.handlePermissionDecision: the decision onto its block
      act(() => {
        store.updateLast((m) => ({ ...m, content: m.content.map((b) => {
          const x = b as unknown as { type: string; requestId?: string };
          return x.type === 'cmd_permission_request' && x.requestId === st.requestId ? { ...b, decision: st.decision } as ContentBlock : b;
        }) }), (m) => m.content.some((b) => (b as unknown as { requestId?: string }).requestId === st.requestId));
      });
      continuationPlaceholder();
    } else {
      // ChatPage.handleAnswerQuestion: the answers onto its block
      act(() => {
        store.updateLast((m) => ({ ...m, content: m.content.map((b) => {
          const x = b as unknown as { type: string; toolUseId?: string };
          return x.type === 'ask_user_question' && x.toolUseId === st.toolUseId ? { ...b, answers: st.answers } as ContentBlock : b;
        }) }), (m) => m.content.some((b) => (b as unknown as { toolUseId?: string; type: string }).type === 'ask_user_question'
          && (b as unknown as { toolUseId?: string }).toolUseId === st.toolUseId));
      });
      continuationPlaceholder();
    }
  }
  noteShown();
  vi.restoreAllMocks();
  return [store, store2, shown] as const;
}

/**
 * Divergences still accepted (none). The P0 roots R1-R10 are fixed (design doc
 * chat-transcript-single-source.md §9); any signature now fails the test.
 */
const KNOWN = new Set<string>([]);

const scenarios = (fixture as { scenarios: Scenario[] }).scenarios;
const report: { stream: Map<string, string[]>; reconciled: Map<string, string[]> } = {
  stream: new Map(), reconciled: new Map(),
};
const note = (m: Map<string, string[]>, sig: string, name: string) => m.set(sig, [...(m.get(sig) ?? []), name]);

describe('live == reload (generated scenarios from the real backend)', () => {
  beforeAll(() => { vi.useFakeTimers(); });   // no timer fires: the stream view is the stream alone
  afterAll(() => {
    vi.useRealTimers();
    const lines: string[] = [];
    for (const [mode, m] of Object.entries(report)) {
      lines.push(`== ${mode}: ${m.size} signature(s)`);
      for (const [sig, names] of [...m.entries()].sort((a, b) => b[1].length - a[1].length)) {
        lines.push(`  ${names.length.toString().padStart(3)}  ${sig}   e.g. ${names.slice(0, 3).join(' ')}`);
      }
    }
    // eslint-disable-next-line no-console
    console.log(`\n[liveReload]\n${lines.join('\n')}\n`);
  });

  it.each(scenarios.map((s) => [s.name, s] as const))('%s', async (_name, s) => {
    const reloadMsgs = s.history.map((r) => toDisplayMessage(toMessageCamelCase(r) as never));
    const reload = view(reloadMsgs);
    const run = async (withReconcile: boolean) => {
      const [store, store2, shown] = await replay(s, () => renderHook(() => useChatStreamingLifecycle(createMockDeps())).result, withReconcile);
      const v = view(store.messages);
      if (process.env.LR_DUMP === s.name) {
        // eslint-disable-next-line no-console
        console.log(withReconcile ? 'RECONCILED' : 'STREAM', JSON.stringify({ live: v, other: view(store2.messages), reload }));
      }
      // shown at some point, absent from the reload: the user saw it, the record lost it
      const kept = keysOf(reloadMsgs);
      const lost = (set: Set<string>, prefix: string) =>
        [...new Set([...set].filter((k) => !kept.has(k)).map((k) => `${prefix}shown live, gone on reload: ${k.split(':')[0]}`))];
      return [...divergences(v, reload), ...divergences(view(store2.messages), reload).map((x) => `other tab: ${x}`),
        ...lost(shown[0], ''), ...lost(shown[1], 'other tab: ')];
    };
    const stream = await run(false);
    for (const sig of stream) note(report.stream, sig, s.name);
    const reconciled = await run(true);
    for (const sig of reconciled) note(report.reconciled, sig, s.name);
    const unknown = [...stream.map((x) => `stream: ${x}`), ...reconciled.map((x) => `reconciled: ${x}`)]
      .filter((x) => !KNOWN.has(x));
    expect(unknown, `${s.name} ${s.shape.join(' > ')}`).toEqual([]);
  });
});
