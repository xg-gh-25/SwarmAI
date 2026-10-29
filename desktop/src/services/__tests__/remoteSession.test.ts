/**
 * The frontend transport of a Hive session (services/remoteSession.ts, design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §5).
 *
 * Questions: is at most one session remote, and is an id the proxy would refuse never
 * registered (R1); does every method of the §5 table make exactly its old call for any
 * other session (R2.1, design I1); for the remote session, does it go to the Host proxy
 * with the old path suffix, method and query, and does every write carry the JSON
 * content type, the user-action header and a JSON body (R3, R4.1); is the local
 * terminal never pointed at the Hive (R5); do the relay calls send this window's id
 * (R6)?
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import api from '../api';
import { chatService } from '../chat';
import { canvasSessionService } from '../canvasSession';
import { getTSCCState, getSystemPromptMetadata, getRecallSnapshot, getSecurityScan } from '../tscc';
import { getApiBaseUrl } from '../tauri';
import {
  setRemote, isRemote, remotePath, remoteWrite, relayClientId, startRelay, stopRelay,
} from '../remoteSession';

const SID = 'hive-sess-1';
const OTHER = 'local-sess-9';
const PREFIX = `/swarm-out/remote/${SID}`;
const WRITE_HEADERS = { 'Content-Type': 'application/json', 'X-Swarm-User-Action': '1' };

const get = vi.spyOn(api, 'get');
const post = vi.spyOn(api, 'post');
const del = vi.spyOn(api, 'delete');

function okResponse() {
  return { status: 200, data: { diff: 'd', artifacts: [], changes: null, workingRepo: null, receipt: [], rev: 0, rows: [] },
    headers: {} };
}

beforeEach(() => {
  setRemote(null);
  for (const m of [get, post, del]) {
    m.mockReset();
    m.mockResolvedValue(okResponse() as never);
  }
  chatService._messageEtags.clear();
  chatService._pageEtags.clear();
});

afterEach(() => setRemote(null));

// ── R1 ──

describe('which session is remote', () => {
  it('holds at most one session, replaced or cleared by the next call', () => {
    setRemote(SID);
    expect(isRemote(SID)).toBe(true);
    expect(isRemote(OTHER)).toBe(false);
    setRemote(OTHER);
    expect(isRemote(SID)).toBe(false);
    expect(isRemote(OTHER)).toBe(true);
    setRemote(null);
    expect(isRemote(OTHER)).toBe(false);
    expect(isRemote(undefined)).toBe(false);
  });

  it.each(['a/b', '..', 'a?b=1', 'a b', '', 'x'.repeat(129), '%2E%2E'])('refuses the id %j', (bad) => {
    setRemote(SID);
    setRemote(bad);
    expect(isRemote(bad)).toBe(false);
    expect(isRemote(SID)).toBe(false);
    expect(remotePath(bad, '/chat/x')).toBe('/chat/x');
  });

  it('prefixes only the remote session', () => {
    setRemote(SID);
    expect(remotePath(SID, '/chat/stream')).toBe(`${PREFIX}/chat/stream`);
    expect(remotePath(OTHER, '/chat/stream')).toBe('/chat/stream');
    expect(remotePath(undefined, '/chat/stream')).toBe('/chat/stream');
    expect(remoteWrite(SID)).toEqual({ headers: WRITE_HEADERS });
    expect(remoteWrite(OTHER)).toBeNull();
  });
});

// ── R2 + R3: the axios methods ──

type Verb = 'get' | 'post' | 'delete';
interface Row {
  name: string;
  call: (sid: string) => Promise<unknown>;
  verb: Verb;
  /** the exact arguments of the call before this change */
  local: (sid: string) => unknown[];
  /** the path after the proxy prefix (the old path) */
  path: (sid: string) => string;
  /** for GET: the config the call passes (kept unchanged when remote) */
  config?: unknown;
  /** for POST: the body the remote call sends */
  body?: unknown;
}

const msgHeaders = {};
const ROWS: Row[] = [
  { name: 'getSession', verb: 'get', call: (s) => chatService.getSession(s),
    local: (s) => [`/chat/sessions/${s}`], path: (s) => `/chat/sessions/${s}` },
  { name: 'getSessionMessages', verb: 'get', call: (s) => chatService.getSessionMessages(s),
    local: (s) => [`/chat/sessions/${s}/messages`, { headers: msgHeaders, validateStatus: expect.any(Function) }],
    path: (s) => `/chat/sessions/${s}/messages` },
  { name: 'getSessionMessagesPaginated', verb: 'get', call: (s) => chatService.getSessionMessagesPaginated(s, 50, 'm1'),
    local: (s) => [`/chat/sessions/${s}/messages?limit=50&before_id=m1`, { headers: msgHeaders, validateStatus: expect.any(Function) }],
    path: (s) => `/chat/sessions/${s}/messages?limit=50&before_id=m1` },
  { name: 'getSessionMessagesPage', verb: 'get', call: (s) => chatService.getSessionMessagesPage(s, 20, 'm2'),
    local: (s) => [`/chat/sessions/${s}/messages?turns=20&before_id=m2`, { headers: msgHeaders, validateStatus: expect.any(Function) }],
    path: (s) => `/chat/sessions/${s}/messages?turns=20&before_id=m2` },
  { name: 'getTranscriptSince', verb: 'get', call: (s) => chatService.getTranscriptSince(s, 7),
    local: (s) => [`/chat/sessions/${s}/transcript?since_rev=7`], path: (s) => `/chat/sessions/${s}/transcript?since_rev=7` },
  { name: 'getReconcileTailWithRev', verb: 'get', call: (s) => chatService.getReconcileTailWithRev(s, 30),
    local: (s) => [`/chat/sessions/${s}/messages?limit=30`, { validateStatus: expect.any(Function) }],
    path: (s) => `/chat/sessions/${s}/messages?limit=30` },
  { name: 'getSessionMessagesReconcileTail', verb: 'get', call: (s) => chatService.getSessionMessagesReconcileTail(s, 30),
    local: (s) => [`/chat/sessions/${s}/messages?limit=30`, { validateStatus: expect.any(Function) }],
    path: (s) => `/chat/sessions/${s}/messages?limit=30` },
  { name: 'getContextUsage', verb: 'get', call: (s) => chatService.getContextUsage(s),
    local: (s) => [`/chat/sessions/${s}/context-usage`], path: (s) => `/chat/sessions/${s}/context-usage` },
  { name: 'getTurnDiff', verb: 'get',
    call: (s) => chatService.getTurnDiff({ gitRoot: '/r', start: 'a', end: 'b', path: 'x.ts', sessionId: s }),
    local: () => ['/chat/turn-diff', { params: { git_root: '/r', start: 'a', end: 'b', path: 'x.ts' }, validateStatus: expect.any(Function) }],
    path: () => '/chat/turn-diff' },
  { name: 'tscc getTSCCState', verb: 'get', call: (s) => getTSCCState(s),
    local: (s) => [`/chat_threads/${s}/tscc`], path: (s) => `/chat_threads/${s}/tscc` },
  { name: 'tscc getSystemPromptMetadata', verb: 'get', call: (s) => getSystemPromptMetadata(s),
    local: (s) => [`/chat/${s}/system-prompt`], path: (s) => `/chat/${s}/system-prompt` },
  { name: 'tscc getRecallSnapshot', verb: 'get', call: (s) => getRecallSnapshot(s),
    local: (s) => [`/chat/${s}/recall`], path: (s) => `/chat/${s}/recall` },
  { name: 'tscc getSecurityScan', verb: 'get', call: (s) => getSecurityScan(s),
    local: (s) => [`/chat/${s}/security-scan`], path: (s) => `/chat/${s}/security-scan` },
  { name: 'canvas artifacts', verb: 'get', call: (s) => canvasSessionService.artifacts(s),
    local: (s) => [`/canvas/session/${s}`, { params: { parts: 'artifacts' } }], path: (s) => `/canvas/session/${s}` },
  { name: 'canvas changes', verb: 'get', call: (s) => canvasSessionService.changes(s),
    local: (s) => [`/canvas/session/${s}`, { params: { parts: 'changes' } }], path: (s) => `/canvas/session/${s}` },
  { name: 'canvas treeDiff', verb: 'get', call: (s) => canvasSessionService.treeDiff('/r', 'a', 'b', 'x.ts', s),
    local: () => ['/canvas/changes/tree-diff', { params: { root: '/r', start: 'a', end: 'b', path: 'x.ts' } }],
    path: () => '/canvas/changes/tree-diff' },
  { name: 'stopSession', verb: 'post', call: (s) => chatService.stopSession(s),
    local: (s) => [`/chat/stop/${s}`], path: (s) => `/chat/stop/${s}`, body: {} },
  { name: 'stopTask', verb: 'post', call: (s) => chatService.stopTask(s, 'task-1'),
    local: (s) => [`/chat/stop/${s}/task/task-1`], path: (s) => `/chat/stop/${s}/task/task-1`, body: {} },
  { name: 'compactSession (no instructions)', verb: 'post', call: (s) => chatService.compactSession(s),
    local: (s) => [`/chat/compact/${s}`, undefined], path: (s) => `/chat/compact/${s}`, body: {} },
  { name: 'compactSession (instructions)', verb: 'post', call: (s) => chatService.compactSession(s, 'keep it'),
    local: (s) => [`/chat/compact/${s}`, { instructions: 'keep it' }], path: (s) => `/chat/compact/${s}`,
    body: { instructions: 'keep it' } },
  { name: 'refreshSession', verb: 'post', call: (s) => chatService.refreshSession(s),
    local: (s) => [`/chat/refresh/${s}`], path: (s) => `/chat/refresh/${s}`, body: {} },
  { name: 'cancelPending', verb: 'delete', call: (s) => chatService.cancelPending(s, 'cid-1'),
    local: (s) => [`/chat/sessions/${s}/pending/cid-1`], path: (s) => `/chat/sessions/${s}/pending/cid-1` },
];

const mockOf = (v: Verb) => ({ get, post, delete: del }[v]);

describe('every §5 method', () => {
  it.each(ROWS.map((r) => [r.name, r] as const))('%s: another session makes its old call', async (_n, row) => {
    setRemote(SID);                       // a Hive session is open, but this call is not for it
    await row.call(OTHER).catch(() => {});   // only the request is asserted; the fake answer fits few readers
    const m = mockOf(row.verb);
    expect(m).toHaveBeenCalledTimes(1);
    expect(m.mock.calls[0]).toEqual(row.local(OTHER));
    setRemote(null);                      // and with no Hive session at all
    m.mockClear();
    await row.call(OTHER).catch(() => {});   // only the request is asserted; the fake answer fits few readers
    expect(m.mock.calls[0]).toEqual(row.local(OTHER));
  });

  it.each(ROWS.map((r) => [r.name, r] as const))('%s: the remote session goes to the proxy', async (_n, row) => {
    setRemote(SID);
    await row.call(SID).catch(() => {});
    const m = mockOf(row.verb);
    expect(m).toHaveBeenCalledTimes(1);
    const args = m.mock.calls[0] as unknown[];
    expect(args[0]).toBe(`${PREFIX}${row.path(SID)}`);
    if (row.verb === 'get') {
      expect(args.slice(1)).toEqual(row.local(SID).slice(1));   // same config (query, validateStatus)
    } else if (row.verb === 'post') {
      expect(args[1]).toEqual(row.body);
      expect(args[2]).toEqual({ headers: WRITE_HEADERS });
    } else {
      expect(args[1]).toEqual({ headers: WRITE_HEADERS, data: {} });
    }
  });
});

// ── R3.1 / R3.2: the three SSE methods ──

describe('the SSE methods', () => {
  const realFetch = global.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn().mockReturnValue(new Promise(() => {}));
    global.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => { global.fetch = realFetch; });

  const noop = () => {};
  const SSE: [string, (sid: string) => () => void, string][] = [
    ['streamChat', (s) => chatService.streamChat({ agentId: 'default', sessionId: s, message: 'hi' } as never, noop, noop, noop),
      '/chat/stream'],
    ['streamAnswerQuestion', (s) => chatService.streamAnswerQuestion(
      { agentId: 'default', sessionId: s, toolUseId: 't1', answers: { q: 'a' } }, noop, noop, noop),
    '/chat/answer-question'],
    ['streamCmdPermissionContinue', (s) => chatService.streamCmdPermissionContinue(
      { sessionId: s, requestId: 'r1', decision: 'approve' } as never, noop, noop, noop),
    '/chat/cmd-permission-continue'],
  ];

  it.each(SSE)('%s: another session posts to the local endpoint with the old headers', (_n, start, endpoint) => {
    setRemote(SID);
    const abort = start(OTHER);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${getApiBaseUrl()}/api${endpoint}`);
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body).session_id).toBe(OTHER);
    abort();
  });

  it.each(SSE)('%s: the remote session posts to the proxy with the write headers', (_n, start, endpoint) => {
    setRemote(SID);
    const abort = start(SID);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${getApiBaseUrl()}/api${PREFIX}${endpoint}`);
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual(WRITE_HEADERS);
    expect(JSON.parse(init.body).session_id).toBe(SID);
    abort();
  });
});

// ── R5 ──

describe('the local terminal', () => {
  it('is never pointed at the Hive', async () => {
    setRemote(SID);
    expect(await canvasSessionService.workingRepo(SID)).toBeNull();
    expect(get).not.toHaveBeenCalled();
    await canvasSessionService.workingRepo(OTHER);
    expect(get).toHaveBeenCalledWith(`/canvas/session/${OTHER}/working-repo`);
  });
});

// ── R6 ──

describe('the relay', () => {
  it('starts and stops with this window id and the write headers', async () => {
    post.mockResolvedValueOnce({ data: { session_id: SID, state: { state: 'streaming' } } } as never);
    expect(await startRelay(SID)).toEqual({ state: 'streaming' });
    expect(post).toHaveBeenCalledWith(`${PREFIX}/relay`, { client_id: relayClientId() }, { headers: WRITE_HEADERS });
    await stopRelay(SID);
    expect(del).toHaveBeenCalledWith(`${PREFIX}/relay`, { data: { client_id: relayClientId() }, headers: WRITE_HEADERS });
    expect(relayClientId()).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
    expect(relayClientId()).toBe(relayClientId());
  });

  it('sends relay calls in call order: a stop still in flight reaches the Host before the next start', async () => {
    const order: string[] = [];
    let finishStop: () => void = () => {};
    del.mockImplementationOnce(() => new Promise((res) => { order.push('stop-sent'); finishStop = () => { order.push('stop-done'); res({} as never); }; }));
    post.mockImplementationOnce(async () => { order.push('start-sent'); return { data: { session_id: SID, state: null } } as never; });
    const stop = stopRelay(SID);
    const start = startRelay(SID);
    await Promise.resolve(); await Promise.resolve();
    expect(order).toEqual(['stop-sent']);
    finishStop();
    await stop; await start;
    expect(order).toEqual(['stop-sent', 'stop-done', 'start-sent']);
  });

  it('a failed relay call does not stop the next one', async () => {
    del.mockRejectedValueOnce(new Error('down'));
    post.mockResolvedValueOnce({ data: { session_id: SID, state: null } } as never);
    await expect(stopRelay(SID)).rejects.toThrow('down');
    expect(await startRelay(SID)).toBeNull();
  });

  it('builds no URL from an id the proxy would refuse', async () => {
    await expect(startRelay('../x')).rejects.toThrow();
    await expect(stopRelay('a/b')).rejects.toThrow();
    expect(post).not.toHaveBeenCalled();
    expect(del).not.toHaveBeenCalled();
  });
});
