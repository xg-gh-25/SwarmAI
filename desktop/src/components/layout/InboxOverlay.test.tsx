/**
 * InboxOverlay — the Inbox surface against a mocked Inbox API (design
 * docs/design/2026-11-20-inbox.md §7-§8; mockup docs/design/2026-11-20-inbox-mockup.html).
 *
 * Invariants under test:
 *   - four tabs with the counts from /counts; the 已发 tab carries an alert count when a
 *     send needs the person, else the drafts waiting on approval as a plain count;
 *     switching tabs shows that lane only;
 *   - 等你回复 lists lane `reply` only; the proposed drafts are reached from 已发 → 去批准,
 *     in the same two panes with "← 已发", where 不用回 has only 不发了;
 *   - 不用回 ▾ sends dismiss / to_aware / mute_sender, 稍后 ▾ sends later with its until;
 *     a mute hides every row of that sender in that source and selects the next one;
 *     the menu is a role=menu that arrow keys move through and Escape closes, and the
 *     lane's keys (E / H / J / K, Cmd+Enter) do nothing while it has focus;
 *   - after a row is handled focus goes to the row now selected;
 *   - a row standing for a thread shows "+N";
 *   - 需要知道: needs_action items first, by due date (no date last), with a due chip
 *     that is red when past and warns today and up to 3 days ahead; the rest grouped by
 *     day, and a day's 这一天的都知道了 asks for a second click, then calls doneMany with
 *     that day's ids, hides them and reports the count the server marked;
 *   - a send posts once with the draft id, the edited text and the target_sha shown,
 *     then selects the next item;
 *   - a 409 target_changed / redacted (or a 403) is shown under the draft with the
 *     server's reason, the redacted spans are marked, and nothing is sent again;
 *     target_changed fetches the target again;
 *   - send is off while the target cannot send;
 *   - keys J / K move, E dismisses, H snoozes 3 hours, Cmd/Ctrl+Enter sends;
 *   - 已发: resend is disabled with 修复渠道 when blocked_by_source; 有回复 jumps to the
 *     item in 等你回复;
 *   - the unhealthy banner's fix and the gear open the channel settings page;
 *   - narrow (< 720 px): one pane at a time, with a back button; after an action the
 *     detail stays open on the next item, the list returns only when none is left;
 *   - send-to-chat dispatches the item as fenced external data and closes.
 *
 * The Inbox service is mocked at the boundary; the overlay's queries are the real ones.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InboxCounts, ItemOut, SendOut, SendResult, SendsOut, TargetOut, DraftOut } from '../../services/inbox';

const svc = vi.hoisted(() => ({
  counts: vi.fn<() => Promise<InboxCounts>>(),
  items: vi.fn<(lane: string) => Promise<ItemOut[]>>(),
  target: vi.fn<(id: string) => Promise<TargetOut>>(),
  draft: vi.fn<(id: string) => Promise<DraftOut>>(),
  redraft: vi.fn<(id: string) => Promise<DraftOut>>(),
  send: vi.fn<(id: string, body: unknown) => Promise<SendResult>>(),
  action: vi.fn<(id: string, action: string, until?: string) => Promise<ItemOut>>(),
  doneMany: vi.fn<(ids: string[]) => Promise<number>>(),
  sends: vi.fn<(filter: string) => Promise<SendsOut>>(),
  notSent: vi.fn(),
  resend: vi.fn(),
  trendSummary: vi.fn(),
  sources: vi.fn(),
  retrySource: vi.fn(),
  availableSources: vi.fn(), addSource: vi.fn(), updateSource: vi.fn(), removeSource: vi.fn(), setSourceSecret: vi.fn(),
  refresh: vi.fn(),
  listFeeds: vi.fn(),
  addFeed: vi.fn(), updateFeed: vi.fn(), deleteFeed: vi.fn(), addFeedMember: vi.fn(), deleteFeedMember: vi.fn(),
}));
vi.mock('../../services/inbox', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/inbox')>();
  return { ...actual, inboxService: svc };
});

import { InboxContent } from './InboxOverlay';
import { ApiError } from '../../services/api';

// ── fixtures ─────────────────────────────────────────────────────────────────
const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const item = (id: string, over: Partial<ItemOut> = {}): ItemOut => ({
  id, lane: 'reply', state: 'open', source_id: 'slack-1', source_kind: 'slack', channel_label: 'Slack',
  author: `Person ${id}`, author_id: `u_${id}`, author_role: 'Your manager', where: 'Slack DM', received_at: ago(25 * 60_000), late: false,
  ask: `Ask of ${id}`, ask_sub: '', excerpt: `Message of ${id}`, url: '', priority: 1, producer: null,
  needs_action: false, due: null, thread_count: 1, ...over,
});
const REPLY = [item('r1'), item('r2', { source_kind: 'mail', channel_label: 'Mail', late: true, received_at: ago(3 * 86_400_000) })];
const PROPOSED = [item('p1', { lane: 'proposed', source_kind: 'github', channel_label: 'GitHub', producer: 'github-outreach' })];
const AWARE = [item('a1', { lane: 'aware', ask: 'Standards change next Monday' })];
const counts = (over: Partial<InboxCounts> = {}): InboxCounts => ({
  reply: 2, proposed: 1, aware: 1, trend: 0, sends_need_you: 2, skipped: 0, unhealthy_sources: [], updated_at: ago(60_000), ...over,
});
const target = (id: string, over: Partial<TargetOut> = {}): TargetOut => ({
  label: `Slack DM · ${id}`, recipients: [id], public: false, flags: [], target_sha: `sha-${id}`, can_send: true, ...over,
});
const draft = (id: string, text = `Draft for ${id}`): DraftOut => ({ id: `d-${id}`, item_id: id, text, lang: 'en', note: 'based on 6 messages', created_at: ago(0) });
const sendOut = (over: Partial<SendOut> = {}): SendOut => ({
  id: 's-new', item_id: 'r1', origin: 'click', producer: null, status: 'sent', to_label: 'Person r1', source_kind: 'slack',
  channel_label: 'Slack', text: 'x', url: '', created_at: ago(0), sent_at: ago(0), error: '', blocked_by_source: false,
  has_reply: false, reply_item_id: null, ...over,
});
const SENDS: SendsOut = {
  need_you: [
    sendOut({ id: 'u1', status: 'unknown', to_label: 'Chen Mo' }),
    sendOut({ id: 'u2', status: 'failed', to_label: 'Sam Ortiz', source_kind: 'mail', channel_label: 'Mail', blocked_by_source: true }),
  ],
  sent: [
    sendOut({ id: 's1', origin: 'auto', to_label: 'agentkit #4790', source_kind: 'github', channel_label: 'GitHub', has_reply: true, reply_item_id: 'r2' }),
    sendOut({ id: 's2', to_label: 'Alex Kim' }),
  ],
  total_7d: 6, auto_7d: 2,
};

let roWidth = 0;
class ResizeObserverStub {
  cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) { this.cb = cb; }
  observe(el: Element) { if (roWidth) this.cb([{ contentRect: { width: roWidth } } as ResizeObserverEntry], this as unknown as ResizeObserver); void el; }
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  roWidth = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
  svc.counts.mockResolvedValue(counts());
  svc.items.mockImplementation(async (lane) => (lane === 'reply' ? REPLY : lane === 'proposed' ? PROPOSED : lane === 'aware' ? AWARE : []));
  svc.target.mockImplementation(async (id) => target(id));
  svc.draft.mockImplementation(async (id) => draft(id));
  svc.send.mockResolvedValue({ ok: true, send: sendOut() });
  svc.action.mockImplementation(async (id) => item(id));
  svc.doneMany.mockImplementation(async (ids) => ids.length);
  svc.sends.mockResolvedValue(SENDS);
  svc.trendSummary.mockResolvedValue({ generated_at: ago(0), points: [], feeds: 0, watched_repos: 0 });
  svc.sources.mockResolvedValue([]);
  svc.listFeeds.mockResolvedValue([]);
  svc.availableSources.mockResolvedValue([]);
  svc.refresh.mockResolvedValue(counts());
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function setup() {
  const close = vi.fn();
  const onDispatch = vi.fn(() => true);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><InboxContent close={close} onDispatch={onDispatch} /></QueryClientProvider>);
  return { close, onDispatch };
}

const selectedRow = () => screen.getAllByTestId('inbox-item').find((b) => b.getAttribute('aria-selected') === 'true');
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });

async function draftReady(id: string) {
  await waitFor(() => expect(screen.getByTestId('inbox-draft')).toHaveValue(`Draft for ${id}`));
  await waitFor(() => expect(screen.getByTestId('inbox-target')).toHaveTextContent(`Slack DM · ${id}`));
}

// ── tabs ─────────────────────────────────────────────────────────────────────
describe('Inbox tabs', () => {
  it('shows the four tabs with their counts and switches lanes', async () => {
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-tab-reply-count')).toHaveTextContent('2'));
    expect(screen.getByTestId('inbox-tab-aware-count')).toHaveTextContent('1');
    expect(screen.queryByTestId('inbox-tab-trend-count')).toBeNull();
    expect(screen.getByTestId('inbox-tab-sent-count')).toHaveTextContent('2');
    expect(screen.getByTestId('inbox-list')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('inbox-tab-aware'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-row')).toHaveLength(1));
    expect(screen.queryByTestId('inbox-list')).toBeNull();
    expect(screen.getByTestId('inbox-tab-aware')).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(screen.getByTestId('inbox-tab-trend'));
    await waitFor(() => expect(screen.getByTestId('inbox-trend')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('inbox-tab-sent'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-need-card')).toHaveLength(2));
    expect(screen.queryByTestId('inbox-aware')).toBeNull();
  });

  it('the sent tab has no count when no send needs the person and no draft waits', async () => {
    svc.counts.mockResolvedValue(counts({ sends_need_you: 0, proposed: 0 }));
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-tab-reply-count')).toHaveTextContent('2'));
    expect(screen.queryByTestId('inbox-tab-sent-count')).toBeNull();
  });

  it('the sent tab counts the drafts waiting on approval, as a plain count, when no send needs the person', async () => {
    svc.counts.mockResolvedValue(counts({ sends_need_you: 0, proposed: 3 }));
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-tab-sent-count')).toHaveTextContent('3'));
    expect(screen.getByTestId('inbox-tab-sent-count').getAttribute('style') ?? '').not.toMatch(/status-error/);
  });

  it('a send that needs the person wins: the alert count, not the drafts', async () => {
    svc.counts.mockResolvedValue(counts({ sends_need_you: 2, proposed: 5 }));
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-tab-sent-count')).toHaveTextContent('2'));
    expect(screen.getByTestId('inbox-tab-sent-count').getAttribute('style')).toMatch(/status-error/);
  });

  it('refresh pulls the sources once and reloads', async () => {
    setup();
    await waitFor(() => expect(svc.counts).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('inbox-refresh'));
    await waitFor(() => expect(svc.refresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(svc.counts).toHaveBeenCalledTimes(2));
  });
});

// ── reply lane ───────────────────────────────────────────────────────────────
describe('Inbox reply lane', () => {
  it('lists the reply items only, in server order, with channel pills and late times', async () => {
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(2));
    const rows = screen.getAllByTestId('inbox-item');
    expect(rows.map((r) => r.getAttribute('data-item-id'))).toEqual(['r1', 'r2']);
    expect(screen.queryByTestId('inbox-proposed-label')).toBeNull();
    expect(screen.getByTestId('inbox-keys')).toHaveTextContent(/H\s*later/);
    expect(within(rows[1]).getByTestId('inbox-channel-pill')).toHaveAttribute('data-channel', 'mail');
    expect(within(rows[1]).getByTestId('inbox-item-time')).toHaveAttribute('data-late', 'true');
    expect(within(rows[0]).getByTestId('inbox-item-time')).not.toHaveAttribute('data-late');
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
    await draftReady('r1');
    expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r1');
  });

  it('send success advancing to the next item', async () => {
    setup();
    await draftReady('r1');
    fireEvent.change(screen.getByTestId('inbox-draft'), { target: { value: 'Edited reply' } });
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2'));
    expect(svc.send).toHaveBeenCalledTimes(1);
    expect(svc.send).toHaveBeenCalledWith('r1', { draft_id: 'd-r1', text: 'Edited reply', target_sha: 'sha-r1' });
    expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['r2']);
    expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r2');
    expect(screen.getByTestId('inbox-status')).toHaveTextContent('Person r1');
  });

  it('a second press while a send is in flight sends nothing more', async () => {
    let finish: (r: SendResult) => void = () => {};
    svc.send.mockImplementation(() => new Promise<SendResult>((res) => { finish = res; }));
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    fireEvent.click(screen.getByTestId('inbox-send'));
    fireEvent.keyDown(screen.getByTestId('inbox-draft'), { key: 'Enter', metaKey: true });
    expect(svc.send).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ ok: true, send: sendOut() }); });
  });

  it('409 target_changed shows the reason, refetches the target and sends nothing more', async () => {
    svc.send.mockResolvedValue({ ok: false, refusal: { status: 409, code: 'target_changed', message: 'The recipients changed', spans: [] } });
    setup();
    await draftReady('r1');
    const targetCalls = svc.target.mock.calls.filter(([id]) => id === 'r1').length;
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-send-error')).toHaveTextContent('The recipients changed'));
    expect(screen.getByTestId('inbox-send-error')).toHaveAttribute('data-code', 'target_changed');
    await waitFor(() => expect(svc.target.mock.calls.filter(([id]) => id === 'r1').length).toBeGreaterThan(targetCalls));
    await settle();
    expect(svc.send).toHaveBeenCalledTimes(1);
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
  });

  it('409 redacted marks the spans and nothing is re-sent', async () => {
    svc.send.mockResolvedValue({ ok: false, refusal: { status: 409, code: 'redacted', message: '', spans: [{ start: 6, end: 9 }] } });
    setup();
    await draftReady('r1');
    fireEvent.change(screen.getByTestId('inbox-draft'), { target: { value: 'token AKI rest' } });
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-send-error')).toHaveTextContent(/looks like a secret/i));
    expect(screen.getAllByTestId('inbox-redacted-span').map((m) => m.textContent)).toEqual(['AKI']);
    await settle();
    expect(svc.send).toHaveBeenCalledTimes(1);
    // editing clears the refusal; nothing is sent until the person presses again
    fireEvent.change(screen.getByTestId('inbox-draft'), { target: { value: 'token rest' } });
    expect(screen.queryByTestId('inbox-send-error')).toBeNull();
    await settle();
    expect(svc.send).toHaveBeenCalledTimes(1);
  });

  it('a 403 shows its reason inline and is not retried', async () => {
    svc.send.mockResolvedValue({ ok: false, refusal: { status: 403, code: 'forbidden', message: '', spans: [] } });
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-send-error')).toHaveTextContent(/only the desktop app/i));
    await settle();
    expect(svc.send).toHaveBeenCalledTimes(1);
  });

  it('reopening an item resolves its target again before Send is on', async () => {
    setup();
    await draftReady('r1');
    fireEvent.keyDown(document.body, { key: 'j' });
    await draftReady('r2');
    let resolveR1: (t: TargetOut) => void = () => {};
    svc.target.mockImplementation((id) => (id === 'r1' ? new Promise<TargetOut>((res) => { resolveR1 = res; }) : Promise.resolve(target(id))));
    fireEvent.keyDown(document.body, { key: 'k' });
    await waitFor(() => expect(screen.getByTestId('inbox-target')).toHaveTextContent(/checking/i));
    expect(screen.getByTestId('inbox-send')).toBeDisabled();
    await act(async () => { resolveR1(target('r1', { target_sha: 'sha-r1-new', label: 'Slack DM · r1 (new)' })); });
    await waitFor(() => expect(screen.getByTestId('inbox-send')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(svc.send).toHaveBeenCalledWith('r1', expect.objectContaining({ target_sha: 'sha-r1-new' })));
  });

  it('a reserved answer is not reported as sent', async () => {
    svc.send.mockResolvedValue({ ok: true, send: sendOut({ status: 'reserved' }) });
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent(/not sure/i));
  });

  // The shared api client rejects every failure as an ApiError; a lost response is one
  // with status 500 (services/api.ts parseApiError), so these are the real shapes.
  it.each([
    ['no answer (network)', () => new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'Unable to connect' }, 500)],
    ['a 5xx', () => new ApiError({ code: 'SERVER_ERROR', message: 'Server error' }, 502)],
    ['a non-api error', () => new Error('socket hang up')],
  ])('a send that fails with %s says the outcome is unclear and is not retried', async (_name, err) => {
    svc.send.mockRejectedValue(err());
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-send-error')).toHaveTextContent(/may have gone out/i));
    expect(screen.getByTestId('inbox-send-error')).not.toHaveTextContent(/not sent/i);
    await settle();
    expect(svc.send).toHaveBeenCalledTimes(1);
  });

  it('a 4xx the router explains shows that it was not sent, with the reason', async () => {
    svc.send.mockRejectedValue(new ApiError({ code: 'VALIDATION_FAILED', message: 'text must not be empty' }, 422));
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getByTestId('inbox-send-error')).toHaveTextContent('Not sent: text must not be empty'));
    expect(svc.send).toHaveBeenCalledTimes(1);
  });

  it('redacted spans count code points, as the Python router does', async () => {
    // "🙂 token AKI": in code points AKI is [8, 11); in UTF-16 units it is [9, 12)
    svc.send.mockResolvedValue({ ok: false, refusal: { status: 409, code: 'redacted', message: 'secret', spans: [{ start: 8, end: 11 }] } });
    setup();
    await draftReady('r1');
    fireEvent.change(screen.getByTestId('inbox-draft'), { target: { value: '🙂 token AKI' } });
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-redacted-span').map((m) => m.textContent)).toEqual(['AKI']));
  });

  it('send is off while the target cannot send, and the target flags show in red', async () => {
    svc.target.mockImplementation(async (id) => target(id, { can_send: false, flags: ['Reply-To differs from From'] }));
    setup();
    await draftReady('r1');
    expect(screen.getByTestId('inbox-send')).toBeDisabled();
    expect(screen.getByTestId('inbox-target-flag')).toHaveTextContent('Reply-To differs from From');
  });

  it('keys: J / K move, E dismisses and selects the next, Cmd+Enter sends', async () => {
    setup();
    await draftReady('r1');
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2');
    fireEvent.keyDown(document.body, { key: 'k' });
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
    // typing in the draft is not a shortcut
    fireEvent.keyDown(screen.getByTestId('inbox-draft'), { key: 'j' });
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
    fireEvent.keyDown(document.body, { key: 'e' });
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r1', 'dismiss'));
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2'));
    await draftReady('r2');
    fireEvent.keyDown(screen.getByTestId('inbox-draft'), { key: 'Enter', metaKey: true });
    await waitFor(() => expect(svc.send).toHaveBeenCalledWith('r2', { draft_id: 'd-r2', text: 'Draft for r2', target_sha: 'sha-r2' }));
  });

  it('after handling a middle item the next one is selected, not the first', async () => {
    svc.items.mockImplementation(async (lane) => (lane === 'reply' ? [...REPLY, item('r3')] : lane === 'proposed' ? PROPOSED : []));
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getAllByTestId('inbox-item')[1]);
    await draftReady('r2');
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    fireEvent.click(screen.getByTestId('inbox-dismiss-elsewhere'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r2', 'dismiss'));
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r3'));
    expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['r1', 'r3']);
    expect(screen.getByTestId('inbox-status')).toHaveTextContent('Marked as handled');
    // focus is on the row now selected, not lost to <body>
    await waitFor(() => expect(selectedRow()).toHaveFocus());
  });

  it('H snoozes the selected item by 3 hours', async () => {
    setup();
    await draftReady('r1');
    fireEvent.keyDown(document.body, { key: 'h' });
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r1', 'later', '3h'));
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2'));
    expect(screen.getByTestId('inbox-status')).toHaveTextContent('You will be reminded in 3 hours');
  });

  it('the detail actions are labelled text buttons: 交给 Swarm, 稍后 ▾, 不用回 ▾', async () => {
    setup();
    await draftReady('r1');
    expect(screen.getByTestId('inbox-to-chat')).toHaveTextContent('Hand to Swarm');
    expect(screen.getByTestId('inbox-to-chat')).toHaveAttribute('title', expect.stringMatching(/new chat/));
    for (const id of ['inbox-later', 'inbox-dismiss']) {
      expect(screen.getByTestId(id)).toHaveAttribute('aria-haspopup', 'menu');
      expect(screen.getByTestId(id)).toHaveAttribute('aria-expanded', 'false');
    }
    expect(screen.getByTestId('inbox-later')).toHaveTextContent('Later');
    expect(screen.getByTestId('inbox-dismiss')).toHaveTextContent('No reply needed');
  });

  it('稍后 ▾ offers 3 hours / tomorrow morning / next Monday and sends later with that until', async () => {
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-later'));
    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['In 3 hours', 'Tomorrow morning', 'Next Monday']);
    fireEvent.click(screen.getByTestId('inbox-later-tomorrow'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r1', 'later', 'tomorrow'));
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent('You will be reminded tomorrow morning'));
    expect(screen.queryByRole('menu')).toBeNull();
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2'));
    fireEvent.click(screen.getByTestId('inbox-later'));
    fireEvent.click(screen.getByTestId('inbox-later-next_week'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r2', 'later', 'next_week'));
  });

  it('不用回 ▾: 改成「需要知道」 sends to_aware, and says the triage learns from it', async () => {
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem')).toHaveLength(3);
    fireEvent.click(screen.getByTestId('inbox-dismiss-to-aware'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r1', 'to_aware'));
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent('Moved to Good to know'));
    expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['r2']);
  });

  it('不用回 ▾: 这个发件人以后都不用回 names the sender and sends mute_sender', async () => {
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    expect(screen.getByTestId('inbox-dismiss-mute')).toHaveTextContent('This sender never needs a reply');
    expect(screen.getByTestId('inbox-dismiss-mute')).toHaveTextContent('Person r1');
    fireEvent.click(screen.getByTestId('inbox-dismiss-mute'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r1', 'mute_sender'));
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent('Messages from Person r1 no longer go to Waiting on you'));
  });

  it('a mute hides every row of that sender in that source and selects the next row still waiting', async () => {
    // matched by sender id, as the server sweeps: not by display name
    const ann = { author: 'Ann', author_id: 'U_ANN', source_id: 'slack-1' };
    svc.items.mockImplementation(async (lane) => (lane === 'reply' ? [
      item('top'), item('m1', ann), item('mid'), item('m2', { ...ann, author: 'Ann (old name)', author_id: 'u_ann' }),
      item('other-src', { ...ann, source_id: 'mail-1' }), item('namesake', { ...ann, author_id: 'U_OTHER_ANN' }),
    ] : []));
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(6));
    fireEvent.click(screen.getAllByTestId('inbox-item')[1]);
    await draftReady('m1');
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    fireEvent.click(screen.getByTestId('inbox-dismiss-mute'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('m1', 'mute_sender'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['top', 'mid', 'other-src', 'namesake']));
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('mid');
    await settle();   // the refetch keeps them hidden and the selection where it is
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('mid');
    expect(svc.action).toHaveBeenCalledTimes(1);
  });

  it('an open menu keeps the lane keys: E / H / J and Cmd+Enter typed in it do nothing', async () => {
    setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-later'));
    const first = within(screen.getByRole('menu')).getAllByRole('menuitem')[0];
    await waitFor(() => expect(first).toHaveFocus());
    for (const key of ['e', 'h', 'j', 'k']) fireEvent.keyDown(first, { key });
    fireEvent.keyDown(first, { key: 'Enter', metaKey: true });
    await settle();
    expect(svc.action).not.toHaveBeenCalled();
    expect(svc.send).not.toHaveBeenCalled();
    expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('the menu: the first choice takes focus, arrow keys move, Escape closes it without acting', async () => {
    setup();
    await draftReady('r1');
    const btn = screen.getByTestId('inbox-dismiss');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem');
    await waitFor(() => expect(items[0]).toHaveFocus());
    fireEvent.keyDown(items[0], { key: 'ArrowDown' });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1], { key: 'ArrowUp' });
    fireEvent.keyDown(items[0], { key: 'ArrowUp' });
    expect(items[2]).toHaveFocus();
    fireEvent.keyDown(items[2], { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(btn).toHaveFocus();
    // a click outside closes it too
    fireEvent.click(btn);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId('inbox-ask'));
    expect(screen.queryByRole('menu')).toBeNull();
    await settle();
    expect(svc.action).not.toHaveBeenCalled();
  });

  it('a row standing for a thread shows +N with the count in its title', async () => {
    svc.items.mockImplementation(async (lane) => (lane === 'reply' ? [item('r1', { thread_count: 3 }), item('r2')] : []));
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(2));
    const [r1, r2] = screen.getAllByTestId('inbox-item');
    expect(within(r1).getByTestId('inbox-thread-more')).toHaveTextContent('+2');
    expect(within(r1).getByTestId('inbox-thread-more')).toHaveAttribute('title', '2 more in this conversation');
    expect(within(r2).queryByTestId('inbox-thread-more')).toBeNull();
  });

  it('with only feeds set up, the reply lane offers 接入 and opens the channel settings', async () => {
    svc.items.mockResolvedValue([]);
    svc.sources.mockResolvedValue([{ id: 'feed', kind: 'feed', channel_label: '信息流', backend: 'feed', identity: '', interval_s: 21600, health: 'ok', fix: '', last_pull_at: null, last_error: '', can_send: false, detail: '22 feeds' }]);
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-reply-no-channels')).toHaveTextContent('No channel to reply in yet'));
    expect(screen.queryByTestId('inbox-reply-empty')).toBeNull();
    fireEvent.click(screen.getByTestId('inbox-add-channels'));
    await waitFor(() => expect(screen.getByTestId('inbox-channels')).toBeInTheDocument());
  });

  it('shows 都回完了 with a link to 需要知道 when nothing waits', async () => {
    svc.items.mockImplementation(async (lane) => (lane === 'aware' ? AWARE : []));
    svc.sources.mockResolvedValue([{ id: 'slack-1', kind: 'slack', channel_label: 'Slack', backend: 'mcp:slack', identity: '@me', interval_s: 900, health: 'ok', fix: '', last_pull_at: null, last_error: '', can_send: true, detail: '' }]);
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-reply-empty')).toHaveTextContent("You're all caught up"));
    fireEvent.click(screen.getByTestId('inbox-empty-go-aware'));
    await waitFor(() => expect(screen.getByTestId('inbox-aware')).toBeInTheDocument());
  });

  it('the unhealthy banner fixes through the channel settings page', async () => {
    svc.counts.mockResolvedValue(counts({ unhealthy_sources: ['mail-1'] }));
    svc.sources.mockResolvedValue([
      { id: 'mail-1', kind: 'mail', channel_label: 'Mail', backend: 'imap', identity: 'me@example.com', interval_s: 900, health: 'auth_failed', fix: 'sign in again', last_pull_at: null, last_error: '', can_send: false, detail: '' },
      { id: 'slack-1', kind: 'slack', channel_label: 'Slack', backend: 'mcp:slack', identity: '@me', interval_s: 900, health: 'ok', fix: '', last_pull_at: null, last_error: '', can_send: true, detail: '3 channels' },
    ]);
    svc.retrySource.mockResolvedValue({});
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-unhealthy-banner')).toHaveTextContent('Mail'));
    fireEvent.click(screen.getByTestId('inbox-banner-fix'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-source-row')).toHaveLength(2));
    const bad = screen.getAllByTestId('inbox-source-row').find((r) => r.getAttribute('data-health') === 'auth_failed')!;
    expect(bad).toHaveTextContent('sign in again');
    expect(screen.getAllByTestId('inbox-source-retry')).toHaveLength(1);
    fireEvent.click(within(bad).getByTestId('inbox-source-retry'));
    await waitFor(() => expect(svc.retrySource).toHaveBeenCalledWith('mail-1'));
    fireEvent.click(screen.getByTestId('inbox-channels-back'));
    await waitFor(() => expect(screen.getByTestId('inbox-list')).toBeInTheDocument());
  });

  it('send to chat hands the item over as fenced external data and closes', async () => {
    const { onDispatch, close } = setup();
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-to-chat'));
    expect(onDispatch).toHaveBeenCalledTimes(1);
    const [prompt, opts] = (onDispatch.mock.calls[0] as unknown) as [string, { autoSend: boolean }];
    expect(opts).toEqual({ autoSend: false });
    expect(prompt).toMatch(/external message/);
    expect(prompt).toMatch(/```external-message\n[\s\S]*Message of r1[\s\S]*\n```/);
    expect(close).toHaveBeenCalled();
  });
});

// ── sent lane ────────────────────────────────────────────────────────────────
describe('Inbox sent lane', () => {
  it('resend is disabled when blocked_by_source, with 修复渠道 beside it', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    const cards = await screen.findAllByTestId('inbox-need-card');
    const failed = cards.find((c) => c.getAttribute('data-status') === 'failed')!;
    expect(within(failed).getByTestId('inbox-need-resend')).toBeDisabled();
    const unknown = cards.find((c) => c.getAttribute('data-status') === 'unknown')!;
    expect(within(unknown).queryByTestId('inbox-need-resend')).toBeNull();
    fireEvent.click(within(failed).getByTestId('inbox-need-fix'));
    await waitFor(() => expect(screen.getByTestId('inbox-channels')).toBeInTheDocument());
    expect(svc.resend).not.toHaveBeenCalled();
  });

  it('a failed send whose channel works can be sent again, on a click only', async () => {
    svc.sends.mockResolvedValue({ ...SENDS, need_you: [sendOut({ id: 'u3', status: 'failed', to_label: 'Sam' })] });
    svc.resend.mockResolvedValue(sendOut({ id: 'u3' }));
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    const btn = await screen.findByTestId('inbox-need-resend');
    expect(btn).not.toBeDisabled();
    expect(svc.resend).not.toHaveBeenCalled();
    fireEvent.click(btn);
    await waitFor(() => expect(svc.resend).toHaveBeenCalledTimes(1));
  });

  it('有回复 jumps to that item in 等你回复', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    fireEvent.click(await screen.findByTestId('inbox-sent-has-reply'));
    await waitFor(() => expect(screen.getByTestId('inbox-tab-reply')).toHaveAttribute('aria-selected', 'true'));
    await waitFor(() => expect(selectedRow()?.getAttribute('data-item-id')).toBe('r2'));
    expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r2');
  });

  it.each([[0, 'wide'], [600, 'narrow']])('有回复 for an item no longer waiting says so instead of opening another one (%s px, %s)', async (width) => {
    roWidth = width;
    svc.sends.mockResolvedValue({ ...SENDS, sent: [sendOut({ id: 's9', to_label: 'X', has_reply: true, reply_item_id: 'gone-1' })] });
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    fireEvent.click(await screen.findByTestId('inbox-sent-has-reply'));
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent(/no longer waiting/i));
    expect(screen.getByTestId('inbox-tab-reply')).toHaveAttribute('aria-selected', 'true');
    // the list is shown again. Narrow: no other item's detail was opened. Wide: the
    // detail is the list's usual first item, the default selection, not one the jump picked.
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(2));
    if (width) {
      expect(screen.queryByTestId('inbox-detail')).toBeNull();
    } else {
      expect(selectedRow()?.getAttribute('data-item-id')).toBe('r1');
      expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r1');
    }
  });

  it('a link that is not http(s) is never offered', async () => {
    svc.sends.mockResolvedValue({ ...SENDS, need_you: [sendOut({ id: 'u9', status: 'unknown', url: 'javascript:alert(1)' })],
      sent: [sendOut({ id: 's8', url: 'javascript:alert(1)' }), sendOut({ id: 's7', url: 'https://example.test/m/1' })] });
    svc.trendSummary.mockResolvedValue({ generated_at: ago(0), feeds: 1, watched_repos: 0, points: [
      { title: 'Bad', why: 'w', source_label: 'RSS', item_id: null, url: 'javascript:alert(1)' },
      { title: 'Good', why: 'w', source_label: 'RSS', item_id: null, url: 'https://example.test/a' },
    ] });
    svc.items.mockImplementation(async (lane) => (lane === 'trend'
      ? [item('t1', { lane: 'trend', url: 'javascript:alert(1)' }), item('t2', { lane: 'trend', url: 'file:///etc/passwd' })]
      : lane === 'reply' ? REPLY : lane === 'proposed' ? PROPOSED : []));
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    await screen.findAllByTestId('inbox-sent-row');
    expect(screen.getAllByTestId('inbox-sent-open')).toHaveLength(1);
    expect(screen.getByTestId('inbox-need-look')).toBeDisabled();
    fireEvent.click(screen.getByTestId('inbox-tab-trend'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-trend-point')).toHaveLength(2));
    // one good point; the two folded items with javascript: / file: links offer nothing
    await waitFor(() => expect(screen.getByTestId('inbox-trend-rest')).toBeInTheDocument());
    expect(screen.getAllByTestId('inbox-trend-open')).toHaveLength(1);
  });

  it('待你批准: the drafts are not in 等你回复; 已发 → 去批准 opens them in the two panes, with only 不发了', async () => {
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['r1', 'r2']));
    fireEvent.click(screen.getByTestId('inbox-tab-sent'));
    const line = await screen.findByTestId('inbox-sent-proposed');
    expect(line).toHaveTextContent('1 comment drafted by github-outreach waits for you');
    expect(line).toHaveTextContent('nothing is sent until you click');
    // above 要你处理
    expect(line.compareDocumentPosition(screen.getAllByTestId('inbox-need-card')[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(screen.getByTestId('inbox-sent-approve'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-item').map((r) => r.getAttribute('data-item-id'))).toEqual(['p1']));
    expect(screen.getByTestId('inbox-tab-sent')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('inbox-proposed-label')).toHaveTextContent('github-outreach');
    await draftReady('p1');
    expect(screen.getByTestId('inbox-to-chat')).toBeInTheDocument();
    expect(screen.getByTestId('inbox-later')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    const choices = within(screen.getByRole('menu')).getAllByRole('menuitem');
    expect(choices.map((m) => m.textContent)).toEqual(["Don't send"]);
    fireEvent.click(screen.getByTestId('inbox-dismiss-drop'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('p1', 'dismiss'));
    await waitFor(() => expect(screen.getByTestId('inbox-proposed-empty')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('inbox-proposed-back'));
    await waitFor(() => expect(screen.getByTestId('inbox-sent')).toBeInTheDocument());
  });

  it('← 已发 leads back from the drafts to the sent page', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    fireEvent.click(await screen.findByTestId('inbox-sent-approve'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(1));
    fireEvent.click(screen.getByTestId('inbox-proposed-back'));
    await waitFor(() => expect(screen.getByTestId('inbox-sent')).toBeInTheDocument());
  });

  it('no 待你批准 line when no draft waits', async () => {
    svc.counts.mockResolvedValue(counts({ proposed: 0 }));
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    await screen.findAllByTestId('inbox-need-card');
    expect(screen.queryByTestId('inbox-sent-proposed')).toBeNull();
  });

  it('the filter reloads the sends for that filter', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-sent'));
    await screen.findAllByTestId('inbox-sent-row');
    fireEvent.click(screen.getByTestId('inbox-sent-filter-auto'));
    await waitFor(() => expect(svc.sends).toHaveBeenCalledWith('auto'));
  });
});

// ── aware lane ───────────────────────────────────────────────────────────────
describe('Inbox aware lane', () => {
  const day0 = new Date(NOW); day0.setHours(0, 0, 0, 0);
  const startToday = day0.getTime();
  const isoAt = (ms: number) => new Date(ms).toISOString();
  // today: within today even right after midnight; yesterday: its noon; older: 5 days back
  const today = (n: number) => isoAt(Math.max(startToday + n * 1000, NOW - n * 60_000));
  const yesterday = isoAt(startToday - 12 * 3_600_000);
  const older = isoAt(startToday - 5 * 86_400_000 + 12 * 3_600_000);
  const ymd = (days: number) => {
    const d = new Date(startToday); d.setDate(d.getDate() + days);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const md = (days: number) => { const [, m, d] = ymd(days).split('-'); return `${Number(m)}/${Number(d)}`; };
  const AW = [
    item('t1', { lane: 'aware', received_at: today(1), thread_count: 4 }),
    item('x-none', { lane: 'aware', received_at: today(2), needs_action: true, due: null, url: 'https://example.test/x' }),
    item('t2', { lane: 'aware', received_at: today(3) }),
    item('x-far', { lane: 'aware', received_at: today(4), needs_action: true, due: ymd(10) }),
    item('y1', { lane: 'aware', received_at: yesterday }),
    item('x-soon', { lane: 'aware', received_at: yesterday, needs_action: true, due: ymd(1), url: 'javascript:alert(1)' }),
    item('o1', { lane: 'aware', received_at: older }),
    item('x-today', { lane: 'aware', received_at: older, needs_action: true, due: ymd(0) }),
    item('x-over', { lane: 'aware', received_at: older, needs_action: true, due: ymd(-2) }),
  ];

  async function openAware() {
    svc.items.mockImplementation(async (lane) => (lane === 'aware' ? AW : lane === 'reply' ? REPLY : []));
    setup();
    fireEvent.click(await screen.findByTestId('inbox-tab-aware'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-act-row')).toHaveLength(5));
  }

  it('leads with the count, then 要你动手 sorted by due date (no date last) with a due chip', async () => {
    await openAware();
    expect(screen.getByTestId('inbox-aware')).toHaveTextContent('9 things worth knowing');
    const all = screen.getAllByTestId('inbox-aware-act-row');
    expect(all.map((r) => r.getAttribute('data-item-id'))).toEqual(['x-over', 'x-today', 'x-soon', 'x-far', 'x-none']);
    const chip = (r: HTMLElement) => within(r).getByTestId('inbox-aware-due');
    expect(chip(all[0])).toHaveTextContent(`Overdue · ${md(-2)}`);
    expect(chip(all[0])).toHaveAttribute('data-tone', 'overdue');
    expect(chip(all[0]).getAttribute('style')).toMatch(/status-error/);
    expect(chip(all[1])).toHaveTextContent('Due today');
    expect(chip(all[1])).toHaveAttribute('data-tone', 'today');
    expect(chip(all[1]).getAttribute('style')).toMatch(/status-warning/);
    const rows = all.slice(2);
    expect(chip(rows[0])).toHaveTextContent(`Due ${md(1)}`);
    expect(chip(rows[0])).toHaveAttribute('data-tone', 'soon');
    expect(chip(rows[0]).getAttribute('style')).toMatch(/status-warning/);
    expect(chip(rows[1])).toHaveAttribute('data-tone', 'later');
    expect(chip(rows[1]).getAttribute('style')).not.toMatch(/status-/);
    expect(within(rows[2]).queryByTestId('inbox-aware-due')).toBeNull();
    // the section comes before the day groups
    const act = screen.getByTestId('inbox-aware-act');
    expect(act.compareDocumentPosition(screen.getAllByTestId('inbox-aware-day')[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 打开原文 only for an http(s) link; 处理好了 always
    expect(within(rows[0]).queryByTestId('inbox-aware-open')).toBeNull();
    expect(within(rows[2]).getByTestId('inbox-aware-open')).toBeInTheDocument();
    expect(screen.getAllByTestId('inbox-aware-handled')).toHaveLength(5);
    fireEvent.click(within(rows[1]).getByTestId('inbox-aware-handled'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('x-far', 'done'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-act-row')).toHaveLength(4));
    // focus moves to the next row shown
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-act-row')[3]).toHaveFocus());
    expect(screen.getAllByTestId('inbox-aware-act-row')[3].getAttribute('data-item-id')).toBe('x-none');
  });

  it('groups the rest by day, newest day first, and 这一天的都知道了 marks that day only', async () => {
    await openAware();
    const days = screen.getAllByTestId('inbox-aware-day');
    expect(days).toHaveLength(3);
    expect(days[0]).toHaveTextContent('Today');
    expect(days[1]).toHaveTextContent('Yesterday');
    expect(days[2].textContent).toMatch(/^[A-Z][a-z]{2} \d{1,2}/);
    expect(within(days[0]).getAllByTestId('inbox-aware-row').map((r) => r.getAttribute('data-item-id'))).toEqual(['t1', 't2']);
    expect(within(days[1]).getAllByTestId('inbox-aware-row').map((r) => r.getAttribute('data-item-id'))).toEqual(['y1']);
    expect(within(days[0]).getAllByTestId('inbox-aware-row')[0]).toHaveTextContent('+3');
    svc.doneMany.mockResolvedValue(5);   // the server also marked the threads' older items
    const btn = within(days[0]).getByTestId('inbox-aware-day-done');
    fireEvent.click(btn);
    // the first click only asks
    expect(btn).toHaveTextContent('Click again to mark these 2 as seen');
    expect(svc.doneMany).not.toHaveBeenCalled();
    // leaving the button takes the question back
    fireEvent.mouseLeave(btn);
    expect(btn).toHaveTextContent('Mark this day as seen');
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(svc.doneMany).toHaveBeenCalledWith(['t1', 't2']));
    expect(svc.doneMany).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-day')).toHaveLength(2));
    expect(screen.getByTestId('inbox-status')).toHaveTextContent('5 marked as seen');
    await waitFor(() => expect(screen.getAllByTestId('inbox-aware-row')[0]).toHaveFocus());
    expect(screen.getAllByTestId('inbox-aware-row').map((r) => r.getAttribute('data-item-id'))).toEqual(['y1', 'o1']);
    expect(svc.action).not.toHaveBeenCalled();
  });

  it('a failed 这一天的都知道了 hides nothing', async () => {
    await openAware();
    svc.doneMany.mockRejectedValue(new Error('boom'));
    const btn = within(screen.getAllByTestId('inbox-aware-day')[1]).getByTestId('inbox-aware-day-done');
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId('inbox-status')).toHaveTextContent("That didn't go through"));
    expect(screen.getAllByTestId('inbox-aware-day')).toHaveLength(3);
  });
});

// ── narrow ───────────────────────────────────────────────────────────────────
describe('Inbox narrow layout', () => {
  it('narrow layout back button: one pane at a time', async () => {
    roWidth = 600;
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-overlay')).toHaveAttribute('data-narrow', 'true'));
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(2));
    expect(screen.queryByTestId('inbox-detail')).toBeNull();
    fireEvent.click(screen.getAllByTestId('inbox-item')[1]);
    await waitFor(() => expect(screen.getByTestId('inbox-detail')).toBeInTheDocument());
    expect(screen.queryByTestId('inbox-list-pane')).toBeNull();
    expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r2');
    fireEvent.click(screen.getByTestId('inbox-back'));
    expect(screen.getByTestId('inbox-list-pane')).toBeInTheDocument();
    expect(screen.queryByTestId('inbox-detail')).toBeNull();
  });

  it('narrow: after an action the detail stays open on the next item; the list returns when none is left', async () => {
    roWidth = 600;
    svc.sources.mockResolvedValue([{ id: 'slack-1', kind: 'slack', channel_label: 'Slack', backend: 'mcp:slack', identity: '@me', interval_s: 900, health: 'ok', fix: '', last_pull_at: null, last_error: '', can_send: true, detail: '' }]);
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-item')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('inbox-item')[0]);
    await draftReady('r1');
    fireEvent.click(screen.getByTestId('inbox-send'));
    await waitFor(() => expect(svc.send).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('inbox-ask')).toHaveTextContent('Ask of r2'));
    expect(screen.queryByTestId('inbox-list-pane')).toBeNull();
    fireEvent.click(screen.getByTestId('inbox-dismiss'));
    fireEvent.click(screen.getByTestId('inbox-dismiss-elsewhere'));
    await waitFor(() => expect(svc.action).toHaveBeenCalledWith('r2', 'dismiss'));
    await waitFor(() => expect(screen.getByTestId('inbox-reply-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('inbox-detail')).toBeNull();
    // no row is left: focus is on the lane, not on <body>
    expect(screen.getByTestId('inbox-lane')).toHaveFocus();
  });

  it('wide layout shows the list and the detail together, with no back button', async () => {
    roWidth = 1000;
    setup();
    await draftReady('r1');
    expect(screen.getByTestId('inbox-list-pane')).toBeInTheDocument();
    expect(screen.queryByTestId('inbox-back')).toBeNull();
  });
});
