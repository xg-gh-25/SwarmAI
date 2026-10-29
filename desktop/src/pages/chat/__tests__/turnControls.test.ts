/**
 * Behaviour tests for pages/chat/turnControls (design 2026-10-24-chat-stream-structure.md A1).
 *
 * What is tested: the send / stop / continue controllers, driven through injected
 * deps. They replace the source-text guards that stood in for a ChatPage harness:
 * turnLiveness.p0 R4 / R5, stopOnce.guard (ChatPage part), serverQueue's busy-send
 * guards, and the clientId / terminalContext scans of the moved sends.
 *
 * Methodology: a fake deps object records every call (POSTs, setters, composer
 * writes); tabs are plain objects in a Map, stores are minimal fakes; a POST is a
 * vi.fn that returns an abort spy and keeps its handlers so a test can answer it.
 * Attachments are read through a deferred promise so a test can act while a send
 * waits on them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const refreshCanvasChanges = vi.fn((_sid?: string | null) => Promise.resolve());
vi.mock('../../../services/canvasSession', () => ({ refreshCanvasChanges: (s?: string | null) => refreshCanvasChanges(s) }));
beforeEach(() => refreshCanvasChanges.mockClear());

import {
  sendMessage, sendWhileBusy, answerQuestion, decidePermission, stopTurn, stopBackground,
  type TurnControlDeps, type TurnStore,
} from '../turnControls';
import type { ChatRequest, ContentBlock, Message, UnifiedAttachment } from '../../../types';
import type { UnifiedTab } from '../../../hooks/useUnifiedTabState';

function fakeStore(initial: Message[] = []): TurnStore & { messages: Message[]; ended: number } {
  const st = {
    messages: [...initial],
    ended: 0,
    append(m: Message) { st.messages = [...st.messages, m]; },
    appendMany(ms: Message[]) { st.messages = [...st.messages, ...ms]; },
    updateById(id: string, fn: (m: Message) => Message) { st.messages = st.messages.map((m) => (m.id === id ? fn(m) : m)); },
    updateLast(fn: (m: Message) => Message, pred: (m: Message) => boolean) {
      const i = st.messages.map(pred).lastIndexOf(true);
      if (i >= 0) st.messages = st.messages.map((m, j) => (j === i ? fn(m) : m));
    },
    endStreaming() { st.ended += 1; },
  };
  return st;
}

function tab(id: string, over: Partial<UnifiedTab> = {}): UnifiedTab {
  return { id, isStreaming: false, messages: [], localSending: false, ...over } as unknown as UnifiedTab;
}

interface Post { req: ChatRequest; handlers: unknown[]; abort: ReturnType<typeof vi.fn> }

function deferred<T>() {
  let resolve!: (v: T) => void; let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(opts: { tabs?: UnifiedTab[]; active?: string; text?: string; attachments?: UnifiedAttachment[]; stores?: Record<string, ReturnType<typeof fakeStore>>; react?: Message[] } = {}) {
  const tabs = new Map((opts.tabs ?? [tab('A')]).map((t) => [t.id, t]));
  const stores: Record<string, ReturnType<typeof fakeStore>> = { ...(opts.stores ?? {}) };
  const posts: Post[] = [];
  const mkPost = () => vi.fn((req: ChatRequest, ...handlers: unknown[]) => {
    const abort = vi.fn();
    posts.push({ req, handlers, abort });
    return abort;
  });
  let active: string | undefined = opts.active ?? 'A';
  let text = opts.text ?? 'hello';
  let attachments = opts.attachments ?? [];
  let content = deferred<ContentBlock[]>();
  let ids = 0;
  let react: Message[] = opts.react ?? [];
  const calls: string[] = [];
  const log = (s: string) => () => { calls.push(s); };
  const stopSession = vi.fn(() => Promise.resolve({ status: 'ok', message: '', receipt: [] }));
  const d = {
    tabs: () => tabs,
    activeTabId: () => active,
    agentId: () => 'agent',
    options: () => ({ enableSkills: true, enableMCP: false }),
    composer: {
      text: () => text,
      attachments: () => attachments,
      clear: vi.fn(() => { calls.push('composer.clear'); text = ''; attachments = []; }),
      clearText: vi.fn(() => { calls.push('composer.clearText'); text = ''; }),
      restore: vi.fn(),
    },
    buildContent: vi.fn(() => { calls.push('buildContent'); return content.promise; }),
    stores: {
      get: (id: string) => stores[id],
      getOrCreate: (id: string) => (stores[id] ??= fakeStore()),
    },
    chat: {
      streamChat: mkPost(),
      streamAnswerQuestion: mkPost(),
      streamCmdPermissionContinue: mkPost(),
      stopSession,
    },
    stream: {
      handler: vi.fn((a: string, t?: string) => Object.assign(() => {}, { tag: `handler:${a}:${t}` })),
      error: vi.fn((a: string, t?: string) => Object.assign(() => { calls.push(`error:${a}:${t}`); }, { tag: `error:${a}:${t}` })),
      complete: vi.fn((t?: string) => Object.assign(() => { calls.push(`complete:${t}`); }, { tag: `complete:${t}` })),
      disconnect: vi.fn((t?: string) => Object.assign(() => {}, { tag: `disconnect:${t}` })),
      liveness: vi.fn(() => ({})),
    },
    initTab: vi.fn((id: string) => { tabs.set(id, tab(id)); }),
    setLocalSending: vi.fn((on: boolean, id?: string) => { calls.push(`localSending:${on}`); const t = id ? tabs.get(id) : undefined; if (t) t.localSending = on; }),
    incrementStreamGen: vi.fn(log('streamGen')),
    setTabStatus: vi.fn(),
    titleFromFirstMessage: vi.fn(),
    updateTab: vi.fn((id: string, patch: Partial<UnifiedTab>) => { Object.assign(tabs.get(id)!, patch); }),
    noteSend: vi.fn((_t: string, r: ChatRequest) => r),
    editorContext: vi.fn(() => ({}) as never),
    pluginCommand: vi.fn(async () => true),
    // as the real one: the tab's local queue is rebuilt from the server's
    rebindQueue: vi.fn((id: string) => { calls.push('rebindQueue'); const t = tabs.get(id); if (t) t._queuedLocal = new Map(); }),
    syncQueuedBadges: vi.fn(),
    setMessages: vi.fn((fn: (prev: Message[]) => Message[]) => { react = fn(react); }),
    clearPendingQuestion: vi.fn(),
    clearPendingPermission: vi.fn(),
    dismissNotice: vi.fn(),
    permissionLoading: new Set<string>(),
    newClientId: () => `local-${++ids}`,
  } satisfies TurnControlDeps;
  return {
    d, tabs, stores, posts, calls,
    react: () => react,
    setActive: (id: string | undefined) => { active = id; },
    type: (t: string) => { text = t; },
    attachmentsRead: (blocks: ContentBlock[] = [{ type: 'text', text: 'hello' }]) => content.resolve(blocks),
    attachmentsFail: () => content.reject(new Error('read failed')),
    resetContent: () => { content = deferred(); },
    stopSession,
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const termAtt = (tail: string) => ({ id: tail, name: 'term', type: 'terminal', textContent: tail, terminal: { cwd: '/w' } }) as unknown as UnifiedAttachment;

describe('sendMessage (normal path)', () => {
  it('takes the draft before the await, POSTs its own keyed request with this tab\'s handlers', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })], attachments: [termAtt('$ ls')] });
    const p = sendMessage(h.d);
    expect(h.calls.indexOf('composer.clear')).toBeLessThan(h.calls.indexOf('buildContent'));
    expect(h.posts).toHaveLength(0);
    h.setActive('B');   // the user switches tabs while the attachments are read
    h.attachmentsRead();
    await p;
    expect(h.d.composer.clear).toHaveBeenCalledTimes(1);   // never the (now other) active composer
    expect(h.posts).toHaveLength(1);
    const { req, handlers } = h.posts[0];
    expect(req.clientId).toBe('local-1');
    expect(req.sessionId).toBe('S');
    expect(req.terminalContext).toEqual({ bufferTail: '$ ls', cwd: '/w' });
    expect((handlers[0] as { tag: string }).tag).toBe('handler:local-1-asst:A');
    expect(h.stores.A.messages.map((m) => m.id)).toEqual(['local-1', 'local-1-asst']);
    expect(h.tabs.get('A')!._sendToken).toBeUndefined();
  });

  it('a reconnect re-sends the same message (same clientId, terminal) with the tab\'s own fresh handlers', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })], attachments: [termAtt('$ ls')] });
    const p = sendMessage(h.d); h.attachmentsRead(); await p;
    h.setActive('B');
    h.tabs.get('A')!.retryStreamFn!();
    expect(h.posts).toHaveLength(2);
    expect(h.posts[1].req.clientId).toBe('local-1');
    expect(h.posts[1].req.terminalContext).toEqual({ bufferTail: '$ ls', cwd: '/w' });
    expect((h.posts[1].handlers[0] as { tag: string }).tag).toBe('handler:local-1-asst:A');
  });

  it('a new tab sends no session (the backend creates one), never the active tab\'s', async () => {
    const h = setup({ tabs: [tab('A')] });
    const p = sendMessage(h.d); h.attachmentsRead(); await p;
    expect(h.posts[0].req.sessionId).toBeUndefined();
  });

  it('R4: a send stopped while its attachments are read writes nothing and opens no POST', async () => {
    for (const sessionId of [undefined, 'S']) {
      const h = setup({ tabs: [tab('A', { sessionId })] });
      const p = sendMessage(h.d);
      stopTurn(h.d);
      h.attachmentsRead();
      await p;
      expect(h.posts, `session ${sessionId}`).toHaveLength(0);
      expect(h.stores.A?.messages ?? []).toEqual([]);
      expect(h.d.composer.restore).toHaveBeenCalledWith('A', 'hello', []);
      // Stop cleared the request; the cancelled send does not touch it again
      expect(h.calls.filter((c) => c.startsWith('localSending')), `session ${sessionId}`).toEqual(['localSending:true', 'localSending:false']);
    }
  });

  it('a send whose tab moved to another session meanwhile is not sent; its draft goes back', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })] });
    const p = sendMessage(h.d);
    h.tabs.get('A')!.sessionId = 'S2';
    h.attachmentsRead(); await p;
    expect(h.posts).toHaveLength(0);
    expect(h.d.composer.restore).toHaveBeenCalledWith('A', 'hello', []);
  });

  it('attachments that fail to read: no POST, the request is closed, the draft goes back', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })] });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const p = sendMessage(h.d); h.attachmentsFail(); await p;
    expect(h.posts).toHaveLength(0);
    expect(h.tabs.get('A')!.localSending).toBe(false);
    expect(h.d.composer.restore).toHaveBeenCalledTimes(1);
  });

  it('a second Send while a send reads its attachments does nothing; only the owner clears the mark', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })] });
    const p = sendMessage(h.d);
    expect(h.tabs.get('A')!._preparingSend).toBeDefined();
    h.type('second');   // typed while the first send still reads its attachments
    await sendMessage(h.d);   // nothing at all: not sent, not queued, the text stays
    expect(h.d.buildContent).toHaveBeenCalledTimes(1);
    expect(h.d.composer.clear).toHaveBeenCalledTimes(1);
    const mine = h.tabs.get('A')!._preparingSend;
    h.tabs.get('A')!._preparingSend = 42;   // someone else's preparation now
    h.attachmentsRead(); await p;
    expect(h.tabs.get('A')!._preparingSend).toBe(42);
    expect(mine).not.toBe(42);
  });

  it('/plugin clears only the text and runs the command, no POST', async () => {
    const h = setup({ text: '/plugin list', attachments: [termAtt('x')] });
    await sendMessage(h.d);
    expect(h.d.composer.clearText).toHaveBeenCalledTimes(1);
    expect(h.d.composer.clear).not.toHaveBeenCalled();
    expect(h.d.pluginCommand).toHaveBeenCalledWith('/plugin list');
    expect(h.posts).toHaveLength(0);
  });
});

describe('sendMessage while busy (the server queues it)', () => {
  const busy = () => setup({ tabs: [tab('A', { sessionId: 'S', isStreaming: true })], attachments: [termAtt('$ pwd')] });

  it('goes to the server on its own: no stream generation, no tab handlers, no local request', async () => {
    const h = busy();
    const p = sendMessage(h.d);
    expect(h.calls.indexOf('composer.clear')).toBeLessThan(h.calls.indexOf('buildContent'));
    h.attachmentsRead(); await p; await flush();
    expect(h.posts).toHaveLength(1);
    expect(h.d.incrementStreamGen).not.toHaveBeenCalled();
    expect(h.d.setLocalSending).not.toHaveBeenCalled();
    expect(h.d.stream.handler).not.toHaveBeenCalled();
    const { req } = h.posts[0];
    expect(req.clientId).toBe('local-1');
    expect(req.terminalContext).toEqual({ bufferTail: '$ pwd', cwd: '/w' });
    const bubble = h.stores.A.messages[0];
    expect(bubble).toMatchObject({ id: 'local-1', isQueued: true, metadata: { client_id: 'local-1' } });
    expect(h.tabs.get('A')!._queuedLocal!.has('local-1')).toBe(true);
  });

  it('`accepted` queued stamps the entry; a send never accepted is "Not sent" and keeps its request', async () => {
    const h = busy();
    const p = sendMessage(h.d); h.attachmentsRead(); await p; await flush();
    const [onEvent] = h.posts[0].handlers as [(e: unknown) => void];
    onEvent({ type: 'accepted', queued: true });
    expect(typeof h.tabs.get('A')!._queuedLocal!.get('local-1')).toBe('number');
    expect(h.tabs.get('A')!._unsentRequests!.has('local-1')).toBe(false);

    const h2 = busy();
    const p2 = sendMessage(h2.d); h2.attachmentsRead(); await p2; await flush();
    const [, onError] = h2.posts[0].handlers as [unknown, () => void];
    onError();
    expect(h2.stores.A.messages[0].sendState).toBe('failed');
    expect(h2.tabs.get('A')!._unsentRequests!.has('local-1')).toBe(true);
  });

  it('T10a: `accepted` steer: Waiting (decided locally until the server speaks), not queued', async () => {
    const h = busy();
    const p = sendMessage(h.d); h.attachmentsRead(); await p; await flush();
    const [onEvent] = h.posts[0].handlers as [(e: unknown) => void];
    onEvent({ type: 'accepted', queued: false, steer: true });
    const t = h.tabs.get('A')!;
    expect(t._queuedLocal!.has('local-1')).toBe(false);
    expect(typeof t._steerLocal!.get('local-1')).toBe('number');
    expect(h.d.syncQueuedBadges).toHaveBeenCalledWith('A');
  });

  it('a receipt that arrives after the session went idle makes nothing Waiting (the turn is over)', async () => {
    const h = busy();
    const p = sendMessage(h.d); h.attachmentsRead(); await p; await flush();
    h.tabs.get('A')!._steerTurnEndAt = Date.now() + 1;     // idle came after this send
    const [onEvent] = h.posts[0].handlers as [(e: unknown) => void];
    onEvent({ type: 'accepted', queued: false, steer: true });
    expect(h.tabs.get('A')!._steerLocal?.has('local-1') ?? false).toBe(false);
  });

  it('T10l: a receipt after the read (the event came first) does not make it Waiting again', async () => {
    const h = busy();
    const p = sendMessage(h.d); h.attachmentsRead(); await p; await flush();
    h.stores.A.updateById('local-1', (m) => ({ ...m, isQueued: undefined, metadata: { ...m.metadata, steered: 'during' } }));
    const [onEvent] = h.posts[0].handlers as [(e: unknown) => void];
    onEvent({ type: 'accepted', queued: false, steer: true });
    expect(h.tabs.get('A')!._steerLocal?.has('local-1') ?? false).toBe(false);
  });

  it('T10e: `accepted` queued with a reason puts the reason on the bubble', async () => {
    const h = busy();
    const p = sendMessage(h.d); h.attachmentsRead(); await p; await flush();
    const [onEvent] = h.posts[0].handlers as [(e: unknown) => void];
    onEvent({ type: 'accepted', queued: true, queuedReason: 'question' });
    expect(h.stores.A.messages[0].queuedReason).toBe('question');
  });

  it('a busy send stopped or rebound while preparing is not sent; its draft goes back to its own tab', async () => {
    const h = busy();
    const p = sendMessage(h.d);
    h.setActive('B');
    stopTurn({ ...h.d, activeTabId: () => 'A' });   // Stop on that tab
    h.attachmentsRead(); await p; await flush();
    expect(h.posts).toHaveLength(0);
    expect(h.d.composer.restore).toHaveBeenCalledWith('A', 'hello', [termAtt('$ pwd')]);
    expect(h.d.composer.clear).toHaveBeenCalledTimes(1);

    const h2 = busy();
    const p2 = sendWhileBusy(h2.d, 'A', 'hi', []);
    h2.tabs.get('A')!.sessionId = 'S2';
    h2.attachmentsRead(); await p2;
    expect(h2.posts).toHaveLength(0);
    expect(h2.d.composer.restore).toHaveBeenCalledWith('A', 'hi', []);
  });
});

const asst = (id: string, content: ContentBlock[] = []): Message => ({ id, role: 'assistant', content, timestamp: '' });

describe('answerQuestion / decidePermission (continuations)', () => {
  const waiting = () => tab('A', { sessionId: 'S', isStreaming: true, activity: 'waiting_input' } as Partial<UnifiedTab>);

  it('an answer is written onto its question block and continues into that bubble', () => {
    const q = { type: 'ask_user_question', toolUseId: 'tu1', questions: [] } as unknown as ContentBlock;
    const h = setup({ tabs: [waiting()], stores: { A: fakeStore([asst('m1', [q]), asst('m2')]) } });
    answerQuestion(h.d, 'tu1', { q: 'yes' });
    expect((h.stores.A.messages[0].content[0] as { answers?: unknown }).answers).toEqual({ q: 'yes' });
    expect(h.tabs.get('A')!._answeredToolUseId).toBe('tu1');
    expect(h.d.chat.streamAnswerQuestion).toHaveBeenCalledTimes(1);
    expect((h.posts[0].handlers[0] as { tag: string }).tag).toBe('handler:m1:A');
    expect(h.tabs.get('A')!.retryStreamFn).toBeTypeOf('function');
    // the reconnect resends the same answer, for this tab, with its current session
    h.tabs.get('A')!.sessionId = 'S2';
    h.setActive('B');
    h.tabs.get('A')!.retryStreamFn!();
    expect(h.posts[1].req).toMatchObject({ sessionId: 'S2', toolUseId: 'tu1', answers: { q: 'yes' }, agentId: 'agent' });
    expect((h.posts[1].handlers as { tag: string }[]).slice(0, 4).map((x) => x.tag))
      .toEqual(['handler:m1:A', 'error:m1:A', 'complete:A', 'disconnect:A']);
  });

  it('a decision\'s error / complete handlers are made when its POST settles (the generation of that moment)', () => {
    const h = setup({ tabs: [waiting()] });
    decidePermission(h.d, 'r1', 'approve');
    expect(h.d.stream.complete).not.toHaveBeenCalled();
    expect(h.d.stream.error).not.toHaveBeenCalled();
    (h.posts[0].handlers[2] as () => void)();
    expect(h.d.stream.complete).toHaveBeenCalledTimes(1);
  });

  it('an answer while the turn is not waiting (or a request is open) does nothing', () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S', isStreaming: true, activity: 'streaming' } as Partial<UnifiedTab>)] });
    answerQuestion(h.d, 'tu1', {});
    expect(h.posts).toHaveLength(0);
  });

  it('R5: a decision clears the previous message\'s reconnect resend (a decision is never re-sent)', () => {
    const prompt = { type: 'cmd_permission_request', requestId: 'r1' } as unknown as ContentBlock;
    const previous = vi.fn();
    const h = setup({ tabs: [tab('A', { ...waiting(), retryStreamFn: previous })], stores: { A: fakeStore([asst('m1', [prompt])]) } });
    decidePermission(h.d, 'r1', 'deny');
    expect(h.d.chat.streamCmdPermissionContinue).toHaveBeenCalledTimes(1);
    expect(h.posts[0].req).toMatchObject({ sessionId: 'S', requestId: 'r1', decision: 'deny' });
    expect(h.tabs.get('A')!.retryStreamFn).toBeUndefined();
    expect((h.stores.A.messages[0].content[0] as { decision?: string }).decision).toBe('deny');
    expect(previous).not.toHaveBeenCalled();
  });

  it('a double-click decides once; the guard lifts when the POST ends (done or failed)', () => {
    const h = setup({ tabs: [waiting()] });
    decidePermission(h.d, 'r1', 'approve');
    h.tabs.get('A')!.localSending = false;   // isolate the per-tab loading guard
    decidePermission(h.d, 'r1', 'approve');
    expect(h.posts).toHaveLength(1);
    const [, onError, onComplete] = h.posts[0].handlers as [unknown, (e: Error) => void, () => void];
    onComplete();
    expect(h.calls).toContain('complete:A');
    expect(h.d.permissionLoading.has('A')).toBe(false);
    decidePermission(h.d, 'r2', 'approve');
    expect(h.posts).toHaveLength(2);
    (h.posts[1].handlers[1] as (e: Error) => void)(new Error('x'));
    expect(h.calls.some((c) => c.startsWith('error:') && c.endsWith(':A'))).toBe(true);
    expect(h.d.permissionLoading.has('A')).toBe(false);
    void onError;
  });
});

describe('stopTurn / stopBackground (one Stop at a time)', () => {
  const running = () => setup({
    tabs: [tab('A', { sessionId: 'S', isStreaming: true, abortController: { abort: vi.fn() } as never, messages: [asst('t1')] })],
    stores: { A: fakeStore([asst('m1')]) },
    react: [asst('r1')],
  });
  const markers = (st: { messages: Message[] }) => st.messages.flatMap((m) => m.content)
    .filter((b) => (b as { text?: string }).text?.includes('*Stopped*')).length;

  it('repeated clicks abort once, mark once, request once; a later Stop works again', async () => {
    const h = running();
    let settle!: () => void;
    h.stopSession.mockImplementationOnce(() => new Promise((res) => { settle = () => res({ status: 'ok', message: '', receipt: [] }); }));
    const abort = (h.tabs.get('A')!.abortController as unknown as { abort: ReturnType<typeof vi.fn> }).abort;
    stopTurn(h.d); stopTurn(h.d); stopTurn(h.d);
    expect(abort).toHaveBeenCalledTimes(1);
    expect(h.stopSession).toHaveBeenCalledTimes(1);
    expect(h.d.incrementStreamGen).toHaveBeenCalledTimes(1);
    expect(markers(h.stores.A)).toBe(1);
    expect(markers({ messages: h.react() })).toBe(1);
    expect(markers({ messages: h.tabs.get('A')!.messages })).toBe(1);
    expect(h.react().map((m) => m.id)).toEqual(['r1']);   // on the last assistant, not a new message
    expect(h.stores.A.ended).toBe(1);
    settle(); await flush();
    expect(h.tabs.get('A')!._stopInFlight).toBe(false);
    stopTurn(h.d);
    expect(h.stopSession).toHaveBeenCalledTimes(2);
  });

  it('a failed backend stop lifts the guard too', async () => {
    const h = running();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    h.stopSession.mockImplementationOnce(() => Promise.reject(new Error('down')));
    stopTurn(h.d); await flush();
    expect(h.tabs.get('A')!._stopInFlight).toBe(false);
    expect(refreshCanvasChanges).not.toHaveBeenCalled();
  });

  it('the Canvas Changes refresh waits for the backend stop (its card is persisted by then)', async () => {
    const h = running();
    let settle!: () => void;
    h.stopSession.mockImplementationOnce(() => new Promise((res) => { settle = () => res({ status: 'ok', message: '', receipt: [] }); }));
    stopTurn(h.d);
    await flush();
    expect(refreshCanvasChanges).not.toHaveBeenCalled();
    settle(); await flush();
    expect(refreshCanvasChanges).toHaveBeenCalledTimes(1);
    expect(refreshCanvasChanges).toHaveBeenCalledWith(h.tabs.get('A')!.sessionId);
  });

  it('a synchronous throw before the request goes out does not leave Stop disabled', () => {
    const h = running();
    h.stores.A.endStreaming = () => { throw new Error('boom'); };
    expect(() => stopTurn(h.d)).toThrow('boom');
    expect(h.stopSession).not.toHaveBeenCalled();
    expect(h.tabs.get('A')!._stopInFlight).toBe(false);
  });

  it('Stop ends a send still preparing, with and without a session', () => {
    for (const sessionId of [undefined, 'S']) {
      const h = setup({ tabs: [tab('A', { sessionId, _sendToken: 1, _preparingSend: 1 } as Partial<UnifiedTab>)] });
      stopTurn(h.d);
      const t = h.tabs.get('A')!;
      expect([t._sendToken, t._preparingSend, t.userStopped], `session ${sessionId}`).toEqual([undefined, undefined, true]);
      expect(h.tabs.get('A')!.localSending).toBe(false);
      expect(h.stopSession).toHaveBeenCalledTimes(sessionId ? 1 : 0);
    }
  });

  it('the background stop is one request at a time and lifts its guard when it settles', async () => {
    const h = setup({ tabs: [tab('A', { sessionId: 'S' })] });
    stopBackground(h.d, 'A'); stopBackground(h.d, 'A');
    expect(h.stopSession).toHaveBeenCalledTimes(1);
    await flush();
    stopBackground(h.d, 'A');
    expect(h.stopSession).toHaveBeenCalledTimes(2);
  });
});

// ── A Hive session (design 2026-10-29-swarm-out-hive-session-tab §7) ──
describe('sendMessage to a Hive session', () => {
  beforeEach(async () => { (await import('../../../services/remoteSession')).setRemote(null); });

  const pathAtt = { id: 'p', name: 'a.md', type: 'text', deliveryStrategy: 'path_hint', workspacePath: 'Knowledge/a.md' } as unknown as UnifiedAttachment;
  const upload = { id: 'u', name: 'b.png', type: 'image', deliveryStrategy: 'base64_image', base64: 'x' } as unknown as UnifiedAttachment;
  const termHint = { ...termAtt('$ ls'), deliveryStrategy: 'terminal_context' } as unknown as UnifiedAttachment;

  it.each([
    ['a /plugin command', '/plugin install x', [] as UnifiedAttachment[]],
    ['a file named by a path on this machine', 'look', [pathAtt]],
    ["this machine's terminal output", 'look', [termHint]],
  ])('refuses %s with a toast, the draft kept, nothing sent', async (_name, text, attachments) => {
    const { setRemote } = await import('../../../services/remoteSession');
    setRemote('H');
    const toasts: string[] = [];
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail?.message);
    document.addEventListener('swarm:toast', onToast);
    try {
      const h = setup({ tabs: [tab('A', { sessionId: 'H' })], text, attachments });
      await sendMessage(h.d);
      expect(toasts).toHaveLength(1);
      expect(h.d.composer.clear).not.toHaveBeenCalled();
      expect(h.d.composer.clearText).not.toHaveBeenCalled();
      expect(h.d.pluginCommand).not.toHaveBeenCalled();
      expect(h.d.setLocalSending).not.toHaveBeenCalled();
      expect(h.posts).toHaveLength(0);
    } finally {
      document.removeEventListener('swarm:toast', onToast);
    }
  });

  it('sends text and an upload to a Hive session as usual', async () => {
    const { setRemote } = await import('../../../services/remoteSession');
    setRemote('H');
    const h = setup({ tabs: [tab('A', { sessionId: 'H' })], attachments: [upload] });
    const p = sendMessage(h.d);
    h.attachmentsRead();
    await p;
    expect(h.posts).toHaveLength(1);
  });

  it('a local session with the same attachments is not refused', async () => {
    const { setRemote } = await import('../../../services/remoteSession');
    setRemote('H');
    const h = setup({ tabs: [tab('A', { sessionId: 'LOCAL' })], text: '/plugin list' });
    await sendMessage(h.d);
    expect(h.d.pluginCommand).toHaveBeenCalled();
  });
});
