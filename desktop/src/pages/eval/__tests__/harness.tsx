/** Shared test harness for the Eval overlay: a routed fake of services/api and a
 *  render with a fresh query client plus the overlay's two contexts. */
import { vi } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { EvalActionsContext, GoContext } from '../ui';

export const routes: Record<string, unknown> = {};
export const calls: { method: string; url: string; body?: unknown }[] = [];
export const failNext: Record<string, { status: number; detail: string }> = {};

export function fakeApi() {
  const handle = (method: string) => vi.fn(async (url: string, body?: unknown) => {
    calls.push({ method, url, body });
    const key = `${method} ${url.split('?')[0]}`;
    if (failNext[key]) {
      const f = failNext[key];
      delete failNext[key];
      throw Object.assign(new Error('bad'), { response: { status: f.status, data: { detail: f.detail } } });
    }
    const hit = routes[key] ?? routes[url.split('?')[0]];
    return { data: typeof hit === 'function' ? (hit as (b: unknown) => unknown)(body) : hit ?? {} };
  });
  return { default: { get: handle('GET'), post: handle('POST'), put: handle('PUT'), delete: handle('DELETE') } };
}

export function reset() {
  for (const k of Object.keys(routes)) delete routes[k];
  calls.length = 0;
}

export function show(ui: ReactNode, actions = { askSwarm: vi.fn(), openSession: vi.fn() }, go = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(
    <QueryClientProvider client={client}>
      <EvalActionsContext.Provider value={actions}>
        <GoContext.Provider value={go}>{ui}</GoContext.Provider>
      </EvalActionsContext.Provider>
    </QueryClientProvider>,
  );
  return { ...r, actions, go };
}

export const check = (id: string, passed: boolean, extra: Record<string, unknown> = {}) => ({ id, passed, cases: [], ...extra });
