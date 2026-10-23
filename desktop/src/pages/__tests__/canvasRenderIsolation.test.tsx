/**
 * T3 (canvas-proprioception.md §7, measured in run 0): Canvas state changes must
 * not re-render the message list (TabView). Written against today's code, which
 * passes; it guards run 1 / run 2.
 *
 * Method: the real ChatPage under the real providers; only GET /agents and
 * /agents/default answer, every other call fails at once, so nothing reaches a
 * backend. TabView is wrapped in a counting
 * memo with the SAME shallow-compare as the real one, so the count rises only when
 * the real memo would let a render through. Each Canvas change goes through the
 * same window/document events the UI and ui_command use.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import { memo, Profiler, useEffect, type ProfilerOnRenderCallback } from 'react';

const counts = { tabView: 0, mounts: 0, changed: [] as string[][] };
let prevProps: Record<string, unknown> | null = null;
vi.mock('../chat/components', async (orig) => {
  const real = await orig<typeof import('../chat/components')>();
  const Counted = memo(function CountedTabView(p: Record<string, unknown>) {
    counts.tabView += 1;
    useEffect(() => { counts.mounts += 1; }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount count
    if (prevProps) counts.changed.push(Object.keys(p).filter((k) => p[k] !== prevProps![k]));
    prevProps = p;
    const T = real.TabView as unknown as (q: Record<string, unknown>) => JSX.Element;
    return <T {...p} />;
  });
  return { ...real, TabView: Counted };
});

globalThis.fetch = vi.fn(() => Promise.reject(new TypeError('offline (run0)'))) as unknown as typeof fetch;
class NoES { close() {} addEventListener() {} removeEventListener() {} }
(globalThis as unknown as { EventSource: unknown }).EventSource = NoES;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

import api from '../../services/api';
// Only the agents answer, so the default tab renders (with both failing ChatPage
// shows the agent-error card instead; ChatPage.backendOffline.test.tsx).
const AGENT = { id: 'default', name: 'Swarm' };
api.defaults.adapter = ((config: { url?: string }) => (config.url === '/agents' || config.url === '/agents/default'
  ? Promise.resolve({ data: config.url === '/agents' ? [AGENT] : AGENT, status: 200, statusText: 'OK', headers: {}, config })
  : Promise.reject(Object.assign(new Error('offline (run0)'), { isAxiosError: true })))) as never;

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { HealthProvider } from '../../contexts/HealthContext';
import ChatPage from '../ChatPage';
import { MemoryRouter } from 'react-router-dom';
import ThreeColumnLayout from '../../components/layout/ThreeColumnLayout';

const mounts: Record<string, number> = {};
function Probe({ n, children }: { n: string; children: React.ReactNode }) {
  useEffect(() => { mounts[n] = (mounts[n] ?? 0) + 1; }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount count
  return <>{children}</>;
}

describe('T3 (run 0) — Canvas changes never re-render the message list', () => {
  it('open, pin, rail, open-file: ChatPage commits, TabView renders 0 times', async () => {
    let page = 0; let dur = 0;
    const onRender: ProfilerOnRenderCallback = (_id, _phase, actual) => { page += 1; dur += actual; };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ThemeProvider><QueryClientProvider client={qc}><ToastProvider><HealthProvider>
        <MemoryRouter><Probe n="layout"><ThreeColumnLayout><Probe n="chatpage"><Profiler id="chat" onRender={onRender}><ChatPage /></Profiler></Probe></ThreeColumnLayout></Probe></MemoryRouter>
      </HealthProvider></ToastProvider></QueryClientProvider></ThemeProvider>,
    );
    // tab restore retries 5x (offline) before the default tab shows: poll for it
    for (let i = 0; i < 100 && counts.tabView === 0; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
    }
    expect(mounts).toEqual({ layout: 1, chatpage: 1 });
    expect(counts.tabView).toBeGreaterThan(0); // a default tab rendered

    const step = async (name: string, fire: () => void) => {
      const p0 = page, t0 = counts.tabView, c0 = counts.changed.length, d0 = dur;
      await act(async () => { fire(); await new Promise((r) => setTimeout(r, 50)); });
      const props = [...new Set(counts.changed.slice(c0).flat())];
      console.log(`RUN0 ${name}: ChatPage commits=${page - p0} (${(dur - d0).toFixed(1)}ms dev-mode jsdom) TabView renders=${counts.tabView - t0}${props.length ? ` props=${props.join(',')}` : ''}`);
      expect(page - p0, `${name} must reach ChatPage`).toBeGreaterThan(name.startsWith('idle') ? -1 : 0);
      expect(counts.tabView - t0, `${name} re-rendered the message list (props: ${props.join(',')})`).toBe(0);
    };
    await step('open-canvas', () => window.dispatchEvent(new CustomEvent('swarm:open-canvas')));
    const click = (re: RegExp) => { const b = [...document.querySelectorAll('button')].find((x) => re.test(x.getAttribute('aria-label') || x.getAttribute('title') || '')); expect(b, `button ${re}`).toBeTruthy(); b!.click(); };
    await step('pin (click)', () => click(/pin/i));
    await step('rail (click)', () => click(/collapse canvas|minimi|to rail/i));
    await step('open-file', () => document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: 'a.md' } })));
    await step('idle (control)', () => {});
  }, 90_000);
});
