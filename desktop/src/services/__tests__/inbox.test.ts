/**
 * inbox client — the desktop mirror of backend/schemas/inbox.py.
 *
 * Invariants:
 *   - every route the schema's docstring lists has a client method that calls it
 *     (read from the real schema file, so a route added there fails here until the
 *     client has it);
 *   - `send` posts exactly once and returns a 403/409 as a refusal value with the
 *     server's reason code, message and redacted spans; a 5xx or network error is
 *     thrown, never retried;
 *   - counts read from a backend without the route (an empty / foreign body) are zeros.
 *
 * The HTTP boundary (services/api) is mocked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const calls: Array<{ method: string; url: string; body?: unknown; config?: unknown }> = [];
const respond = vi.fn<(method: string, url: string) => unknown>(() => ({ data: {}, status: 200 }));
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  const rec = (method: string) => vi.fn(async (url: string, body?: unknown, config?: unknown) => {
    calls.push({ method, url, body, config });
    const r = respond(method, url);
    if (r instanceof Error) throw r;
    return r;
  });
  return { ...actual, default: { get: rec('GET'), post: rec('POST'), put: rec('PUT'), delete: rec('DELETE') } };
});

import { inboxService, parseSendRefusal, channelKey } from '../inbox';

beforeEach(() => { calls.length = 0; respond.mockReset(); respond.mockImplementation(() => ({ data: {}, status: 200 })); });

/** Routes from the schema docstring: "GET  /api/inbox/x", "GET/POST /api/inbox/feeds". */
function schemaRoutes(): Array<{ method: string; path: string }> {
  const src = readFileSync(join(__dirname, '../../../../backend/schemas/inbox.py'), 'utf8');
  const doc = src.slice(0, src.indexOf('"""', 3));
  const out: Array<{ method: string; path: string }> = [];
  for (const m of doc.matchAll(/((?:GET|POST|PUT|DELETE)(?:\/(?:GET|POST|PUT|DELETE))*)\s+`*(\/api\/inbox[^\s`?,]*)/g)) {
    for (const method of m[1].split('/')) out.push({ method, path: m[2] });
  }
  return out;
}

const asRegex = (path: string) =>
  new RegExp(`^${path.replace(/^\/api/, '').replace(/\{[^}]+\}/g, '[^/?]+').replace(/\//g, '\\/')}(\\?.*)?$`);

describe('inbox client: the schema is covered', () => {
  it('every route in the schema has a client method', async () => {
    const routes = schemaRoutes();
    expect(routes.length).toBeGreaterThan(15); // the scan really reads the docstring
    respond.mockImplementation(() => ({ data: {}, status: 200 }));
    await inboxService.counts();
    for (const lane of ['reply', 'proposed', 'aware', 'trend'] as const) await inboxService.items(lane);
    await inboxService.target('i1');
    await inboxService.draft('i1');
    await inboxService.redraft('i1');
    await inboxService.send('i1', { draft_id: 'd1', text: 'hi', target_sha: 'sha' });
    await inboxService.action('i1', 'later');
    await inboxService.sends('all');
    await inboxService.notSent('s1');
    await inboxService.resend('s1');
    await inboxService.trendSummary();
    await inboxService.sources();
    await inboxService.retrySource('slack');
    await inboxService.refresh();
    await inboxService.listFeeds();
    await inboxService.addFeed({ id: 'f', name: 'F', type: 'rss' });
    await inboxService.updateFeed('f', { enabled: false });
    await inboxService.deleteFeed('f');
    await inboxService.addFeedMember('f', 'https://a/b');
    await inboxService.deleteFeedMember('f', 'https://a/b');
    const missing = routes.filter((r) => !calls.some((c) => c.method === r.method && asRegex(r.path).test(c.url)));
    expect(missing).toEqual([]);
    // the client never adds a second /api (the axios instance prepends it)
    expect(calls.filter((c) => c.url.startsWith('/api/'))).toEqual([]);
  });

  it('sends the request body the schema names (draft_id, text, target_sha) and the action', async () => {
    await inboxService.send('i 1', { draft_id: null, text: 'hello', target_sha: 'abc' });
    await inboxService.action('i 1', 'dismiss');
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/inbox/items/i%201/send', body: { draft_id: null, text: 'hello', target_sha: 'abc' } });
    expect(calls[1]).toMatchObject({ method: 'POST', url: '/inbox/items/i%201/action', body: { action: 'dismiss' } });
  });
});

describe('inbox client: writes say they come from the app', () => {
  it('every write carries X-Swarm-User-Action', async () => {
    await inboxService.redraft('i1');
    await inboxService.send('i1', { draft_id: null, text: 'x', target_sha: 's' });
    await inboxService.action('i1', 'done');
    await inboxService.notSent('s1');
    await inboxService.resend('s1');
    await inboxService.retrySource('mail');
    await inboxService.refresh();
    await inboxService.addFeed({ id: 'f', name: 'F', type: 'rss' });
    await inboxService.updateFeed('f', { tier: 'x' });
    await inboxService.deleteFeed('f');
    await inboxService.addFeedMember('f', 'v');
    await inboxService.deleteFeedMember('f', 'v');
    expect(calls).toHaveLength(12);
    for (const c of calls) {
      const cfg = (c.method === 'DELETE' ? c.body : c.config) as { headers?: Record<string, string> } | undefined;
      expect([c.url, cfg?.headers?.['X-Swarm-User-Action']]).toEqual([c.url, expect.stringMatching(/^inbox-/)]);
    }
  });
});

describe('inbox client: send', () => {
  it('send returns a refusal for 409 and posts once', async () => {
    respond.mockImplementation(() => ({ status: 409, data: { detail: { code: 'redacted', message: 'Looks like a key', spans: [[2, 5], { start: 7, end: 9 }] } } }));
    const r = await inboxService.send('i1', { draft_id: 'd', text: 'x', target_sha: 's' });
    expect(r).toEqual({ ok: false, refusal: { status: 409, code: 'redacted', message: 'Looks like a key', spans: [{ start: 2, end: 5 }, { start: 7, end: 9 }] } });
    expect(calls.filter((c) => c.url.endsWith('/send'))).toHaveLength(1);
    // 403 / 409 are accepted as answers; anything else still fails the request
    const cfg = calls[0].config as { validateStatus: (s: number) => boolean };
    expect([200, 403, 409].map(cfg.validateStatus)).toEqual([true, true, true]);
    expect([400, 500, 502].map(cfg.validateStatus)).toEqual([false, false, false]);
  });

  it('a 403 with a sentence is a forbidden refusal carrying that sentence', async () => {
    respond.mockImplementation(() => ({ status: 403, data: { detail: 'caller is not the desktop UI' } }));
    const r = await inboxService.send('i1', { draft_id: null, text: 'x', target_sha: 's' });
    expect(r).toEqual({ ok: false, refusal: { status: 403, code: 'forbidden', message: 'caller is not the desktop UI', spans: [] } });
  });

  it('an error is thrown once and never retried', async () => {
    respond.mockImplementation(() => new Error('network down'));
    await expect(inboxService.send('i1', { draft_id: null, text: 'x', target_sha: 's' })).rejects.toThrow('network down');
    expect(calls.filter((c) => c.url.endsWith('/send'))).toHaveLength(1);
  });

  it('parses every refusal form the router may use', () => {
    expect(parseSendRefusal(409, { detail: 'target_changed' }).code).toBe('target_changed');
    expect(parseSendRefusal(409, { detail: { reason: 'not_open' } }).code).toBe('not_open');
    expect(parseSendRefusal(409, { code: 'redacted', message: 'm', spans: [[0, 3]] })).toEqual({ status: 409, code: 'redacted', message: 'm', spans: [{ start: 0, end: 3 }] });
    expect(parseSendRefusal(409, null).code).toBe('conflict');
    // malformed spans are dropped, not trusted
    expect(parseSendRefusal(409, { detail: { code: 'redacted', spans: [[5, 2], ['a', 3], [-1, 2]] } }).spans).toEqual([]);
  });
});

describe('inbox client: tolerant reads', () => {
  it('counts from a body that is not the schema are zeros', async () => {
    respond.mockImplementation(() => ({ status: 200, data: [] }));
    expect(await inboxService.counts()).toEqual({
      reply: 0, proposed: 0, aware: 0, trend: 0, sends_need_you: 0, skipped: 0, unhealthy_sources: [], updated_at: null,
    });
  });

  it('feeds are read from {sources} like Community and mapped to its shape', async () => {
    respond.mockImplementation(() => ({ status: 200, data: { sources: [{ id: 'a', name: 'A', type: 'rss', tier: 't', enabled: true, managed_by: 'user', member_kind: 'urls', members: ['u'], member_count: 1 }] } }));
    expect(await inboxService.listFeeds()).toEqual([{ id: 'a', name: 'A', type: 'rss', tier: 't', enabled: true, managedBy: 'user', members: ['u'], memberCount: 1, membersTruncated: false, memberKind: 'urls', tags: [] }]);
  });

  it('channelKey maps every source kind to a channel colour family', () => {
    expect(['slack', 'mail', 'github', 'feed', 'feed:rss', 'jira', ''].map(channelKey)).toEqual(['slack', 'mail', 'github', 'feed', 'feed', 'other', 'other']);
  });
});
