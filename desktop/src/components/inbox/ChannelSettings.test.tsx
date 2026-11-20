/**
 * ChannelSettings — adding, configuring and removing Inbox sources against a mocked
 * Inbox API (design docs/design/2026-11-20-inbox.md §4, §7).
 *
 * Invariants under test:
 *   - an available source connects with one click: one POST with its kind, backend and
 *     options; the identity the backend found is shown; an added one offers no button;
 *   - a refusal (whoami failed) shows the source's message and fix in place;
 *   - the manual Slack-token and IMAP/SMTP forms post once with the secret in `secrets`
 *     (never in options) and clear the typed secret afterwards; a form missing a
 *     required field posts nothing;
 *   - remove needs a second, confirming click; only then is DELETE sent, once;
 *   - pull on/off, interval and the watched channels go through updateSource; a
 *     read-only source shows why it cannot send; the feed row cannot be removed here.
 *
 * The Inbox service is mocked at the boundary; the page's queries are the real ones.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AvailableSourceOut, SourceOut } from '../../services/inbox';

const svc = vi.hoisted(() => ({
  sources: vi.fn(),
  retrySource: vi.fn(),
  availableSources: vi.fn(),
  addSource: vi.fn(),
  updateSource: vi.fn(),
  removeSource: vi.fn(),
  setSourceSecret: vi.fn(),
  listFeeds: vi.fn(),
}));
vi.mock('../../services/inbox', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/inbox')>();
  return { ...actual, inboxService: svc };
});

import { ChannelSettings } from './ChannelSettings';

const source = (id: string, over: Partial<SourceOut> = {}): SourceOut => ({
  id, kind: 'slack', channel_label: 'Slack', backend: 'mcp:work-slack', identity: '@me', interval_s: 900,
  health: 'ok', fix: '', last_pull_at: null, last_error: '', can_send: true, detail: '', status_detail: '',
  enabled: true, options: { tool_family: 'slack-mcp', watched_channels: ['#ops'] }, ...over,
});
const available = (over: Partial<AvailableSourceOut> = {}): AvailableSourceOut => ({
  kind: 'slack', backend: 'mcp:work-slack', channel_label: 'Slack', label: 'work-slack', options: { tool_family: 'slack-mcp' },
  identity: '', detail: 'slack-mcp', added: false, source_id: null, ...over,
});

beforeEach(() => {
  svc.sources.mockResolvedValue([]);
  svc.availableSources.mockResolvedValue([]);
  svc.listFeeds.mockResolvedValue([]);
  svc.addSource.mockResolvedValue({ ok: true, source: source('slack-new') });
  svc.updateSource.mockImplementation(async (id: string) => source(id));
  svc.removeSource.mockImplementation(async (id: string) => ({ id, items: 3, drafts: 1, sends: 1 }));
  svc.setSourceSecret.mockImplementation(async (id: string) => source(id));
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function setup() {
  const notify = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><ChannelSettings onBack={vi.fn()} notify={notify} /></QueryClientProvider>);
  return { notify };
}

describe('Inbox channel settings: adding a channel', () => {
  it('one click connects an available source and shows the identity found', async () => {
    svc.availableSources.mockResolvedValue([
      available(),
      available({ kind: 'github', backend: 'gh', channel_label: 'GitHub', label: 'GitHub CLI (gh)', options: {}, identity: '@octo', added: true, source_id: 'github-1' }),
    ]);
    let resolve: (v: unknown) => void = () => {};
    svc.addSource.mockImplementation(() => new Promise((r) => { resolve = r; }));
    const { notify } = setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-available-row')).toHaveLength(2));
    const [slack, gh] = screen.getAllByTestId('inbox-available-row');
    expect(gh).toHaveTextContent('@octo');
    expect(within(gh).queryByTestId('inbox-available-connect')).toBeNull();
    const connect = within(slack).getByTestId('inbox-available-connect');
    fireEvent.click(connect);
    fireEvent.click(connect);   // a second click while the first is in flight sends nothing
    expect(svc.addSource).toHaveBeenCalledTimes(1);
    expect(svc.addSource).toHaveBeenCalledWith({ kind: 'slack', backend: 'mcp:work-slack', options: { tool_family: 'slack-mcp' } });
    resolve({ ok: true, source: source('slack-new', { identity: '@me' }) });
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Connected Slack · @me'));
  });

  it('a refused add shows the source message and its fix, and stores nothing', async () => {
    svc.availableSources.mockResolvedValue([available()]);
    svc.addSource.mockResolvedValue({ ok: false, refusal: { status: 400, code: 'whoami_failed', message: 'login expired', fix: 'run mwinit -f' } });
    const { notify } = setup();
    fireEvent.click(await screen.findByTestId('inbox-available-connect'));
    expect(await screen.findByTestId('inbox-add-refusal')).toHaveTextContent('login expired');
    expect(screen.getByTestId('inbox-add-refusal')).toHaveTextContent('run mwinit -f');
    expect(notify).not.toHaveBeenCalled();
  });

  it('the manual Slack form posts the token as a secret once and clears it', async () => {
    setup();
    await waitFor(() => expect(screen.getByTestId('inbox-available-empty')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('inbox-add-slack-open'));
    const token = screen.getByTestId('inbox-add-slack-token');
    expect(token).toHaveAttribute('type', 'password');
    fireEvent.change(token, { target: { value: ' xoxp-123 ' } });
    fireEvent.click(screen.getByTestId('inbox-add-slack-submit'));
    await waitFor(() => expect(svc.addSource).toHaveBeenCalledTimes(1));
    expect(svc.addSource).toHaveBeenCalledWith({ kind: 'slack', backend: 'slack-web', options: {}, secrets: { user_token: 'xoxp-123' } });
    await waitFor(() => expect(screen.queryByTestId('inbox-add-slack-form')).toBeNull());
  });

  it('a refused manual form keeps the form, clears the secret and shows the fix', async () => {
    svc.addSource.mockResolvedValue({ ok: false, refusal: { status: 400, code: 'whoami_failed', message: 'Slack: invalid_auth', fix: 'paste a new token' } });
    setup();
    fireEvent.click(await screen.findByTestId('inbox-add-slack-open'));
    fireEvent.change(screen.getByTestId('inbox-add-slack-token'), { target: { value: 'xoxp-bad' } });
    fireEvent.click(screen.getByTestId('inbox-add-slack-submit'));
    expect(await screen.findByTestId('inbox-add-refusal')).toHaveTextContent('paste a new token');
    expect(screen.getByTestId('inbox-add-slack-token')).toHaveValue('');
  });

  it('the IMAP/SMTP form posts host, user and SMTP as options and the password as a secret', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('inbox-add-imap-open'));
    fireEvent.change(screen.getByTestId('inbox-imap-host'), { target: { value: 'imap.example.com' } });
    fireEvent.change(screen.getByTestId('inbox-imap-username'), { target: { value: 'me@example.com' } });
    fireEvent.click(screen.getByTestId('inbox-add-imap-submit'));
    expect(svc.addSource).not.toHaveBeenCalled();   // no password yet
    expect(screen.getByText('Fill in every field marked *')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('inbox-imap-password'), { target: { value: 'app-password' } });
    fireEvent.change(screen.getByTestId('inbox-imap-smtp_host'), { target: { value: 'smtp.example.com' } });
    fireEvent.click(screen.getByTestId('inbox-add-imap-submit'));
    await waitFor(() => expect(svc.addSource).toHaveBeenCalledTimes(1));
    const body = svc.addSource.mock.calls[0][0];
    expect(body).toEqual({
      kind: 'mail', backend: 'imap',
      options: { host: 'imap.example.com', port: 993, username: 'me@example.com', smtp_host: 'smtp.example.com', smtp_port: 465, smtp_security: 'ssl' },
      secrets: { password: 'app-password' },
    });
    expect(JSON.stringify(body.options)).not.toContain('app-password');
  });
});

describe('Inbox channel settings: a connected source', () => {
  it('remove needs a confirming click, then deletes once', async () => {
    svc.sources.mockResolvedValue([source('slack-1')]);
    const { notify } = setup();
    fireEvent.click(await screen.findByTestId('inbox-source-settings'));
    fireEvent.click(screen.getByTestId('inbox-source-remove'));
    expect(svc.removeSource).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('inbox-source-remove-confirm'));
    await waitFor(() => expect(svc.removeSource).toHaveBeenCalledTimes(1));
    expect(svc.removeSource).toHaveBeenCalledWith('slack-1');
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Slack removed'));
  });

  it('pull on/off, interval and watched channels go through updateSource', async () => {
    svc.sources.mockResolvedValue([source('slack-1')]);
    setup();
    fireEvent.click(await screen.findByTestId('inbox-source-settings'));
    fireEvent.click(screen.getByTestId('inbox-source-enabled'));
    await waitFor(() => expect(svc.updateSource).toHaveBeenCalledWith('slack-1', { enabled: false }));
    fireEvent.change(screen.getByTestId('inbox-source-interval'), { target: { value: '3600' } });
    await waitFor(() => expect(svc.updateSource).toHaveBeenCalledWith('slack-1', { interval_s: 3600 }));
    const list = screen.getByTestId('inbox-source-list-input');
    expect(list).toHaveValue('#ops');
    fireEvent.change(list, { target: { value: '#ops\n C0TEAM1 ,\n' } });
    fireEvent.click(screen.getByTestId('inbox-source-list-save'));
    await waitFor(() => expect(svc.updateSource).toHaveBeenCalledWith('slack-1', { watched_channels: ['#ops', 'C0TEAM1'] }));
  });

  it('a new token for a Web API source is stored as a secret and cleared', async () => {
    svc.sources.mockResolvedValue([source('slack-2', { backend: 'slack-web', health: 'auth_failed', fix: 'paste a new token' })]);
    setup();
    fireEvent.click(await screen.findByTestId('inbox-source-settings'));
    fireEvent.change(screen.getByTestId('inbox-source-secret-input'), { target: { value: 'xoxp-new' } });
    fireEvent.click(screen.getByTestId('inbox-source-secret-save'));
    await waitFor(() => expect(svc.setSourceSecret).toHaveBeenCalledWith('slack-2', 'user_token', 'xoxp-new'));
    expect(screen.getByTestId('inbox-source-secret-input')).toHaveValue('');
  });

  it('a read-only source says why, and the feed row cannot be removed or switched off here', async () => {
    svc.sources.mockResolvedValue([
      source('mail-1', { kind: 'mail', channel_label: 'Mail', backend: 'mcp:outlook', health: 'read_only', can_send: false,
        status_detail: 'sending is off: OUTLOOK_MCP_ENABLE_WRITES is not true', options: {} }),
      source('feed', { kind: 'feed', channel_label: 'Feeds', backend: 'feed', interval_s: 21600, options: {} }),
    ]);
    setup();
    await waitFor(() => expect(screen.getAllByTestId('inbox-source-row')).toHaveLength(2));
    expect(screen.getByTestId('inbox-source-status-detail')).toHaveTextContent('OUTLOOK_MCP_ENABLE_WRITES');
    const feed = screen.getAllByTestId('inbox-source-row')[1];
    fireEvent.click(within(feed).getByTestId('inbox-source-settings'));
    expect(within(feed).queryByTestId('inbox-source-remove')).toBeNull();
    expect(within(feed).queryByTestId('inbox-source-enabled')).toBeNull();
    expect(within(feed).getByTestId('inbox-source-interval')).toHaveValue('21600');
  });
});
