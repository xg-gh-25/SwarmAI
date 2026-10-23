/**
 * ChatPage with the backend unreachable must not loop.
 *
 * Bug (found by canvas-proprioception run 0): `const { data: agents = [] }` made a
 * NEW empty array on every render while the agent list was unavailable; the
 * default-agent effect depends on `agents`, so each render re-ran it →
 * setAgentLoadError(null) → GET /agents/default fails → setAgentLoadError(msg) →
 * render → … ~17k ChatPage commits and ~5.7k TabView remounts in 4 s.
 *
 * Method: the real ChatPage under the real providers, EVERY request failing at
 * once. Asserts: ChatPage commits stay small, /agents/default is asked a bounded
 * number of times, and the "Failed to load agent" card is what the user sees.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, act, screen } from '@testing-library/react';
import { Profiler } from 'react';

globalThis.fetch = vi.fn(() => Promise.reject(new TypeError('offline'))) as unknown as typeof fetch;
class NoES { close() {} addEventListener() {} removeEventListener() {} }
(globalThis as unknown as { EventSource: unknown }).EventSource = NoES;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

import api from '../../services/api';
const calls: Record<string, number> = {};
api.defaults.adapter = ((config: { url?: string }) => {
  calls[config.url ?? ''] = (calls[config.url ?? ''] ?? 0) + 1;
  return Promise.reject(Object.assign(new Error('offline'), { isAxiosError: true }));
}) as never;

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { HealthProvider } from '../../contexts/HealthContext';
import ThreeColumnLayout from '../../components/layout/ThreeColumnLayout';
import ChatPage from '../ChatPage';

describe('ChatPage — backend unreachable', () => {
  it('shows the agent error without a render loop', async () => {
    let commits = 0;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ThemeProvider><QueryClientProvider client={qc}><ToastProvider><HealthProvider>
        <MemoryRouter><ThreeColumnLayout>
          <Profiler id="chat" onRender={() => { commits += 1; }}><ChatPage /></Profiler>
        </ThreeColumnLayout></MemoryRouter>
      </HealthProvider></ToastProvider></QueryClientProvider></ThemeProvider>,
    );
    await act(async () => { await new Promise((r) => setTimeout(r, 1500)); });
    expect(commits).toBeLessThan(60);
    expect(calls['/agents/default'] ?? 0).toBeLessThanOrEqual(3);
    expect(screen.getByText(/Failed to Load Agent/i)).toBeTruthy();
  }, 30_000);
});
