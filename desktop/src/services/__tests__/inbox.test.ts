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
  return { ...actual, default: { get: rec('GET'), post: rec('POST'), put: rec('PUT'), patch: rec('PATCH'), delete: rec('DELETE') } };
});

import { inboxService, parseSendRefusal, channelKey } from '../inbox';

beforeEach(() => { calls.length = 0; respond.mockReset(); respond.mockImplementation(() => ({ data: {}, status: 200 })); });

/** Routes from the schema docstring: "GET  /api/inbox/x", "GET/POST /api/inbox/feeds". */
function schemaRoutes(): Array<{ method: string; path: string }> {
  const src = readFileSync(join(__dirname, '../../../../backend/schemas/inbox.py'), 'utf8');
  const doc = src.slice(0, src.indexOf('"""', 3));
  const out: Array<{ method: string; path: string }> = [];
  for (const m of doc.matchAll(/((?:GET|POST|PUT|PATCH|DELETE)(?:\/(?:GET|POST|PUT|PATCH|DELETE))*)\s+`*(\/api\/inbox[^\s`?,]*)/g)) {
    for (const method of m[1].split('/')) out.push({ method, path: m[2] });
  }
  return out;
}

const asRegex = (path: string) =>
  new RegExp(`^${path.replace(/^\/api/, '').replace(/\{[^}]+\}/g, '[^/?]+').replace(/\//g, '\\/')}(\\?.*)?$`);

describe('inbox client: the schema is covered', () => {
  it('every route in the schema has a client method', async () => {
    const routes = schemaRoutes();
    expect(routes.length).toBeGreaterThan(20); // the scan really reads the docstring
    expect(routes).toContainEqual({ method: 'PATCH', path: '/api/inbox/sources/{id}' });
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
    await inboxService.availableSources();
    await inboxService.addSource({ kind: 'slack', backend: 'slack-web', secrets: { user_token: 't' } });
    await inboxService.updateSource('slack-1', { enabled: false });
    await inboxService.removeSource('slack-1');
    await inboxService.setSourceSecret('slack-1', 'user_token', 't');
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
    await inboxService.addSource({ kind: 'slack', backend: 'slack-web' });
    await inboxService.updateSource('s', { interval_s: 900 });
    await inboxService.removeSource('s');
    await inboxService.setSourceSecret('s', 'user_token', 'v');
    expect(calls).toHaveLength(16);
    for (const c of calls) {
      const cfg = (c.method === 'DELETE' ? c.body : c.config) as { headers?: Record<string, string> } | undefined;
      expect([c.url, cfg?.headers?.['X-Swarm-User-Action']]).toEqual([c.url, expect.stringMatching(/^inbox-/)]);
    }
  });
});

describe('inbox client: send', () => {
  it('send returns a refusal for 409 and posts once', async () => {
    respond.mockImplementation(() => ({ status: 409, data: { detail: { code: 'redacted', message: 'Looks like a key', spans: [{ start: 2, end: 5, replacement: '[x]' }, { start: 7, end: 9, replacement: '' }], target: null, reason: '' } } }));
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

  it('reads the SendRefusalOut body of the contract', () => {
    const target = { label: 'x', recipients: ['a@b'], public: false, flags: [], target_sha: 'n', can_send: true };
    expect(parseSendRefusal(409, { detail: { code: 'target_changed', message: 'm', spans: [], target, reason: '' } }))
      .toEqual({ status: 409, code: 'target_changed', message: 'm', spans: [], target });
    expect(parseSendRefusal(403, { detail: { code: 'forbidden', message: 'only the desktop UI may send', spans: [], target: null, reason: 'backend_descendant' } }))
      .toEqual({ status: 403, code: 'forbidden', message: 'only the desktop UI may send', spans: [], reason: 'backend_descendant' });
    expect(parseSendRefusal(409, null).code).toBe('conflict');
    // malformed spans are dropped, not trusted
    expect(parseSendRefusal(409, { detail: { code: 'redacted', spans: [{ start: 5, end: 2 }, { start: 'a', end: 3 }, { start: -1, end: 2 }, [0, 3]] } }).spans).toEqual([]);
  });
});

describe('inbox client: sources', () => {
  it('addSource returns a refusal with the fix as a value, posting once', async () => {
    respond.mockImplementation(() => ({ status: 400, data: { detail: { code: 'whoami_failed', message: 'login expired', fix: 'run mwinit -f' } } }));
    const r = await inboxService.addSource({ kind: 'slack', backend: 'mcp:s', options: { tool_family: 'slack-mcp' } });
    expect(r).toEqual({ ok: false, refusal: { status: 400, code: 'whoami_failed', message: 'login expired', fix: 'run mwinit -f' } });
    expect(calls).toHaveLength(1);
    const cfg = calls[0].config as { validateStatus: (s: number) => boolean };
    expect([200, 400, 409, 422, 503].map(cfg.validateStatus)).toEqual([true, true, true, true, true]);
    expect([403, 500].map(cfg.validateStatus)).toEqual([false, false]);
  });

  it('a secret goes only in the request body, and sources call their routes', async () => {
    await inboxService.setSourceSecret('s 1', 'user_token', 'xoxp-1');
    await inboxService.updateSource('s 1', { watched_channels: ['#a'] });
    await inboxService.availableSources(true);
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/inbox/sources/s%201/secret', body: { name: 'user_token', value: 'xoxp-1' } });
    expect(calls[1]).toMatchObject({ method: 'PATCH', url: '/inbox/sources/s%201', body: { watched_channels: ['#a'] } });
    expect(calls[2]).toMatchObject({ method: 'GET', url: '/inbox/sources/available?refresh=true' });
    // listing starts the configured MCP servers: it says it comes from the app as well
    expect((calls[2].body as { headers?: Record<string, string> }).headers?.['X-Swarm-User-Action']).toMatch(/^inbox-/);
    expect(calls.map((c) => c.url).join(' ')).not.toContain('xoxp-1');
  });
});

describe('inbox client: tolerant reads', () => {
  it('counts from a body that is not the schema are zeros', async () => {
    respond.mockImplementation(() => ({ status: 200, data: [] }));
    expect(await inboxService.counts()).toEqual({
      reply: 0, proposed: 0, aware: 0, trend: 0, sends_need_you: 0, skipped: 0, unhealthy_sources: [], updated_at: null,
    });
  });

  it('feeds are read from {sources} and mapped to the FeedSource shape', async () => {
    respond.mockImplementation(() => ({ status: 200, data: { sources: [{ id: 'a', name: 'A', type: 'rss', tier: 't', enabled: true, managed_by: 'user', member_kind: 'urls', members: ['u'], member_count: 1 }] } }));
    expect(await inboxService.listFeeds()).toEqual([{ id: 'a', name: 'A', type: 'rss', tier: 't', enabled: true, managedBy: 'user', members: ['u'], memberCount: 1, membersTruncated: false, memberKind: 'urls', tags: [] }]);
  });

  it('channelKey maps every source kind to a channel colour family', () => {
    expect(['slack', 'mail', 'github', 'feed', 'feed:rss', 'jira', ''].map(channelKey)).toEqual(['slack', 'mail', 'github', 'feed', 'feed', 'other', 'other']);
  });
});
