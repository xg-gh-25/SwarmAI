/**
 * A render error in the Swarm Out dashboard stays in the dashboard.
 *
 * Questions: with the dashboard throwing on every render, does the real ChatPage, under
 * the app's own boundary (App.tsx, variant "app"), still render its chat column and tab
 * row instead of the app's Reload screen; does the dashboard's place show the region's
 * error line only while the Swarm Out tab is active, never on another tab.
 *
 * Method: the real ChatPage under the real providers, SwarmOutDashboard replaced by a
 * component that throws; only GET /agents and /agents/default answer.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, act, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../components/swarm-out/SwarmOutDashboard', () => ({
  SwarmOutDashboard: () => { throw new Error('dashboard broke'); },
}));

globalThis.fetch = vi.fn(() => Promise.reject(new TypeError('offline'))) as unknown as typeof fetch;
class NoES { close() {} addEventListener() {} removeEventListener() {} }
(globalThis as unknown as { EventSource: unknown }).EventSource = NoES;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

import api from '../../services/api';
const AGENT = { id: 'default', name: 'Swarm', description: '', model: 'm', skillIds: [], mcpIds: [] };
api.defaults.adapter = ((config: { url?: string }) => (config.url === '/agents' || config.url === '/agents/default'
  ? Promise.resolve({ data: config.url === '/agents' ? [AGENT] : AGENT, status: 200, statusText: 'OK', headers: {}, config })
  : Promise.reject(Object.assign(new Error('offline'), { isAxiosError: true })))) as never;

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { HealthProvider } from '../../contexts/HealthContext';
import ThreeColumnLayout from '../../components/layout/ThreeColumnLayout';
import { ErrorBoundary } from '../../components/common';
import ChatPage from '../ChatPage';

describe('ChatPage — the Swarm Out dashboard has its own boundary', () => {
  it('a dashboard that throws leaves the app running; its error line shows only on its tab', async () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <ErrorBoundary variant="app">
        <ThemeProvider><QueryClientProvider client={qc}><ToastProvider><HealthProvider>
          <MemoryRouter><ThreeColumnLayout><ChatPage /></ThreeColumnLayout></MemoryRouter>
        </HealthProvider></ToastProvider></QueryClientProvider></ThemeProvider>
      </ErrorBoundary>,
    );
    await waitFor(() => expect(screen.getByTestId('swarm-out-icon')).toBeTruthy(), { timeout: 10_000 });
    expect(screen.queryByText(/Reload app/i)).toBeNull();
    expect(screen.getByTestId('chat-panel')).toBeTruthy();
    // another tab is active: the hidden dashboard's error is not shown there
    expect(screen.queryByTestId('layout-region-error')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByTestId('swarm-out-icon')); });
    expect(await screen.findByTestId('layout-region-error')).toBeTruthy();
    expect(screen.getByTestId('chat-panel')).toBeTruthy();
    expect(screen.queryByText(/Reload app/i)).toBeNull();
    quiet.mockRestore();
  }, 30_000);
});
