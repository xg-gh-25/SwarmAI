/**
 * The Hive tab in the real ChatPage (design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4).
 *
 * Questions: does a run's "Open session" (a running run's card on the run board, or a settled
 * run's big card) open one Hive tab after Swarm Out, reading through the
 * Host proxy and holding the relay (R1.1, R2.1); does another run switch it (old relay
 * dropped, still one tab) and the same run select it (R2.2); is a switch refused while
 * the Hive tab holds a draft (R2.3); does a relay 404 close the tab (R2.4); does
 * closing it drop the relay and release nothing local (R3.1); does a saved Hive tab
 * come back with its relay and proxy reads (R3.2); does a link to Swarm Out settings
 * switch to the Swarm Out tab on its settings view.
 *
 * Method: the real ChatPage under the real providers; every api call answered by one
 * adapter keyed on the URL, which records each call.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, screen, fireEvent, waitFor, within } from '@testing-library/react';

globalThis.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
class NoES { close() {} addEventListener() {} removeEventListener() {} }
(globalThis as unknown as { EventSource: unknown }).EventSource = NoES;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

import api from '../../services/api';
import { openSwarmOutSettings } from '../../components/common/settingsEvents';

type Call = { method: string; url: string; data?: unknown };
const calls: Call[] = [];
let savedTabs: unknown = null;
let relayStatus = 200;

const run = (id: string, sid: string, title: string, over: Record<string, unknown> = {}) => ({
  dispatch_id: id, hive_id: 'h1', hive: 'hive-a', session_id: sid, title, origin: 'chat',
  sent_at: new Date().toISOString(), state: 'running', ...over,
});
/** the second run, accepted: its session opens read-only (run-review §3.4) */
let secondAccepted = false;

function answer(method: string, url: string): unknown {
  if (url === '/agents/default') return { id: 'default', name: 'Swarm', description: '', model: 'm', skillIds: [], mcpIds: [] };
  if (url === '/agents') return [{ id: 'default', name: 'Swarm', description: '', model: 'm', skillIds: [], mcpIds: [] }];
  if (url.startsWith('/system/max-tabs')) return { max_tabs: 6, chat_max: 4, memory_pressure: 'ok' };
  if (url === '/settings/open-tabs') return method === 'get' ? savedTabs : { status: 'ok' };
  if (url === '/swarm-out/fleet') return { hives: [{ name: 'hive-a', status: 'running', region: null, version: null, instance_type: null, reachable: true, streaming: 1, saturated: false, memory_percent: 40 }], running: 1 };
  if (url === '/swarm-out/runs') return { runs: [run('d1', 'S1', 'First run'), run('d2', 'S2', 'Second run', secondAccepted
    ? { state: 'stopped', stage: 'accepted', actions: [{ key: 'open_session', primary: false, variant: 'read_only' }] } : {})], asks: [], errors: [] };
  if (/^\/swarm-out\/remote\/S\d\/relay$/.test(url)) {
    if (relayStatus !== 200) throw Object.assign(new Error('relay'), { response: { status: relayStatus } });
    return { session_id: url.split('/')[3], state: null };
  }
  if (url.startsWith('/swarm-out/remote/')) return url.includes('/messages') ? [] : {};
  throw Object.assign(new Error('not stubbed'), { response: { status: 404 } });
}

api.defaults.adapter = (async (config: { url?: string; method?: string; data?: string }) => {
  const url = config.url ?? '';
  const method = (config.method ?? 'get').toLowerCase();
  calls.push({ method, url, data: config.data ? JSON.parse(config.data) : undefined });
  try {
    const data = answer(method, url);
    return { data, status: 200, statusText: 'OK', headers: {}, config };
  } catch (err) {
    const status = (err as { response?: { status?: number } }).response?.status ?? 500;
    return Promise.reject(Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, config, response: { status, data: {}, headers: {}, config } }));
  }
}) as never;

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { HealthProvider } from '../../contexts/HealthContext';
import ThreeColumnLayout from '../../components/layout/ThreeColumnLayout';
import ChatPage from '../ChatPage';
import { SWARM_OUT_TAB_ID, hiveTabId } from '../chat/swarmOut';
import { isRemote, setRemote } from '../../services/remoteSession';

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><ToastProvider><HealthProvider>
      <MemoryRouter><ThreeColumnLayout><ChatPage /></ThreeColumnLayout></MemoryRouter>
    </HealthProvider></ToastProvider></QueryClientProvider></ThemeProvider>,
  );
}

const toasts: string[] = [];
document.addEventListener('swarm:toast', (e) => toasts.push((e as CustomEvent).detail?.message));

const hiveTabs = () => screen.queryAllByTestId('hive-tab-icon');
const relayCalls = (method: string, sid: string) => calls.filter((c) => c.method === method && c.url === `/swarm-out/remote/${sid}/relay`);
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 50)); });

async function openSwarmOut() {
  await waitFor(() => expect(screen.getByTestId('swarm-out-icon')).toBeTruthy(), { timeout: 5000 });
  fireEvent.click(screen.getByTestId('swarm-out-icon'));
  // the run board shows both runs: a running one as a card, a settled one as a row under
  // "Recently done" (always shown, nothing to expand)
  await waitFor(() => expect(screen.queryAllByTestId('so-run-card').length + screen.queryAllByTestId('so-done-row').length).toBe(2), { timeout: 5000 });
}
/** How a person opens a run's Hive session from the run board: a running run's card has
 *  "Open session"; a settled run's row opens its big card, whose command opens it. */
async function openRun(title: string) {
  const card = screen.queryAllByTestId('so-run-card').find((c) => c.textContent?.includes(title));
  if (card) {
    await act(async () => { fireEvent.click(within(card).getByTestId('so-open-session')); });
    return;
  }
  const row = screen.getAllByTestId('so-done-row').find((r) => r.dataset.title === title)!;
  await act(async () => { fireEvent.click(row); });
  const cmd = await screen.findByTestId('swarm-out-cmd-open_session', {}, { timeout: 5000 });
  await act(async () => { fireEvent.click(cmd); });
}

beforeEach(() => {
  calls.length = 0; toasts.length = 0; savedTabs = null; relayStatus = 200; setRemote(null); secondAccepted = false;
});

describe('ChatPage — the Hive tab', () => {
  it('"Open session" on a run opens one Hive tab after Swarm Out, read through the proxy, with the relay held', async () => {
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    expect(hiveTabs()).toHaveLength(1);
    expect(screen.getByText('First run · hive-a')).toBeTruthy();
    expect(relayCalls('post', 'S1')).toHaveLength(1);
    expect(calls.some((c) => c.url.startsWith('/swarm-out/remote/S1/') && c.url.includes('/messages'))).toBe(true);
    expect(calls.some((c) => !c.url.startsWith('/swarm-out/') && c.url.includes('S1'))).toBe(false);
    expect(isRemote('S1')).toBe(true);
  }, 30_000);

  it('another run switches the Hive tab (old relay dropped, still one tab); the same run selects it', async () => {
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    fireEvent.click(screen.getByTestId('swarm-out-icon'));
    await openRun('Second run');
    await settle();
    expect(hiveTabs()).toHaveLength(1);
    expect(screen.getByText('Second run · hive-a')).toBeTruthy();
    expect(relayCalls('delete', 'S1')).toHaveLength(1);
    expect(relayCalls('post', 'S2')).toHaveLength(1);
    expect(isRemote('S2')).toBe(true);
    expect(isRemote('S1')).toBe(false);
    fireEvent.click(screen.getByTestId('swarm-out-icon'));
    await openRun('Second run');
    await settle();
    expect(relayCalls('post', 'S2')).toHaveLength(1);
    expect(hiveTabs()).toHaveLength(1);
  }, 30_000);

  it('a switch is refused while the Hive tab holds a draft', async () => {
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    const box = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'half a thought' } });
    fireEvent.click(screen.getByTestId('swarm-out-icon'));
    await settle();
    await openRun('Second run');
    await settle();
    expect(toasts.length).toBeGreaterThan(0);
    expect(screen.getByText('First run · hive-a')).toBeTruthy();
    expect(relayCalls('post', 'S2')).toHaveLength(0);
    expect(relayCalls('delete', 'S1')).toHaveLength(0);
    expect(isRemote('S1')).toBe(true);
  }, 30_000);

  it('a backend recovery starts the relay again', async () => {
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    await act(async () => { window.dispatchEvent(new Event('swarm:backend-recovered')); });
    await settle();
    expect(relayCalls('post', 'S1')).toHaveLength(2);
  }, 30_000);

  it('a relay 404 closes the Hive tab with a toast', async () => {
    relayStatus = 404;
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    expect(hiveTabs()).toHaveLength(0);
    expect(toasts.length).toBeGreaterThan(0);
    expect(isRemote('S1')).toBe(false);
  }, 30_000);

  it('a relay 409 (a local session holds the id) closes the tab and says why', async () => {
    relayStatus = 409;
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    expect(hiveTabs()).toHaveLength(0);
    expect(toasts.some((m) => /also a session on this machine|撞了/.test(m))).toBe(true);
  }, 30_000);

  it('a relay 410 (the run was discarded or expired) closes the tab and says the run is closed', async () => {
    relayStatus = 410;
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    expect(hiveTabs()).toHaveLength(0);
    expect(toasts.some((m) => /discarded or expired|已丢弃或已过期/.test(m))).toBe(true);
  }, 30_000);

  it('an accepted run opens read-only: a line instead of the input', async () => {
    secondAccepted = true;
    mount();
    await openSwarmOut();
    await openRun('Second run');
    await settle();
    expect(hiveTabs()).toHaveLength(1);
    expect(screen.getByTestId('hive-tab-read-only')).toBeTruthy();
    fireEvent.click(screen.getByTestId('swarm-out-icon'));
    await openRun('First run');
    await settle();
    expect(screen.queryByTestId('hive-tab-read-only')).toBeNull();      // an open run takes messages
  }, 30_000);

  it('a link to Swarm Out settings (from Settings or Usage) switches to the Swarm Out tab on its settings view', async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId('swarm-out-icon')).toBeTruthy(), { timeout: 5000 });
    expect(screen.queryByTestId('swarm-out-settings')).toBeNull();
    await act(async () => { openSwarmOutSettings(); });
    await settle();
    expect(screen.getByTestId('swarm-out-dashboard').style.display).not.toBe('none');
    expect(screen.getByTestId('swarm-out-settings')).toBeTruthy();
  }, 30_000);

  it('closing the Hive tab drops the relay, releases nothing local and goes back to Swarm Out', async () => {
    mount();
    await openSwarmOut();
    await openRun('First run');
    await settle();
    await act(async () => { fireEvent.click(screen.getByLabelText('Close First run · hive-a')); });
    await settle();
    expect(hiveTabs()).toHaveLength(0);
    expect(relayCalls('delete', 'S1')).toHaveLength(1);
    expect(calls.some((c) => c.url.includes('release'))).toBe(false);
    expect(screen.getByTestId('swarm-out-dashboard').style.display).not.toBe('none');
    expect(isRemote('S1')).toBe(false);
  }, 30_000);

  // The Hive session's id must never become a session of the tab focus goes back to:
  // a send there would go to this machine's backend under the Hive session's id, which
  // creates a local session of that id, and the run could never be opened again.
  it.each([['closed by the person', 200], ['closed by a relay 404', 404]])(
    'a Hive tab %s leaves its session id on no local tab', async (_how, status) => {
      relayStatus = status;
      mount();
      await openSwarmOut();
      await openRun('First run');
      await settle();
      if (status === 200) {
        await act(async () => { fireEvent.click(screen.getByLabelText('Close First run · hive-a')); });
      }
      await settle();
      expect(hiveTabs()).toHaveLength(0);
      const mark = calls.length;
      // open_tabs.json is written as soon as the tabs change: once with the Hive tab, then without it
      const puts = () => calls.filter((c) => c.method === 'put' && c.url === '/settings/open-tabs')
        .map((c) => (c.data as { tabs: { id: string; sessionId?: string }[] }).tabs);
      expect(puts().some((tabs) => tabs.some((t) => t.sessionId === 'S1'))).toBe(true);
      await waitFor(() => expect(puts().at(-1)!.filter((t) => t.sessionId === 'S1')).toEqual([]), { timeout: 5000 });
      expect(calls.slice(mark).some((c) => !c.url.startsWith('/swarm-out/') && c.url.includes('S1'))).toBe(false);
    }, 30_000);

  it('a saved Hive tab comes back with its relay and proxy reads', async () => {
    savedTabs = {
      tabs: [
        { id: 'a', title: 'mine', agentId: 'default', isNew: true },
        { id: SWARM_OUT_TAB_ID, title: 'Swarm Out', agentId: 'default', isNew: true },
        { id: hiveTabId('S1'), title: 'First run · hive-a', agentId: 'default', isNew: false, sessionId: 'S1' },
      ],
      activeTabId: hiveTabId('S1'),
    };
    mount();
    await waitFor(() => expect(hiveTabs()).toHaveLength(1), { timeout: 5000 });
    await settle();
    expect(relayCalls('post', 'S1')).toHaveLength(1);
    expect(calls.some((c) => c.url.startsWith('/swarm-out/remote/S1/') && c.url.includes('/messages'))).toBe(true);
    expect(calls.some((c) => c.url.startsWith('/chat/') && c.url.includes('S1'))).toBe(false);
  }, 30_000);
});
